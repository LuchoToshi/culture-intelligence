import os
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from culture.config import Settings
from culture.logging import get_logger

log = get_logger("culture.analysis.provider")

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_SYNTHESIS_MODEL = "claude-opus-5"

T = TypeVar("T", bound=BaseModel)


class ProviderError(Exception):
    """Provider-level failure (configuration, refusal, transport exhaustion)."""


def _cached_system(system: str) -> list[Any]:
    """Mark the (fully static, identical-every-call) system prompt as an
    ephemeral cache breakpoint. Anthropic requires ~1024+ tokens for a block
    to actually cache — shorter prompts silently pass through uncached, at
    no cost. Manual per-block placement (not top-level auto-cache) so the
    per-call user content/images, which do vary, are never mistakenly
    included in the cached prefix."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _log_cache_usage(response) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    created = getattr(usage, "cache_creation_input_tokens", 0) or 0
    if read or created:
        log.debug("prompt cache: read=%d created=%d", read, created)


class AIProvider(Protocol):
    """Transport abstraction. Prompts and business logic never live here, so
    providers are swappable without touching analysis code."""

    name: str
    model: str

    def generate_structured(
        self,
        system: str,
        user: str,
        output_format: type[T],
        images: list[tuple[str, bytes]] | None = None,
    ) -> T: ...

    def generate_text(self, system: str, user: str, max_tokens: int = 16000) -> str: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None) -> None:
        import anthropic

        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key or None)

    def generate_structured(
        self,
        system: str,
        user: str,
        output_format: type[T],
        images: list[tuple[str, bytes]] | None = None,
    ) -> T:
        """images: optional (media_type, raw bytes) pairs sent ahead of the text."""
        import base64
        from typing import Any

        content: Any
        if images:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    },
                }
                for media_type, data in images
            ] + [{"type": "text", "text": user}]
        else:
            content = user
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=_cached_system(system),
            messages=[{"role": "user", "content": content}],
            output_format=output_format,
        )
        if response.stop_reason == "refusal":
            raise ProviderError(f"Model refused the request: {response.stop_details}")
        if response.parsed_output is None:
            raise ProviderError(f"No parsable output (stop_reason={response.stop_reason})")
        _log_cache_usage(response)
        return response.parsed_output

    def generate_text(self, system: str, user: str, max_tokens: int = 16000) -> str:
        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=_cached_system(system),
            messages=[{"role": "user", "content": user}],
        ) as stream:
            response = stream.get_final_message()
        if response.stop_reason == "refusal":
            raise ProviderError(f"Model refused the request: {response.stop_details}")
        _log_cache_usage(response)
        return "".join(block.text for block in response.content if block.type == "text")


def get_provider(settings: Settings, model: str | None = None) -> AIProvider:
    if settings.ai_provider == "anthropic":
        api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            raise ProviderError(
                "No Anthropic credentials found. Add ANTHROPIC_API_KEY=<your key> to the "
                "project .env file (never commit it)."
            )
        model = model or settings.ai_model or DEFAULT_ANTHROPIC_MODEL
        log.debug("using anthropic provider with model %s", model)
        return AnthropicProvider(model=model, api_key=api_key or None)
    if settings.ai_provider == "openai":
        raise ProviderError(
            "The OpenAI provider is not implemented yet. Set AI_PROVIDER=anthropic, or ask "
            "for an OpenAIProvider — the AIProvider interface makes it a one-class addition."
        )
    raise ProviderError(f"Unknown AI_PROVIDER: {settings.ai_provider!r}")
