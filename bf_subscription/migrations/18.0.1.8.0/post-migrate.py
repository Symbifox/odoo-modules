"""18.0.1.8.0 : remettre à jour les dates de renouvellement restées au passé.

Avant cette version, la date stockée ne bougeait qu'à l'écriture d'une dépendance ;
une fois l'échéance franchie, elle restait au passé. La tâche des relances la
recalcule désormais chaque jour ; on rattrape ici les fiches existantes sans attendre.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    subs = env['subscription.subscription'].with_context(active_test=False).search([])
    subs._refresh_next_billing_date()
