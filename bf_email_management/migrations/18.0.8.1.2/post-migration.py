"""Recalcule les aperçus déjà stockés, entités comprises.

⚠️ Corriger `_compute_body_preview` ne suffit PAS : `body_preview` est un champ
calculé STOCKÉ, et Odoo ne le recalcule pas parce que le corps de la méthode a
changé — seulement quand une dépendance bouge. Sans cette migration, le
correctif ne vaudrait que pour les courriels reçus après la mise à niveau, et
les 3 798 aperçus déjà en base garderaient leur « &nbsp; » pour toujours.

Mesuré avant correction : 3 798 aperçus avec `&nbsp;`, 499 avec `&#xA0;`,
285 avec `&amp;`, 214 avec `&#39;`, 20 avec `&quot;`.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Email = env["bf.email"]
    # Uniquement ce qui porte une entité : inutile de rebrasser toute la base,
    # et ça rend la migration relisible dans les journaux.
    courriels = Email.search(["|", "|", "|", "|",
                              ("body_preview", "like", "&#x"),
                              ("body_preview", "like", "&nbsp;"),
                              ("body_preview", "like", "&amp;"),
                              ("body_preview", "like", "&#39;"),
                              ("body_preview", "like", "&quot;")])
    if not courriels:
        _logger.info("Aperçus : rien à recalculer.")
        return
    _logger.info("Aperçus : recalcul de %d courriels…", len(courriels))
    # Par tranches : la base en compte des dizaines de milliers, et invalider
    # tout d'un coup ferait enfler le cache jusqu'à l'étouffement.
    for debut in range(0, len(courriels), 500):
        lot = courriels[debut:debut + 500]
        lot.invalidate_recordset(["body_preview"])
        lot.modified(["body_html"])
        lot._compute_body_preview()
        lot.flush_recordset(["body_preview"])
    _logger.info("Aperçus : %d recalculés.", len(courriels))
