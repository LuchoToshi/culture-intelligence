"""profiles table

One row per Supabase Auth identity (`auth.users`). Registration creates a
`pending` row; the owner grants access through /admin/approvals. Exactly one
`role='owner'` row can ever exist (partial unique index).

The FK to `auth.users(id)` and the insert trigger are Supabase/Postgres-only
DDL: they require this migration to run against the Supabase project's own
Postgres (where the `auth` schema exists), not a bare Postgres instance. That
is intentional here (see the "authoritative implementation spec", D1) — this
statement fails loudly, not silently, if pointed at the wrong database, which
is the correct failure mode. Rows can never be missing a profile: the trigger
fires on every `auth.users` insert.

RLS is defense-in-depth, not the primary boundary (the app connects with a
privileged role via SQLAlchemy and enforces authorization in FastAPI — see
culture.web.auth). It exists so no Supabase client path (PostgREST, a leaked
anon key, future tooling) can read or write this table directly: no INSERT/
UPDATE/DELETE policy exists for any non-service role, ever, and a narrow view
exposes only non-privileged columns of a user's own row.

Revision ID: f8a21b02712b
Revises: f4a1c9d27b31
Create Date: 2026-08-28
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8a21b02712b"
down_revision: Union[str, Sequence[str], None] = "f4a1c9d27b31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("full_name", sa.Text()),
        sa.Column("organization", sa.Text()),
        sa.Column("role", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by", sa.Text()),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_by", sa.Text()),
        sa.Column("suspended_at", sa.DateTime(timezone=True)),
        sa.Column("suspended_by", sa.Text()),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("disabled_by", sa.Text()),
        sa.Column("last_status_reason", sa.Text()),
        sa.Column("legacy_magic_link", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_profiles_role"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'suspended', 'disabled')",
            name="ck_profiles_status",
        ),
    )
    op.create_index("ix_profiles_status_created_at", "profiles", ["status", "created_at"])
    op.create_index(
        "ix_profiles_single_owner",
        "profiles",
        ["role"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
        sqlite_where=sa.text("role = 'owner'"),
    )

    # --- Supabase/Postgres-only: FK to auth.users and the provisioning trigger ---
    op.execute(
        "ALTER TABLE profiles ADD CONSTRAINT profiles_id_fkey "
        "FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.handle_new_user()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
          INSERT INTO public.profiles (id, email, status)
          VALUES (new.id, new.email, 'pending');
          RETURN new;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users "
        "FOR EACH ROW EXECUTE FUNCTION public.handle_new_user()"
    )

    # --- Row Level Security: deny-by-default, narrow self-read via a view ---
    op.execute("ALTER TABLE profiles ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE VIEW public.own_profile
        WITH (security_invoker = true)
        AS SELECT id, full_name, email, status FROM public.profiles WHERE id = auth.uid()
        """
    )
    op.execute("GRANT SELECT ON public.own_profile TO authenticated")
    # No CREATE POLICY grants SELECT/INSERT/UPDATE/DELETE on the base table
    # to anon/authenticated, by design: every client-role query against
    # `profiles` itself is denied; only the view above is reachable, and
    # only for the caller's own row.


def downgrade() -> None:
    op.execute("REVOKE SELECT ON public.own_profile FROM authenticated")
    op.execute("DROP VIEW IF EXISTS public.own_profile")
    op.execute("ALTER TABLE profiles DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users")
    op.execute("DROP FUNCTION IF EXISTS public.handle_new_user()")
    op.drop_index("ix_profiles_single_owner", table_name="profiles")
    op.drop_index("ix_profiles_status_created_at", table_name="profiles")
    op.drop_table("profiles")
