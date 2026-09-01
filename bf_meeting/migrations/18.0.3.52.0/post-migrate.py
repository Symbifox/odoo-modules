"""Recalcul des pastilles OdJ / compte rendu après changement de la règle.

Depuis cette version, la dispense (`bf_skip_agenda` / `bf_skip_dashboard`)
l'emporte sur l'existence d'un document : une rencontre dispensée qui portait
déjà un ordre du jour affichait « rédigé » dans la grille tout en étant absente
du tableau de bord, qui la filtre sur le même champ. Les deux surfaces disaient
deux choses du même état.

⚠️ Le `-u` seul ne suffit PAS. `bf_agenda_state` et `bf_minutes_state` sont des
champs calculés STOCKÉS : changer le corps du calcul ne réévalue rien, Odoo ne
recalcule que ce qu'une dépendance a invalidé. Sans ce script, les rencontres
concernées garderaient leur ancienne valeur en base — indéfiniment, et sans le
moindre signe.

On force donc le recalcul, en le bornant aux seules rencontres que la nouvelle
règle peut faire changer d'avis : celles qui portent une dispense. Sur un
agenda réel il y en a quelques dizaines contre quinze mille, et repasser sur
tout le calendrier coûterait des minutes pour rien.
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Event = env["calendar.event"]
    concernees = Event.with_context(active_test=False).search([
        "|", ("bf_skip_agenda", "=", True), ("bf_skip_dashboard", "=", True),
    ])
    if not concernees:
        return
    for nom in ("bf_agenda_state", "bf_minutes_state"):
        env.add_to_compute(Event._fields[nom], concernees)
    env.flush_all()
