"""Le guide « Crédit et identité », lisible dans l'appli.

Le texte vit dans un gabarit QWeb (views/credit_guide_templates.xml), écrit en
anglais ; le français vient de i18n/fr_CA.po, terme par terme. Le rendu se fait
dans la langue de la personne qui lit (``env.lang``).

⚠️ Odoo ne traduit pas un ``href`` : les liens gardent l'adresse de la source,
c'est-à-dire la page française lue le 2026-10-02. La version anglaise le dit.
"""
from odoo import api, models


class CreditGuide(models.AbstractModel):
    _name = "bf.credit.guide"
    _description = "Credit and identity guide"

    @api.model
    def get_guide_html(self):
        return self.env["ir.qweb"]._render("bf_credit_identity.credit_guide_content", {})
