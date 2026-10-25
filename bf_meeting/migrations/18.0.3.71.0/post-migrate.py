"""Pastilles du calendrier : un OdJ annulé éteint la pastille.

La version 3.70.0 sortait du tableau de bord et du digest la rencontre dont l'OdJ
est annulé. La grille du calendrier, elle, gardait la pastille « rédigé » (états
stockés, calculés d'après l'OdJ). Le calcul lit désormais l'annulation ; les
valeurs déjà stockées pour les OdJ annulés se refont ici.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    n = env['calendar.event']._bf_recompute_cancelled_agenda_states()
    _logger.info("bf_meeting 3.71.0 : pastilles refaites pour %s rencontre(s) à OdJ annulé", n)
