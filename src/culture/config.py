from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/culture_intelligence"

    ai_provider: str = "anthropic"
    ai_model: str = ""
    # Weekly synthesis is one call a week where judgment quality is the product;
    # it defaults to a stronger model than per-item analysis.
    ai_synthesis_model: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # Hard ceiling on real dollar spend per `culture analyze` invocation
    # (shared across item analysis and the signal-matching that follows it
    # in the same run). A run stops cleanly between items once reached —
    # nothing lost, remaining items retry next run. None = uncapped.
    ai_max_spend_per_run: float | None = 5.0
    # Managed scraping for Instagram/TikTok (no official API for monitoring
    # arbitrary public accounts). Empty = those platforms stay uncollected,
    # exactly as before — no regression when the token is absent.
    apify_token: str = ""
    # Max posts pulled per social account per run (cost control).
    apify_posts_per_account: int = 10

    log_level: str = "INFO"

    # Web viewer auth (magic-link, invite-only).
    session_secret: str = ""
    resend_api_key: str = ""
    email_from: str = "Culture Intelligence <onboarding@resend.dev>"
    # Comma-separated allow-list. Empty = nobody can log in (fail closed, not open).
    allowed_emails: str = ""

    # Public Substack publication feed (e.g. https://<name>.substack.com/feed).
    # Empty = the homepage publication section stays hidden entirely.
    substack_feed_url: str = ""

    # Comma-separated admin allow-list: admins additionally see /sources
    # (the proprietary source registry + collection health). Empty means
    # every allowed email is an admin — correct for today's single-operator
    # deployment, and the value to set the day a first customer is invited.
    admin_emails: str = ""

    @property
    def allowed_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    def is_admin(self, email: str | None) -> bool:
        if email is None:
            return False
        if not self.admin_email_set:
            return email.strip().lower() in self.allowed_email_set
        return email.strip().lower() in self.admin_email_set


@lru_cache
def get_settings() -> Settings:
    return Settings()
