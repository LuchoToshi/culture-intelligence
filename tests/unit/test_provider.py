import pytest

from culture.analysis.provider import (
    DEFAULT_CHEAP_MODEL,
    PRICING_PER_TOKEN,
    AnthropicProvider,
    BudgetExceededError,
    ProviderError,
    RoutingProvider,
    estimate_cost_usd,
    get_provider,
    get_routing_provider,
)
from culture.config import Settings


class FakeSubProvider:
    def __init__(self, model, cost_per_call=0.01):
        self.model = model
        self.spent_usd = 0.0
        self.cost_per_call = cost_per_call
        self.calls = []
        self.client = object()

    def generate_structured(self, system, user, output_format, images=None):
        self.calls.append(images)
        self.spent_usd += self.cost_per_call
        return f"response-from-{self.model}"

    def generate_text(self, system, user, max_tokens=16000):
        self.calls.append(None)
        self.spent_usd += self.cost_per_call
        return f"text-from-{self.model}"


class FakeUsage:
    def __init__(self, input_tokens=0, cache_read=0, cache_write=0, output_tokens=0):
        self.input_tokens = input_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write
        self.output_tokens = output_tokens


def test_estimate_cost_usd_known_model():
    input_rate, output_rate = PRICING_PER_TOKEN["claude-sonnet-5"]
    usage = FakeUsage(input_tokens=1000, output_tokens=500)
    cost = estimate_cost_usd(usage, "claude-sonnet-5")
    assert cost == pytest.approx(1000 * input_rate + 500 * output_rate)


def test_estimate_cost_usd_applies_cache_multipliers():
    input_rate, output_rate = PRICING_PER_TOKEN["claude-sonnet-5"]
    usage = FakeUsage(input_tokens=100, cache_read=1000, cache_write=200, output_tokens=50)
    cost = estimate_cost_usd(usage, "claude-sonnet-5")
    expected = (
        100 * input_rate
        + 1000 * input_rate * 0.1
        + 200 * input_rate * 1.25
        + 50 * output_rate
    )
    assert cost == pytest.approx(expected)


def test_estimate_cost_usd_unrecognized_model_returns_none():
    usage = FakeUsage(input_tokens=1000, output_tokens=500)
    assert estimate_cost_usd(usage, "some-future-model") is None


def test_estimate_cost_usd_batch_halves_every_rate():
    usage = FakeUsage(input_tokens=1000, cache_read=1000, cache_write=1000, output_tokens=500)
    live = estimate_cost_usd(usage, "claude-sonnet-5", batch=False)
    batched = estimate_cost_usd(usage, "claude-sonnet-5", batch=True)
    assert batched == pytest.approx(live / 2)


def test_check_budget_raises_before_dispatch():
    provider = AnthropicProvider(model="claude-sonnet-5", api_key="sk-test", max_spend_usd=0.0)
    with pytest.raises(BudgetExceededError, match=r"\$0\.00 spent of \$0\.00 budget"):
        provider._check_budget()


def test_check_budget_allows_under_cap():
    provider = AnthropicProvider(model="claude-sonnet-5", api_key="sk-test", max_spend_usd=5.0)
    provider._check_budget()  # should not raise


def test_check_budget_uncapped_never_raises():
    provider = AnthropicProvider(model="claude-sonnet-5", api_key="sk-test", max_spend_usd=None)
    provider.spent_usd = 1_000_000.0
    provider._check_budget()  # should not raise


def test_track_spend_accumulates_across_calls():
    provider = AnthropicProvider(model="claude-sonnet-5", api_key="sk-test", max_spend_usd=None)

    class Response:
        usage = FakeUsage(input_tokens=1000, output_tokens=500)

    provider._track_spend(Response())
    provider._track_spend(Response())
    input_rate, output_rate = PRICING_PER_TOKEN["claude-sonnet-5"]
    per_call = 1000 * input_rate + 500 * output_rate
    assert provider.spent_usd == pytest.approx(per_call * 2)


