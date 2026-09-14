"""One person's Nextcloud connection, obtained through Login Flow v2.

Nextcloud's Login Flow v2 is the supported way for a third-party application to
act on someone's behalf. Odoo asks Nextcloud for a flow, the person approves it
in a Nextcloud window (so whatever authenticates Nextcloud, Authentik included,
authenticates them), and Odoo polls until Nextcloud hands over an app password
made for this device. Odoo never sees the person's password.

Four properties of that exchange drive the design:

* the app password is a PERMANENT token: it never expires by itself. Revocation
  is explicit, on either side: "Deconnecter" in Odoo (which also deletes it on
  Nextcloud), Security > Devices in Nextcloud, or archiving the Odoo account;
* the poll token is as good as the app password for twenty minutes, so it never
  leaves the server and is stored encrypted like the password itself;
* whoever opens the login page approves it. A login page forwarded to someone
  else would connect the sender to the recipient's files, so by default the
  approved account must match the Odoo user's LOGIN (email address or login
  name). Not the Odoo email field: people may edit their own email address,
  and could set it to a colleague's before forwarding the page;
* an app password is only ever sent to the server that issued it. The
  connection remembers that server. Revoking a token is also sending it, so
  a token is revoked on its own server or not at all, and a configuration
  pointed elsewhere revokes on the old server and drops its connections
  instead of replaying them on the new one.

Every URL Nextcloud hands back is compared, as a string, to the configured
server. Parsing it would not do: Python's urlparse and urllib3 (which requests
uses to connect) disagree on URLs such as ``https://127.0.0.1\\@nextcloud.test``,
one reading the configured host and the other connecting elsewhere. No request
of the flow follows redirects, for the same reason.

Rows are created and changed only by server code (the browser facade), never
through RPC: people can read their own row, administrators can read and revoke
everyone's, nobody can write one directly.
"""

import logging
import re
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Nextcloud keeps a Login Flow v2 open for twenty minutes.
FLOW_TTL = timedelta(minutes=20)
HTTP_TIMEOUT = 15
# Revocation is best effort and may run for every connection of a configuration
# in one request: a silent old server must not hold the worker for long.
REVOKE_TIMEOUT = 5
# Nextcloud serves the flow with or without the front controller in the path.
FLOW_ROOTS = ("/login/v2", "/index.php/login/v2")
FLOW_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{32,256}$")
# Characters that make URL parsers disagree, or have no business in these URLs.
UNSAFE_URL_RE = re.compile(r"[\\@\s\x00-\x1f\x7f]")


def _server_key(url):
    """The form under which a connection remembers its server."""
    return (url or "").strip().rstrip("/").lower()


