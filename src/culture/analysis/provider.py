import os
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from culture.config import Settings
from culture.logging import get_logger

log = get_logger("culture.analysis.provider")

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_SYNTHESIS_MODEL = "claude-opus-5"
# Item-analysis default when routing is enabled (see RoutingProvider) — the
# cheapest current-generation model, for text-only items.
DEFAULT_CHEAP_MODEL = "claude-haiku-4-5"

# USD per token (not per-million — pre-divided so cost math stays a plain
# multiply). Source: Anthropic's published rates, verified live 2026-08-25.
# Sonnet 5's $2/$10 was originally an intro rate; Anthropic has since made
# it permanent (the planned reversion to $3/$15 will not happen).
PRICING_PER_TOKEN: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00 / 1_000_000, 25.00 / 1_000_000),
    "claude-sonnet-5": (2.00 / 1_000_000, 10.00 / 1_000_000),  # intro price
    "claude-haiku-4-5": (1.00 / 1_000_000, 5.00 / 1_000_000),
}
# Cache reads cost ~10% of the input rate; cache writes cost ~125%.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25

T = TypeVar("T", bound=BaseModel)


class ProviderError(Exception):
    """Provider-level failure (configuration, refusal, transport exhaustion)."""


class BudgetExceededError(ProviderError):
    """Raised before a call would be made once max_spend_usd is reached.

    Never mid-call — the check happens before dispatch, so a run stops
    cleanly between items/batches rather than being cut off mid-response.
    """

    def __init__(self, spent_usd: float, max_spend_usd: float) -> None:
        self.spent_usd = spent_usd
        self.max_spend_usd = max_spend_usd
        super().__init__(
            f"Spend cap reached: ${spent_usd:.2f} spent of ${max_spend_usd:.2f} budget "
            "for this run. Remaining items stay queued for the next run. Raise the cap "
            "with AI_MAX_SPEND_PER_RUN or --max-spend."
        )


def estimate_cost_usd(usage, model: str) -> float | None:
    """Real dollar cost of one response, from its actual token usage.

    Returns None for an unrecognized model — spend just isn't tracked for
    it, rather than silently under- or over-counting.
    """
    rates = PRICING_PER_TOKEN.get(model)
    if rates is None:
        return None
    input_rate, output_rate = rates
    fresh_input = getattr(usage, "input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    output = getattr(usage, "output_tokens", 0) or 0
    return (
        fresh_input * input_rate
        + cache_read * input_rate * CACHE_READ_MULTIPLIER
        + cache_write * input_rate * CACHE_WRITE_MULTIPLIER
        + output * output_rate
    )


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
    spent_usd: float
    max_spend_usd: float | None

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

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        max_spend_usd: float | None = None,
    ) -> None:
        import anthropic

        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key or None)
        self.max_spend_usd = max_spend_usd
        self.spent_usd = 0.0

    def _check_budget(self) -> None:
        if self.max_spend_usd is not None and self.spent_usd >= self.max_spend_usd:
            raise BudgetExceededError(self.spent_usd, self.max_spend_usd)

    def _track_spend(self, response) -> None:
        _log_cache_usage(response)
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        cost = estimate_cost_usd(usage, self.model)
        if cost is not None:
            self.spent_usd += cost
            log.debug("call cost: $%.4f (run total: $%.4f)", cost, self.spent_usd)

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

        self._check_budget()
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
        self._track_spend(response)
        if response.stop_reason == "refusal":
            raise ProviderError(f"Model refused the request: {response.stop_details}")
        if response.parsed_output is None:
            raise ProviderError(f"No parsable output (stop_reason={response.stop_reason})")
        return response.parsed_output

    def generate_text(self, system: str, user: str, max_tokens: int = 16000) -> str:
        self._check_budget()
        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=_cached_system(system),
            messages=[{"role": "user", "content": user}],
        ) as stream:
            response = stream.get_final_message()
        self._track_spend(response)
        if response.stop_reason == "refusal":
            raise ProviderError(f"Model refused the request: {response.stop_details}")
        return "".join(block.text for block in response.content if block.type == "text")


