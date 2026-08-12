"""Read-only IMAP folder browser.

Lets the user peek into any IMAP folder (Archives, Trash, Junk, Drafts,
…) without ingesting messages permanently. Each row has an explicit
« Ingérer » button so the user can fish out interesting messages on
demand and route them via the Reroute wizard.

All IMAP primitives come from ``bf_email_imap``; this wizard only
orchestrates connection lifecycle, pagination, and UI state. Nothing is
persisted to PostgreSQL beyond the TransientModel scratch rows that
Odoo garbage-collects.
"""

import logging

from odoo import _, api, exceptions, fields, models

from ..models import bf_email_imap

_logger = logging.getLogger(__name__)


def _resolve_account(env, account_id=None):
    """Return the bf.email.account for the current user (or raise)."""
    Account = env["bf.email.account"]
    if account_id:
        acct = Account.search([
            ("id", "=", int(account_id)),
            ("user_id", "=", env.uid),
        ], limit=1)
        if acct:
            return acct
    acct = Account.search([
        ("user_id", "=", env.uid),
        ("active", "=", True),
    ], limit=1, order="id")
    if not acct:
        raise exceptions.UserError(_(
            "Aucun compte IMAP configuré pour vous. "
            "Paramètres → Inbox unifiée → Mes comptes IMAP."
        ))
    return acct


def _open_account_conn(account):
    if not (account.host and account.login and account.password):
        raise exceptions.UserError(_(
            "Le compte « %s » n'a pas d'identifiants valides.",
            account.name,
        ))
    return bf_email_imap.open_connection(
        account.host, account.port, account.login, account.password,
    )


