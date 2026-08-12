"""One-per-message row in the IMAP browser tree.

Transient: rows are repopulated every time the user changes folder or
turns the page. The body is NOT stored here — it is fetched on demand
when the user clicks « Aperçu ».
"""

import logging

from odoo import _, exceptions, fields, models

from ..models import bf_email_imap

_logger = logging.getLogger(__name__)


class BfEmailBrowserLine(models.TransientModel):
    _name = "bf.email.browser.line"
    _description = "Ligne du navigateur IMAP"
    _order = "date desc, uid desc"

    browser_id = fields.Many2one(
        comodel_name="bf.email.browser",
        required=True,
        ondelete="cascade",
    )
    uid = fields.Char(string="UID", required=True)
    date = fields.Datetime(string="Date")
    email_from = fields.Char(string="Expéditeur")
    subject = fields.Char(string="Sujet")
    message_id_header = fields.Char(string="Message-ID")
    already_in_bf_email = fields.Boolean(
        string="Déjà ingéré",
        help="Vrai si une ligne bf.email avec le même Message-ID existe.",
    )

    # ------------------------------------------------------------------
    # Per-row actions
    # ------------------------------------------------------------------
    def action_preview(self):
        self.ensure_one()
        browser = self.browser_id
        raw = browser._fetch_one_rfc822(self.uid)
        if not raw:
            raise exceptions.UserError(_(
                "Impossible de récupérer le UID %s dans %s.",
                self.uid, browser.folder,
            ))
        msg = bf_email_imap.parse_rfc822(raw)
        body_html, body_plain = bf_email_imap.extract_body(msg)
        body = body_html or (f"<pre>{body_plain}</pre>" if body_plain else _("(corps vide)"))
        message_id = str(msg.get("Message-ID", "")).strip() or False
        bf_email = False
        if message_id:
            bf_email = self.env["bf.email"].sudo().with_context(active_test=False).search([
                ("message_id_header", "=", message_id),
                ("user_id", "=", self.env.uid),
            ], limit=1)
        browser.write({
            "preview_uid": self.uid,
            "preview_subject": str(msg.get("Subject", ""))[:255],
            "preview_from": str(msg.get("From", ""))[:255],
            "preview_to": str(msg.get("To", ""))[:255],
            "preview_date": bf_email_imap.parse_date(msg.get("Date")),
            "preview_body_html": body,
            "preview_already_ingested": bool(bf_email),
            "preview_bf_email_id": bf_email.id if bf_email else False,
        })
        return browser._reopen_form()

    def action_ingest(self):
        self.ensure_one()
        browser = self.browser_id
        if self.already_in_bf_email:
            return browser._reopen_form()
        raw = browser._fetch_one_rfc822(self.uid)
        if not raw:
            raise exceptions.UserError(_(
                "Impossible de récupérer le UID %s.", self.uid,
            ))
        account = browser.account_id
        if not account or account.user_id.id != self.env.uid:
            raise exceptions.UserError(_(
                "Compte IMAP introuvable ou n'appartient pas à l'utilisateur courant.",
            ))
        # Ingest under the account's owner so user_id is set correctly.
        self.env["bf.email"].with_user(account.user_id)._ingest_rfc822(
            raw, int(self.uid), browser.folder, account,
        )
        new_bf = False
        if self.message_id_header:
            new_bf = self.env["bf.email"].sudo().with_context(active_test=False).search([
                ("message_id_header", "=", self.message_id_header),
                ("user_id", "=", account.user_id.id),
            ], limit=1)
        self.write({"already_in_bf_email": True})
        if browser.preview_uid == self.uid:
            browser.write({
                "preview_already_ingested": True,
                "preview_bf_email_id": new_bf.id if new_bf else False,
            })
        return browser._reopen_form()

    def action_ingest_and_reroute(self):
        self.ensure_one()
        self.action_ingest()
        if not self.message_id_header:
            raise exceptions.UserError(_(
                "Ce message n'a pas de Message-ID — impossible de retrouver "
                "la ligne bf.email après ingestion.",
            ))
        bf = self.env["bf.email"].sudo().with_context(active_test=False).search([
            ("message_id_header", "=", self.message_id_header),
            ("user_id", "=", self.env.uid),
        ], limit=1)
        if not bf:
            raise exceptions.UserError(_(
                "Ingestion réussie mais ligne bf.email introuvable.",
            ))
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.email.reroute",
            "view_mode": "form",
            "target": "new",
            "context": {"default_bf_email_ids": [(6, 0, [bf.id])]},
        }
