"""Mesures livrées en noupdate : une mise à jour ne leur donne pas leur mode de comparaison.

On le pose ici, seulement si la mesure a encore la valeur par défaut (un réglage fait à la main
n'est pas écrasé)."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in ['bf_bi_hosting.measure_backup_success_rate']:
        measure = env.ref(xmlid, raise_if_not_found=False)
        if measure and measure.comparison == "percentage":
            measure.comparison = "difference"
