from odoo import SUPERUSER_ID, api

from odoo.addons.hosting_management.models.hosting_maintenance_window import recharger_traduction_avis


def migrate(cr, version):
    """L'avis de maintenance gagne le lien de la page d'état : sa traduction française est
    rechargée, sinon l'avis partirait en français sans ce paragraphe."""
    recharger_traduction_avis(api.Environment(cr, SUPERUSER_ID, {}))
