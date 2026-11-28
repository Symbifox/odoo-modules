"""Libellés stockés des paliers recalculés dans la langue de la maison.

`hour.bank.threshold.line.name` est stocké et ne se recalcule que si le mode ou la
valeur change : sans ce passage, un palier créé avant la bascule garde le texte de
l'ancienne source (« Dette de 5.0h ») chez un locataire anglophone. Le calcul prend la
langue de la maison quand le contexte n'en a pas (`get_lang`), comme le faisait `_()`.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Ligne = env["hour.bank.threshold.line"]
    lignes = Ligne.with_context(active_test=False).search([])
    if lignes:
        env.add_to_compute(Ligne._fields["name"], lignes)
        env.flush_all()
