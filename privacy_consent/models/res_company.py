from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    default_privacy_framework_id = fields.Many2one(
        comodel_name="privacy.framework",
        string="Cadre de confidentialité par défaut",
        domain="['|', ('company_id', '=', False), ('company_id', '=', id)]",
        help="Cadre réglementaire appliqué par défaut aux nouveaux "
        "enregistrements de vie privée de cette société.",
    )

    # Le responsable de la société elle-même : un seul stockage, sur son partenaire.
    privacy_officer_partner_id = fields.Many2one(
        related="partner_id.privacy_officer_partner_id", readonly=False,
        groups="privacy_consent.group_privacy_user")
    privacy_officer_email = fields.Char(
        related="partner_id.privacy_officer_email", readonly=False,
        groups="privacy_consent.group_privacy_user")
    privacy_officer_public_url = fields.Char(
        related="partner_id.privacy_officer_public_url", readonly=False,
        groups="privacy_consent.group_privacy_user")
