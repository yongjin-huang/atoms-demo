"""One registry, two adapters — a straight port of lib/models.ts.

DeepSeek, OpenAI and OpenRouter all speak the OpenAI wire format, so they share
a single client and differ only by base_url + key. Anthropic has its own SDK.

That this ported across languages unchanged is a decent sign it was the right
abstraction.
"""

from dataclasses import dataclass

from settings import settings


@dataclass(frozen=True)
class Provider:
    label: str
    env_key: str
    base_url: str | None = None  # None for Anthropic


PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider("DeepSeek", "DEEPSEEK_API_KEY", "https://api.deepseek.com"),
    "openai": Provider("OpenAI", "OPENAI_API_KEY", "https://api.openai.com/v1"),
    "openrouter": Provider("OpenRouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "anthropic": Provider("Anthropic", "ANTHROPIC_API_KEY"),
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


def api_key_for(provider: str) -> str:
    return getattr(settings, PROVIDERS[provider].env_key, "") or ""


def available_models() -> list[ModelSpec]:
    """Only models whose provider key is actually configured. An unconfigured
    provider never reaches the picker, so it can't fail at generate time."""
    return [m for m in MODELS if api_key_for(m.provider)]


def find_model(model_id: str) -> ModelSpec | None:
    return next((m for m in available_models() if m.id == model_id), None)
