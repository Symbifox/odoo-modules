from odoo import SUPERUSER_ID, api

from odoo.addons.hosting_management.models.hosting_maintenance_window import recharger_traduction_avis


def migrate(cr, version):
    """Le paragraphe du lien d'état de l'avis porte une classe (`o_hosting_status_link`) que
    bf_hosting_status lit à l'envoi. La traduction française du gabarit est stockée d'un
    bloc : rechargée, sinon l'avis en français partirait sans la marque."""
    recharger_traduction_avis(api.Environment(cr, SUPERUSER_ID, {}))
