import base64
import io
import re

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .letter_template import LETTERHEAD_MODES

# Flags un-rendered merge syntax left over in a letter body.
_RESIDUAL_TOKEN_RE = re.compile(r"{{|t-out=|t-field=|t-esc=")

_DEFAULT_NAME = "Nouvelle lettre"


class LetterDocument(models.Model):
    _name = "letter.document"
    _description = "Lettre"
    # mail.render.mixin provides `lang` + `_render_template` (the mail-merge
    # engine shared with mail.template).
    _inherit = ["mail.thread", "mail.activity.mixin", "mail.render.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Titre", required=True, default=_DEFAULT_NAME, tracking=True,
    )
    active = fields.Boolean(default=True)
    reference = fields.Char(
        string="Référence", readonly=True, copy=False, default="New",
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("finalized", "Finalisée"),
            ("sent", "Envoyée"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Expéditeur",
        required=True,
        default=lambda self: self.env.company,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Destinataire", required=True, tracking=True,
    )
    recipient_name = fields.Char(string="Nom du destinataire")
    recipient_address = fields.Text(string="Adresse du destinataire")
    letter_date = fields.Date(
        string="Date", default=fields.Date.context_today, tracking=True,
    )
    salutation = fields.Char(string="Appel", help="Ex. : Madame, Monsieur,")
    body_html = fields.Html(string="Corps", sanitize=False, tracking=True)
    closing = fields.Char(
        string="Salutation finale",
        help="Ex. : Veuillez agréer, Madame, mes salutations distinguées.",
    )
    template_id = fields.Many2one("letter.template", string="Modèle")
    letterhead_style = fields.Selection(
        LETTERHEAD_MODES,
        string="En-tête",
        default="banner",
        required=True,
    )
    letterhead_image_preview = fields.Binary(
        related="company_id.letterhead_image",
        string="Aperçu de l'en-tête",
        readonly=True,
    )
    signatory_id = fields.Many2one("res.partner", string="Signataire")
    signatory_function = fields.Char(string="Fonction du signataire")
    pdf_attachment_id = fields.Many2one(
        "ir.attachment", string="Dernier PDF", readonly=True, copy=False,
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
                vals.setdefault("lang", partner.lang or self.env.lang)
                # Auto-title from the recipient when no title was given.
                if vals.get("name", _DEFAULT_NAME) == _DEFAULT_NAME:
                    vals["name"] = _("Lettre à %s") % partner.name
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
                    "Des champs de fusion semblent non remplis. Appliquez le "
                    "modèle ou corrigez le corps avant de finaliser."
                )
            else:
                letter.merge_warning = False

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        partner = self.partner_id
        if not partner:
            return
        self.recipient_name = partner.name
        self.recipient_address = self._format_partner_address(partner)
        self.lang = partner.lang or self.env.lang
        if not self.name or self.name == _DEFAULT_NAME:
            self.name = _("Lettre à %s") % partner.name
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
            raise UserError(_("Sélectionnez d'abord un modèle de lettre."))
        template = self.template_id
        self.body_html = self._render_body(template.body_html)
        self.letterhead_style = template.letterhead_style
        # Replace an auto-generated title (sentinel or "Lettre à X") with the
        # rendered template subject; keep a title the user typed themselves.
        auto_name = _("Lettre à %s") % self.partner_id.name if self.partner_id else _DEFAULT_NAME
        if template.subject and (not self.name or self.name in (_DEFAULT_NAME, auto_name)):
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
                    _("Le corps de la lettre %s est vide.") % letter.reference
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
                "name": "Apercu_%s.pdf" % (self.reference or "lettre"),
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
                "name": "%s.pdf" % (self.reference or "lettre"),
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
            "name": _("Envoyer la lettre"),
            "res_model": "letter.send.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_letter_id": self.id},
        }

    def action_open_quicktext_picker(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Insérer un bloc de texte"),
            "res_model": "letter.quicktext.picker",
            "view_mode": "form",
            "target": "new",
            "context": {"default_letter_id": self.id},
        }

    def action_review_with_claude(self):
        self.ensure_one()
        if not self.claude_available:
            raise UserError(
                _("La revue par Claude requiert le module bf_persona / bf_claude_chat.")
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
