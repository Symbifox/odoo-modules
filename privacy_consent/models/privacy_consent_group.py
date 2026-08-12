from odoo import fields, models


class PrivacyConsentGroup(models.Model):
    _name = "privacy.consent.group"
    _description = "Groupe de consentement"
    _order = "sequence, name"

    name = fields.Char(
        string="Nom",
        required=True,
        translate=True,
    )
    notice_ids = fields.Many2many(
        comodel_name="privacy.notice",
        relation="privacy_consent_group_notice_rel",
        column1="group_id",
        column2="notice_id",
        string="Modèles de consentement",
        domain="[('requires_consent', '=', True)]",
    )
    notice_count = fields.Integer(
        compute="_compute_notice_count",
        string="Nombre de modèles",
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )

    def _compute_notice_count(self):
        for record in self:
            record.notice_count = len(record.notice_ids)
