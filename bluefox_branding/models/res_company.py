from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # report_brand_primary / report_brand_dark / report_brand_logo now live in
    # bf_onboarding_base (a dependency of this module) so the bf_* suite renders
    # without the white-label panel. This module styles/surfaces them but no
    # longer owns the field definitions.

    brand_email_tagline = fields.Char(
        string="Tagline de marque (courriels)",
        help=(
            "Courte phrase d'accroche affichée sous le nom de la société dans le pied "
            "des courriels brandés. Laissez vide pour utiliser l'en-tête du rapport."
        ),
    )
    brand_email_footer_html = fields.Html(
        string="Pied de page personnalisé (courriels)",
        sanitize=False,
        help=(
            "HTML qui remplace la ligne automatique courriel · téléphone · site web "
            "dans le pied des courriels brandés. Laissez vide pour utiliser les "
            "coordonnées de la société."
        ),
    )
    brand_email_signature_default = fields.Html(
        string="Signature par défaut (courriels)",
        sanitize=False,
        help=(
            "Signature HTML utilisée dans le bloc signature des courriels brandés "
            "quand l'utilisateur n'a pas de signature personnelle. Laissez vide pour "
            "le comportement Odoo standard."
        ),
    )
    brand_privacy_url = fields.Char(
        string="Lien politique de confidentialité (courriels)",
        help=(
            "URL affichée dans le pied des courriels brandés. Laissez vide pour "
            "masquer le lien."
        ),
    )
    brand_terms_url = fields.Char(
        string="Lien conditions (courriels)",
        help=(
            "URL affichée dans le pied des courriels brandés. Laissez vide pour "
            "masquer le lien."
        ),
    )
    favicon = fields.Binary(
        string="Favicon",
        attachment=True,
        help=(
            "Favicon affiché dans les onglets du navigateur pour les rapports web "
            "de la société. Si vide, le logo est utilisé."
        ),
    )
