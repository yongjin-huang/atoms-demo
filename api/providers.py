"""One registry, two adapters.

DeepSeek, OpenAI and OpenRouter all speak the OpenAI wire format, so they share
a single client and differ only by base_url + key. Anthropic has its own SDK.
"""

from dataclasses import dataclass

from models import User


@dataclass(frozen=True)
class Provider:
    label: str
    key_attr: str
    base_url: str | None = None  # None for Anthropic


PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider("DeepSeek", "deepseek_api_key", "https://api.deepseek.com"),
    "openai": Provider("OpenAI", "openai_api_key", "https://api.openai.com/v1"),
    "openrouter": Provider("OpenRouter", "openrouter_api_key", "https://openrouter.ai/api/v1"),
    "anthropic": Provider("Anthropic", "anthropic_api_key"),
}


@dataclass(frozen=True)
class ModelSpec:
    id: str        # stable id we store on the version
    label: str     # what the picker shows
    provider: str  # key into PROVIDERS
    model: str     # the provider's own model string


MODELS: list[ModelSpec] = [
    ModelSpec("deepseek/deepseek-chat", "DeepSeek V3", "deepseek", "deepseek-chat"),
    ModelSpec("deepseek/deepseek-reasoner", "DeepSeek R1", "deepseek", "deepseek-reasoner"),
    ModelSpec("openai/gpt-4o-mini", "GPT-4o mini", "openai", "gpt-4o-mini"),
    ModelSpec("anthropic/claude-sonnet", "Claude Sonnet", "anthropic", "claude-sonnet-4-5"),
]

DEFAULT_MODEL_ID = "deepseek/deepseek-chat"


def api_key_for(provider: str, user: User) -> str:
    return getattr(user, PROVIDERS[provider].key_attr, "") or ""


def available_models(user: User) -> list[ModelSpec]:
    """Only models whose provider key is configured in user settings."""
    return [m for m in MODELS if api_key_for(m.provider, user)]


def default_model_for(user: User) -> str | None:
    models = available_models(user)
    ids = {m.id for m in models}
    if user.default_model_id in ids:
        return user.default_model_id
    return DEFAULT_MODEL_ID if DEFAULT_MODEL_ID in ids else (models[0].id if models else None)


def find_model(model_id: str, user: User) -> ModelSpec | None:
    return next((m for m in available_models(user) if m.id == model_id), None)
