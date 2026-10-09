"""Qualifier un événement de sécurité au regard des renseignements personnels des clients.

C'est cette qualification, et non le transport, qui déclenche l'obligation d'aviser
(art. 18.3 P-39.1). Le « non » se motive autant que le « oui » : un registre qui ne
garde que les oui ne prouve rien sur les autres.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext


class HostingSecurityEvent(models.Model):
    _inherit = "hosting.security.event"

    privacy_assessment = fields.Selection([
        ("pending", "À évaluer"),
        ("no", "Ne touche aucun renseignement d'un client"),
        ("yes", "Touche des renseignements d'un client"),
    ], string="Renseignements des clients", default="pending", required=True, tracking=True)
    privacy_rationale = fields.Text(string="Motif de la qualification", tracking=True)
    privacy_notice_type = fields.Selection([
        ("breach", "Violation"),
        ("attempt", "Tentative ciblée"),
    ], string="Type d'avis", default="breach")
    privacy_partner_ids = fields.Many2many(
        "res.partner", "hosting_security_event_privacy_partner_rel", "event_id", "partner_id",
        string="Organisations touchées", domain="[('is_company', '=', True)]")
    privacy_notice_ids = fields.One2many("privacy.breach.notice", "security_event_id",
                                         string="Avis de violation", groups="privacy_consent.group_privacy_user")
    privacy_notice_count = fields.Integer(compute="_compute_privacy_notice_count", string="Avis",
                                          groups="privacy_consent.group_privacy_user")

    @api.depends("privacy_notice_ids")
    def _compute_privacy_notice_count(self):
        for event in self:
            event.privacy_notice_count = len(event.privacy_notice_ids)

    @api.constrains("privacy_assessment", "privacy_rationale")
    def _check_privacy_rationale(self):
        for event in self:
            if event.privacy_assessment != "pending" and not (event.privacy_rationale or "").strip():
                raise ValidationError(_(
                    "Une qualification se motive, dans un sens comme dans l'autre : "
                    "dites pourquoi cet événement touche ou ne touche pas de renseignements d'un client."))

    @api.onchange("privacy_assessment")
    def _onchange_privacy_assessment(self):
        if self.privacy_assessment == "yes" and not self.privacy_partner_ids:
            self.privacy_partner_ids = self._privacy_default_partners()

    def _privacy_default_partners(self):
        self.ensure_one()
        own = self.env.company.partner_id
        return self.service_ids.mapped("partner_id.commercial_partner_id").filtered(
            lambda p: p != own and p.is_company)

    def action_prepare_privacy_notices(self):
        """Un brouillon d'avis par organisation touchée qui n'en a pas encore pour cet événement."""
        self.ensure_one()
        if self.privacy_assessment != "yes":
            raise UserError(_("Qualifiez d'abord l'événement : touche-t-il des renseignements d'un client ?"))
        partners = self.privacy_partner_ids.mapped("commercial_partner_id")
        if not partners:
            raise UserError(_("Indiquez les organisations touchées."))
        Notice = self.env["privacy.breach.notice"]
        done = self.privacy_notice_ids.mapped("responsible_id")
        circumstances = "\n\n".join(filter(None, [
            self.name, html2plaintext(self.description or "").strip()]))
        measures = html2plaintext(self.resolution or "").strip()
        for partner in partners - done:
            Notice.create({
                "responsible_id": partner.id,
                "security_event_id": self.id,
                "notice_type": self.privacy_notice_type or "breach",
                "circumstances": circumstances,
                "discovered_at": self.event_date,
                "measures_taken": measures or False,
            })
        return self.action_view_privacy_notices()

    def action_view_privacy_notices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Avis de violation"),
            "res_model": "privacy.breach.notice", "view_mode": "list,form",
            "domain": [("security_event_id", "=", self.id)],
            "context": {"default_security_event_id": self.id},
        }


class PrivacyBreachNotice(models.Model):
    _inherit = "privacy.breach.notice"

    security_event_id = fields.Many2one("hosting.security.event", string="Événement de sécurité",
                                        index=True, ondelete="restrict", copy=True)

    def _breach_content_fields(self):
        return super()._breach_content_fields() | {"security_event_id"}
