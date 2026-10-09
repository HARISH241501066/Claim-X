"""Brief generation: an outside LLM may help, but only ever sees masked data.

LLM_PROVIDER picks who writes the brief: none (the default), anthropic, xai or groq. With none, a
missing key, or any problem (a leak check that fails, a timeout, an error, or text that fails
validation) the deterministic template brief is returned instead. The pack is reshaped to the
fields the masker allows, identifiers become placeholders, and a leak check runs before every
request. The reply is validated while still masked and only then restored for display. The key
is only handed to the provider's client; it is never logged or returned.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import openai

from backend.brief.evidence import DB_PATH, DEFAULT_HORIZON, Pack, build_pack
from backend.brief.llm_payload import IDENTIFIER, prepare, unknown_placeholders
from backend.brief.masker import LeakError, Vault, audit_record, unmask_text
from backend.brief.template import FINAL_LINE, SECTIONS, render_template
from backend.brief.validator import validate_brief

log = logging.getLogger("claimshield.brief")
REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_TOKENS = 8000
REQUEST_TIMEOUT_SECONDS = 15.0
RETRY_PAUSE_SECONDS = 3.0  # the shortest wait before the one retry of a 429 or a timeout
INTERACTIVE_WAIT_CAP = 8.0  # a person is waiting on the page: do not stall longer than this
PREWARM_WAIT_CAP = 45.0  # a background run can wait out a provider's rate-limit window
MASKED_NOTE = "Generated from masked data. No personal details were shared."


@dataclass(frozen=True)
class Provider:
    key: str
    label: str  # what the screen calls it
    default_model: str
    base_url: str | None = None  # set for providers reached through the openai package


PROVIDERS = {
    "anthropic": Provider("anthropic", "Claude", "claude-opus-5-5"),
    "xai": Provider("xai", "Grok", "grok-4", "https://api.x.ai/v1"),
    "groq": Provider("groq", "Groq", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1"),
}

SYSTEM_PROMPT = f"""You write investigation briefs for a healthcare payer's special \
investigations unit. All data is synthetic.

