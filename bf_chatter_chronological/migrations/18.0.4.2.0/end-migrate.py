# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Source anglaise : les deux actions posées par le crochet changent de langue.

Les actions déclarées en XML reçoivent leur nom anglais du fichier et leur
français du catalogue. Celles que ``post_init_hook`` a créées (rencontres et
ordres du jour, sans xmlid) ne passent par aucun des deux : leur en_US porte
encore le français livré. On les bascule seulement si le nom est resté celui
livré ; un nom retouché à la main n'est pas touché.
"""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.bf_chatter_chronological.hooks import (
    LIBELLE, LIBELLE_FR_LIVRE, liaisons_posees)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    actions = liaisons_posees(env)
    if not actions:
        return
    # Lu dans la colonne, langue par langue.
    cr.execute("SELECT id FROM ir_act_server WHERE id IN %s AND name->>'en_US' = %s",
               (tuple(actions.ids), LIBELLE_FR_LIVRE))
    livrees = env["ir.actions.server"].browse([r[0] for r in cr.fetchall()])
    if not livrees:
        return
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    for action in livrees:
        cr.execute("SELECT name FROM ir_act_server WHERE id = %s", (action.id,))
        noms = cr.fetchone()[0] or {}
        # Une langue retouchée garde sa retouche : on n'écrit que là où le nom manque
        # ou porte encore le français livré.
        valeurs = {"en_US": LIBELLE._translate("en_US")}
        valeurs.update({lang: LIBELLE._translate(lang) for lang in langues
                        if noms.get(lang) in (None, LIBELLE_FR_LIVRE)})
        action.update_field_translations("name", valeurs)
    _logger.info("bf_chatter_chronological: %s liaison(s) basculée(s) en source anglaise",
                 len(livrees))