def test_get_provider_defaults_to_configured_spend_cap():
    settings = Settings(
        _env_file=None,
        ai_provider="anthropic",
        anthropic_api_key="sk-test",
        ai_max_spend_per_run=3.0,
    )
    provider = get_provider(settings)
    assert provider.max_spend_usd == 3.0


def test_get_provider_explicit_override_wins():
    settings = Settings(
        _env_file=None,
        ai_provider="anthropic",
        anthropic_api_key="sk-test",
        ai_max_spend_per_run=3.0,
    )
    provider = get_provider(settings, max_spend_usd=10.0)
    assert provider.max_spend_usd == 10.0


def test_get_provider_explicit_none_is_uncapped_regardless_of_config():
    settings = Settings(
        _env_file=None,
        ai_provider="anthropic",
        anthropic_api_key="sk-test",
        ai_max_spend_per_run=3.0,
    )
    provider = get_provider(settings, max_spend_usd=None)
    assert provider.max_spend_usd is None


def test_routing_provider_routes_text_only_to_cheap():
    cheap, capable = FakeSubProvider("haiku"), FakeSubProvider("sonnet")
    router = RoutingProvider(cheap=cheap, capable=capable, max_spend_usd=None)
    router.generate_structured("sys", "user", str)
    assert cheap.calls == [None]
    assert capable.calls == []
    assert router.model == "haiku"


def test_routing_provider_routes_images_to_capable():
    cheap, capable = FakeSubProvider("haiku"), FakeSubProvider("sonnet")
    router = RoutingProvider(cheap=cheap, capable=capable, max_spend_usd=None)
    images = [("image/jpeg", b"fake")]
    router.generate_structured("sys", "user", str, images=images)
    assert capable.calls == [images]
    assert cheap.calls == []
    assert router.model == "sonnet"


def test_routing_provider_spent_usd_is_combined_across_both_models():
    cheap, capable = FakeSubProvider("haiku", cost_per_call=0.01), FakeSubProvider(
        "sonnet", cost_per_call=0.04
    )
    router = RoutingProvider(cheap=cheap, capable=capable, max_spend_usd=None)
    router.generate_structured("sys", "user", str)  # cheap: +0.01
    router.generate_structured("sys", "user", str, images=[("image/jpeg", b"x")])  # capable: +0.04
    assert router.spent_usd == pytest.approx(0.05)


def test_routing_provider_check_budget_uses_combined_spend():
    cheap, capable = FakeSubProvider("haiku", cost_per_call=0.03), FakeSubProvider(
        "sonnet", cost_per_call=0.03
    )
    router = RoutingProvider(cheap=cheap, capable=capable, max_spend_usd=0.05)
    router.generate_structured("sys", "user", str)  # cheap: total 0.03, under cap
    router.generate_structured(
        "sys", "user", str, images=[("image/jpeg", b"x")]
    )  # capable: total 0.06
    with pytest.raises(BudgetExceededError):
        # Neither model's own spend (0.06 combined, but 0.03 each individually)
        # would trip this cap alone — proves the check reads the router's
        # combined total across both sub-providers, not either one's own.
        router.generate_structured("sys", "user", str)


def test_get_routing_provider_uses_haiku_and_configured_capable_model():
    settings = Settings(
        _env_file=None,
        ai_provider="anthropic",
        anthropic_api_key="sk-test",
        ai_model="claude-sonnet-5",
    )
    router = get_routing_provider(settings)
    assert router.cheap.model == DEFAULT_CHEAP_MODEL
    assert router.capable.model == "claude-sonnet-5"
    assert router.cheap.max_spend_usd is None  # capped only at the router
    assert router.capable.max_spend_usd is None


def test_get_routing_provider_requires_anthropic_provider():
    settings = Settings(_env_file=None, ai_provider="tarot")
    with pytest.raises(ProviderError, match="only implemented for AI_PROVIDER=anthropic"):
        get_routing_provider(settings)


def test_get_routing_provider_requires_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    settings = Settings(_env_file=None, ai_provider="anthropic", anthropic_api_key="")
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        get_routing_provider(settings)