class BfNcUserCredential(models.Model):
    _name = "bf.nc.user.credential"
    _description = "Connexion Nextcloud d'une personne"
    _order = "config_id, user_id"

    user_id = fields.Many2one(
        "res.users",
        string="Personne",
        required=True,
        ondelete="cascade",
        index=True,
        readonly=True,
    )
    config_id = fields.Many2one(
        "nextcloud.document.config",
        string="Configuration",
        required=True,
        ondelete="cascade",
        readonly=True,
    )
    state = fields.Selection(
        [("pending", "En attente d'approbation"), ("connected", "Connecte")],
        string="Etat",
        required=True,
        default="pending",
        readonly=True,
    )
    nc_login = fields.Char(string="Identifiant Nextcloud", readonly=True)
    nc_user_id = fields.Char(string="Compte Nextcloud", readonly=True)
    nc_display_name = fields.Char(string="Nom dans Nextcloud", readonly=True)
    nc_server = fields.Char(
        string="Serveur",
        readonly=True,
        help="Le serveur Nextcloud qui a delivre le mot de passe d'application. "
        "Il n'est jamais envoye ailleurs.",
    )
    connected_on = fields.Datetime(string="Connecte le", readonly=True)
    app_password_encrypted = fields.Char(
        groups="base.group_system", readonly=True, copy=False
    )
    flow_poll_token_encrypted = fields.Char(
        groups="base.group_system", readonly=True, copy=False
    )
    flow_poll_endpoint = fields.Char(
        groups="base.group_system", readonly=True, copy=False
    )
    flow_started_on = fields.Datetime(readonly=True, copy=False)

    _sql_constraints = [
        (
            "user_config_uniq",
            "unique(user_id, config_id)",
            "Une seule connexion Nextcloud par personne et par configuration.",
        ),
    ]

    @api.depends("user_id", "nc_display_name", "nc_login")
    def _compute_display_name(self):
        for cred in self:
            who = cred.nc_display_name or cred.nc_login or _("en attente")
            cred.display_name = "%s -> %s" % (cred.user_id.name or "", who)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    @api.model
    def _for(self, config, user=None, create=False):
        """The row of `user` (default: the caller) for `config`, in sudo."""
        user = user or self.env.user
        cred = self.sudo().search(
            [("user_id", "=", user.id), ("config_id", "=", config.id)], limit=1
        )
        if not cred and create:
            cred = self.sudo().create({"user_id": user.id, "config_id": config.id})
        return cred

    def _status(self):
        """What the browser shows. Never carries a secret."""
        if not self:
            return {"connected": False, "pending": False}
        self.ensure_one()
        return {
            "connected": self.state == "connected",
            "pending": bool(self.flow_started_on),
            "login": self.nc_login or "",
            "display_name": self.nc_display_name or self.nc_login or "",
        }

    # ------------------------------------------------------------------
    # URL checks: strings, never parsers
    # ------------------------------------------------------------------
    @api.model
    def _refuse_url(self, config, what, url):
        _logger.warning(
            "Login Flow v2 on config %s: refused %s %r", config.id, what, str(url)[:200]
        )
        return UserError(
            _("Nextcloud a repondu avec une adresse qui n'est pas celle du serveur configure.")
        )

    @api.model
    def _clean_url(self, config, what, url):
        if not isinstance(url, str) or not url or UNSAFE_URL_RE.search(url):
            raise self._refuse_url(config, what, url)
        return url

    @api.model
    def _check_login_url(self, config, url):
        url = self._clean_url(config, "login page", url)
        base = config.nextcloud_base_url.rstrip("/")
        for root in FLOW_ROOTS:
            prefix = base + root + "/flow/"
            if url[: len(prefix)].lower() == prefix.lower() and FLOW_TOKEN_RE.match(url[len(prefix):]):
                return url
        raise self._refuse_url(config, "login page", url)

    @api.model
    def _check_poll_endpoint(self, config, url):
        url = self._clean_url(config, "poll endpoint", url)
        base = config.nextcloud_base_url.rstrip("/")
        if url.lower() in {(base + root + "/poll").lower() for root in FLOW_ROOTS}:
            return url
        raise self._refuse_url(config, "poll endpoint", url)

    @api.model
    def _check_server(self, config, url):
        url = self._clean_url(config, "server", url)
        if url.rstrip("/").lower() != config.nextcloud_base_url.rstrip("/").lower():
            raise self._refuse_url(config, "server", url)
        return url

    # ------------------------------------------------------------------
    # Login Flow v2
    # ------------------------------------------------------------------
    @api.model
    def _flow_start(self, config):
        base = config.nextcloud_base_url.rstrip("/")
        # Nextcloud shows this string under Security > Devices: it is how the
        # person recognises, and can revoke, this connection later.
        agent = "Symbifox Odoo (%s)" % (self.env.company.name or self.env.cr.dbname)
        try:
            resp = requests.post(
                base + "/index.php/login/v2",
                headers={"User-Agent": agent, "Accept": "application/json"},
                timeout=HTTP_TIMEOUT,
                verify=config._tls_verify,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            raise UserError(_("Impossible de joindre Nextcloud : %s") % e) from e
        if resp.status_code != 200:
            raise UserError(
                _("Nextcloud a refuse d'ouvrir une connexion (HTTP %s).") % resp.status_code
            )
        try:
            data = resp.json()
            login_url = data["login"]
            token = data["poll"]["token"]
            endpoint = data["poll"]["endpoint"]
        except (ValueError, KeyError, TypeError) as e:
            raise UserError(_("Reponse de Nextcloud illisible.")) from e
        login_url = self._check_login_url(config, login_url)
        endpoint = self._check_poll_endpoint(config, endpoint)
        if not isinstance(token, str) or not token:
            raise UserError(_("Reponse de Nextcloud illisible."))

        cred = self._for(config, create=True)
        cred.write({
            "flow_poll_token_encrypted": config._encrypt_value(token),
            "flow_poll_endpoint": endpoint,
            "flow_started_on": fields.Datetime.now(),
        })
        return {"login_url": login_url, **cred._status()}

    def _flow_clear(self):
        self.write({
            "flow_poll_token_encrypted": False,
            "flow_poll_endpoint": False,
            "flow_started_on": False,
        })

    def _flow_fail(self, config, message):
        """End a flow that produced an app password Odoo will not keep.

        Returned rather than raised: raising would roll back the clearing of
        the flow, and the poll token Nextcloud has already consumed would keep
        the browser waiting for twenty minutes.
        """
        self._flow_clear()
        if self.state == "pending":
            self.unlink()
            return {"connected": False, "pending": False, "expired": False, "error": message}
        # A refused RE-connection leaves the previous one intact: say so, or
        # the browser shows "connect your Nextcloud" to someone still connected.
        return {**self._status(), "pending": False, "expired": False, "error": message}

    @api.model
    def _flow_poll(self, config):
        cred = self._for(config)
        if not cred or not cred.flow_started_on:
            return {**cred._status(), "expired": False}

        token = config._decrypt_value(cred.flow_poll_token_encrypted)
        # Re-checked at use: a row written by an older version, or by hand,
        # must not steer the poll elsewhere.
        endpoint = self._check_poll_endpoint(config, cred.flow_poll_endpoint)
        try:
            resp = requests.post(
                endpoint,
                data={"token": token},
                timeout=HTTP_TIMEOUT,
                verify=config._tls_verify,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            raise UserError(_("Impossible de joindre Nextcloud : %s") % e) from e
        if resp.status_code == 404:
            # Not approved yet: Nextcloud answers 404 until the person grants.
            # The deadline is checked only now, after a last poll: an approval
            # given in the final seconds is collected rather than orphaned.
            if fields.Datetime.now() - cred.flow_started_on > FLOW_TTL:
                cred._flow_clear()
                if cred.state == "pending":
                    cred.unlink()
                    return {"connected": False, "pending": False, "expired": True}
                return {**cred._status(), "expired": True}
            return {**cred._status(), "expired": False}
        if resp.status_code != 200:
            raise UserError(
                _("Nextcloud a refuse la connexion (HTTP %s).") % resp.status_code
            )
        try:
            data = resp.json()
            server = data["server"]
            login = data["loginName"]
            app_password = data["appPassword"]
        except (ValueError, KeyError, TypeError) as e:
            raise UserError(_("Reponse de Nextcloud illisible.")) from e
        if not (isinstance(login, str) and login and isinstance(app_password, str) and app_password):
            raise UserError(_("Reponse de Nextcloud illisible."))

        # From here on Nextcloud has issued a permanent token. Anything that
        # stops Odoo from keeping it must delete it, or it lives on unseen.
        try:
            self._check_server(config, server)
            nc_id, display, email = self._whoami(config, login, app_password)
            self._check_identity(config, cred.user_id, login, email)
        except UserError as e:
            self._revoke_remote(config, login, app_password)
            return cred._flow_fail(config, str(e))

        previous_login = cred.nc_login
        # Read before the write below rebinds the row to this server.
        previous = (
            config._decrypt_value(cred.app_password_encrypted)
            if cred.state == "connected" and cred._issued_by(config) else False
        )
        cred.write({
            "state": "connected",
            "nc_login": login,
            "nc_user_id": nc_id,
            "nc_display_name": display,
            "nc_server": _server_key(config.nextcloud_base_url),
            "connected_on": fields.Datetime.now(),
            "app_password_encrypted": config._encrypt_value(app_password),
            "flow_poll_token_encrypted": False,
            "flow_poll_endpoint": False,
            "flow_started_on": False,
        })
        if previous and previous != app_password:
            # The old token belongs to the old login, which may differ (a login
            # by email address, or another account altogether).
            self._revoke_remote(config, previous_login, previous)
        return {**cred._status(), "expired": False, "just_connected": True}

    @api.model
    def _whoami(self, config, login, app_password):
        """Account id (the WebDAV path is keyed by it, not by the login name),
        display name and email of the account an app password belongs to."""
        base = config.nextcloud_base_url.rstrip("/")
        try:
            resp = requests.get(
                base + "/ocs/v1.php/cloud/user",
                params={"format": "json"},
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(login, app_password),
                timeout=HTTP_TIMEOUT,
                verify=config._tls_verify,
                allow_redirects=False,
            )
            data = resp.json()["ocs"]["data"]
            nc_id = data["id"]
            if not isinstance(nc_id, str) or not nc_id:
                raise ValueError("no account id")
        except (requests.RequestException, UnicodeError, ValueError, KeyError, TypeError) as e:
            raise UserError(
                _("Nextcloud a delivre un acces, mais refuse de dire a quel compte il appartient.")
            ) from e
        display = data.get("display-name") or data.get("displayname") or nc_id
        email = data.get("email") if isinstance(data.get("email"), str) else ""
        return nc_id, display, email

    @api.model
    def _check_identity(self, config, user, nc_login, nc_email):
        """The approved account must be the person's own (see module docstring).

        Compared with the Odoo LOGIN only: it is the one identifier people
        cannot change on their own record (the email field is self-writable).
        Accepted when the Nextcloud login name or the Nextcloud email address
        equals it, ignoring case and surrounding spaces.
        """
        if not config.nc_require_email_match:
            return
        mine = (user.login or "").strip().lower()
        theirs = {(v or "").strip().lower() for v in (nc_login, nc_email)} - {""}
        if not mine or mine not in theirs:
            raise UserError(_(
                "Le compte Nextcloud approuve ne correspond pas a votre identifiant Odoo. "
                "Connectez-vous a Nextcloud avec votre propre compte, puis recommencez. "
                "Un administrateur peut desactiver cette verification sur la configuration."
            ))

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------
    def _issued_by(self, config):
        """Whether this connection's app password comes from the server the
        configuration names right now."""
        self.ensure_one()
        return bool(self.nc_server) and self.nc_server == _server_key(config.nextcloud_base_url)

    @api.model
    def _revoke_remote(self, config, login, app_password):
        """Delete an app password on the configured server (see _revoke_at).

        Only for a token that server issued: one just handed over by the flow,
        or a stored one whose _issued_by(config) holds.
        """
        return self._revoke_at(config.nextcloud_base_url, config._tls_verify, login, app_password)

    @api.model
    def _revoke_at(self, base, verify, login, app_password):
        """Delete an app password on Nextcloud, with the app password itself.

        Best effort: an unreachable server must not keep a person connected in
        Odoo. The token then survives in Nextcloud, where the person or an
        administrator can still revoke it (Security > Devices). The request
        carries the token, so ``base`` must be the server that issued it, never
        an address Nextcloud handed back.
        """
        if not (base and login and app_password):
            return False
        try:
            resp = requests.delete(
                base.strip().rstrip("/") + "/ocs/v2.php/core/apppassword",
                headers={"OCS-APIRequest": "true"},
                auth=(login, app_password),
                timeout=REVOKE_TIMEOUT,
                verify=verify,
                allow_redirects=False,
            )
        except (requests.RequestException, UnicodeError) as e:
            _logger.warning("Nextcloud app password revocation failed: %s", type(e).__name__)
            return False
        # 401: already revoked on the Nextcloud side, which is the goal.
        if resp.status_code not in (200, 401):
            _logger.warning("Nextcloud app password revocation answered HTTP %s", resp.status_code)
            return False
        return True

    def unlink(self):
        # Access first: the revocation below is a network call that no
        # rollback undoes. Checked after it, a person allowed to call unlink()
        # on someone else's row would end that person's Nextcloud access and
        # only then be refused.
        self.check_access("unlink")
        for cred in self.sudo():
            if cred.state != "connected" or not cred.config_id:
                continue
            config = cred.config_id
            if not cred._issued_by(config):
                # The configuration now names another server, possibly in the
                # very write that deletes this row: sending the token there
                # would hand it over. Its own server is revoked by that write
                # (nextcloud.document.config.write), or by hand in Nextcloud.
                _logger.warning(
                    "Nextcloud connection %s dropped without revocation: issued by another server",
                    cred.id,
                )
                continue
            self._revoke_remote(
                config, cred.nc_login, config._decrypt_value(cred.app_password_encrypted)
            )
        return super().unlink()

    def _drop_without_revoking(self):
        """Delete rows whose tokens were already revoked where they live.

        A private method rather than a context flag: an RPC client chooses its
        own context, and could otherwise delete a row while leaving its
        permanent token alive on Nextcloud.
        """
        return super().unlink()
