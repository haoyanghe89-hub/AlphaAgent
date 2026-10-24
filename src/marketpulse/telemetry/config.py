"""Explicit configuration; credentials never enter a business record."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


@dataclass(frozen=True)
class TraceConfig:
    enabled: bool = False
    public_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    base_url: str = "https://cloud.langfuse.com"
    journal_path: Path = Path("data/observability/traces.sqlite3")
    environment: str = "development"
    timeout: float = 10

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or parsed.scheme not in {"http", "https"}
            or parsed.scheme == "http"
            and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("Langfuse URL must be HTTPS or credential-free local HTTP")
        if not 0 < self.timeout <= 10:
            raise ValueError("Langfuse timeout must be between zero and ten seconds")

    @property
    def configured(self):
        return bool(self.public_key and self.secret_key)

    @classmethod
    def from_env(cls, env_file=None):
        values = {
            **dotenv_values(
                env_file or os.getenv("MARKETPULSE_ENV_FILE", ".env"), interpolate=False
            ),
            **os.environ,
        }
        public = values.get("LANGFUSE_PUBLIC_KEY") or ""
        secret = values.get("LANGFUSE_SECRET_KEY") or ""
        enabled = values.get("ALPHAAGENT_OBSERVABILITY_ENABLED")
        return cls(
            enabled=enabled.lower() == "true" if enabled is not None else bool(public and secret),
            public_key=public,
            secret_key=secret,
            base_url=(
                values.get("LANGFUSE_BASE_URL")
                or values.get("LANGFUSE_HOST")
                or "https://cloud.langfuse.com"
            ).rstrip("/"),
            journal_path=Path(
                values.get("ALPHAAGENT_TRACE_JOURNAL") or "data/observability/traces.sqlite3"
            ),
            environment=values.get("LANGFUSE_TRACING_ENVIRONMENT") or "development",
            timeout=float(values.get("ALPHAAGENT_TRACE_TIMEOUT_SECONDS") or 10),
        )
