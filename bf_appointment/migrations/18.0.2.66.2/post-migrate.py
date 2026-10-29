# -*- coding: utf-8 -*-
"""Donner un hôte aux salles Talk déjà faites .

Un déplacement garde la salle Talk de la réservation, sauf si l'hôte change :
la salle est modérée par l'hôte pour qui elle a été créée, et
`video_room_host_id` le retient désormais. Les réservations d'avant cette
version n'en ont pas. Leur salle a été faite pour l'organisateur d'alors, qui
est encore le leur : un déplacement au back-office ne touchait pas la salle.

Sans ce rattrapage, toute salle existante serait vue comme celle d'un autre
hôte et remplacée au premier déplacement, ce que ce correctif veut justement éviter.
Rejouable : seules les lignes sans hôte sont touchées.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    n = env["resource.booking"]._bf_backfill_video_room_host()
    _logger.info("bf_appointment 18.0.2.66.2 : hôte de salle posé sur %s réservation(s)", n)
