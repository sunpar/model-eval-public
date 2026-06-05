from __future__ import annotations

import os

from model_eval_api.providers.errors import ProviderBlockedError
from model_eval_api.providers.models import ProviderExecutionConfig, ProviderRequest


def provider_config_from_env() -> ProviderExecutionConfig:
    return ProviderExecutionConfig(
        local_only=_env_bool("MODEL_EVAL_LOCAL_ONLY", default=True),
        allowed_providers=_csv_env("MODEL_EVAL_ALLOWED_PROVIDERS"),
        denied_providers=_csv_env("MODEL_EVAL_DENIED_PROVIDERS") or (),
    )


def enforce_provider_config(
    request: ProviderRequest,
    config: ProviderExecutionConfig,
    *,
    dry_run: bool,
) -> None:
    provider = request.provider.lower()
    allowed = {item.lower() for item in config.allowed_providers or ()}
    denied = {item.lower() for item in config.denied_providers or ()}
    if config.allowed_providers is not None and provider not in allowed:
        raise ProviderBlockedError(f"Provider '{request.provider}' is not in the allow list.")
    if provider in denied:
        raise ProviderBlockedError(f"Provider '{request.provider}' is on the deny list.")
    if config.local_only and not dry_run:
        raise ProviderBlockedError("Local-only mode blocks outbound provider calls.")


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str) -> tuple[str, ...] | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return tuple(item.strip() for item in value.split(",") if item.strip())
