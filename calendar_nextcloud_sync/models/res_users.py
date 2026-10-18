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
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import email_normalize, escape_psql

_logger = logging.getLogger(__name__)

# Durée de vie du state OAuth : le temps de passer l'écran de consentement
# Google. Au-delà, le retour est refusé et il faut relancer depuis les
# préférences.
OAUTH_STATE_TTL_MINUTES = 10

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
    # Les cinq champs Google lisibles (expiration, portées, courriel, booléen
    # « connecté », alias) sont réservés aux usagers internes : la base donne
    # la lecture de res.users au portail, qui les lisait par RPC sur toutes
    # les fiches.
    x_google_oauth_expiry = fields.Datetime(
        string="Google OAuth Token Expiry",
        groups="base.group_user",
        copy=False,
    )
    x_google_oauth_scope = fields.Char(
        string="Google OAuth Scopes",
        groups="base.group_user",
        copy=False,
        help="Space-separated list of scopes consented by the user",
    )
    x_google_email = fields.Char(
        string="Google Account Email",
        groups="base.group_user",
        copy=False,
        help="Email of the Google account connected by this user",
    )
    x_google_oauth_state = fields.Char(
        string="OAuth CSRF State",
        groups="base.group_system",
        copy=False,
        help="Random state value for in-flight OAuth flow (CSRF protection)",
    )
    x_google_oauth_state_date = fields.Datetime(
        string="OAuth State Created",
        groups="base.group_system",
        copy=False,
        help="Creation time of the in-flight OAuth state; the state expires "
        "after OAUTH_STATE_TTL_MINUTES.",
    )
    x_google_pkce_verifier = fields.Char(
        string="OAuth PKCE Verifier",
        groups="base.group_system",
        copy=False,
        help="PKCE code_verifier for in-flight OAuth flow. Persisted on the "
        "user record as a fallback when request.session is rotated between "
        "initiate() and callback() (e.g., after intermediate login/TOTP).",
    )
    # compute_sudo : le calcul lit le jeton de rafraîchissement, réservé à
    # base.group_system. Sans sudo, quiconque n'est pas administrateur et
    # lisait ce booléen (onglet Google Calendar de « Mon profil », fiche
    # d'un usager) recevait une « Erreur d'accès » sur le jeton. Seul le
    # booléen sort; le jeton reste illisible.
    x_google_oauth_connected = fields.Boolean(
        string="Google Calendar Connected",
        compute="_compute_google_oauth_connected",
        compute_sudo=True,
        store=False,
        groups="base.group_user",
    )
    x_google_self_addresses = fields.Text(
        string="Google Self-Addresses (aliases)",
        groups="base.group_user",
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

    # Champs que l'onglet Google Calendar met dans « Mon profil »
    # (base.view_users_form_simple_modif, donc aussi hr.res_users_view_form_profile).
    # Odoo ne lit le profil en sudo que si TOUS ses champs sont « lisibles par
    # soi » : un seul champ hors liste et la lecture se fait avec les droits de
    # l'usager, ce qui refuse les champs privés de hr (adresse, NAS...) à tout
    # employé qui n'est pas agent RH. Les jetons, le state et le vérificateur
    # PKCE n'y sont PAS : ils restent réservés à base.group_system.
    _GOOGLE_PROFILE_FIELDS = [
        "x_google_oauth_connected",
        "x_google_email",
        "x_google_oauth_expiry",
        "x_google_self_addresses",
    ]

    # Réservé aux usagers internes, comme les champs eux-mêmes : la liste
    # « lisible par soi » fait lire en sudo, elle rouvrirait ces champs au
    # portail sur sa propre fiche.
    # 🔴 `_is_internal()` lit les groupes de l'usager quand leur cache est
    # froid; cette lecture repasse par SELF_READABLE_FIELDS (récursion sans
    # fin). La clé de contexte suit l'usager jusqu'à cette
    # lecture imbriquée (sudo() garde le contexte), qui n'a pas besoin des
    # champs Google et sort à vide. Aucun état global : une exception ne
    # laisse rien derrière elle.
    def _google_self_fields(self, fnames):
        if self.env.context.get("_bf_google_garde"):
            return []
        user = self.env.user
        if not user:
            return []
        return fnames if user.with_context(
            _bf_google_garde=True)._is_internal() else []

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + self._google_self_fields(
            self._GOOGLE_PROFILE_FIELDS)

    @property
    def SELF_WRITEABLE_FIELDS(self):
        # Les alias sont éditables dans le profil; sans cette ligne, enregistrer
        # le profil après les avoir changés était refusé (écriture sur res.users).
        return super().SELF_WRITEABLE_FIELDS + self._google_self_fields(
            ["x_google_self_addresses"])

    @api.model
    def _split_google_self_addresses(self, raw):
        """Les alias tels que la poussée Google les lit : séparés par des
        virgules, des points-virgules ou des sauts de ligne, en minuscules."""
        return [
            token.strip().lower()
            for token in (raw or "").replace("\n", ",").replace(";", ",").split(",")
            if token.strip()
        ]

    @api.constrains("x_google_self_addresses")
    def _check_google_self_addresses(self):
        """Un alias ne peut pas être l'adresse d'un AUTRE usager.

        La poussée Google retire des invités toute adresse de la liste
        (`_get_google_self_addresses`), et la lecture suivante remplace les
        participants de l'événement Odoo par ce que porte la copie Google.
        L'adresse d'un ou d'une collègue inscrite en alias faisait donc
        disparaître cette personne des événements, en silence. Ses propres
        adresses secondaires restent permises.
        """
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        for user in self:
            for alias in self._split_google_self_addresses(
                    user.sudo().x_google_self_addresses):
                # « Nom <x@y> » se compare aussi par son adresse nue.
                normalise = email_normalize(alias) or alias
                autre = Users.search([
                    ("id", "!=", user.id),
                    "|", "|",
                    ("login", "=ilike", escape_psql(alias)),
                    ("login", "=ilike", escape_psql(normalise)),
                    ("partner_id.email_normalized", "=", normalise),
                ], limit=1)
                if autre:
                    raise ValidationError(_(
                        "%(address)s is the address of another account: it "
                        "cannot be used as a Google alias. Only list your own "
                        "secondary addresses here.",
                        address=alias,
                    ))

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
        for token in self._split_google_self_addresses(
                self.x_google_self_addresses):
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
        self.sudo().write({
            "x_google_oauth_state": state,
            "x_google_oauth_state_date": fields.Datetime.now(),
        })
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
        limite = fields.Datetime.now() - timedelta(
            minutes=OAUTH_STATE_TTL_MINUTES)
        for user in users:
            if user.x_google_oauth_state and secrets.compare_digest(
                user.x_google_oauth_state, state
            ):
                # Un state sans date (antérieur à 18.0.2.19.1) ou trop vieux
                # ne vaut plus rien : on le consomme et on refuse.
                date = user.x_google_oauth_state_date
                if not date or date < limite:
                    user._consume_oauth_state()
                    return self.browse()
                return user
        return self.browse()

    def _consume_oauth_state(self):
        """Invalide le state en cours et la copie du vérificateur PKCE.

        Usage unique : appelé dès que le retour OAuth a trouvé son usager,
        avant tout échange de jeton, pour qu'un même (code, state) ne se
        rejoue pas.
        """
        self.sudo().write({
            "x_google_oauth_state": False,
            "x_google_oauth_state_date": False,
        })

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
        # Soi-même ou un administrateur seulement : le bouton est
        # appelable par RPC sur n'importe quelle fiche usager lisible.
        if (
            not self.env.su
            and self != self.env.user
            and not self.env.user.has_group("base.group_system")
        ):
            raise AccessError(_(
                "You can only disconnect your own Google Calendar."
            ))
        self.sudo().write({
            "x_google_oauth_token_encrypted": False,
            "x_google_oauth_refresh_token_encrypted": False,
            "x_google_oauth_expiry": False,
            "x_google_oauth_scope": False,
            "x_google_email": False,
            "x_google_oauth_state": False,
            "x_google_oauth_state_date": False,
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
