# Production cutover runbook: Neon to Supabase Postgres

Written from an actual rehearsal against a copy of production (28 Aug 2026), not
from theory. Numbers below are real, not estimates. Nothing in this document has
been run against the real production database.

## Why this is needed

`profiles.id` is a foreign key to Supabase Auth's `auth.users` table, which only
exists inside a Supabase-hosted Postgres. The account-based auth system built on
`auth-admin-workspace` cannot go live against the current Neon database. The
schema change itself is a normal Alembic migration; the risk is entirely in
moving production's live data to a new host without losing or corrupting any of
it.

## What the rehearsal actually showed

1. **Production is small.** 17 MB total. Largest table is `content_items` at
   1,525 rows; `sources` has 74 rows; nothing else clears 600 rows.
2. **`pg_dump` of the full production database: 11.7 seconds.**
3. **Restore into a fresh local database: well under 1 second** for a database
   this size (a restore into Supabase's own Postgres over the network will be
   slower than local-to-local, but not by an order of magnitude at this size).
4. **`alembic upgrade head` against the restored copy: clean, under 1 second.**
   Verified afterward: all 74 `sources` rows preserved with original
   name/platform intact, `cadence` correctly defaulted to `weekly` on every
   existing row, `normalized_identifier` correctly `NULL` (not yet backfilled,
   as designed), and `content_items`/`content_analyses`/`signals`/
   `signal_evidence`/`weekly_reports` untouched at their original row counts.
5. **`alembic downgrade` back to the pre-auth-work revision: also clean.** The
   new columns were removed and all 1,525 `content_items` rows remained intact
   throughout.
6. **One pre-existing, unrelated finding**: production's database already
   contains a set of empty, unused tables (`user`, `organization`, `session`,
   `account`, `invitation`, `verification`, `jwks`, `member`) that appear to be
   leftover schema from Neon's own auth product (Neon Auth), never used by this
   codebase. They are not touched by this migration and are not part of the
   Alembic-managed schema. Worth a separate decision on whether to drop them,
   unrelated to this cutover.

**Bottom line: the data-copy step itself is fast and, on this rehearsal, lossless
in both directions.** The risk in a real cutover is not "will pg_dump handle the
data" — it clearly can — it's process risk: doing every step in the right order,
against the right database, with writes stopped for the window, and a tested way
back if something is wrong before writes resume.

## Preconditions before scheduling a real cutover

- [ ] A verified Supabase project exists with the full migration chain applied
      (already true as of this writing: project `uqfafnvysfeainbeomaz`, EU
      region, migrated through `848708701be7`, but that copy has **no**
      production data in it yet, only the schema).
- [ ] `culture auth provision-owner` has been run against that project (already
      done as of this writing).
- [ ] The findings from the code review of `auth-admin-workspace` are triaged;
      at minimum the correctness bugs (registration controls not enforced,
      duplicate approval emails, missing status guards on suspend/disable) are
      fixed and covered by a passing test before any real cutover, since the
      cutover is exactly what makes those code paths reachable by real users.
- [ ] A maintenance window is chosen and communicated (even a solo-operator
      project benefits from picking a specific time rather than doing this
      opportunistically).

## Cutover sequence

1. **Freeze writes.** Stop the pipeline (`scripts/daily_run.sh` / any cron
   invoking `culture ingest` or `culture analyze`) for the duration of the
   window. The web app is already read-only except for the new `/admin/*`
   routes, which won't be live yet at this point (`AUTH_MODE` still unset).
2. **Dump production** with the version-matched client tools (Neon currently
   runs Postgres 18; a `pg_dump`/`pg_restore` binary from Postgres 16 will
   refuse the dump with a version-mismatch error, as it did in this rehearsal):
   ```bash
   pg_dump "$PRODUCTION_DATABASE_URL" --no-owner --no-privileges -Fc -f prod_cutover.pgdump
   ```
3. **Restore into the Supabase project's Postgres** (the one already migrated
   to head):
   ```bash
   pg_restore -d "$SUPABASE_DATABASE_URL" --no-owner --no-privileges --clean --if-exists prod_cutover.pgdump
   ```
   `--clean --if-exists` drops the Supabase project's existing (empty, schema-only)
   tables before restoring, so the production data lands cleanly rather than
   conflicting with rows already there (there shouldn't be any real rows there
   yet, but this makes the restore idempotent if it needs a retry).
4. **Verify row counts match** between the old and new databases for every
   table that had data before the freeze (a two-line `psql` count query per
   table, or reuse the counts in this document as the "before" baseline before
   the cutover's own dump is taken).
5. **Point `DATABASE_URL` at the Supabase project's Postgres** — this is the
   single Vercel env var change that actually moves the app's data source.
6. **Rotate `SESSION_SECRET`** — this logs out every existing session at once,
   on purpose, as part of the same deploy.
7. **Set `AUTH_MODE=supabase`** in Production.
8. **Redeploy** (env var changes need a fresh deploy to take effect for
   already-running functions).
9. **Smoke test**: sign in as owner, confirm `/dashboard` shows real data
   (signals, sources, reports), confirm `/admin/approvals` loads.
10. **Un-freeze the pipeline.**

## Rollback (if something is wrong before step 10)

Because nothing is deleted from the original Neon database in this sequence,
rollback is simply: revert `DATABASE_URL` to the original Neon connection
string, revert `AUTH_MODE` to unset (or `magiclink`), redeploy. Production's
original database was never modified, so this is a same-state rollback, not a
restore-from-backup — the Neon database sitting untouched throughout the
cutover *is* the rollback plan, as long as nothing in steps 1-9 wrote to it.
The pipeline freeze in step 1 is what keeps that true; if the pipeline runs
during the cutover window, its writes would land only in Neon and be missing
from the new database after cutover, which is the one way this rollback story
breaks.

## What this document does not cover

- The actual choice of *when* to run this (a business decision, not an
  engineering one).
- Whatever the current findings from the `auth-admin-workspace` code review
  turn up as must-fix-first items — those are tracked separately, not
  duplicated here.
- Removing the magic-link code paths after the cutover is confirmed stable —
  that is a follow-up cleanup deploy, not part of the cutover itself.
