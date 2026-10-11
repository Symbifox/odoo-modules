import base64
import io
import re

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .letter_template import LETTERHEAD_MODES

# Flags un-rendered merge syntax left over in a letter body.
_RESIDUAL_TOKEN_RE = re.compile(r"{{|t-out=|t-field=|t-esc=")

_DEFAULT_NAME = "New letter"
# Titles given before the labels went English: still recognised as automatic.
_LEGACY_DEFAULT_NAME = "Nouvelle lettre"
_LEGACY_AUTO_NAME = "Lettre à %s"


class LetterDocument(models.Model):
    _name = "letter.document"
    _description = "Letter"
    # mail.render.mixin provides `lang` + `_render_template` (the mail-merge
    # engine shared with mail.template).
    _inherit = ["mail.thread", "mail.activity.mixin", "mail.render.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Title", required=True, default=lambda self: self.env._("New letter"),
        tracking=True,
    )
    active = fields.Boolean(default=True)
    reference = fields.Char(
        string="Reference", readonly=True, copy=False, default="New",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("finalized", "Finalized"),
            ("sent", "Sent"),
        ],
        string="State",
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Sender",
        required=True,
        default=lambda self: self.env.company,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Recipient", required=True, tracking=True,
    )
    recipient_name = fields.Char(string="Recipient name")
    recipient_address = fields.Text(string="Recipient address")
    letter_date = fields.Date(
        string="Date", default=fields.Date.context_today, tracking=True,
    )
    salutation = fields.Char(string="Salutation", help="E.g.: Dear Sir or Madam,")
    # Corps saisi par tout usager interne et relu par les autres (écran, PDF,
    # courriel) : on le nettoie, en gardant les styles en ligne permis.
    body_html = fields.Html(
        string="Body", sanitize=True, sanitize_style=True, tracking=True,
    )
    closing = fields.Char(
        string="Complimentary close",
        help="E.g.: Yours faithfully,",
    )
    template_id = fields.Many2one("letter.template", string="Template")
    letterhead_style = fields.Selection(
        LETTERHEAD_MODES,
        string="Letterhead",
        default="banner",
        required=True,
    )
    letterhead_image_preview = fields.Binary(
        related="company_id.letterhead_image",
        string="Letterhead preview",
        readonly=True,
    )
    signatory_id = fields.Many2one("res.partner", string="Signatory")
    signatory_function = fields.Char(string="Signatory title")
    pdf_attachment_id = fields.Many2one(
        "ir.attachment", string="Latest PDF", readonly=True, copy=False,
    )
    claude_available = fields.Boolean(compute="_compute_integrations")
    persona_available = fields.Boolean(compute="_compute_integrations")
    merge_warning = fields.Char(compute="_compute_merge_warning")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("reference", "New") == "New":
                vals["reference"] = (
                    self.env["ir.sequence"].next_by_code("letter.document")
                    or "New"
                )
            company = self.env["res.company"].browse(
                vals.get("company_id") or self.env.company.id
            )
            # Default the letterhead mode from the template, else the company.
            if not vals.get("letterhead_style"):
                template = (
                    self.env["letter.template"].browse(vals["template_id"])
                    if vals.get("template_id")
                    else None
                )
                vals["letterhead_style"] = (
                    (template and template.letterhead_style)
                    or company.letter_default_mode
                    or "banner"
                )
            # Default the signatory from the company letterhead config.
            if not vals.get("signatory_id") and company.letter_signatory_id:
                vals["signatory_id"] = company.letter_signatory_id.id
                vals.setdefault(
                    "signatory_function",
                    company.letter_signatory_function
                    or company.letter_signatory_id.function,
                )
            # Snapshot the recipient block for letters created outside the
            # form (e.g. the bulk merge wizard, which bypasses onchanges).
            if vals.get("partner_id"):
                partner = self.env["res.partner"].browse(vals["partner_id"])
                vals.setdefault("recipient_name", partner.name)
                vals.setdefault(
                    "recipient_address", self._format_partner_address(partner)
                )
                vals.setdefault("lang", self._bf_reader_lang(partner))
                # Auto-title from the recipient when no title was given, in the
                # letter's language: the title is also the email subject.
                if (vals.get("name") or False) in self._bf_auto_names():
                    vals["name"] = self._bf_auto_name(partner, vals["lang"])
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    @api.depends("partner_id")
    def _compute_integrations(self):
        persona_installed = self._bf_module_installed("bf_persona")
        for letter in self:
            has_persona = False
            partner = letter.partner_id
            if persona_installed and partner and "has_persona" in partner._fields:
                has_persona = bool(partner.has_persona)
            letter.persona_available = has_persona
            letter.claude_available = persona_installed

    @api.depends("body_html")
    def _compute_merge_warning(self):
        for letter in self:
            body = str(letter.body_html or "")
            if body and _RESIDUAL_TOKEN_RE.search(body):
                letter.merge_warning = _(
                    "Some merge fields seem to be unfilled. Apply the "
                    "template or fix the body before finalizing."
                )
            else:
                letter.merge_warning = False

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    def _bf_reader_lang(self, partner=None, lang=None):
        """The language the recipient reads: the letter's, else the recipient's, else
        the writer's. One rule for the title and the email, so they never differ."""
        installed = {code for code, _name in self.env["res.lang"].get_installed()}
        for code in (lang, partner.lang if partner else None, self.env.lang, self.env.user.lang):
            if code in installed:
                return code
        return "en_US"

    def _bf_auto_name(self, partner, lang):
        """The automatic title, in the letter's language."""
        return self.with_context(lang=self._bf_reader_lang(partner, lang)).env._(
            "Letter to %s", partner.name)

    def _bf_auto_names(self, partner=None):
        """Titles that are automatic, not typed by the user: the default and
        "Letter to X", in every installed language and in the French given
        before the labels went English."""
        names = {False, "", _DEFAULT_NAME, _LEGACY_DEFAULT_NAME}
        codes = {code for code, _name in self.env["res.lang"].get_installed()} | {"en_US"}
        for code in codes:
            env = self.with_context(lang=code).env
            names.add(env._("New letter"))
            if partner:
                names.add(env._("Letter to %s", partner.name))
        if partner:
            names.add(_LEGACY_AUTO_NAME % partner.name)
        return names

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        partner = self.partner_id
        if not partner:
            return
        self.recipient_name = partner.name
        self.recipient_address = self._format_partner_address(partner)
        self.lang = self._bf_reader_lang(partner)
        if not self.name or self.name in self._bf_auto_names(self._origin.partner_id):
            self.name = self._bf_auto_name(partner, self.lang)
        self._apply_persona_defaults()

    @api.onchange("company_id")
    def _onchange_company_id(self):
        company = self.company_id
        if company and company.letter_signatory_id and not self.signatory_id:
            self.signatory_id = company.letter_signatory_id
            self.signatory_function = (
                company.letter_signatory_function
                or company.letter_signatory_id.function
            )

    @api.onchange("signatory_id")
    def _onchange_signatory_id(self):
        if self.signatory_id and not self.signatory_function:
            self.signatory_function = (
                self.company_id.letter_signatory_function
                or self.signatory_id.function
            )

    @api.onchange("template_id")
    def _onchange_template_id(self):
        if self.template_id:
            self.letterhead_style = self.template_id.letterhead_style

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _bf_module_installed(self, name):
        return bool(
            self.env["ir.module.module"]
            .sudo()
            .search_count([("name", "=", name), ("state", "=", "installed")])
        )

    @staticmethod
    def _format_partner_address(partner):
        lines = [partner.name]
        if partner.parent_id and partner.parent_id != partner:
            lines.append(partner.parent_id.name)
        street = " ".join(filter(None, [partner.street, partner.street2]))
        if street:
            lines.append(street)
        city_line = " ".join(
            filter(
                None,
                [
                    partner.zip,
                    partner.city,
                    partner.state_id.name if partner.state_id else None,
                ],
            )
        )
        if city_line:
            lines.append(city_line)
        if partner.country_id:
            lines.append(partner.country_id.name)
        return "\n".join(line for line in lines if line)

    def _apply_persona_defaults(self):
        """Prefill salutation / closing from bf_persona, when installed."""
        if not self._bf_module_installed("bf_persona"):
            return
        partner = self.partner_id
        if not partner or "persona_id" not in partner._fields:
            return
        persona = partner.persona_id
        if not persona:
            return
        if not self.salutation and getattr(persona, "preferred_salutation", False):
            self.salutation = persona.preferred_salutation
        if not self.closing and getattr(persona, "closing_formula", False):
            self.closing = persona.closing_formula

    def _render_body(self, body):
        """Two-pass render of ``body`` against this letter.

        Pass 1 expands ``{{ }}`` inline-template tokens (the Word-style merge
        fields). Pass 2 runs the QWeb engine only if ``<t t-out>`` / ``t-field``
        survive, for power-user templates. Result is wrapped in ``Markup`` so
        it is never double-escaped downstream.
        """
        self.ensure_one()
        if not body:
            return Markup("")
        lang = self.lang or (self.partner_id and self.partner_id.lang) or self.env.lang
        renderer = self.with_context(lang=lang)
        rendered = renderer._render_template(
            str(body), "letter.document", [self.id], engine="inline_template"
        )[self.id]
        if rendered and ("t-out" in rendered or "t-field" in rendered):
            rendered = renderer._render_template(
                rendered, "letter.document", [self.id], engine="qweb"
            )[self.id]
        return Markup(rendered or "")

    def _get_pdf_binary(self):
        """Render the branded letter PDF and return its raw bytes.

        In ``pdf_overlay`` mode the chrome-less body is stamped onto every page
        of the company letterhead PDF.
        """
        self.ensure_one()
        pdf_content, _dummy = self.env["ir.actions.report"]._render_qweb_pdf(
            "bf_letter_writer.action_report_letter_document", self.ids
        )
        if (
            self.letterhead_style == "pdf_overlay"
            and self.company_id.letterhead_pdf
            and pdf_content[:5] == b"%PDF-"
        ):
            pdf_content = self._stamp_on_letterhead(pdf_content)
        return pdf_content

    def _stamp_on_letterhead(self, body_pdf_bytes):
        """Overlay the rendered body onto each page of the letterhead PDF."""
        self.ensure_one()
        try:
            from PyPDF2 import PdfReader, PdfWriter
        except ImportError:  # pragma: no cover - dependency declared in manifest
            return body_pdf_bytes
        letterhead_bytes = base64.b64decode(self.company_id.letterhead_pdf)
        body_reader = PdfReader(io.BytesIO(body_pdf_bytes))
        writer = PdfWriter()
        for body_page in body_reader.pages:
            # Re-read the letterhead per page so merges never accumulate.
            background = PdfReader(io.BytesIO(letterhead_bytes)).pages[0]
            background.merge_page(body_page)
            writer.add_page(background)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_apply_template(self):
        self.ensure_one()
        if not self.template_id:
            raise UserError(_("Select a letter template first."))
        template = self.template_id
        self.body_html = self._render_body(template.body_html)
        self.letterhead_style = template.letterhead_style
        # Replace an auto-generated title (sentinel or "Lettre à X") with the
        # rendered template subject; keep a title the user typed themselves.
        if template.subject and (not self.name or self.name in self._bf_auto_names(self.partner_id)):
            rendered_subject = self._render_template(
                template.subject,
                "letter.document",
                [self.id],
                engine="inline_template",
            )[self.id]
            if rendered_subject:
                self.name = rendered_subject
        return True

    def action_finalize(self):
        for letter in self:
            if not str(letter.body_html or "").strip():
                raise UserError(
                    _("The body of letter %s is empty.") % letter.reference
                )
            vals = {"state": "finalized"}
            if not letter.recipient_name:
                vals["recipient_name"] = letter.partner_id.name
            if not letter.recipient_address:
                vals["recipient_address"] = self._format_partner_address(
                    letter.partner_id
                )
            letter.write(vals)
        return True

    def action_mark_sent(self):
        self.write({"state": "sent"})
        return True

    def action_reset_draft(self):
        self.write({"state": "draft"})
        return True

    def action_preview_pdf(self):
        self.ensure_one()
        pdf_data = self._get_pdf_binary()
        attachment = self.env["ir.attachment"].create(
            {
                "name": _("Preview_%s.pdf", self.reference or _("letter")),
                "type": "binary",
                "datas": base64.b64encode(pdf_data),
                "mimetype": "application/pdf",
                "res_model": self._name,
                "res_id": self.id,
            }
        )
        self.pdf_attachment_id = attachment.id
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%d?download=false" % attachment.id,
            "target": "new",
        }

    def action_download_pdf(self):
        self.ensure_one()
        pdf_data = self._get_pdf_binary()
        attachment = self.env["ir.attachment"].create(
            {
                "name": "%s.pdf" % (self.reference or "letter"),
                "type": "binary",
                "datas": base64.b64encode(pdf_data),
                "mimetype": "application/pdf",
                "res_model": self._name,
                "res_id": self.id,
            }
        )
        self.pdf_attachment_id = attachment.id
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%d?download=true" % attachment.id,
            "target": "self",
        }

    def action_open_send_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Send the letter"),
            "res_model": "letter.send.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_letter_id": self.id},
        }

    def action_open_quicktext_picker(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Insert a text block"),
            "res_model": "letter.quicktext.picker",
            "view_mode": "form",
            "target": "new",
            "context": {"default_letter_id": self.id},
        }

    def action_review_with_claude(self):
        self.ensure_one()
        if not self.claude_available:
            raise UserError(
                _("Review with Claude requires the bf_persona / "
                  "bf_claude_chat module.")
            )
        prompt = (
            "Révise cette lettre avant envoi : %s, adressée à %s. Vérifie le "
            "ton, la clarté et la cohérence avec les règles de marque."
            % (self.reference, self.partner_id.display_name)
        )
        return {
            "type": "ir.actions.client",
            "tag": "claude_chat_launch",
            "params": {"prompt": prompt, "autosend": False},
        }
