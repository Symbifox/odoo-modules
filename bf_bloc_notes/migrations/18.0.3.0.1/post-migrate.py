"""Recalcule `bf.note.link.res_name` sous les droits de l'auteur.

Jusqu'à 18.0.3.0.0, le nom stocké d'une fiche liée était calculé en
superutilisateur (champ stocké, `compute_sudo`) : une note liée à une
fiche que son auteur ne peut pas lire en portait le nom. Le calcul corrigé ne
s'applique qu'aux liens écrits APRÈS la montée ; les noms déjà stockés sont donc
recalculés ici, une fois, puis `bf.note.res_name` (stocké lui aussi).
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Link = env["bf.note.link"].with_context(active_test=False)
    liens = Link.search([])
    avant = {l.id: l.res_name for l in liens}
    env.add_to_compute(Link._fields["res_name"], liens)
    liens.flush_recordset(["res_name"])
    # `bf.note.res_name` (stocké, tiré des noms de liens) n'est recalculé par
    # Odoo que si un nom de lien a CHANGÉ ; il a pu être stocké en
    # superutilisateur lui aussi : on le recalcule explicitement.
    Note = env["bf.note"].with_context(active_test=False)
    notes = Note.search([])
    env.add_to_compute(Note._fields["res_name"], notes)
    notes.flush_recordset(["res_name"])
    env.invalidate_all()
    changes = sum(1 for l in Link.browse(list(avant)) if l.res_name != avant[l.id])
    _logger.info("bf_bloc_notes 18.0.3.0.1 : %s nom(s) de lien recalculé(s), %s changé(s).",
                 len(liens), changes)
