"""How a notification reaches people: in the platform always, by email only when enabled.

InAppNotifier writes the notification. SnsEmailNotifier publishes to an AWS SNS topic, only when
NOTIFY_EMAIL_ENABLED is true and a topic is configured. ConsoleNotifier stands in when email is
enabled but AWS is not configured: it logs, and the email counts as failed. An email problem of
any kind (missing config, an AWS error, a 5 second timeout) is logged and recorded as `failed`;
it never stops the in-app notification or the pipeline.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config

from backend.brief.generate import resolve_setting
from backend.notify.store import NotifyStore

log = logging.getLogger("claimx.notify")
SNS_TIMEOUT_SECONDS = 5
DEFAULT_BASE_URL = "http://localhost:5173"


@dataclass(frozen=True)
class Message:
    """An email: only ever built by notify.emails, which keeps personal details out of it."""

    subject: str
    body: str
    case_id: str | None = None


@dataclass(frozen=True)
class NotifyConfig:
    email_enabled: bool = False
    topic_arn: str = ""
    region: str = ""
    base_url: str = DEFAULT_BASE_URL

    @property
    def aws_configured(self) -> bool:
        return bool(self.topic_arn)


def load_config(env: Mapping[str, str] | None = None) -> NotifyConfig:
    """Settings from the environment, then the repo-root .env. Email is off unless asked for."""
    enabled = resolve_setting("NOTIFY_EMAIL_ENABLED", env).lower() in {"true", "1", "yes"}
    return NotifyConfig(
        email_enabled=enabled,
        topic_arn=resolve_setting("SNS_TOPIC_ARN", env),
        region=resolve_setting("AWS_REGION", env),
        base_url=resolve_setting("APP_BASE_URL", env) or DEFAULT_BASE_URL,
    )


class Notifier(ABC):
    """One way of delivering. `send` returns a short status and never raises."""

    @abstractmethod
    def send(self, message: Message) -> str: ...


class InAppNotifier(Notifier):
    """Always on: the notification row is written by the dispatcher through this channel."""

    def __init__(self, store: NotifyStore):
        self.store = store

    def add(self, **fields: Any) -> dict:
        return self.store.add_notification(**fields)

    def send(self, message: Message) -> str:  # the in-app copy is the row itself
        return "stored"


class ConsoleNotifier(Notifier):
    """Used when email is enabled but AWS is not configured: nothing is sent, so it is `failed`."""

    def send(self, message: Message) -> str:
        log.warning("email not sent (AWS SNS is not configured): %s", message.subject)
        return "failed"


def _aws_code(exc: Exception) -> str:
    """' (AccessDenied)'-style suffix from an AWS error, or '' when there is none. Never raises."""
    try:
        code = exc.response["Error"]["Code"]  # type: ignore[attr-defined]
        return f" ({code})" if code else ""
    except Exception:  # noqa: BLE001 - timeouts and others carry no response
        return ""


class SnsEmailNotifier(Notifier):
    def __init__(self, config: NotifyConfig, client_factory: Callable[[], Any] | None = None):
        self.config = config
        self.client_factory = client_factory or self._default_client
        self.last_error: str | None = None  # class and AWS code only, never the message text

    def _default_client(self) -> Any:
        timeouts = Config(
            connect_timeout=SNS_TIMEOUT_SECONDS, read_timeout=SNS_TIMEOUT_SECONDS,
            retries={"max_attempts": 0},
        )  # fmt: skip
        # boto3 reads credentials from the process environment or ~/.aws, not from .env, so keys
        # kept in the git-ignored .env are passed in here. They are never logged or stored.
        keys = {
            "aws_access_key_id": resolve_setting("AWS_ACCESS_KEY_ID", None),
            "aws_secret_access_key": resolve_setting("AWS_SECRET_ACCESS_KEY", None),
            "aws_session_token": resolve_setting("AWS_SESSION_TOKEN", None),
        }
        given = {name: value for name, value in keys.items() if value}
        if "aws_access_key_id" not in given or "aws_secret_access_key" not in given:
            given = {}  # incomplete: fall back to boto3's usual sources (profile, role)
        return boto3.client(
            "sns", region_name=self.config.region or None, config=timeouts, **given
        )

    def send(self, message: Message) -> str:
        self.last_error = None
        try:
            self.client_factory().publish(
                TopicArn=self.config.topic_arn, Subject=message.subject, Message=message.body
            )
        except Exception as exc:  # noqa: BLE001 - email must never break the caller
            self.last_error = type(exc).__name__ + _aws_code(exc)
            log.warning("SNS publish failed: %s", self.last_error)
            return "failed"
        return "sent"


def email_channel(config: NotifyConfig, client_factory: Callable[[], Any] | None = None) -> Notifier | None:
    """None when email is off; SNS when configured; the console stand-in otherwise."""
    if not config.email_enabled:
        return None
    if config.aws_configured:
        return SnsEmailNotifier(config, client_factory)
    return ConsoleNotifier()
