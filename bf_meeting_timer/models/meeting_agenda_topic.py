# -*- coding: utf-8 -*-
"""Le segment vit sur le sujet, et c'est une SOMME d'intervalles.

Un sujet quitté puis repris plus tard cumule ses passages : `timer_seconds` est
un total, `timer_visits` compte les fois où on y est revenu. C'est ce qui
sépare une rencontre d'une course, où un segment est un intervalle unique.
"""

from odoo import api, fields, models


class MeetingAgendaTopic(models.Model):
    _inherit = 'meeting.agenda.topic'

    timer_seconds = fields.Integer(
        string='Temps réel (secondes)',
        default=0,
        copy=False,
        help="Somme des passages sur ce sujet, hors pauses.",
    )
    timer_visits = fields.Integer(
        string='Passages',
        default=0,
        copy=False,
    )
    timer_state = fields.Selection(
        [
            ('pending', 'À venir'),
            ('current', 'En cours'),
            ('done', 'Fait'),
            ('skipped', 'Sauté'),
        ],
        string='État au chronomètre',
        default='pending',
        required=True,
        copy=False,
    )
    timer_first_at = fields.Datetime(
        string='Premier passage',
        copy=False,
    )
    timer_minutes = fields.Float(
        string='Temps réel (min)',
        compute='_compute_timer_minutes',
        help="Le temps déjà versé. Le sujet ouvert porte en plus la tranche en "
             "cours, que seul le panneau affiche en direct.",
    )
    timer_delta_minutes = fields.Float(
        string='Écart (min)',
        compute='_compute_timer_minutes',
        help="Temps réel moins temps alloué. Positif : le sujet a débordé.",
    )

    @api.depends('timer_seconds', 'duration_planned')
    def _compute_timer_minutes(self):
        for topic in self:
            topic.timer_minutes = round(topic.timer_seconds / 60.0, 2)
            topic.timer_delta_minutes = round(
                (topic.timer_seconds - (topic.duration_planned or 0) * 60) / 60.0, 2)
