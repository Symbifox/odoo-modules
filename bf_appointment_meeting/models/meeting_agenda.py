# -*- coding: utf-8 -*-
"""Le demandeur d'un rendez-vous, retenu sur l'ordre du jour qu'il a fait naître."""

from odoo import fields, models


class MeetingAgenda(models.Model):
    _inherit = "meeting.agenda"

    # ⚠️ Le seul moyen sûr de retrouver l'OdJ d'un rendez-vous annulé. Sans
    # `bf_calendar_invite`, l'annulation SUPPRIME l'événement et le lien de
    # l'OdJ passe à NULL ; avec lui, l'événement est annulé mais rien ne dit
    # que l'OdJ est né d'un rendez-vous plutôt qu'écrit à la main. Or
    # seul le premier peut être repris pour le même demandeur : un OdJ rédigé
    # à la main peut porter des notes de travail, et la reprise rouvre sa
    # page de contribution au demandeur.
    bf_booking_partner_id = fields.Many2one(
        "res.partner",
        string="Demandeur du rendez-vous",
        index="btree_not_null",
        readonly=True,
        copy=False,
        help="Celui qui a pris le rendez-vous dont cet ordre du jour est né. "
             "S'il reprend un rendez-vous après une annulation, l'ordre du "
             "jour gardé lui sert de nouveau.",
    )
