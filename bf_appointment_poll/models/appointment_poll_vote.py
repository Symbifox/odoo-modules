# -*- coding: utf-8 -*-
"""Une réponse : un participant, un créneau, trois valeurs possibles."""

from odoo import api, fields, models


class AppointmentPollVote(models.Model):
    _name = "appointment.poll.vote"
    _description = "Réponse à un créneau"
    _order = "slot_id, participant_id"

    participant_id = fields.Many2one(
        "appointment.poll.participant",
        string="Participant",
        required=True,
        ondelete="cascade",
        index=True,
    )
    slot_id = fields.Many2one(
        "appointment.poll.slot",
        string="Créneau",
        required=True,
        ondelete="cascade",
        index=True,
    )
    poll_id = fields.Many2one(
        related="participant_id.poll_id", store=True, index=True
    )
    answer = fields.Selection(
        [
            ("yes", "Oui"),
            ("ifneedbe", "Si nécessaire"),
            ("no", "Non"),
        ],
        string="Réponse",
        required=True,
        default="yes",
    )

    _sql_constraints = [
        (
            "one_vote_per_slot",
            "UNIQUE(participant_id, slot_id)",
            "Un participant ne répond qu'une fois par créneau.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        votes = super().create(vals_list)
        votes._release_refused_holds()
        return votes

    def write(self, vals):
        result = super().write(vals)
        self._release_refused_holds()
        return result

    def _release_refused_holds(self):
        """Un « Non » d'un participant obligatoire libère la plage tout de suite.

        C'est le comportement demandé : inutile de garder une retenue dans
        l'agenda de l'organisateur pour un créneau qui ne peut plus servir.
        """
        refused = self.filtered(
            lambda v: v.answer == "no" and v.participant_id.required
        )
        if refused:
            refused.mapped("slot_id")._release_hold()
        return True
