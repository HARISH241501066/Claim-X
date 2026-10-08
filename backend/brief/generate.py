"""Brief generation: the LLM writes only when LLM_API_KEY is set, and only validated text is used.

Any problem (no key, bad key, network error, refusal, truncation, or text that fails the
validator) returns the deterministic template brief instead. The key is only handed to the SDK
client; it is never logged or returned.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic

from backend.brief.evidence import DB_PATH, DEFAULT_HORIZON, Pack, build_pack
from backend.brief.template import FINAL_LINE, SECTIONS, render_template
from backend.brief.validator import validate_brief

log = logging.getLogger("claimshield.brief")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 8000
REQUEST_TIMEOUT_SECONDS = 60.0

SYSTEM_PROMPT = f"""You write investigation briefs for a healthcare payer's special \
investigations unit. All data is synthetic.

Rules:
- Use only the evidence pack you are given. Do not add facts, names, numbers or evidence.
- Cite every factual statement with its evidence key in square brackets, such as [E1]. \
Use only keys that appear in the pack, one key per bracket.
- Describe behaviour as suspicious or as warranting review. Never state or imply guilt, \
and never call anyone a fraudster or say anyone committed fraud.
- Do not change the confidence level or the limitations: copy them exactly as given.
- Write exactly these sections as markdown headings (##), in this order: \
{', '.join(SECTIONS)}.
- End the brief with this exact line and nothing after it: {FINAL_LINE}"""


class LLMUnusableError(Exception):
    """The model replied, but not with a usable brief (refusal, truncation or empty text)."""


@dataclass
class Brief:
    case_id: str
    text: str
    source: str  # "llm" or "template"
    fallback_reason: str | None = None
    model: str | None = None


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


def build_prompt(pack: Pack) -> str:
    data = json.dumps(pack.to_prompt_dict(), indent=2, sort_keys=True, ensure_ascii=False)
    limitations = "\n".join(f"- {text}" for text in pack.limitations)
    return (
        f"Write the investigation brief for case {pack.case_id}.\n\n"
        f"In the Confidence section include this exact line: "
        f"Confidence level: {pack.confidence['level']}\n"
        f"In the Limitations section include these bullets exactly:\n{limitations}\n\n"
        f"Evidence pack (JSON):\n{data}"
    )


def call_llm(client: Any, model: str, pack: Pack) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": build_prompt(pack)}],
    )
    if response.stop_reason == "refusal":
        raise LLMUnusableError("the model declined the request")
    if response.stop_reason == "max_tokens":
        raise LLMUnusableError("the reply was cut off")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise LLMUnusableError("the reply was empty")
    return text


def _describe(exc: Exception) -> str:
    """A safe description of a failure: the class and status only, never the message."""
    status = getattr(exc, "status_code", None)
    return f"{type(exc).__name__}" + (f" ({status})" if status else "")


def generate_brief(
    case_id: str,
    horizon: int = DEFAULT_HORIZON,
    db_path: str | Path = DB_PATH,
    client: Any = None,
    env: Mapping[str, str] | None = None,
) -> Brief:
    pack = build_pack(case_id, horizon, db_path)
    template = render_template(pack)

    def fallback(reason: str) -> Brief:
        log.info("brief for %s uses the template: %s", case_id, reason)
        return Brief(case_id, template, "template", reason)

    key = resolve_setting("LLM_API_KEY", env)
    if client is None:
        if not key:
            return fallback("no LLM_API_KEY is set")
        client = anthropic.Anthropic(
            api_key=key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1
        )
    model = resolve_setting("LLM_MODEL", env) or DEFAULT_MODEL
    try:
        text = call_llm(client, model, pack)
    except LLMUnusableError as exc:
        return fallback(f"LLM reply unusable: {exc}")
    except Exception as exc:  # noqa: BLE001 - fail safely: any LLM problem returns the template
        log.warning("LLM call failed: %s", _describe(exc))
        return fallback(f"LLM call failed: {_describe(exc)}")
    problems = validate_brief(text, pack)
    if problems:
        return fallback(f"LLM text rejected: {'; '.join(problems)}")
    return Brief(case_id, text.rstrip() + "\n", "llm", None, model)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    brief = generate_brief(sys.argv[1] if len(sys.argv) > 1 else "CASE-0001")
    print(brief.text)
    print(f"[source: {brief.source}" + (f"; {brief.fallback_reason}" if brief.fallback_reason else "") + "]")
