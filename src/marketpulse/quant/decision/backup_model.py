"""Optional independent, server-only OpenAI-compatible model transport."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import SecretStr

from marketpulse.agents.factory import configure_deepseek
from marketpulse.errors import ConfigurationError
from marketpulse.investigation.adapters.model import OpenAICompatibleModelAdapter


def configuration():
    values = {
        **dotenv_values(Path(os.getenv("MARKETPULSE_ENV_FILE", ".env")), interpolate=False),
        **os.environ,
    }
    names = ("DECISION_BACKUP_BASE_URL", "DECISION_BACKUP_API_KEY", "DECISION_BACKUP_MODEL")
    items = [values.get(name) or "" for name in names]
    if not any(items):
        return None
    if not all(items):
        raise ConfigurationError("独立备用模型配置不完整，请配置地址、密钥和模型名称。")
    base, key, model = items
    url = urlsplit(base)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ConfigurationError("独立备用模型地址必须为不含凭据的 HTTPS 地址。")
    return SimpleNamespace(
        base_url=base.rstrip("/"), api_key=SecretStr(key), model=model, provider=url.hostname
    )


@asynccontextmanager
async def open_backup(settings, *, enabled=True, repo=None, state=None):
    config = configuration() if enabled else None
    if config is None:
        yield None
        return
    client = configure_deepseek(
        settings.model_copy(
            update=dict(
                deepseek_base_url=config.base_url,
                deepseek_api_key=config.api_key,
                model=config.model,
            )
        )
    )

    def archive(request, content):
        if repo is not None and state is not None:
            state["calls"][-1]["raw_model_response_ref"] = repo.raw(content.encode("utf8"))

    try:
        yield SimpleNamespace(
            model=config.model,
            port=OpenAICompatibleModelAdapter(
                client,
                provider=config.provider,
                default_model=config.model,
                thinking_enabled=False,
                response_observer=archive,
            ),
        )
    finally:
        await client.close()
