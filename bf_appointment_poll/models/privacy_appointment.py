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
        notes.append(self.with_context(lang=self._bf_poll_register_lang()).env._(
            "availability poll no. %(ids)s deleted with its %(count)s "
            "participant(s), their votes and its thread",
            ids=ids, count=count,
        ))
        return notes

    def _bf_poll_register_lang(self):
        """La langue de la note au registre : le français, s'il est actif ici.

        🔴 `with_context(lang="fr_CA")` sur une base où fr_CA n'est pas active
        (un locataire n'a que en_US) fait lever `env.lang` (« Invalid language
        code: fr_CA ») : l'anonymisation d'une réservation qui a un sondage
        tombait, travail planifié compris. Sans fr_CA, la note suit la langue
        de l'environnement si elle est active, sinon l'anglais de la source.
        """
        installees = {code for code, _nom in self.env["res.lang"].get_installed()}
        if "fr_CA" in installees:
            return "fr_CA"
        lang = self.env.context.get("lang")
        return lang if lang in installees else "en_US"
