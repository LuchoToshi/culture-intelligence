import pytest

from culture.analysis.provider import (
    PRICING_PER_TOKEN,
    AnthropicProvider,
    BudgetExceededError,
    estimate_cost_usd,
    get_provider,
)
from culture.config import Settings


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
