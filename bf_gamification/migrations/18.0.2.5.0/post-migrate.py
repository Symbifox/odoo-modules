"""Fox Quest v2.5.0 — répare le nom de champ des badges « La Tanière ».

Les trois badges de maintenance pointaient sur ``last_performed_date``, un champ
qui n'existe pas sur ``hosting.maintenance.schedule`` (le vrai nom est
``last_performed``). Le domaine levait donc une exception à chaque vérification
de profil : Fox Quest journalisait « invalid condition_domain » et n'a jamais
attribué ces badges.

Les enregistrements vivent dans un bloc ``noupdate="1"`` : corriger le XML ne
suffit pas, il faut réécrire les lignes déjà en base.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_BAD_DOMAIN = "[('last_performed_date', '!=', False)]"
_GOOD_DOMAIN = "[('last_performed', '!=', False)]"

_BADGE_XML_IDS = (
    "bf_gamification.badge_first_maintenance",
    "bf_gamification.badge_10_maintenance",
    "bf_gamification.badge_50_maintenance",
)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _fix_maintenance_badge_domains(env)


def _fix_maintenance_badge_domains(env):
    fixed = 0
    for xml_id in _BADGE_XML_IDS:
        badge = env.ref(xml_id, raise_if_not_found=False)
        if not badge:
            continue
        if badge.condition_domain != _BAD_DOMAIN:
            continue
        badge.write({"condition_domain": _GOOD_DOMAIN})
        fixed += 1
        _logger.info("Fox Quest: domaine corrigé pour %s", xml_id)

    # Filet de sécurité : rattrape toute copie du mauvais domaine créée à la
    # main ou par une migration antérieure, y compris sur des badges hors data.
    strays = env["bf.gamification.badge"].search([
        ("condition_domain", "like", "last_performed_date"),
    ])
    for badge in strays:
        badge.condition_domain = badge.condition_domain.replace(
            "last_performed_date", "last_performed",
        )
        fixed += 1
        _logger.info("Fox Quest: domaine corrigé pour le badge %s", badge.name)

    _logger.info("Fox Quest v2.5.0: %s domaine(s) de badge corrigé(s)", fixed)