class BfEmailBrowser(models.TransientModel):
    _name = "bf.email.browser"
    _description = "Navigateur IMAP"
    _rec_name = "folder"

    account_id = fields.Many2one(
        comodel_name="bf.email.account",
        string="Compte IMAP",
        required=True,
        domain="[('user_id', '=', uid), ('active', '=', True)]",
        default=lambda self: self.env["bf.email.account"].search(
            [("user_id", "=", self.env.uid), ("active", "=", True)],
            limit=1, order="id",
        ),
    )
    folder = fields.Char(
        string="Dossier",
        required=True,
        default="INBOX",
        help="Nom exact du dossier IMAP (ex.: Archives/2026, Trash, Junk).",
    )
    available_folders = fields.Text(
        string="Dossiers disponibles",
        readonly=True,
        help="Liste des dossiers détectés via IMAP LIST, un par ligne.",
    )
    folder_message_count = fields.Integer(
        string="Total messages",
        readonly=True,
    )
    page_offset = fields.Integer(string="Décalage", default=0)
    page_size = fields.Integer(string="Taille de page", default=100)
    line_ids = fields.One2many(
        comodel_name="bf.email.browser.line",
        inverse_name="browser_id",
        string="Messages",
    )

    # Preview pane state
    preview_uid = fields.Char(string="UID prévisualisé", readonly=True)
    preview_subject = fields.Char(string="Sujet", readonly=True)
    preview_from = fields.Char(string="Expéditeur", readonly=True)
    preview_to = fields.Char(string="Destinataire", readonly=True)
    preview_date = fields.Datetime(string="Date", readonly=True)
    preview_body_html = fields.Html(
        string="Corps",
        readonly=True,
        sanitize=True,
        help="Aperçu assaini du corps du courriel (le HTML brut entrant "
             "n'est jamais rendu tel quel — défense anti-XSS).",
    )
    preview_already_ingested = fields.Boolean(
        string="Déjà ingéré dans bf.email",
        readonly=True,
    )
    preview_bf_email_id = fields.Many2one(
        "bf.email",
        string="Ligne bf.email correspondante",
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-populate folder list and INBOX page on first open."""
        records = super().create(vals_list)
        for rec in records:
            try:
                rec.action_refresh_folder_list()
                rec.action_load_page()
            except Exception:
                _logger.warning(
                    "bf.email.browser auto-bootstrap failed", exc_info=True,
                )
        return records

    # ------------------------------------------------------------------
    # Folder list
    # ------------------------------------------------------------------
    def action_refresh_folder_list(self):
        self.ensure_one()
        account = _resolve_account(self.env, self.account_id.id)
        try:
            conn = _open_account_conn(account)
        except bf_email_imap.ImapConnectionError as exc:
            raise exceptions.UserError(_("Connexion IMAP impossible : %s", exc)) from exc
        try:
            status, raw = conn.list()
            folders = []
            if status == "OK" and raw:
                for line in raw:
                    if not line:
                        continue
                    decoded = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
                    # LIST response shape: ``(\Flags) "delimiter" name`` where
                    # name may be unquoted (Brouillons) or quoted ("My Folder").
                    # Split on the rightmost whitespace and strip optional quotes.
                    tokens = decoded.rsplit(None, 1)
                    name = tokens[-1].strip().strip('"') if tokens else decoded
                    if name:
                        folders.append(name)
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        self.available_folders = "\n".join(folders)
        return None

    # ------------------------------------------------------------------
    # Load a page of messages from ``folder``
    # ------------------------------------------------------------------
    def action_load_page(self):
        self.ensure_one()
        if not self.folder:
            raise exceptions.UserError(_("Choisissez un dossier."))
        account = _resolve_account(self.env, self.account_id.id)
        try:
            conn = _open_account_conn(account)
        except bf_email_imap.ImapConnectionError as exc:
            raise exceptions.UserError(_("Connexion IMAP impossible : %s", exc)) from exc

        try:
            if not bf_email_imap.select_folder(conn, self.folder, readonly=True):
                raise exceptions.UserError(_(
                    "Dossier introuvable : %s. Cliquez « Rafraîchir » pour "
                    "voir la liste des dossiers disponibles.", self.folder,
                ))
            all_uids = bf_email_imap.search_uids_in_range(conn)
            total = len(all_uids)
            # Newest first: reverse the asc-sorted UID list.
            all_uids.reverse()
            offset = max(0, self.page_offset or 0)
            size = max(1, min(self.page_size or 100, 500))
            page_uids = all_uids[offset:offset + size]
            headers = bf_email_imap.fetch_headers_bulk(conn, page_uids)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        # Dedup against bf.email by Message-ID (single query, not per-row).
        msg_ids = [
            (str(headers[u][0].get("Message-ID", "")).strip() or None)
            for u in page_uids if u in headers
        ]
        msg_ids = [m for m in msg_ids if m]
        existing = set()
        if msg_ids:
            existing = set(
                self.env["bf.email"].with_context(active_test=False).sudo().search([
                    ("message_id_header", "in", msg_ids),
                    ("user_id", "=", self.env.uid),
                ]).mapped("message_id_header")
            )

        # Wipe previous lines + repopulate.
        self.line_ids.unlink()
        line_vals = []
        for uid in page_uids:
            entry = headers.get(uid)
            if not entry:
                # Header fetch failed for this UID — render a stub row so the
                # user knows it exists and can retry.
                line_vals.append({
                    "browser_id": self.id,
                    "uid": str(uid),
                    "subject": "(impossible de lire l'en-tête)",
                })
                continue
            msg, _seen = entry
            message_id = str(msg.get("Message-ID", "")).strip() or False
            line_vals.append({
                "browser_id": self.id,
                "uid": str(uid),
                "date": bf_email_imap.parse_date(msg.get("Date")),
                "email_from": str(msg.get("From", ""))[:255],
                "subject": str(msg.get("Subject", ""))[:255],
                "message_id_header": message_id or False,
                "already_in_bf_email": bool(message_id and message_id in existing),
            })
        if line_vals:
            self.env["bf.email.browser.line"].create(line_vals)
        self.folder_message_count = total
        return None

    def action_next_page(self):
        self.ensure_one()
        self.page_offset = (self.page_offset or 0) + (self.page_size or 100)
        return self.action_load_page()

    def action_prev_page(self):
        self.ensure_one()
        new = (self.page_offset or 0) - (self.page_size or 100)
        self.page_offset = max(0, new)
        return self.action_load_page()

    # ------------------------------------------------------------------
    # Preview / ingest are line-level — see bf.email.browser.line
    # ------------------------------------------------------------------
    def _fetch_one_rfc822(self, uid):
        """Open a fresh conn, fetch a single UID's body, return raw bytes."""
        account = _resolve_account(self.env, self.account_id.id)
        conn = _open_account_conn(account)
        try:
            if not bf_email_imap.select_folder(conn, self.folder, readonly=True):
                raise exceptions.UserError(_(
                    "Dossier %s indisponible.", self.folder,
                ))
            return bf_email_imap.fetch_rfc822(conn, int(uid))
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _reopen_form(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
            "views": [[False, "form"]],
        }
