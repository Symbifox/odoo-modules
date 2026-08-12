"""Mailbox connector used by the clawback (PhishRIP-style) remediation.

Once an employee reports a *real* malicious email and a manager confirms the
threat, the connector lets us reach every internal mailbox, find the offending
message (by exact ``Message-ID`` when known, else a From/Subject heuristic) and
move it to a hidden quarantine folder — reversibly.

Two backends, so the "internal" scope is achievable on whatever mail stack the
company runs:

* ``m365_oauth`` — app-only OAuth2 (client-credentials grant) against Exchange
  Online, authenticating to IMAP with XOAUTH2. One Entra app credential reaches
  every mailbox the service principal was granted ``FullAccess`` on.
* ``imap_password`` — a per-mailbox app password (any IMAP provider that issues
  app passwords), stored encrypted on child :class:`BfMailClawbackMailbox` rows.
  No admin API needed.

SECURITY: the OAuth client secret and per-mailbox passwords are Fernet-encrypted
with a key read from the environment / odoo.conf (never the DB). The connector
never stores message bodies. See ``SECURITY.md``.
"""

import imaplib
import logging
import os

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - mirrors bf_webmail's guard
    Fernet = None
    InvalidToken = Exception
    _logger.warning(
        "bf_security_awareness: cryptography not installed — clawback "
        "credential encryption is unavailable.")

TOKEN_ENDPOINT = "https://login.microsoftonline.com/%s/oauth2/v2.0/token"
OAUTH2_SCOPE = "https://outlook.office365.com/.default"


def _fernet_key():
    """Fernet key from env var or odoo.conf, never the DB. Falls back to the
    bf_webmail key so an admin can share a single secret."""
    if not Fernet:
        return None
    from odoo.tools import config
    for src in (
        os.environ.get("BF_SECAWARE_FERNET_KEY"),
        config.get("bf_secaware_fernet_key"),
        os.environ.get("BF_WEBMAIL_FERNET_KEY"),
        config.get("bf_webmail_fernet_key"),
    ):
        if src:
            return src.encode() if isinstance(src, str) else src
    _logger.error(
        "bf_security_awareness: no Fernet key — set BF_SECAWARE_FERNET_KEY "
        "(env) or bf_secaware_fernet_key (odoo.conf).")
    return None


def _encrypt(value):
    if not value:
        return ""
    if not Fernet:
        raise UserError(_(
            "Le paquet « cryptography » est requis pour stocker le secret de "
            "manière sécurisée (pip install cryptography)."))
    key = _fernet_key()
    if not key:
        raise UserError(_(
            "Aucune clé de chiffrement configurée (BF_SECAWARE_FERNET_KEY ou "
            "bf_secaware_fernet_key dans odoo.conf)."))
    return Fernet(key).encrypt(value.encode()).decode()


def _decrypt(encrypted):
    if not encrypted:
        return ""
    key = _fernet_key()
    if not key:
        return ""
    try:
        return Fernet(key).decrypt(encrypted.encode()).decode()
    except InvalidToken:
        _logger.warning("bf_security_awareness: clawback secret decrypt failed.")
        return ""
    except Exception:  # noqa: BLE001
        _logger.exception("bf_security_awareness: clawback secret decrypt error.")
        return ""


