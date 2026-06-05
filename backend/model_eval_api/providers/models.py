from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ProviderCapabilities(BaseModel):
    supports_images: bool = False
    supports_files: bool = False
    supports_tools: bool = False
    supports_json_schema: bool = False


class ProviderUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ProviderRequest(BaseModel):
    provider: str
    model: str
    payload: dict[str, Any]
    raw_provider_params: dict[str, Any] = Field(default_factory=dict)
    normalized_config: dict[str, Any] = Field(default_factory=dict)


class ProviderResponse(BaseModel):
    provider: str
    model: str
    response_payload: dict[str, Any] = Field(default_factory=dict)
    provider_response_id: str | None = None
    output_text: str = ""
    usage: ProviderUsage = Field(default_factory=ProviderUsage)
    cost_usd: float | None = None
    dry_run: bool = False
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


ProviderClient = Callable[[ProviderRequest], dict[str, Any]]


@dataclass(frozen=True)
class ProviderExecutionConfig:
    local_only: bool = True
    allowed_providers: Collection[str] | None = None
    denied_providers: Collection[str] = ()
    client: ProviderClient | None = None

    @classmethod
    def from_env(cls) -> ProviderExecutionConfig:
        from model_eval_api.providers.settings import provider_config_from_env

        return provider_config_from_env()


class ProviderAdapter(Protocol):
    provider: str

    def capabilities(self, model: str) -> ProviderCapabilities:
        ...

    def build_request(self, run_snapshot: dict[str, Any]) -> ProviderRequest:
        ...

    def execute(
        self,
        request: ProviderRequest,
        *,
        config: ProviderExecutionConfig | None = None,
        dry_run: bool = True,
    ) -> ProviderResponse:
        ...

    def extract_tokens(self, response: ProviderResponse) -> ProviderUsage:
        ...

    def estimate_cost(self, usage: ProviderUsage, *, model: str) -> float | None:
        ...

    def normalize_response(
        self, request: ProviderRequest, response_payload: dict[str, Any]
    ) -> ProviderResponse:
        ...
