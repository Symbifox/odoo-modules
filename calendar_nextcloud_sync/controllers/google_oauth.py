# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""HTTP controllers for the Google Calendar OAuth 2.0 flow.

Routes
------
- GET  /bf_calendar/google/oauth/initiate
    Starts the OAuth flow. Generates a CSRF state, builds the Google
    consent URL, redirects the user to Google.
- GET  /bf_calendar/google/oauth/callback
    Endpoint registered with Google as the redirect URI. Validates the
    CSRF state, exchanges the authorization code for tokens, stores
    them on the current res.users record (encrypted), redirects to
    user preferences.
"""

import logging
import os
from urllib.parse import urlencode

import werkzeug
from markupsafe import escape

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

# Google's incremental authorization remembers every scope a user has
# previously granted to this OAuth client and returns the union on the
# next consent. oauthlib then raises "Scope has changed" on fetch_token()
# because the response scope ⊋ the requested scope. Relaxing the check
# is safe here: we only ever read `credentials.scopes` for diagnostics
# and the access token can only do what Google decided to grant — extra
# scopes don't widen our reach. setdefault so a container env var wins.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

try:
    from google_auth_oauthlib.flow import Flow
except ImportError:
    Flow = None


GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
CALLBACK_PATH = "/bf_calendar/google/oauth/callback"


def _render_error(title, message, recoverable=True):
    """Return a minimal HTML error page.

    ⚠ ``message`` carries attacker-controlled text on the callback route: Google
    hands back ``?error=<code>``, but nothing stops a third party from sending a
    victim straight to ``/bf_calendar/google/oauth/callback?error=<payload>``.
    The route is ``auth="public"`` and this response is served as text/html on
    the Odoo origin, so an unescaped interpolation here is a reflected XSS that
    can drive ``call_kw`` as whoever is logged in. Both parts are escaped, and a
    locked-down CSP is attached as a second line of defence.
    """
    title, message = escape(title), escape(message)
    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="utf-8"/>
    <title>{title}</title>
    <style>
        body {{ font-family: sans-serif; margin: 3em auto; max-width: 40em;
                color: #333; padding: 0 1em; }}
        .box {{ border: 1px solid #d9534f; border-radius: 4px;
                padding: 1em 1.5em; background: #f9eaea; }}
        h1 {{ color: #d9534f; margin-top: 0; }}
        a {{ color: #337ab7; }}
    </style>
</head>
<body>
    <div class="box">
        <h1>{title}</h1>
        <p>{message}</p>
        {'<p><a href="/odoo/preferences">Retour aux préférences</a></p>' if recoverable else ''}
    </div>
</body>
</html>
"""
    return request.make_response(
        html,
        headers=[
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Security-Policy",
             "default-src 'none'; style-src 'unsafe-inline'; "
             "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
            ("X-Frame-Options", "DENY"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ],
    )


def _redirect_uri():
    """Build the absolute redirect URI from web.base.url.

    Avoids the reverse-proxy Werkzeug bug that plagues Odoo's native
    `google_calendar` module — we explicitly read the
    stored system parameter rather than relying on the request host.

    HTTPS is enforced. Localhost / 127.0.0.1 are allowed over HTTP for
    local dev/staging since Google's OAuth allows http on loopback.
    """
    ICP = request.env["ir.config_parameter"].sudo()
    base = ICP.get_param("web.base.url", "").rstrip("/")
    if not base:
        raise UserError(_(
            "web.base.url is not set. Configure it in Settings > "
            "Technical > Parameters before connecting Google Calendar."
        ))
    is_loopback = (
        base.startswith("http://localhost")
        or base.startswith("http://127.0.0.1")
    )
    if not base.startswith("https://") and not is_loopback:
        raise UserError(_(
            "OAuth security: web.base.url must use HTTPS (except "
            "http://localhost and http://127.0.0.1 for local dev). "
            "Current value: %s"
        ) % base)
    return base + CALLBACK_PATH


def _build_flow():
    """Construct a Flow object from stored OAuth client credentials."""
    if Flow is None:
        raise UserError(_(
            "google-auth-oauthlib is not installed in the Odoo container."
        ))
    ICP = request.env["ir.config_parameter"].sudo()
    client_id = ICP.get_param(
        "calendar_nextcloud_sync.google_oauth_client_id", ""
    )
    secret_enc = ICP.get_param(
        "calendar_nextcloud_sync.google_oauth_client_secret_enc", ""
    )
    client_secret = ""
    if secret_enc:
        client_secret = request.env[
            "nextcloud.calendar.sync.config"
        ]._decrypt_value(secret_enc)
    if not client_id or not client_secret:
        raise UserError(_(
            "Google OAuth client credentials are not configured. "
            "Go to Settings > Calendar > Google Calendar to set them."
        ))

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
            "redirect_uris": [_redirect_uri()],
        }
    }
    flow = Flow.from_client_config(
        client_config, scopes=GOOGLE_SCOPES, redirect_uri=_redirect_uri()
    )
    return flow


