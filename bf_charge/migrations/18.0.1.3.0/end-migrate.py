"""18.0.1.3.0 : le résumé et les signaux du plan sont rédigés dans chaque langue.

Avant, ils l'étaient dans la seule langue de qui calculait : en français pour un
cron ou une montée, donc en français pour un anglophone. La montée convertit les
colonnes en champs traduisibles, l'ancien texte passant dans la fente `en_US`,
celle que lit toute langue qui n'a pas la sienne.

On re-rédige donc les plans en vigueur, et eux seuls, SANS les recalculer : leurs
chiffres et leur date de calcul ne bougent pas, le résumé se relit des champs
stockés. Seuls les signaux, mesurés à date par nature, sont refaits. Les plans plus
anciens restent l'archive de ce qu'ils ont dit, dans la langue d'alors.

⚠️ En `end`, comme 18.0.1.2.0 : les signaux lisent les banques d'heures, dont le
modèle n'est pas encore chargé quand bf_charge passe son `post`.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Le nom du relevé n'est plus stocké : sa colonne garderait un texte figé
    # qu'Odoo reprendrait sans le recalculer si le champ redevenait stocké.
    cr.execute("ALTER TABLE bf_charge_snapshot DROP COLUMN IF EXISTS name")
    env = api.Environment(cr, SUPERUSER_ID, {})
    Plan = env["bf.charge.plan"]
    plans = Plan.browse(Plan._plans_en_vigueur()).exists()
    refaits = 0
    for plan in plans:
        try:
            with cr.savepoint():
                plan._rediger()
                refaits += 1
        except Exception:  # un plan qui ne se rédige pas ne bloque pas la montée
            _logger.warning("bf_charge : plan %s non rédigé", plan.id, exc_info=True)
    if refaits < len(plans):
        _logger.error("bf_charge : %s plan(s) en vigueur sur %s non rédigés dans chaque langue",
                      len(plans) - refaits, len(plans))
    else:
        _logger.info("bf_charge : %s plan(s) en vigueur rédigés dans chaque langue", refaits)
