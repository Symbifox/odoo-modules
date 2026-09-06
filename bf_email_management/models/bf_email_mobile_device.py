"""Mobile device (native Android app) bound to the email module.

One row per app installation. Authentication is a bearer ``device_token``
issued exclusively through the web-login capture flow:

  1. The app opens ``/mobile/v1/auth/start`` in a browser tab. ``auth="user"``
     means Odoo shows /web/login when needed, so password, Authentik SSO and
     TOTP all apply untouched.
  2. That route issues a short-lived, single-use ``pending_code`` and redirects
     to the app's deep link. The bearer token itself never travels in a URL.
  3. The app exchanges the code over HTTPS for ``device_token``.

There is deliberately NO password route on this API. ``bf_sms_archive`` still
carries one for API completeness and had to grow per-IP and per-login rate
limits plus a uniform failure response to stop it being a credential oracle
(security review of 2026-08-11, finding 4). Not shipping the route at all is
the same protection with nothing to get wrong.

``push_endpoint`` is a UnifiedPush (ntfy) endpoint — see push_transport.py.
No FCM field: the app carries no Google dependency.
"""
import base64
import hashlib
import logging
import secrets
from datetime import timedelta

from psycopg2 import OperationalError

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

CODE_TTL_MINUTES = 3  # single-use exchange code lifetime

# Sends allowed per device per hour. A person answering mail on a phone does
# not approach this; a stolen token trying to use the tenant's SMTP as a relay
# does. The point is to bound the damage window, not to police normal use —
# and to keep the sending domain off blocklists while somebody notices.
SEND_PER_HOUR = 100

# Fraîcheur en deçà de laquelle « vu la dernière fois » n'est pas réécrit.
# Voir ``_touch_last_seen`` : un battement par minute suffit à une liste
# d'appareils, et c'est ce qui laisse deux appels simultanés du même
# téléphone sans rien à se disputer.
HEARTBEAT_SECONDS = 60


