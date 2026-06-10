"""LiteLLM client wrapper. Единственият entry point към LLM-и в кода.

Кодът никога не пише `model="claude-sonnet-4-6"`. Винаги alias:
    cheap | smart | premium
които се mapват в `litellm/config.yaml`.

Pydantic AI има native `LiteLLMProvider` за тази цел.
"""

from __future__ import annotations

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider

from bogi.config import settings


def make_model(tier: str | None = None) -> OpenAIChatModel:
    """Връща Pydantic AI OpenAIChatModel насочен към LiteLLM proxy.

    Tier е alias от litellm/config.yaml: 'cheap', 'smart', 'premium'.
    """
    model_name = tier or settings.default_model
    provider = LiteLLMProvider(
        api_base=f"{settings.litellm_base_url.rstrip('/')}/v1",
        api_key=settings.litellm_master_key,
    )
    return OpenAIChatModel(model_name, provider=provider)
