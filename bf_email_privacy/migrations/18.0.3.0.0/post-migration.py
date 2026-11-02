"""18.0.2.0.0 → 18.0.3.0.0 : l'aperçu mémorisé décrit un monde disparu.

La détection de clôture change de critère (silence réel au lieu d'étape repliée
ou archivage). L'aperçu stocké par la version précédente compte des dossiers
« clos sans date de clôture lisible », motif qui n'existe plus. Le laisser
afficherait un état que plus aucun balayage ne produit ; on l'efface, et
l'écran dira « jamais calculé » jusqu'au prochain passage.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    stale = env["ir.config_parameter"].sudo().search(
        [("key", "=", "bf_email_privacy.last_scan")]
    )
    if stale:
        stale.unlink()
        _logger.info(
            "bf_email_privacy: aperçu mémorisé effacé (critère de clôture changé)"
        )
