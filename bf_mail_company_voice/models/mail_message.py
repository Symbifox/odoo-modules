# -*- coding: utf-8 -*-
"""Automatic notices speak for the company, never as « Public user ».

1. Author. A visitor who signs a document, answers a date poll or books a slot
   does so as the public user, and every SYSTEM message that request writes (the
   « document signed » note, the e-mail sent to the parties, the creation and
   tracking notes, the activity notice) was authored by « Public user ». Those
   notices now carry the contact of the company. The person who acted stays named in the text.

   Only system messages are concerned: a comment (free text a visitor typed
   through a portal link) keeps its author, so nothing a stranger writes is
   ever shown under the company's name.

2. Sender name. A message sent from a company's address with no name
   (« info@… » alone) or under an automatism's name (the root contact,
   « OdooBot », « Public user ») leaves as « Company <address> ». Any other name was chosen
   by someone (a newsletter sent by the cron under a person's name, a team
   signature « Company, Support ») and is kept. Discuss channels are left
   alone. An incoming e-mail is never touched.

The company is the record's (multi-company), unless the system parameter
``bf_mail_company_voice.company_voice`` is ``session`` (the session's company
speaks for every record) or a company id (that one company always speaks).

Only ``mail.message`` is extended (``mail.mail`` goes through it by delegation):
extending ``mail.thread`` would re-initialise every model with a chatter on
each update of this module.
"""
import logging

from odoo import api, models, tools

_logger = logging.getLogger(__name__)

# System messages: what the code writes, not what a person typed or sent.
SYSTEM_TYPES = ("notification", "email_outgoing", "user_notification", "auto_comment")


def _partner_id(value):
    """``author_id`` arrives as an id, a record or a falsy value."""
    if isinstance(value, models.BaseModel):
        return value.id if len(value) == 1 else False
    return value if isinstance(value, int) and not isinstance(value, bool) else False


