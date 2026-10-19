"""18.0.1.2.0 : le plan ne lit plus que les tâches de sa personne.

Les plans déjà calculés gardent les chiffres de TOUTE la firme (vu à la démo :
500 à 800 % la première semaine), et le menu « Semaines » montre désormais le
dernier plan de chacun. On recalcule donc ces plans en vigueur, et eux seuls :
les plus anciens restent l'archive de ce qu'ils ont dit.

⚠️ En `end` : le plan lit les banques d'heures, dont le modèle n'est pas encore
chargé quand bf_charge passe son `post` (KeyError).
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Plan = env["bf.charge.plan"]
    for plan in Plan.browse(Plan._plans_en_vigueur()).exists():
        try:
            with cr.savepoint():
                plan.with_context(lang=plan.user_id.lang or "fr_CA")._build()
        except Exception:  # un plan qui ne se recalcule pas ne bloque pas la montée
            _logger.warning("bf_charge : plan %s non recalculé", plan.id, exc_info=True)
    _logger.info("bf_charge : plans en vigueur recalculés sur la portée de leur personne")
