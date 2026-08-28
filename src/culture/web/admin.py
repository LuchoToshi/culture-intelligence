"""Owner-only admin workspace: approvals, users, sources, audit, settings.

Not an APIRouter — routes are closures registered directly on the FastAPI
app, mirroring how _mount_auth_routes/_mount_supabase_auth_routes already
work in app.py (this codebase has never used APIRouter). Every route calls
auth.require_owner(request) explicitly, on top of AuthMiddleware's
ADMIN_ONLY_PREFIXES check — the same defense-in-depth /sources already
practiced before this workspace existed. Every mutation opens its own
transaction via session_scope(request.app.state.engine), the same
self-contained-transaction convention used by the CLI and the Phase 3 auth
routes (there's no Depends(db) reachable here: that dependency is a closure
defined inside create_app, after routes are mounted).
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from culture.database import session_scope
from culture.logging import get_logger
from culture.models.auth_event import AuthEvent
from culture.models.email_log import EmailLog
from culture.models.profile import ROLE_MEMBER, ROLE_OWNER, Profile
from culture.web import auth, queries

log = get_logger("culture.web.admin")

TEMPLATES_DIR = Path(__file__).parent / "templates"

# email/audit templates for each account-mutation action, keyed by the verb
# used in the route path (approve/reject/suspend/disable/reactivate).
_ACTION_EVENT = {
    "approve": "account_approved",
    "reject": "account_rejected",
    "suspend": "account_suspended",
    "disable": "account_disabled",
    "reactivate": "account_reactivated",
}


def mount_admin_routes(app: FastAPI) -> None:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def _settings():
        from culture.config import get_settings

        return get_settings()

    def _render(request: Request, template: str, active_tab: str, context: dict):
        context.setdefault("active_tab", active_tab)
        context.setdefault("user_email", getattr(request.state, "user_email", None))
        context.setdefault(
            "csrf_token", auth.create_csrf_token(request, _settings())
        )
        return templates.TemplateResponse(request, template, context)

    def _queue_email(session, to_email: str, template: str, dedupe_key: str) -> None:
        from sqlalchemy import select as _select

        already = session.scalar(_select(EmailLog).where(EmailLog.dedupe_key == dedupe_key))
        if already is None:
            session.add(EmailLog(to_email=to_email, template=template, dedupe_key=dedupe_key))

    # --- landing -------------------------------------------------------

    @app.get("/admin", include_in_schema=False)
    def admin_index(request: Request):
        auth.require_owner(request)
        return RedirectResponse("/admin/approvals", status_code=303)

    # --- approvals -------------------------------------------------------

    @app.get("/admin/approvals", response_class=HTMLResponse, include_in_schema=False)
    def admin_approvals(request: Request):
        auth.require_owner(request)
        with session_scope(request.app.state.engine) as session:
            profiles = queries.pending_approvals(session)
            session.expunge_all()
        return _render(request, "admin_approvals.html", "approvals", {"profiles": profiles})

    @app.post("/admin/approvals/{profile_id}/approve", include_in_schema=False)
    def admin_approve(request: Request, profile_id: str, csrf_token: str = Form(...)):
        return _decide(request, profile_id, "approve", csrf_token)

    @app.post("/admin/approvals/{profile_id}/reject", include_in_schema=False)
    def admin_reject(request: Request, profile_id: str, csrf_token: str = Form(...)):
        return _decide(request, profile_id, "reject", csrf_token)

    def _decide(request: Request, profile_id: str, verb: str, csrf_token: str):
        auth.require_owner(request)
        auth.require_csrf(csrf_token, request, _settings())
        actor = getattr(request.state, "user_email", None)

        with session_scope(request.app.state.engine) as session:
            profile = session.get(Profile, UUID(profile_id))
            if profile is None or profile.status != "pending":
                raise HTTPException(409, "This request has already been decided.")
            if verb == "approve" and profile.email_verified_at is None:
                raise HTTPException(409, "This address hasn't verified its email yet.")

            now = datetime.now(UTC)
            if verb == "approve":
                profile.status = "approved"
                profile.role = ROLE_MEMBER  # the approvals queue only ever grants member
                profile.approved_at = now
                profile.approved_by = actor
            else:
                profile.status = "rejected"
                profile.rejected_at = now
                profile.rejected_by = actor

            email = profile.email
            session.add(
                AuthEvent(
                    event=_ACTION_EVENT[verb], email=email, actor=actor,
                    target_type="profile", target_id=str(profile.id),
                )
            )
            _queue_email(
                session, email, _ACTION_EVENT[verb], f"{verb}:{profile.id}:{now.isoformat()}"
            )

        return RedirectResponse("/admin/approvals", status_code=303)

    # --- users -------------------------------------------------------

    @app.get("/admin/users", response_class=HTMLResponse, include_in_schema=False)
    def admin_users(request: Request, status: str | None = None, q: str | None = None):
        auth.require_owner(request)
        with session_scope(request.app.state.engine) as session:
            profiles = queries.all_profiles(session, status=status, q=q)
            session.expunge_all()
        return _render(
            request, "admin_users.html", "users",
            {"profiles": profiles, "status": status, "q": q},
        )

    @app.post("/admin/users/{profile_id}/{verb}", include_in_schema=False)
    def admin_user_action(
        request: Request, profile_id: str, verb: str, csrf_token: str = Form(...)
    ):
        if verb not in ("suspend", "disable", "reactivate", "approve"):
            raise HTTPException(404, "No such action.")
        auth.require_owner(request)
        auth.require_csrf(csrf_token, request, _settings())
        actor = getattr(request.state, "user_email", None)

        with session_scope(request.app.state.engine) as session:
            profile = session.get(Profile, UUID(profile_id))
            if profile is None:
                raise HTTPException(404, "No such account.")
            if profile.role == ROLE_OWNER:
                raise HTTPException(403, "The owner account can't be modified here.")
            if profile.email == actor:
                raise HTTPException(403, "You can't act on your own account.")

            now = datetime.now(UTC)
            if verb == "suspend":
                profile.status = "suspended"
                profile.suspended_at, profile.suspended_by = now, actor
            elif verb == "disable":
                profile.status = "disabled"
                profile.disabled_at, profile.disabled_by = now, actor
            elif verb == "reactivate":
                profile.status = "approved"
            elif verb == "approve":  # re-admission of a previously rejected account
                if profile.status != "rejected":
                    raise HTTPException(409, "Only a rejected account can be re-admitted here.")
                profile.status, profile.role = "approved", ROLE_MEMBER
                profile.approved_at, profile.approved_by = now, actor

            email = profile.email
            revoke = verb in ("suspend", "disable")
            session.add(
                AuthEvent(
                    event=_ACTION_EVENT.get(verb, f"account_{verb}"), email=email, actor=actor,
                    target_type="profile", target_id=str(profile.id),
                )
            )
            _queue_email(
                session, email, _ACTION_EVENT.get(verb, f"account_{verb}"),
                f"{verb}:{profile.id}:{now.isoformat()}",
            )
            user_id_for_revocation = str(profile.id) if revoke else None

        if user_id_for_revocation:
            from culture.web import supabase

            try:
                supabase.admin_sign_out_user(user_id_for_revocation, _settings())
            except supabase.SupabaseAuthError:
                log.info(
                    "session revocation failed for uid=%s", user_id_for_revocation, exc_info=True
                )

        return RedirectResponse("/admin/users", status_code=303)

    # --- audit -------------------------------------------------------

    @app.get("/admin/audit", response_class=HTMLResponse, include_in_schema=False)
    def admin_audit(request: Request, event: str | None = None):
        auth.require_owner(request)
        from sqlalchemy import select

        with session_scope(request.app.state.engine) as session:
            stmt = select(AuthEvent).order_by(AuthEvent.created_at.desc()).limit(200)
            if event:
                stmt = stmt.where(AuthEvent.event == event)
            rows = list(session.scalars(stmt))
            session.expunge_all()
        return _render(request, "admin_audit.html", "audit", {"rows": rows, "event": event})

    # --- settings -------------------------------------------------------

    @app.get("/admin/settings", response_class=HTMLResponse, include_in_schema=False)
    def admin_settings(request: Request):
        auth.require_owner(request)
        with session_scope(request.app.state.engine) as session:
            row = _get_or_create_settings(session)
            session.expunge_all()
        return _render(request, "admin_settings.html", "settings", {"settings": row})

    @app.post("/admin/settings", include_in_schema=False)
    def admin_settings_update(
        request: Request,
        csrf_token: str = Form(...),
        registration_open: str | None = Form(None),
        invite_only: str | None = Form(None),
        allowed_domains: str = Form(""),
        allowlist_emails: str = Form(""),
    ):
        auth.require_owner(request)
        auth.require_csrf(csrf_token, request, _settings())
        actor = getattr(request.state, "user_email", None)

        with session_scope(request.app.state.engine) as session:
            row = _get_or_create_settings(session)
            before = (
                f"registration_open={row.registration_open} invite_only={row.invite_only}"
            )
            row.registration_open = registration_open is not None
            row.invite_only = invite_only is not None
            row.allowed_domains = allowed_domains.strip()
            row.allowlist_emails = allowlist_emails.strip()
            row.updated_at = datetime.now(UTC)
            row.updated_by = actor
            after = (
                f"registration_open={row.registration_open} invite_only={row.invite_only}"
            )
            session.add(
                AuthEvent(
                    event="settings_changed", email=actor, actor=actor,
                    target_type="app_settings", target_id="1",
                    detail=f"{before} -> {after}",
                )
            )
        return RedirectResponse("/admin/settings", status_code=303)


def _get_or_create_settings(session):
    from culture.models.app_settings import AppSettings

    row = session.get(AppSettings, 1)
    if row is None:
        row = AppSettings(id=1)
        session.add(row)
        session.flush()
    return row
