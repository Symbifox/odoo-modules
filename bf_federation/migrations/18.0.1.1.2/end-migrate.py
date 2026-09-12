"""Le dernier geste : retirer la traduction française de l'architecture des vues.

Il se fait à l'étape « end », une fois tout le chargement terminé. À l'étape
« post », Odoo réécrit encore les vues et reconstitue les termes français à
partir de la valeur précédente : la suppression y serait défaite en silence.

Sans entrée `fr_CA`, Odoo sert la source, qui est déjà en français. Voir
`post-migrate.py` pour le reste et pour le pourquoi.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    if not env["res.lang"].search_count([("code", "=", "fr_CA"), ("active", "=", True)]):
        return
    vues = env["ir.model.data"].search([("module", "=", "bf_federation"), ("model", "=", "ir.ui.view")]).mapped("res_id")
    if not vues:
        return
    cr.execute("UPDATE ir_ui_view SET arch_db = arch_db - 'fr_CA' WHERE id IN %s AND arch_db ? 'fr_CA'", (tuple(vues),))
    _logger.info("bf_federation : %s vue(s) rendues à leur source française", cr.rowcount)
    env["ir.ui.view"].invalidate_model(["arch_db"])