Rules:
- Use only the evidence pack you are given. Do not add facts, names, numbers or evidence.
- Cite every factual statement with its evidence key in plain ASCII square brackets, \
such as [E1], never 【E1】 or (E1). Use only keys that appear in the pack, one key per bracket.
- Describe behaviour as suspicious or as warranting review. Never state or imply guilt, \
and never call anyone a fraudster or say anyone committed fraud.
- People and organisations appear as placeholders such as PERSON_1 and ORG_2. Use them \
exactly as given and never guess who they are.
- Do not change the confidence level or the limitations: copy them exactly as given.
- In the Evidence section, begin each item with its [E#] and then the type of check that found it, \
copied exactly from its detector field (for example "Duplicate billing (claim rule)"), then what was found.
- Write exactly these sections as markdown headings (##), in this order: \
{', '.join(SECTIONS)}. Use short paragraphs and bullet lists; do not use tables.
- End the brief with this exact line and nothing after it: {FINAL_LINE}"""


FULLWIDTH_CITATION = re.compile(r"[【［]\s*(E\d+)\s*[】］]")


TYPOGRAPHIC_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-"})  # hyphen, non-breaking hyphen


def normalize_reply(text: str) -> str:
    """Undo two typographic habits of some models before validating: fullwidth citation
    brackets and non-breaking hyphens (which break an exact heading such as human-review).
    Only look-alike characters change; no check is relaxed."""
    return normalize_citations(text.translate(TYPOGRAPHIC_HYPHENS))


def normalize_citations(text: str) -> str:
    """Turn 【E1】 (fullwidth brackets, which some models like) into [E1]. Only the bracket style
    changes: the key inside is still checked against the pack, so a made-up 【E99】 is rejected."""
    return FULLWIDTH_CITATION.sub(r"[\1]", text)


class LLMUnusableError(Exception):
    """The model replied, but not with a usable brief (refusal, truncation or empty text)."""


@dataclass
class Brief:
    case_id: str
    text: str
    source: str  # "llm" or "template"
    fallback_reason: str | None = None
    model: str | None = None
    provider: str | None = None  # anthropic | xai | groq, only when an LLM wrote it
    masked: bool = False  # True when an LLM wrote it from masked data
    retryable: bool = False  # True when an LLM was tried and failed, so asking again may succeed
    cached: bool = False  # True when the stored brief for unchanged evidence was reused
    attempts: int = 0  # how many LLM calls this request made (0 for a cache hit or a template)


@dataclass
class LLMReply:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read KEY=VALUE lines from the repo-root .env file, if there is one."""
    path = path or REPO_ROOT / ".env"
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    return values


def resolve_setting(name: str, env: Mapping[str, str] | None) -> str:
    """Environment first, then .env. A blank value counts as not set."""
    source = os.environ if env is None else env
    value = (source.get(name) or "").strip()
    if not value and env is None:
        value = load_env().get(name, "").strip()
    return value


def build_prompt(masked: dict, pack: Pack) -> str:
    """The user message: the masked pack plus the two lines the reply must copy exactly."""
    data = json.dumps(masked, indent=2, sort_keys=True, ensure_ascii=False)
    limitations = "\n".join(f"- {text}" for text in pack.limitations)
    return (
        f"Write the investigation brief for case {pack.case_id}.\n\n"
        f"In the Confidence section include this exact line: "
        f"Confidence level: {pack.confidence['level']}\n"
        f"In the Limitations section include these bullets exactly:\n{limitations}\n\n"
        f"Evidence pack (JSON):\n{data}"
    )


def make_client(provider: Provider, key: str) -> Any:
    if provider.key == "anthropic":
        return anthropic.Anthropic(api_key=key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
    return openai.OpenAI(
        api_key=key, base_url=provider.base_url, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0
    )


def call_llm(client: Any, provider: Provider, model: str, user_prompt: str) -> LLMReply:
    if provider.key == "anthropic":
        response = client.messages.create(
            model=model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": user_prompt}],
        )  # fmt: skip
        if response.stop_reason == "refusal":
            raise LLMUnusableError("the model declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMUnusableError("the reply was cut off")
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = getattr(response, "usage", None)
        tokens = (getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))
    else:
        response = client.chat.completions.create(
            model=model, max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": user_prompt}],
        )  # fmt: skip
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMUnusableError("the reply was cut off")
        if choice.finish_reason == "content_filter":
            raise LLMUnusableError("the reply was filtered")
        text = choice.message.content or ""
        usage = getattr(response, "usage", None)
        tokens = (getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))
    if not text.strip():
        raise LLMUnusableError("the reply was empty")
    return LLMReply(normalize_reply(text.strip()), tokens[0], tokens[1])


def _describe(exc: Exception) -> str:
    """A safe description of a failure: the class and status only, never the message."""
    status = getattr(exc, "status_code", None)
    return f"{type(exc).__name__}" + (f" ({status})" if status else "")


def _is_timeout(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or type(exc).__name__ == "APITimeoutError"


def retry_wait(exc: Exception, cap: float) -> float | None:
    """Seconds to wait before the single retry, or None when this failure is not worth retrying.
    A timeout waits a flat pause. A 429 waits for the provider's Retry-After if that is longer.
    A wait beyond `cap` is not taken: the template is used now and the next request tries again."""
    if _is_timeout(exc):
        return RETRY_PAUSE_SECONDS
    if getattr(exc, "status_code", None) != 429:
        return None
    wait = RETRY_PAUSE_SECONDS
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        wait = max(wait, float(headers.get("retry-after"))) if headers else wait
    except (TypeError, ValueError, AttributeError):
        pass
    return wait if wait <= cap else None


def pack_hash(masked: dict) -> str:
    """Identifies the evidence as the LLM sees it (masked, so no real value is in it) plus the
    instructions, so a change to either one regenerates the brief."""
    canonical = json.dumps(masked, sort_keys=True, ensure_ascii=False) + SYSTEM_PROMPT
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_brief(
    case_id: str,
    horizon: int = DEFAULT_HORIZON,
    db_path: str | Path = DB_PATH,
    client: Any = None,
    env: Mapping[str, str] | None = None,
    record: Callable[[dict], None] | None = None,
    cache: Any = None,
    wait_cap: float = INTERACTIVE_WAIT_CAP,
) -> Brief:
    """The brief for a case. `record` receives one dict per LLM attempt (no real values in it).
    `cache` (an AuditLog) stores accepted briefs so unchanged evidence needs no new LLM call.
    A 429 or timeout is retried once; a reply that fails validation is retried once with the
    validator's message added to the prompt; after that the template is used."""
    pack = build_pack(case_id, horizon, db_path)
    template = render_template(pack)

    def use_template(reason: str, retryable: bool = False) -> Brief:
        log.info("brief for %s uses the template: %s", case_id, reason)
        return Brief(case_id, template, "template", reason, retryable=retryable)

    name = (resolve_setting("LLM_PROVIDER", env) or "none").lower()
    if name == "none":
        return use_template("LLM_PROVIDER is none")
    provider = PROVIDERS.get(name)
    if provider is None:
        return use_template(f"unknown LLM_PROVIDER '{name[:20]}'")
    key = resolve_setting("LLM_API_KEY", env)
    if client is None and not key:
        return use_template("no LLM_API_KEY is set")
    model = resolve_setting("LLM_MODEL", env) or provider.default_model

    def log_request(
        outcome: str, vault: Vault, detail: str | None, reply: LLMReply | None = None, attempt: int = 1
    ):
        if record is None:
            return
        try:
            record(
                {
                    "case_id": case_id, "provider": provider.key, "model": model,
                    "outcome": outcome, "audit": audit_record(vault, provider.key),
                    "input_tokens": reply.input_tokens if reply else None,
                    "output_tokens": reply.output_tokens if reply else None, "detail": detail,
                    "attempt": attempt,
                }
            )  # fmt: skip
        except Exception:  # noqa: BLE001 - a logging problem must not break the brief
            log.warning("could not record the LLM request for %s", case_id)

    try:
        masked, vault = prepare(pack)
    except LeakError as exc:  # never send anything if a value slipped through masking
        log.warning("LLM request for %s blocked: %s", case_id, exc)
        log_request("leak_blocked", getattr(exc, "vault", Vault()), str(exc))
        return use_template(f"blocked before sending: {exc}", retryable=True)

    digest = pack_hash(masked)
    if cache is not None:
        try:
            hit = cache.get_cached_brief(case_id, horizon, provider.key, digest)
        except Exception:  # noqa: BLE001 - a cache problem just means asking the LLM
            hit = None
        if hit:
            text = unmask_text(hit["masked_text"], vault)
            if not validate_brief(text, pack):  # stale or damaged entries are ignored, not served
                return Brief(case_id, text.rstrip() + "\n", "llm", None, hit["model"],
                             provider.key, True, cached=True)  # fmt: skip

    llm = client or make_client(provider, key)
    base_prompt = build_prompt(masked, pack)
    prompt = base_prompt
    transport_retried = validation_retried = False
    attempt = 0
    while True:
        attempt += 1
        try:
            reply = call_llm(llm, provider, model, prompt)
        except Exception as exc:  # noqa: BLE001 - fail safely: any LLM problem returns the template
            detail = f"unusable reply: {exc}" if isinstance(exc, LLMUnusableError) else _describe(exc)
            log.warning("LLM call for %s failed (attempt %d): %s", case_id, attempt, detail)
            log_request("error", vault, detail, attempt=attempt)
            wait = None if transport_retried else retry_wait(exc, wait_cap)
            if wait is None:
                out = use_template(f"LLM call failed: {detail}", retryable=True)
                out.attempts = attempt
                return out
            transport_retried = True
            time.sleep(wait)
            continue

        # Validate the reply while it is still masked, then restore the identifiers for display.
        masked_problems = validate_brief(reply.text, pack)
        masked_problems += [f"unknown placeholder {t}" for t in unknown_placeholders(reply.text, vault)]
        problems = list(masked_problems)
        text = unmask_text(reply.text, vault) if not problems else ""
        problems += validate_brief(text, pack) if text else []
        if not problems:
            log_request("llm_ok", vault, None, reply, attempt)
            if cache is not None:
                try:
                    cache.put_cached_brief(
                        case_id=case_id, horizon=horizon, provider=provider.key, pack_hash=digest,
                        masked_text=reply.text, model=model, input_tokens=reply.input_tokens,
                        output_tokens=reply.output_tokens,
                    )  # fmt: skip
                except Exception:  # noqa: BLE001 - failing to store must not lose the brief
                    log.warning("could not cache the brief for %s", case_id)
            return Brief(case_id, text.rstrip() + "\n", "llm", None, model, provider.key, True,
                         attempts=attempt)  # fmt: skip
        log_request("validation_failed", vault, "; ".join(problems), reply, attempt)
        if validation_retried:
            out = use_template(f"LLM text rejected: {'; '.join(problems)}", retryable=True)
            out.attempts = attempt
            return out
        validation_retried = True
        # Only problems found in the masked reply are quoted back: they contain no real value.
        feedback = "; ".join(masked_problems) or "the restored text failed the same checks"
        prompt = (
            f"{base_prompt}\n\nYour previous reply was rejected by the validator: {feedback}. "
            "Write the whole brief again and fix these problems."
        )
        if IDENTIFIER.search(prompt):  # the same leak check, on the longer retry prompt
            log_request("leak_blocked", vault, "An identifier pattern appeared in the retry prompt",
                        attempt=attempt + 1)  # fmt: skip
            out = use_template("blocked before sending: identifier in retry prompt", retryable=True)
            out.attempts = attempt
            return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    brief = generate_brief(sys.argv[1] if len(sys.argv) > 1 else "CASE-0001")
    print(brief.text)
    note = f"; {brief.fallback_reason}" if brief.fallback_reason else f"; {MASKED_NOTE}"
    print(f"[source: {brief.source}{' via ' + brief.provider if brief.provider else ''}{note}]")
