"""Every new table's migration enables Row Level Security.

RLS (Postgres-only) cannot be exercised by this unit-test suite, which runs
on in-memory SQLite (see conftest.py) and never runs Alembic migrations
(`Base.metadata.create_all()` is used instead). True RLS deny-case testing
is a manual/staging step against a real Postgres before production cutover
(see implementation-spec.md §16). This is the automatable substitute: a
source scan asserting the SQL that turns RLS on is actually present in each
new table's migration, so it can't be silently dropped later — the same
static-scan approach `test_no_em_dash.py` uses for a different guarantee.
"""

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"

NEW_TABLE_MIGRATIONS = {
    "profiles": "f8a21b02712b_profiles_table.py",
    "app_settings": "bd42a54e85df_app_settings_table.py",
    "collection_requests": "1c9d917396b8_collection_requests_table.py",
    "email_log": "848708701be7_email_log_table.py",
}


def test_every_new_table_enables_row_level_security():
    for table, filename in NEW_TABLE_MIGRATIONS.items():
        path = MIGRATIONS_DIR / filename
        assert path.exists(), f"expected migration file missing: {filename}"
        text = path.read_text(encoding="utf-8")
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in text, (
            f"{filename} does not enable RLS on {table}"
        )


def test_profiles_has_no_client_writable_policy():
    """The self-read path is a narrow VIEW exposing non-privileged columns,
    never a policy or grant giving INSERT/UPDATE/DELETE on the base table to
    any client role — see the migration's own docstring for the reasoning.
    Comment lines are stripped first so a code comment *describing* that
    absence doesn't trip the very check it's explaining."""
    raw = (MIGRATIONS_DIR / NEW_TABLE_MIGRATIONS["profiles"]).read_text(encoding="utf-8")
    code_lines = [line for line in raw.splitlines() if not line.strip().startswith("#")]
    code = "\n".join(code_lines).upper()
    assert "CREATE POLICY" not in code
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert f"GRANT {verb}" not in code
    assert "CREATE VIEW public.own_profile" in raw
    assert "GRANT SELECT ON public.own_profile TO authenticated" in raw