class BfMailClawbackConnector(models.Model):
    """A mailbox backend the clawback uses to reach internal inboxes."""

    _name = "bf.mail.clawback.connector"
    _description = "Connecteur courriel (clawback)"
    _order = "name"

    name = fields.Char(required=True, default="Connecteur courriel interne")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", string="Société",
        default=lambda self: self.env.company, index=True)

    backend = fields.Selection(
        [
            ("m365_oauth", "Microsoft 365 (OAuth2 app-only / XOAUTH2)"),
            ("imap_password", "IMAP (mot de passe applicatif par boîte)"),
        ],
        string="Type", required=True, default="m365_oauth")

    imap_host = fields.Char(
        string="Hôte IMAP", required=True, default="outlook.office365.com")
    imap_port = fields.Integer(string="Port IMAP", required=True, default=993)
    search_folder = fields.Char(
        string="Dossier à balayer", default="INBOX",
        help="Dossier IMAP fouillé dans chaque boîte (par défaut INBOX).")
    quarantine_folder = fields.Char(
        string="Dossier de quarantaine", required=True, default="Quarantaine BF",
        help="Dossier (créé au besoin) où les courriels retirés sont déplacés. "
             "Le retrait est réversible : la restauration les ramène à INBOX.")

    # --- m365_oauth credentials -----------------------------------------
    tenant_id = fields.Char(string="Tenant ID (Entra)")
    client_id = fields.Char(string="Client ID (app Entra)")
    client_secret = fields.Char(
        string="Secret client",
        compute="_compute_client_secret", inverse="_inverse_client_secret",
        help="Stocké chiffré (Fernet). Saisie en écriture seule.")
    client_secret_encrypted = fields.Char(
        string="Secret client (chiffré)", copy=False)

    # --- audience -------------------------------------------------------
    mailbox_source = fields.Selection(
        [
            ("internal_users", "Tous les usagers internes (res.users)"),
            ("manual", "Liste manuelle d'adresses"),
            ("credentials", "Boîtes déclarées (avec mot de passe)"),
        ],
        string="Boîtes ciblées", required=True, default="internal_users")
    manual_emails = fields.Text(
        string="Adresses (une par ligne)",
        help="Utilisé quand « Liste manuelle » est choisi.")
    mailbox_ids = fields.One2many(
        "bf.mail.clawback.mailbox", "connector_id", string="Boîtes déclarées")

    # ------------------------------------------------------------------ #
    # Encrypted secret field
    # ------------------------------------------------------------------ #
    def _compute_client_secret(self):
        # Never reveal the stored secret back into the UI.
        for rec in self:
            rec.client_secret = "********" if rec.client_secret_encrypted else ""

    def _inverse_client_secret(self):
        for rec in self:
            val = rec.client_secret or ""
            if val and val != "********":
                rec.client_secret_encrypted = _encrypt(val)

    # ------------------------------------------------------------------ #
    # OAuth2
    # ------------------------------------------------------------------ #
    def _mint_token(self):
        """App-only client-credentials token for Exchange Online IMAP.

        Tokens live ~60 min and have no refresh token — callers re-mint per
        batch (standard Microsoft identity platform client-credentials grant)."""
        self.ensure_one()
        if self.backend != "m365_oauth":
            return None
        secret = _decrypt(self.client_secret_encrypted)
        if not (self.tenant_id and self.client_id and secret):
            raise UserError(_(
                "Connecteur M365 incomplet : tenant, client ID et secret requis."))
        resp = requests.post(
            TOKEN_ENDPOINT % self.tenant_id,
            data={
                "client_id": self.client_id,
                "client_secret": secret,
                "scope": OAUTH2_SCOPE,
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise UserError(_(
                "Échec de l'obtention du jeton OAuth2 (%(code)s) : %(body)s") % {
                    "code": resp.status_code, "body": resp.text[:500]})
        token = resp.json().get("access_token")
        if not token:
            raise UserError(_("Réponse OAuth2 sans access_token."))
        return token

    def action_test_connection(self):
        """Mint a token (M365) and log into the first target mailbox."""
        self.ensure_one()
        mailboxes = self._target_mailboxes()
        if not mailboxes:
            raise UserError(_("Aucune boîte ciblée à tester."))
        token = self._mint_token() if self.backend == "m365_oauth" else None
        email = mailboxes[0]
        try:
            conn = self._login(email, token=token)
            conn.select(self.search_folder, readonly=True)
            conn.logout()
        except Exception as exc:  # noqa: BLE001
            raise UserError(_(
                "Connexion à %(box)s échouée : %(err)s") % {
                    "box": email, "err": exc})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Connexion réussie"),
                "message": _("Authentifié sur %(box)s (%(n)s boîtes ciblées).") % {
                    "box": email, "n": len(mailboxes)},
                "type": "success",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------ #
    # Audience resolution
    # ------------------------------------------------------------------ #
    def _target_mailboxes(self):
        """Return the list of email addresses to sweep for this connector."""
        self.ensure_one()
        if self.mailbox_source == "manual":
            return [e.strip() for e in (self.manual_emails or "").splitlines()
                    if e.strip()]
        if self.mailbox_source == "credentials":
            return [m.email for m in self.mailbox_ids if m.email]
        # internal_users: real, active, non-portal users with an email.
        users = self.env["res.users"].sudo().search([
            ("share", "=", False), ("active", "=", True),
        ])
        emails = []
        for user in users:
            email = user.email or user.partner_id.email or user.login
            if email and "@" in email:
                emails.append(email.strip())
        return sorted(set(emails))

    # ------------------------------------------------------------------ #
    # IMAP session
    # ------------------------------------------------------------------ #
    def _login(self, email, token=None):
        """Open and authenticate an IMAP session for one mailbox."""
        self.ensure_one()
        conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        if self.backend == "m365_oauth":
            if not token:
                token = self._mint_token()
            auth = "user=%s\x01auth=Bearer %s\x01\x01" % (email, token)
            conn.authenticate("XOAUTH2", lambda _x: auth.encode())
        else:
            mailbox = self.mailbox_ids.filtered(lambda m: m.email == email)[:1]
            if not mailbox:
                raise UserError(_(
                    "Aucun mot de passe déclaré pour la boîte %s.") % email)
            conn.login(email, mailbox._get_password())
        return conn

    # ------------------------------------------------------------------ #
    # IMAP operations — search / move / restore
    # ------------------------------------------------------------------ #
    @staticmethod
    def _imap_quote(value):
        """Build an IMAP quoted-string, refusing control characters.

        SECURITY: ``imaplib`` sends each command as ``data + CRLF`` with NO
        validation, so a CR/LF (or NUL) embedded in an attacker-influenced
        SEARCH argument (Subject / From / Message-ID from a reported .eml or a
        typed report) would terminate the line and inject a new IMAP command
        across every swept mailbox. Reject control chars here — the single
        chokepoint for every value that reaches a SEARCH/CREATE."""
        value = value or ""
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
            raise UserError(_(
                "Critère de recherche invalide : caractères de contrôle "
                "interdits (risque d'injection IMAP)."))
        return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')

    def _imap_search(self, conn, folder, message_id=None, email_from=None,
                     subject=None, since=None):
        """Return a list of UID strings matching the criteria in ``folder``.

        NB: not named ``_search`` — that is the ORM's private search method and
        overriding it would break ``connector.search(...)``."""
        typ, _data = conn.select(folder, readonly=True)
        if typ != "OK":
            return []
        if message_id:
            criteria = ["HEADER", "Message-ID", self._imap_quote(message_id)]
        else:
            criteria = []
            if email_from:
                criteria += ["FROM", self._imap_quote(email_from)]
            if subject:
                criteria += ["SUBJECT", self._imap_quote(subject)]
            if since:
                # since is a date; IMAP wants e.g. 01-Jan-2026
                criteria += ["SINCE", since.strftime("%d-%b-%Y")]
            if not criteria:
                return []
        typ, data = conn.uid("SEARCH", None, *criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    def _ensure_folder(self, conn, name):
        # CREATE is a no-op error if it already exists; ignore that.
        try:
            conn.create(self._imap_quote(name))
        except Exception:  # noqa: BLE001
            pass

    def _imap_move(self, conn, folder, uids, dest):
        """Move UIDs from the currently relevant folder to ``dest``.

        Tries UID MOVE (RFC 6851, supported by Exchange & Dovecot); falls back
        to COPY + \\Deleted + EXPUNGE. Returns the count actually moved."""
        if not uids:
            return 0
        typ, _data = conn.select(folder)
        if typ != "OK":
            return 0
        self._ensure_folder(conn, dest)
        uidset = b",".join(uids).decode()
        dest_q = self._imap_quote(dest)
        typ, _data = conn.uid("MOVE", uidset, dest_q)
        if typ == "OK":
            return len(uids)
        # Fallback: copy then flag-delete then expunge.
        typ, _data = conn.uid("COPY", uidset, dest_q)
        if typ != "OK":
            return 0
        conn.uid("STORE", uidset, "+FLAGS", "(\\Deleted)")
        conn.expunge()
        return len(uids)


class BfMailClawbackMailbox(models.Model):
    """A declared mailbox + its app password (for the ``imap_password`` backend)."""

    _name = "bf.mail.clawback.mailbox"
    _description = "Boîte courriel déclarée (clawback)"
    _order = "email"

    connector_id = fields.Many2one(
        "bf.mail.clawback.connector", required=True, ondelete="cascade")
    email = fields.Char(required=True)
    password = fields.Char(
        string="Mot de passe applicatif",
        compute="_compute_password", inverse="_inverse_password",
        help="Stocké chiffré (Fernet). Saisie en écriture seule.")
    password_encrypted = fields.Char(string="Mot de passe (chiffré)", copy=False)

    _sql_constraints = [
        ("email_uniq_per_connector", "unique(connector_id, email)",
         "Cette boîte est déjà déclarée pour ce connecteur."),
    ]

    def _compute_password(self):
        for rec in self:
            rec.password = "********" if rec.password_encrypted else ""

    def _inverse_password(self):
        for rec in self:
            val = rec.password or ""
            if val and val != "********":
                rec.password_encrypted = _encrypt(val)

    def _get_password(self):
        self.ensure_one()
        return _decrypt(self.password_encrypted)
