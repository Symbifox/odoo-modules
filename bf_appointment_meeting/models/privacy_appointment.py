# -*- coding: utf-8 -*-
"""Anonymisation d'une réservation : ce que ce satellite a recopié.

À la confirmation, ce module rattache le demandeur à l'ordre du jour
(`bf_booking_partner_id`) et sème la première réponse du formulaire d'accueil
comme premier sujet, avec le nom et le courriel du demandeur. Quand la règle
RDV-001 anonymise la réservation, cette copie part avec elle.

⚠️ L'ordre du jour lui-même (titre, participants, autres sujets) reste : c'est
une pièce de la rencontre, régie par la règle de conservation des comptes rendus (RENC-001),
et l'entrée de registre le dit.
"""

from odoo import _, models


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    def _bf_privacy_anonymize_extra(self):
        notes = super()._bf_privacy_anonymize_extra()
        agenda = self._bf_agenda().sudo()
        if not agenda:
            return notes
        answers = {
            (a.value or "").strip()[:200]
            for a in self.intake_answer_ids
            if (a.value or "").strip()
        }
        seeded = agenda.topic_ids.filtered(
            lambda t: t.source == "contributed" and t.name in answers
        )
        # Un sujet semé que l'organisateur a retouché, ou une contribution du
        # demandeur, n'est plus une copie du formulaire : son texte relève de
        # la rencontre (RENC-001). Seul le nom du demandeur qui le signe part.
        emails = {
            (p.email or "").strip().lower()
            for p in (self.partner_id | self.partner_ids)
            if p.email
        }
        signed = (agenda.topic_ids - seeded).filtered(
            lambda t: t.source == "contributed"
            and (t.contributor_email or "").strip().lower() in emails
        )
        if signed:
            signed.write({"contributor_name": False, "contributor_email": False})
        if seeded:
            seeded.write({
                "name": _("Réponse au formulaire d'accueil retirée (RDV-001)"),
                "description": False,
                "contributor_name": False,
                "contributor_email": False,
            })
        detached = bool(agenda.bf_booking_partner_id)
        if detached:
            agenda.write({"bf_booking_partner_id": False})
        notes.append(_(
            "ordre du jour n° %(id)s : %(topics)s sujet(s) semé(s) depuis le "
            "formulaire d'accueil retiré(s), %(signed)s autre(s) sujet(s) "
            "désormais sans le nom du demandeur%(detached)s",
            id=agenda.id,
            topics=len(seeded),
            signed=len(signed),
            detached=_(", demandeur détaché") if detached else "",
        ))
        return notes
