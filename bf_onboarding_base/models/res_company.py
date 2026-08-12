from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Core brand colours/logo consumed by branded emails, reports and public
    # pages across the bf_* suite (bf_appointment, bf_meeting, bf_hour_bank,
    # bf_sign, …). Defined here — in the shared onboarding base every bf_*
    # module already depends on — so those modules render correctly WITHOUT the
    # optional white-label module `bluefox_branding`. The white-label panel
    # (bluefox_branding) surfaces and styles these fields, but no longer owns
    # them.
    report_brand_primary = fields.Char(
        string="Couleur primaire (marque)",
        default="#714B67",
        help="Couleur d'accent pour la navbar, les boutons et les courriels brandés "
             "(ex: bannière, bulles, barres). Les rapports PDF utilisent plutôt "
             "« Couleur primaire (PDF) ».",
    )
    report_brand_dark = fields.Char(
        string="Couleur foncée (marque)",
        default="#212529",
        help="Couleur de fond foncée pour les en-têtes de courriels brandés et la navbar. "
             "Les rapports PDF utilisent plutôt « Couleur secondaire (PDF) ».",
    )
    report_brand_logo = fields.Binary(
        string="Logo sur fond foncé (marque)",
        attachment=True,
        help="Logo — idéalement blanc/clair — utilisé sur les fonds FONCÉS : en-têtes "
             "de courriels brandés et pages publiques. Le logo standard de la société "
             "(souvent en couleur) reste utilisé sur les documents à fond clair. "
             "Si vide, le logo standard est utilisé partout.",
    )