class MailMessage(models.Model):
    _inherit = "mail.message"

    @api.model
    def _bf_public_partner_ids(self):
        """Contacts of the public users: the base one, one per website, the
        current one."""
        ids = set(self.env["res.users"].sudo().with_context(active_test=False).search(
            [("groups_id", "in", self.env.ref("base.group_public").id)]).partner_id.ids)
        public_partner = self.env.ref("base.public_partner", raise_if_not_found=False)
        if public_partner:
            ids.add(public_partner.id)
        if self.env.user._is_public():
            ids.add(self.env.user.partner_id.id)
        return ids

    @api.model
    def _bf_record_company(self, model, res_id):
        """Company of the record the message is posted on, or an empty record."""
        if model and res_id and model in self.env:
            field = self.env[model]._fields.get("company_id")
            if field and field.type == "many2one" and field.comodel_name == "res.company":
                return self.env[model].sudo().browse(res_id).exists().company_id
        return self.env["res.company"]

    @api.model
    def _bf_voice_fixed_company(self):
        """Company that speaks for EVERY record, or an empty record.

        System parameter ``bf_mail_company_voice.company_voice``:
        ``record`` (default) lets the record's company speak; ``session`` the
        session's company; a company id names the one company that always
        speaks (a database whose public user or scheduled actions belong to a
        second company that must not appear in notices)."""
        voice = (self.env["ir.config_parameter"].sudo().get_param(
            "bf_mail_company_voice.company_voice") or "record").strip()
        Company = self.env["res.company"].sudo()
        if voice == "session":
            return self.env.company.sudo()
        if voice.isdigit():
            return Company.browse(int(voice)).exists()
        return Company

    @api.model
    def _bf_voice_company(self, model=False, res_id=False):
        """Company that speaks for a message on ``model``/``res_id``."""
        return (self._bf_voice_fixed_company()
                or self._bf_record_company(model, res_id)
                or self.env.company.sudo())

    @api.model
    def _bf_company_sender(self, email_from, companies, record_company, automaton_names):
        """« Company <address> » for ``email_from``, or False to leave it alone.

        Only a bare address or the name of an automatism (the root contact, « OdooBot »,
        « Public user ») is replaced. Any other name was chosen by someone: the
        sender of a newsletter (a person's name, sent by the cron) or a team
        signature (« Company, Support »), and it is kept."""
        if not email_from or not isinstance(email_from, str):
            return False
        pairs = tools.mail.email_split_tuples(email_from)
        if len(pairs) != 1 or not pairs[0][1]:
            return False
        name, address = pairs[0][0].strip(), tools.email_normalize(pairs[0][1])
        if not address:
            return False
        matches = companies.filtered(lambda c: tools.email_normalize(c.email) == address)
        if not matches:
            return False
        if len(matches) > 1:
            # Several companies share the address: take the record's, then the
            # session's; when neither tells, rename nothing rather than guess.
            matches = (matches & record_company) or (matches & self.env.company)
            if len(matches) != 1:
                return False
        company = matches
        if name and name.casefold() not in automaton_names:
            return False
        formatted = tools.formataddr((company.name or "", address))
        return formatted if formatted != email_from else False

    @api.model_create_multi
    def create(self, vals_list):
        memo = {}  # lookups shared by the messages of this call
        for vals in vals_list:
            try:
                self._bf_company_voice(vals, memo)
            except Exception:  # noqa: BLE001
                # A naming nicety must never block a message: a signature is
                # sealed in the same transaction as its « signed » note.
                _logger.exception("bf_mail_company_voice: company voice skipped")
        return super().create(vals_list)

    @api.model
    def _bf_company_voice(self, vals, memo):
        """Rewrite ``vals`` in place: company as author, company name as sender.
        ``memo`` keeps the lookups (public contacts, companies, automatism names)
        for the other messages of the same ``create`` call."""
        message_type = vals.get("message_type") or "comment"
        if message_type == "email" or vals.get("model") == "discuss.channel":
            # An incoming e-mail keeps the header it arrived with; channel
            # conversations keep their author's voice.
            return
        author_id = _partner_id(vals.get("author_id"))
        if "author_id" not in vals and self.env.user._is_public():
            author_id = self.env.user.partner_id.id
        if "fixed" not in memo:
            memo["fixed"] = self._bf_voice_fixed_company()
        record_company = memo["fixed"] or self._bf_record_company(
            vals.get("model"), vals.get("res_id"))

        # The author is replaced on system messages only: a comment is text
        # someone typed, and a stranger's text never goes out as the company's.
        if author_id and message_type in SYSTEM_TYPES:
            if "public" not in memo:
                memo["public"] = self._bf_public_partner_ids()
            if author_id in memo["public"]:
                company = record_company or self.env.company
                vals["author_id"] = company.partner_id.id
                if not vals.get("email_from") and company.partner_id.email:
                    vals["email_from"] = company.partner_id.email_formatted

        if vals.get("email_from"):
            if "companies" not in memo:
                memo["companies"] = self.env["res.company"].sudo().search_fetch(
                    [("email", "!=", False)], ["email", "partner_id", "name"])
            if not memo["companies"]:
                return
            if "names" not in memo:
                memo["names"] = self._bf_automaton_names()
            sender = self._bf_company_sender(
                vals["email_from"], memo["companies"], record_company, memo["names"])
            if sender:
                vals["email_from"] = sender

    @api.model
    def _bf_automaton_names(self):
        """Names an automatism signs with: the root contact (« OdooBot »,
        or the name it was given) and the public users' contacts."""
        partners = self.env["res.partner"].sudo().browse(list(self._bf_public_partner_ids()))
        root = self.env.ref("base.partner_root", raise_if_not_found=False)
        if root:
            partners |= root.sudo()
        names = {(name or "").strip().casefold() for name in partners.with_context(active_test=False).mapped("name")}
        names |= {"odoobot", "public user"}
        names.discard("")
        return names
