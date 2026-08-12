from odoo import api, fields, models


class PrivacyFrameworkMixin(models.AbstractModel):
    """Résolution du cadre réglementaire applicable à un enregistrement.

    Ajoute `framework_id` (avec un défaut sensé) et `get_framework()`,
    qui retombe successivement sur : le cadre de l'enregistrement → le cadre
    par défaut de la société → le cadre Loi 25 du noyau. Les modèles concrets
    fournissent `company_id`.
    """

    _name = "privacy.framework.mixin"
    _description = "Résolution du cadre de confidentialité"

    framework_id = fields.Many2one(
        comodel_name="privacy.framework",
        string="Cadre juridique",
        index=True,
        default=lambda self: self._default_framework(),
        help="Cadre réglementaire de confidentialité applicable "
        "(Loi 25, GDPR, etc.). Hérité de la société si vide.",
    )

    @api.model
    def _default_framework(self):
        company = self.env.company
        if company.default_privacy_framework_id:
            return company.default_privacy_framework_id.id
        fallback = self.env.ref(
            "privacy_consent.framework_loi25", raise_if_not_found=False
        )
        return fallback.id if fallback else False

    def get_framework(self):
        """Return the applicable framework record (never an empty recordset
        unless the Loi 25 fallback itself is missing).

        Public (no leading underscore) so it is callable from QWeb mail
        templates and reports — sandboxed mail QWeb blocks `_`-prefixed methods.
        """
        self.ensure_one()
        framework = self.framework_id
        if not framework:
            company = getattr(self, "company_id", False)
            framework = company.default_privacy_framework_id if company else False
        if not framework:
            framework = self.env.ref(
                "privacy_consent.framework_loi25", raise_if_not_found=False
            )
        return framework