class GoogleCalendarOAuthController(http.Controller):

    @http.route(
        "/bf_calendar/google/oauth/initiate",
        type="http",
        auth="user",
        website=False,
        csrf=False,
    )
    def initiate(self, **kwargs):
        """Redirect the user to Google's OAuth consent screen."""
        try:
            flow = _build_flow()
        except UserError as e:
            return _render_error(_("Configuration manquante"), str(e))

        state = request.env.user._generate_oauth_state()
        auth_url, _state = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=state,
        )
        # Google enforces PKCE on Web application clients. The Flow object
        # generates a `code_verifier` during authorization_url() and expects
        # the same value on fetch_token(). Since the callback creates a new
        # Flow instance, we persist the verifier in two places:
        # - request.session (fast path, no DB write on callback)
        # - res.users.x_google_pkce_verifier (fallback when the session is
        #   rotated between initiate() and callback() — e.g. an intermediate
        #   login/TOTP step landed the callback in a fresh session).
        request.session["google_pkce_verifier"] = flow.code_verifier
        request.env.user._store_google_pkce_verifier(flow.code_verifier)
        return werkzeug.utils.redirect(auth_url, code=302)

    @http.route(
        CALLBACK_PATH,
        type="http",
        auth="public",
        website=False,
        csrf=False,
    )
    def callback(self, code=None, state=None, error=None, **kwargs):
        """Handle Google's redirect back with an authorization code.

        Public auth: Google's cross-site redirect (SameSite=Lax cookie
        rules) may not carry the user's Odoo session cookie, so we
        resolve the user from the ``state`` parameter (32-byte CSRF
        token persisted on ``res.users.x_google_oauth_state`` by
        ``initiate()``) instead of relying on ``request.env.user``.
        """
        if error:
            _logger.warning(
                "Google OAuth callback returned error: %s", error,
            )
            return _render_error(
                _("Autorisation refusée"),
                _("Google a renvoyé l'erreur : %s") % error,
            )
        if not code:
            return _render_error(
                _("Paramètre manquant"),
                _("Aucun code d'autorisation reçu de Google."),
            )
        target_user = request.env["res.users"]._find_by_oauth_state(state)
        if not target_user:
            _logger.warning(
                "OAuth state did not match any user (possible CSRF / "
                "stale state / replay)"
            )
            return _render_error(
                _("État OAuth invalide"),
                _("La valeur de state ne correspond pas. Réessayez depuis vos préférences."),
            )
        # Clear the state IMMEDIATELY after lookup to prevent replay of the
        # same (code, state) pair. Even if token exchange below fails, the
        # state is consumed — user must restart the flow.
        target_user.sudo().write({"x_google_oauth_state": False})

        try:
            flow = _build_flow()
            # Restore the PKCE code_verifier. Session may have been lost on
            # the cross-site redirect, so the DB copy persisted by
            # initiate() is the reliable path.
            verifier = request.session.pop("google_pkce_verifier", None)
            if not verifier:
                verifier = target_user._pop_google_pkce_verifier()
            else:
                # Session had it — clear the DB copy too so it can't be reused.
                target_user._pop_google_pkce_verifier()
            if verifier:
                flow.code_verifier = verifier
            flow.fetch_token(code=code)
        except UserError as e:
            # UserError messages are our own, safe to display
            return _render_error(_("Configuration manquante"), str(e))
        except Exception:
            # Don't leak raw exception text (may contain tokens, client_secret,
            # or library internals). Details go to the server log only.
            _logger.exception(
                "Google OAuth token exchange failed for user %s",
                target_user.login,
            )
            return _render_error(
                _("Échec de l'échange de token"),
                _("Une erreur est survenue lors de l'échange avec Google. "
                  "Vérifiez les logs du serveur ou contactez un administrateur."),
            )

        creds = flow.credentials

        # Identify the connected Google account. We avoid the userinfo
        # endpoint (would require `openid`+`userinfo.email` scopes) and
        # instead read the primary calendar's id, which equals the account
        # email for personal Google accounts. Works with just the calendar
        # scope, which is what we actually need.
        email = None
        try:
            from googleapiclient.discovery import build
            svc = build(
                "calendar", "v3", credentials=creds, cache_discovery=False
            )
            primary = svc.calendars().get(calendarId="primary").execute()
            email = primary.get("id")
        except Exception as e:
            _logger.warning(
                "Failed to read primary calendar id (account email): %s", e,
            )

        target_user._store_google_tokens(creds, email=email)
        _logger.info(
            "Google OAuth connected for user %s (google_email=%s)",
            target_user.login, email,
        )

        # Redirect to the Odoo home (the user can re-open Preferences from
        # the top-right menu to verify their connection status).
        return werkzeug.utils.redirect("/odoo", code=302)