class RoutingProvider:
    """Routes each item to the cheapest model that reliably handles it.

    Images carry the fashion-specific detail this platform depends on
    (garment texture, brand marks). Verified against Sonnet on ~50 real
    items (2026-08-25): on images, Haiku read detail less precisely (e.g.
    "yellow cardigan" for what Sonnet correctly read as a "mustard
    bouclé/tweed jacket") and missed tagged brand names in a flat-lay post.
    Every item with images therefore goes to the capable model. Text-only
    items (articles, captions, transcripts, show notes) tested reliably
    close to Sonnet, across lengths from ~250 to ~50,000 characters, at
    ~29% of the cost — those route to the cheap model.

    Only used for item analysis (one call per item). Signal matching
    batches many items into a single shared call and can't route per-item,
    so it stays on the capable model, unchanged.
    """

    name = "anthropic"

    def __init__(
        self, cheap: "AnthropicProvider", capable: "AnthropicProvider", max_spend_usd: float | None
    ) -> None:
        self.cheap = cheap
        self.capable = capable
        self.max_spend_usd = max_spend_usd
        self.model = capable.model
        # A plain synced attribute, not a computed property — the AIProvider
        # Protocol expects spent_usd to be settable (see cli.py, which
        # hands the combined total off to the capable sub-provider before
        # signal matching runs).
        self.spent_usd = cheap.spent_usd + capable.spent_usd

    def _resync_spend(self) -> None:
        self.spent_usd = self.cheap.spent_usd + self.capable.spent_usd

    def _check_budget(self) -> None:
        if self.max_spend_usd is not None and self.spent_usd >= self.max_spend_usd:
            raise BudgetExceededError(self.spent_usd, self.max_spend_usd)

    def generate_structured(
        self,
        system: str,
        user: str,
        output_format: type[T],
        images: list[tuple[str, bytes]] | None = None,
    ) -> T:
        self._check_budget()
        target = self.capable if images else self.cheap
        self.model = target.model
        result = target.generate_structured(system, user, output_format, images=images)
        self._resync_spend()
        return result

    def generate_text(self, system: str, user: str, max_tokens: int = 16000) -> str:
        # Not used by item analysis; delegates to the capable model for
        # Protocol compliance (e.g. if ever called for synthesis-style text).
        self._check_budget()
        self.model = self.capable.model
        result = self.capable.generate_text(system, user, max_tokens=max_tokens)
        self._resync_spend()
        return result


def _anthropic_api_key(settings: Settings) -> str:
    api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise ProviderError(
            "No Anthropic credentials found. Add ANTHROPIC_API_KEY=<your key> to the "
            "project .env file (never commit it)."
        )
    return api_key


def get_provider(
    settings: Settings, model: str | None = None, max_spend_usd: float | None = -1.0
) -> AIProvider:
    """max_spend_usd: explicit override, or the sentinel -1.0 (default) to
    fall back to settings.ai_max_spend_per_run. Pass None explicitly for an
    uncapped provider regardless of the configured default."""
    if max_spend_usd == -1.0:
        max_spend_usd = settings.ai_max_spend_per_run
    if settings.ai_provider == "anthropic":
        api_key = _anthropic_api_key(settings)
        model = model or settings.ai_model or DEFAULT_ANTHROPIC_MODEL
        log.debug("using anthropic provider with model %s", model)
        return AnthropicProvider(model=model, api_key=api_key or None, max_spend_usd=max_spend_usd)
    if settings.ai_provider == "openai":
        raise ProviderError(
            "The OpenAI provider is not implemented yet. Set AI_PROVIDER=anthropic, or ask "
            "for an OpenAIProvider — the AIProvider interface makes it a one-class addition."
        )
    raise ProviderError(f"Unknown AI_PROVIDER: {settings.ai_provider!r}")


def get_routing_provider(settings: Settings, max_spend_usd: float | None = -1.0) -> AIProvider:
    """Item-analysis provider that routes each item to Haiku or Sonnet based
    on whether it carries images — see RoutingProvider. Both sub-providers
    are built individually uncapped; the shared budget is enforced once, at
    the router, so image and text items draw from the same pool."""
    if max_spend_usd == -1.0:
        max_spend_usd = settings.ai_max_spend_per_run
    if settings.ai_provider != "anthropic":
        raise ProviderError("Model routing is only implemented for AI_PROVIDER=anthropic.")
    api_key = _anthropic_api_key(settings)
    capable_model = settings.ai_model or DEFAULT_ANTHROPIC_MODEL
    cheap = AnthropicProvider(
        model=DEFAULT_CHEAP_MODEL, api_key=api_key or None, max_spend_usd=None
    )
    capable = AnthropicProvider(model=capable_model, api_key=api_key or None, max_spend_usd=None)
    log.debug("using routing provider: cheap=%s capable=%s", DEFAULT_CHEAP_MODEL, capable_model)
    return RoutingProvider(cheap=cheap, capable=capable, max_spend_usd=max_spend_usd)
