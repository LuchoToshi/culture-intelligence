from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/culture_intelligence"
    # Ahead of the full Neon->Supabase production data migration (D1), lets
    # one environment (e.g. Preview) run the new auth stack against
    # Supabase's own Postgres — required for the profiles->auth.users FK —
    # while every other environment keeps using DATABASE_URL untouched.
    # Empty (the default everywhere except that one environment) means
    # database_url below is exactly what it always was.
    supabase_database_url: str = ""

    @model_validator(mode="after")
    def _prefer_supabase_database_url(self) -> "Settings":
        if self.supabase_database_url:
            self.database_url = self.supabase_database_url
        return self

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
    # Demo account: fixed credentials for demos and testing. Empty = disabled.
    # The password is stored as a sha256 hex digest, never plaintext.
    demo_email: str = ""
    demo_password_sha256: str = ""

    # Public Substack publication feed (e.g. https://<name>.substack.com/feed).
    # Empty = the homepage publication section stays hidden entirely.
    substack_feed_url: str = ""

    # Comma-separated admin allow-list: admins additionally see /sources
    # (the proprietary source registry + collection health). Empty means
    # every allowed email is an admin — correct for today's single-operator
    # deployment, and the value to set the day a first customer is invited.
    admin_emails: str = ""

    # --- Account-based auth (Supabase), replacing the magic-link flow above ---
    # "magiclink" keeps every existing route/behavior unchanged; "supabase"
    # switches _mount_auth_routes to register/verify/approve. Default stays
    # magiclink until the Phase 7 cutover (real Supabase project, data
    # migration, SESSION_SECRET rotation) is explicitly performed.
    auth_mode: str = "magiclink"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    # Server-only: never rendered in templates, JS, or logs.
    supabase_service_role_key: str = ""
    # The sole account provisioned as role='owner' by `culture auth
    # provision-owner`. Never trusted from a request; read only by that CLI
    # command and the migration/recovery tooling.
    owner_email: str = ""
    # Canonical base URL for links in transactional email. Never derived
    # from request headers in production (a spoofed Host header must not be
    # able to redirect a password-reset link).
    site_url: str = ""

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
