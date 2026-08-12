# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Per-user Google OAuth 2.0 token storage for Google Calendar sync.

Each Odoo user connects their own Google account via the OAuth flow
(triggered from user preferences). The returned access_token and
refresh_token are stored encrypted on res.users. Token refresh is
automatic on every access via google-auth's built-in Credentials.refresh().
"""

import json
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GAuthRequest
except ImportError:
    Credentials = None
    GAuthRequest = None


class ResUsers(models.Model):
    _inherit = "res.users"

    x_google_oauth_token_encrypted = fields.Char(
        string="Google OAuth Access Token (encrypted)",
        groups="base.group_system",
        copy=False,
    )
    x_google_oauth_refresh_token_encrypted = fields.Char(
        string="Google OAuth Refresh Token (encrypted)",
        groups="base.group_system",
        copy=False,
    )
    x_google_oauth_expiry = fields.Datetime(
        string="Google OAuth Token Expiry",
        copy=False,
    )
    x_google_oauth_scope = fields.Char(
        string="Google OAuth Scopes",
        copy=False,
        help="Space-separated list of scopes consented by the user",
    )
    x_google_email = fields.Char(
        string="Google Account Email",
        copy=False,
        help="Email of the Google account connected by this user",
    )
    x_google_oauth_state = fields.Char(
        string="OAuth CSRF State",
        groups="base.group_system",
        copy=False,
        help="Random state value for in-flight OAuth flow (CSRF protection)",
    )
    x_google_pkce_verifier = fields.Char(
        string="OAuth PKCE Verifier",
        groups="base.group_system",
        copy=False,
        help="PKCE code_verifier for in-flight OAuth flow. Persisted on the "
        "user record as a fallback when request.session is rotated between "
        "initiate() and callback() (e.g., after intermediate login/TOTP).",
    )
    x_google_oauth_connected = fields.Boolean(
        string="Google Calendar Connected",
        compute="_compute_google_oauth_connected",
        store=False,
    )
    x_google_self_addresses = fields.Text(
        string="Google Self-Addresses (aliases)",
        copy=False,
        help="Comma- or newline-separated extra email addresses that "
        "resolve to the same Google account as the OAuth-connected one "
        "(e.g. Google Workspace alternate addresses, alias domains). "
        "Attendees whose email matches one of these — or the OAuth Google "
        "email, the Odoo login, or the user/partner email (always included "
        "automatically) — are stripped from outbound events to prevent "
        "Google from sending a courtesy invitation back to the calendar "
        "owner under a different identity.",
    )

    @api.depends(
        "x_google_oauth_refresh_token_encrypted", "x_google_oauth_expiry"
    )
    def _compute_google_oauth_connected(self):
        for user in self:
            user.x_google_oauth_connected = bool(
                user.x_google_oauth_refresh_token_encrypted
            )

    # === Helpers ===

    def _get_google_self_addresses(self):
        """Return the normalized set of email addresses that represent
        this user when pushing attendees to Google.

        Google Calendar API does NOT canonicalize Workspace aliases in the
        attendees list — an event whose attendee email is an alias of the
        calendar owner is still treated as a third party, triggering an
        iCal invitation back to the owner's inbox. We strip those entries
        before push to keep events clean.
        """
        self.ensure_one()
        result = set()

        def _add(value):
            if value:
                normalized = value.strip().lower()
                if normalized:
                    result.add(normalized)

        _add(self.x_google_email)
        _add(self.login)
        _add(self.email)
        if self.partner_id:
            _add(self.partner_id.email)
        raw = self.x_google_self_addresses or ""
        for token in raw.replace("\n", ",").replace(";", ",").split(","):
            _add(token)
        return result

    def _google_encrypt(self, value):
        """Reuse the sync config's Fernet helpers (same ICP key)."""
        if not value:
            return False
        return self.env["nextcloud.calendar.sync.config"]._encrypt_value(value)

    def _google_decrypt(self, encrypted):
        if not encrypted:
            return False
        return self.env["nextcloud.calendar.sync.config"]._decrypt_value(
            encrypted
        )

    def _get_google_oauth_client(self):
        """Return (client_id, client_secret) from system parameters."""
        ICP = self.env["ir.config_parameter"].sudo()
        client_id = ICP.get_param(
            "calendar_nextcloud_sync.google_oauth_client_id", ""
        )
        secret_enc = ICP.get_param(
            "calendar_nextcloud_sync.google_oauth_client_secret_enc", ""
        )
        client_secret = (
            self.env["nextcloud.calendar.sync.config"]._decrypt_value(secret_enc)
            if secret_enc
            else ""
        )
        return client_id or "", client_secret or ""

    def _get_google_credentials(self):
        """Return a google.oauth2.credentials.Credentials for this user.

        Automatically refreshes the access token if expired. Persists the
        refreshed token back to res.users. Raises UserError if the user
        hasn't connected their Google account.

        Access control: only callable for self, or by members of
        base.group_system (superuser, admins, and cron jobs running as
        OdooBot). Prevents a regular user from obtaining another user's
        decrypted tokens via `env['res.users'].browse(other_id)._get_...()`.
        """
        self.ensure_one()
        env_uid = self.env.uid or 1
        is_self = self.id == env_uid
        is_admin = self.env.user.has_group("base.group_system")
        is_superuser = env_uid == 1 or self.env.su
        if not (is_self or is_admin or is_superuser):
            raise AccessError(_(
                "You cannot access another user's Google credentials."
            ))
        if Credentials is None:
            raise UserError(_(
                "The google-auth Python library is not installed in the Odoo "
                "container. Run `pip install google-auth google-auth-oauthlib "
                "google-api-python-client` and restart."
            ))
        sudo = self.sudo()
        if not sudo.x_google_oauth_refresh_token_encrypted:
            raise UserError(_(
                "User %s has not connected their Google Calendar account. "
                "Go to Preferences > Google Calendar and click Connect."
            ) % self.name)

        access_token = sudo._google_decrypt(sudo.x_google_oauth_token_encrypted)
        refresh_token = sudo._google_decrypt(
            sudo.x_google_oauth_refresh_token_encrypted
        )
        client_id, client_secret = sudo._get_google_oauth_client()
        if not client_id or not client_secret:
            raise UserError(_(
                "Google OAuth client credentials are not configured. "
                "Go to Settings > Calendar > Google Calendar to set them."
            ))

        scopes = (sudo.x_google_oauth_scope or " ".join(GOOGLE_SCOPES)).split()
        # Pass the stored expiry so google-auth can detect an expired token
        # and refresh it proactively. Without it, creds.expiry is None,
        # creds.expired is always False, creds.valid is always True, and the
        # block below never refreshes -- the access token only gets renewed on
        # an actual 401 from Google. Odoo stores Datetime fields naive in UTC,
        # which is exactly what google-auth expects for creds.expiry.
        creds = Credentials(
            token=access_token or None,
            refresh_token=refresh_token,
            token_uri=GOOGLE_TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            expiry=sudo.x_google_oauth_expiry or None,
        )

        # Refresh if expired or near-expiry (google-auth handles the check
        # via creds.expired, but that requires creds.expiry to be set)
        if not creds.valid:
            try:
                creds.refresh(GAuthRequest())
            except Exception as e:
                err_text = str(e)
                _logger.error(
                    "Failed to refresh Google token for user %s (login=%s): %s",
                    self.id, self.login, err_text,
                )
                # If Google says the grant is invalid (user revoked or token
                # expired beyond recovery), clear the stored tokens so the
                # next sync attempt fails fast and prompts reconnection.
                if (
                    "invalid_grant" in err_text
                    or "unauthorized_client" in err_text
                    or "Token has been expired or revoked" in err_text
                ):
                    sudo.write({
                        "x_google_oauth_token_encrypted": False,
                        "x_google_oauth_refresh_token_encrypted": False,
                        "x_google_oauth_expiry": False,
                    })
                    raise UserError(_(
                        "Your Google account was disconnected "
                        "(revoked or expired). Please reconnect from "
                        "Preferences > Google Calendar."
                    ))
                raise UserError(_(
                    "Could not refresh Google OAuth token. "
                    "Please reconnect your Google account from Preferences. "
                    "(See server logs for details.)"
                ))
            # Persist the refreshed access token. Wrap in savepoint so a
            # concurrent refresh doesn't crash this one.
            new_refresh = creds.refresh_token or refresh_token
            try:
                with self.env.cr.savepoint():
                    sudo.write({
                        "x_google_oauth_token_encrypted": sudo._google_encrypt(
                            creds.token
                        ),
                        "x_google_oauth_refresh_token_encrypted": sudo._google_encrypt(
                            new_refresh
                        ),
                        "x_google_oauth_expiry": creds.expiry,
                    })
            except Exception as e:
                _logger.warning(
                    "Concurrent token refresh write conflict for user %s: %s",
                    self.id, e,
                )

        return creds

    def _store_google_tokens(self, credentials, email=None):
        """Persist tokens returned by the OAuth callback.

        Called from the controller after Flow.fetch_token(). Expects a
        google.oauth2.credentials.Credentials object.
        """
        self.ensure_one()
        sudo = self.sudo()
        vals = {
            "x_google_oauth_token_encrypted": sudo._google_encrypt(
                credentials.token
            ),
            "x_google_oauth_expiry": credentials.expiry,
            "x_google_oauth_scope": " ".join(credentials.scopes or GOOGLE_SCOPES),
            "x_google_oauth_state": False,
        }
        if credentials.refresh_token:
            vals["x_google_oauth_refresh_token_encrypted"] = sudo._google_encrypt(
                credentials.refresh_token
            )
        if email:
            vals["x_google_email"] = email
        sudo.write(vals)

    def _generate_oauth_state(self):
        """Generate a random CSRF state and persist it on the user record."""
        self.ensure_one()
        state = secrets.token_urlsafe(32)
        self.sudo().write({"x_google_oauth_state": state})
        return state

    def _validate_oauth_state(self, state):
        """Return True if the given state matches the stored one."""
        self.ensure_one()
        stored = self.sudo().x_google_oauth_state
        if not stored or not state:
            return False
        return secrets.compare_digest(stored, state)

    @api.model
    def _find_by_oauth_state(self, state):
        """Resolve the user record holding this in-flight OAuth state.

        The OAuth callback runs as ``auth="public"`` because Google's
        cross-site redirect may not carry the user's session cookie
        (SameSite=Lax). State is a 32-byte ``secrets.token_urlsafe``,
        unique enough that one stored row is the legitimate caller.
        Returns an empty recordset if no match.
        """
        if not state:
            return self.browse()
        users = self.sudo().search(
            [("x_google_oauth_state", "!=", False)]
        )
        for user in users:
            if user.x_google_oauth_state and secrets.compare_digest(
                user.x_google_oauth_state, state
            ):
                return user
        return self.browse()

    def _store_google_pkce_verifier(self, verifier):
        """Persist the PKCE code_verifier on the user record."""
        self.ensure_one()
        self.sudo().write({"x_google_pkce_verifier": verifier or False})

    def _pop_google_pkce_verifier(self):
        """Read and clear the persisted PKCE code_verifier."""
        self.ensure_one()
        sudo = self.sudo()
        verifier = sudo.x_google_pkce_verifier
        if verifier:
            sudo.write({"x_google_pkce_verifier": False})
        return verifier or None

    # === Actions ===

    def action_connect_google_calendar(self):
        """Redirect the user to the OAuth initiate endpoint."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/bf_calendar/google/oauth/initiate",
            "target": "self",
        }

    def action_disconnect_google_calendar(self):
        """Clear OAuth tokens. Does NOT revoke with Google — user can do
        that manually via https://myaccount.google.com/permissions."""
        self.ensure_one()
        self.sudo().write({
            "x_google_oauth_token_encrypted": False,
            "x_google_oauth_refresh_token_encrypted": False,
            "x_google_oauth_expiry": False,
            "x_google_oauth_scope": False,
            "x_google_email": False,
            "x_google_oauth_state": False,
            "x_google_pkce_verifier": False,
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Google Calendar"),
                "message": _(
                    "Disconnected. Visit "
                    "https://myaccount.google.com/permissions to fully revoke "
                    "access for this app."
                ),
                "type": "success",
                "sticky": False,
            },
        }