class BfEmailMobileDevice(models.Model):
    _name = "bf.email.mobile.device"
    _description = "Appareil mobile — courriel"
    _rec_name = "name"
    _order = "last_seen desc, id desc"

    user_id = fields.Many2one(
        "res.users", string="Utilisateur", required=True, ondelete="cascade",
        index=True,
    )
    name = fields.Char(string="Appareil", default="Appareil Android")
    device_token = fields.Char(
        string="Jeton d'appareil", required=True, index=True, copy=False,
        groups="bf_email_management.group_email_admin",
    )
    push_endpoint = fields.Char(
        string="Endpoint UnifiedPush",
        help="URL d'endpoint UnifiedPush (ntfy) vers laquelle pousser les "
             "notifications de cet appareil.",
    )
    platform = fields.Char(default="android")
    # Sliding window for the send quota (see _check_send_quota).
    send_window_start = fields.Datetime(string="Début de fenêtre d'envoi")
    send_count = fields.Integer(string="Envois dans la fenêtre", default=0)
    app_version = fields.Char(string="Version de l'app")
    active = fields.Boolean(default=True)
    last_seen = fields.Datetime(string="Vu la dernière fois")
    # Single-use code for the web-login → token exchange.
    pending_code = fields.Char(
        index=True, copy=False,
        groups="bf_email_management.group_email_admin",
    )
    pending_code_expiry = fields.Datetime()
    # The PKCE challenge, and the whole reason this mechanism exists: a custom
    # app scheme is NOT exclusive on Android. Another app may declare the same
    # ``…://auth`` link and receive the pairing code instead of ours. Without
    # PKCE it would trade that code for a bearer token, hence for the person's
    # mailbox. With it, an intercepted code is worth nothing: the exchange
    # demands a verifier only the app that started the pairing holds, and that
    # never left it.
    #
    # The redirect-scheme allowlist does not cover this: it closes the open
    # redirect on the SERVER, not the local interception of the code on the
    # device.
    pkce_challenge = fields.Char(
        string="Défi PKCE", copy=False,
        groups="bf_email_management.group_email_admin",
    )

    _sql_constraints = [
        ("device_token_uniq", "unique(device_token)",
         "Ce jeton d'appareil existe déjà."),
    ]

    @api.model
    def _issue(self, user_id, name=None, platform="android"):
        """Create a device row carrying a fresh bearer token."""
        return self.sudo().create({
            "user_id": user_id,
            "name": (name or "Appareil Android")[:80],
            "device_token": secrets.token_urlsafe(32),
            "platform": platform,
        })

    @api.model
    def _issue_pending(self, user_id, name=None, platform="android",
                       challenge=None):
        """Create a device row and return its single-use exchange code.

        Only the code is handed to the browser; the bearer token is revealed
        at exchange time, over HTTPS, in a response body.
        """
        device = self._issue(user_id, name=name, platform=platform)
        code = secrets.token_urlsafe(24)
        device.write({
            "pending_code": code,
            "pending_code_expiry":
                fields.Datetime.now() + timedelta(minutes=CODE_TTL_MINUTES),
            "pkce_challenge": challenge or False,
        })
        return code

    @api.model
    def _verifie_pkce(self, attendu, verificateur):
        """True when the verifier matches the stored challenge.

        The challenge is the SHA-256 of the verifier, base64url without
        padding. The standard's ``plain`` method is not accepted here: it would
        let a challenge equal to its verifier through, and protect nothing.
        """
        if not verificateur:
            return False
        condense = hashlib.sha256(verificateur.encode("utf-8")).digest()
        calcule = base64.urlsafe_b64encode(condense).decode().rstrip("=")
        # Constant time: a challenge compares like a secret.
        return secrets.compare_digest(calcule, (attendu or "").strip())

    @api.model
    def _exchange(self, code, verificateur=None):
        """Consume a live exchange code, returning its device (or empty).

        The code alone is NOT enough: the PKCE verifier decides. A custom app
        scheme not being exclusive on Android, it is the only thing telling the
        app that started the pairing from the one that intercepted its answer.
        """
        if not code:
            return self.browse()
        device = self.sudo().search([("pending_code", "=", code)], limit=1)
        if not device:
            return self.browse()
        # The pending row is DROPPED, not left in place, on both refusals: its
        # bearer token was never revealed — only the exchange hands it out —
        # and a code replayed until the right app shows up would give the one
        # that intercepted it a second chance.
        if not device.pending_code_expiry \
                or device.pending_code_expiry < fields.Datetime.now():
            device.unlink()
            return self.browse()
        if not self._verifie_pkce(device.pkce_challenge, verificateur):
            _logger.warning(
                "Mobile mail API: exchange refused, PKCE verifier missing or "
                "wrong")
            device.unlink()
            return self.browse()
        device.write({
            "pending_code": False,
            "pending_code_expiry": False,
            "pkce_challenge": False,
        })
        return device

    @api.model
    def _resolve(self, token):
        """Return the active device carrying this bearer token (or empty).

        The device's *user* must still be active too. Tokens never expire, so
        without this an employee who leaves keeps a working mailbox on their
        phone: archiving the account in Odoo revokes the web session but says
        nothing about a bearer token issued months earlier. Deactivating the
        user is the one gesture everybody actually performs on departure, so it
        has to be the gesture that closes this door as well.
        """
        if not token:
            return self.browse()
        device = self.sudo().search(
            [("device_token", "=", token), ("active", "=", True)], limit=1,
        )
        if device and (not device.user_id.active or device.user_id.share):
            _logger.info(
                "bf.email mobile: jeton refusé — l'utilisateur %s n'est plus "
                "un interne actif.", device.user_id.login,
            )
            return self.browse()
        return device

    def _touch_last_seen(self):
        """Note que l'appareil vient de parler — hors de la transaction de la requête.

        🔴 Écrire ``last_seen`` par l'ORM à chaque appel authentifié posait un
        UPDATE de la ligne d'appareil dans la transaction de CHAQUE requête.
        Deux appels simultanés du même téléphone — un archivage et la relecture
        de la liste, deux archivages rapprochés, la liste et ses compteurs —
        écrivaient donc la même ligne en même temps, et sous REPEATABLE READ
        le second échoue : « could not serialize access due to concurrent
        update ». L'erreur sortait au ``flush`` des compteurs, c'est-à-dire
        APRÈS que ``action_archive`` eut déplacé le message côté IMAP ; la
        transaction Odoo était annulée, pas le déplacement. Le téléphone
        recevait un 500, remettait la ligne en boîte, et le miroir IMAP la
        marquait traitée cinq minutes plus tard. an internal report: « some come back for a time ».

        Deux règles règlent le conflit :

        - **Un battement par minute**, pas par requête. Une liste d'appareils
          n'a pas besoin de la seconde près.
        - **Dans son propre curseur, validé sur-le-champ.** La transaction de
          la requête ne touche plus jamais la ligne d'appareil, donc n'a rien
          à y perdre ; et deux battements qui se croisent malgré tout se
          règlent ici, en silence : celui de l'autre appel vaut le nôtre.

        Retourne True si le battement a été écrit.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        seen = self.sudo().last_seen
        if seen and (now - seen) < timedelta(seconds=HEARTBEAT_SECONDS):
            return False
        stale_before = now - timedelta(seconds=HEARTBEAT_SECONDS)
        try:
            with self.env.registry.cursor() as cr:
                # SKIP LOCKED : un battement déjà en cours dans un autre
                # appel n'est ni attendu ni disputé, on passe. Le curseur est
                # neuf, donc son instantané date de cette ligne : un battement
                # déjà validé se lit dans ``last_seen`` et le seuil le filtre.
                cr.execute(
                    "UPDATE bf_email_mobile_device SET last_seen = %s "
                    "WHERE id = (SELECT id FROM bf_email_mobile_device "
                    "            WHERE id = %s "
                    "              AND (last_seen IS NULL OR last_seen < %s) "
                    "            FOR UPDATE SKIP LOCKED)",
                    (now, self.id, stale_before),
                    log_exceptions=False,
                )
                written = cr.rowcount == 1
        except OperationalError:
            # Il reste la fenêtre entre l'instantané et le verrou : l'autre
            # battement vaut le nôtre, on se tait.
            return False
        # Le cache de la requête garde l'ancienne valeur ; personne ne la
        # relit dans la même requête, mais autant ne pas mentir.
        self.invalidate_recordset(["last_seen"])
        return written

    def _check_send_quota(self, limit=SEND_PER_HOUR):
        """Consume one send from this device's hourly allowance.

        A bearer token lives on a phone and never expires. Whoever holds it can
        send mail as its owner, from the tenant's own domain — the classic way
        a legitimate sending reputation gets destroyed by someone else. This
        does not prevent that; it bounds how much can go out before anyone
        notices, which is the realistic goal.

        Only enforced when a device is in play: the desktop and the ORM path
        never pass one, and neither is reachable from a stolen phone.
        """
        self.ensure_one()
        device = self.sudo()
        now = fields.Datetime.now()
        if not device.send_window_start or \
                (now - device.send_window_start) > timedelta(hours=1):
            device.write({"send_window_start": now, "send_count": 1})
            return
        if device.send_count >= limit:
            _logger.warning(
                "bf.email mobile: plafond d'envoi atteint (appareil %s, "
                "usager %s, %d/h).", device.id, device.user_id.login, limit)
            raise UserError(_(
                "Plafond d'envoi atteint pour cet appareil (%d par heure). "
                "Réessayez plus tard, ou envoyez depuis Odoo.") % limit)
        device.send_count += 1

    @api.model
    def _gc_pending(self):
        """Drop devices whose exchange code expired unclaimed.

        An abandoned /auth/start (user closed the tab) leaves a row holding a
        live bearer token nobody will ever collect. Called from the sync cron
        rather than its own scheduled action.
        """
        stale = self.sudo().search([
            ("pending_code", "!=", False),
            ("pending_code_expiry", "<", fields.Datetime.now()),
        ])
        if stale:
            stale.unlink()
        return len(stale)
