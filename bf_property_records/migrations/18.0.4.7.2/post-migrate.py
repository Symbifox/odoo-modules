"""Les crons du module tournent à 05 h 30 UTC.

Un cron n'a pas de fuseau : sa date du jour est celle de l'UTC, et il tournait
à l'heure de sa pose. À 05 h 30 UTC, la date est la même au Québec, été comme
hiver. Le XML porte cette heure pour une installation neuve ; il est en
`noupdate`, d'où ce recalage des crons déjà en base.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.bf_property_core.tools import anchor_crons_at_dawn

CRONS = [
    "bf_property_records.cron_log_refresh_schedule",
    "bf_property_records.cron_study_refresh_schedule",
    "bf_property_records.cron_attestation_refresh_state",
    "bf_property_records.cron_syndicat_refresh_loi16",
]


def migrate(cr, version):
    anchor_crons_at_dawn(api.Environment(cr, SUPERUSER_ID, {}), CRONS)
