# -*- coding: utf-8 -*-
"""Anonymisation d'une réservation : le sondage qui l'a fait naître.

Le sondage garde chaque participant (nom, courriel), ses votes et son fil.
Sa fin est accomplie quand il a produit la réservation : quand la règle
RDV-001 anonymise la réservation, le sondage est supprimé, avec ses
participants, ses votes, ses créneaux et son fil.
"""

from odoo import _, models


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    def _bf_privacy_anonymize_extra(self):
        notes = super()._bf_privacy_anonymize_extra()
        polls = self.env["appointment.poll"].sudo().with_context(
            active_test=False
        ).search([("booking_id", "=", self.id)])
        if not polls:
            return notes
        participants = polls.mapped("participant_ids")
        self._bf_privacy_drop_messages(
            "appointment.poll.participant", participants.ids)
        for poll in polls:
            self._bf_privacy_drop_thread(poll)
        count = len(participants)
        ids = ", ".join(str(i) for i in polls.ids)
        polls.unlink()
        # L'entrée de registre est rédigée en français par bf_appointment : la
        # note suit, quelle que soit la langue de l'usager du cron (en_US chez
        # certains locataires), pour ne pas laisser une entrée immuable à moitié
        # anglaise.
        notes.append(self.with_context(lang="fr_CA").env._(
            "availability poll no. %(ids)s deleted with its %(count)s "
            "participant(s), their votes and its thread",
            ids=ids, count=count,
        ))
        return notes
