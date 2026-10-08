# -*- coding: utf-8 -*-
"""La reprise d'un rendez-vous reprend son ordre du jour.

À l'annulation, un ordre du jour qui porte du travail survit. Quand le même
demandeur reprend un rendez-vous, la confirmation en fabriquait un second et
le premier restait à côté, avec ses sujets. Ce que ces essais gardent :

* le premier est repris : même document, fenêtre rouverte, date du nouveau
  créneau, et un jeton NEUF (l'ancien lien a pu partir chez d'autres) ;
* il ne l'est que pour le MÊME demandeur, dans le MÊME projet ;
* jamais un ordre du jour écrit à la main : la reprise rouvre sa page de
  contribution au demandeur, et un OdJ de travail peut porter des notes
  qui ne lui sont pas destinées ;
* jamais un ordre du jour annulé (resté vide, il ne racontait rien) ;
* 🔴 jamais par la page publique : elle retrouve le demandeur par la seule
  adresse tapée, sans la vérifier, et montre le lien de contribution.
  Reprendre là rendait l'ordre du jour d'un client à quiconque tape son
  adresse (relecture adverse). La page publique reçoit un OdJ neuf, et une
  note nomme l'OdJ gardé.
"""

from odoo import Command, fields
from odoo.tests import tagged

from .test_agenda_from_booking import TestAgendaFromBooking


@tagged("bf_appointment_meeting", "bf_appointment_meeting_reprise")
class TestRepriseDuRendezVous(TestAgendaFromBooking):

    def _annule_avec_travail(self, rang=0):
        booking = self._booking(rang=rang)
        booking.action_confirm()
        agenda = booking._bf_agenda()
        agenda.objectives = "Arrêter le périmètre"
        self.env["meeting.agenda.topic"].create({
            "agenda_id": agenda.id, "name": "Sujet préparé", "sequence": 10})
        booking.action_cancel()
        self.assertEqual(agenda.state, "draft", "l'OdJ qui porte du travail survit")
        return agenda

    def test_le_demandeur_est_retenu(self):
        booking = self._booking()
        booking.action_confirm()
        self.assertEqual(booking._bf_agenda().bf_booking_partner_id, self.client)

    def test_la_reprise_reprend_l_ordre_du_jour_garde(self):
        garde = self._annule_avec_travail()
        jeton = garde.access_token
        self.assertTrue(garde.bf_to_schedule)

        reprise = self._booking(rang=3)
        reprise.action_confirm()

        self.assertEqual(reprise._bf_agenda(), garde, "pas de second ordre du jour")
        self.assertEqual(garde.calendar_event_id, reprise.meeting_id)
        self.assertEqual(garde.date, reprise.start)
        self.assertEqual(garde.objectives, "Arrêter le périmètre")
        self.assertEqual(garde.topic_ids.mapped("name"), ["Sujet préparé"])
        self.assertTrue(garde.access_token)
        self.assertNotEqual(garde.access_token, jeton,
                            "jeton neuf : l'ancien lien a pu partir chez d'autres")
        self.assertTrue(garde.contributions_open, "la fenêtre se rouvre")
        self.assertEqual(self.env["meeting.agenda"].search_count([
            ("bf_booking_partner_id", "=", self.client.id)]), 1)
        self.assertIn("Rendez-vous repris",
                      " ".join(garde.message_ids.mapped("body")))
        liens = [lien["url"] for lien in reprise.bf_extra_links()]
        self.assertTrue(any(garde.access_token in url for url in liens),
                        "le nouveau lien part avec la nouvelle confirmation")
        self.assertFalse(any(jeton in url for url in liens), "l'ancien ne part plus")

    def test_la_page_publique_ne_reprend_pas(self):
        garde = self._annule_avec_travail()
        public = self.env.ref("base.public_user")
        booking = self.type.with_user(public).sudo()._bf_create_booking(
            self._slot(3), partners=self.client, confirm=False)
        self.assertTrue(booking.create_uid.share, "né sous l'usager public")
        booking.action_confirm()
        neuf = booking._bf_agenda()
        self.assertTrue(neuf)
        self.assertNotEqual(neuf, garde)
        self.assertFalse(garde.contributions_open, "la fenêtre du gardé reste fermée")
        self.assertNotEqual(garde.calendar_event_id, booking.meeting_id)
        self.assertFalse(any(garde.access_token in lien["url"]
                             for lien in booking.bf_extra_links()),
                         "le lien du gardé ne sort pas sur la page publique")
        self.assertIn("Un ordre du jour gardé existe",
                      " ".join(neuf.message_ids.mapped("body")))
        self.assertTrue(garde.bf_to_schedule, "il reste à rattacher à la main")

    def test_la_reprise_suit_le_nouveau_creneau_dans_le_titre(self):
        """Le titre porte la date de l'ancien rendez-vous : elle doit suivre.

        ⚠️ Les créneaux du banc tombent souvent le même jour : on recule
        l'OdJ gardé de dix jours pour que les deux dates diffèrent vraiment,
        sinon l'essai ne vérifiait rien (mutation survivante).
        """
        garde = self._annule_avec_travail()
        garde.date = fields.Datetime.subtract(garde.date, days=10)
        ancienne = fields.Datetime.context_timestamp(garde, garde.date).strftime("%Y-%m-%d")
        garde.name = "Suivi Cliente — %s" % ancienne
        reprise = self._booking(rang=3)
        reprise.action_confirm()
        self.assertEqual(reprise._bf_agenda(), garde)
        nouvelle = fields.Datetime.context_timestamp(garde, garde.date).strftime("%Y-%m-%d")
        self.assertNotEqual(nouvelle, ancienne)
        self.assertEqual(garde.name, "Suivi Cliente — %s" % nouvelle)

    def test_un_rendez_vous_a_plusieurs_ne_retient_ni_ne_reprend_personne(self):
        """🔴 `partner_id` est le premier par ordre alphabétique : à plusieurs,
        on reprenait l'OdJ de l'un et son lien partait chez tous."""
        garde = self._annule_avec_travail()
        aaron = self.env["res.partner"].create({
            "name": "Aaron Avant-Tout", "email": "aaron@test.invalid"})
        booking = self.type._bf_create_booking(
            self._slot(3), partners=self.client | aaron, confirm=False)
        booking.action_confirm()
        neuf = booking._bf_agenda()
        self.assertNotEqual(neuf, garde)
        self.assertFalse(neuf.bf_booking_partner_id,
                         "à plusieurs, personne n'est retenu comme demandeur")
        self.assertFalse(garde.contributions_open)

    def test_un_rendez_vous_ne_d_un_sondage_ne_reprend_pas(self):
        garde = self._annule_avec_travail()
        booking = self._booking(rang=3)
        booking.bf_source_ref = "res.partner,%d" % self.client.id
        booking.action_confirm()
        self.assertNotEqual(booking._bf_agenda(), garde)

    def test_un_odj_ne_sur_la_page_publique_n_est_jamais_repris(self):
        """🔴 L'intrus réserve sous l'adresse de la cliente, voit le lien, y
        laisse un sujet, annule. Plus tard, le personnel prend le vrai
        rendez-vous : l'OdJ de l'intrus ne doit pas servir, car il en connaît
        le jeton."""
        public = self.env.ref("base.public_user")
        intrus = self.type.with_user(public).sudo()._bf_create_booking(
            self._slot(0), partners=self.client, confirm=False)
        intrus.action_confirm()
        piege = intrus._bf_agenda()
        self.assertFalse(piege.bf_booking_partner_id,
                         "né sur la page publique : personne n'est retenu")
        piege.objectives = "Sujet laissé par l'intrus"
        intrus.action_cancel()
        self.assertEqual(piege.state, "draft", "gardé, puisqu'il porte du travail")

        vrai = self._booking(rang=3)
        vrai.action_confirm()
        self.assertNotEqual(vrai._bf_agenda(), piege)
        self.assertFalse(any(piege.access_token in lien["url"]
                             for lien in vrai.bf_extra_links()))

    def test_un_autre_demandeur_n_herite_pas(self):
        garde = self._annule_avec_travail()
        autre = self.env["res.partner"].create({
            "name": "Autre demandeur", "email": "autre@test.invalid"})
        booking = self.type._bf_create_booking(
            self._slot(3), partners=autre, confirm=False)
        booking.action_confirm()
        self.assertNotEqual(booking._bf_agenda(), garde)
        self.assertTrue(booking._bf_agenda())
        self.assertTrue(garde.bf_to_schedule)

    def test_un_autre_projet_n_herite_pas(self):
        garde = self._annule_avec_travail()
        ailleurs = self._make_type("Type ailleurs", projet=self.projet_repli)
        booking = self._booking(ailleurs, rang=3)
        booking.action_confirm()
        self.assertNotEqual(booking._bf_agenda(), garde)

    def test_un_ordre_du_jour_ecrit_a_la_main_ne_se_reprend_pas(self):
        a_la_main = self.env["meeting.agenda"].with_context(
            skip_auto_refine=True).create({
                "project_id": self.projet.id,
                "date": self._slot(3),
                "participant_ids": [Command.set([self.client.id])],
                "context_html": "<p>Notes de travail</p>",
            })
        self.assertTrue(a_la_main.bf_to_schedule)
        booking = self._booking()
        booking.action_confirm()
        self.assertNotEqual(booking._bf_agenda(), a_la_main)
        self.assertFalse(a_la_main.calendar_event_id)
        self.assertFalse(a_la_main.contributions_open)

    def test_un_ordre_du_jour_annule_ne_se_reprend_pas(self):
        booking = self._booking()
        booking.action_confirm()
        vide = booking._bf_agenda()
        booking.action_cancel()
        self.assertEqual(vide.state, "cancelled")
        reprise = self._booking(rang=3)
        reprise.action_confirm()
        self.assertNotEqual(reprise._bf_agenda(), vide)

    def test_la_reponse_du_formulaire_ne_se_repete_pas(self):
        champ = self.env["appointment.intake.field"].create({
            "type_id": self.type.id, "name": "De quoi s'agit-il ?",
            "field_type": "textarea", "sequence": 10})

        def reserve(rang, valeur):
            booking = self._booking(rang=rang)
            self.env["appointment.intake.answer"].create({
                "booking_id": booking.id, "field_id": champ.id, "value": valeur})
            booking.action_confirm()
            return booking

        premier = reserve(0, "Reprendre le calendrier de migration")
        agenda = premier._bf_agenda()
        agenda.objectives = "Arrêter le périmètre"
        premier.action_cancel()

        second = reserve(3, "Reprendre le calendrier de migration")
        self.assertEqual(second._bf_agenda(), agenda)
        self.assertEqual(agenda.topic_ids.mapped("name"),
                         ["Reprendre le calendrier de migration"],
                         "la même réponse ne se répète pas")

        # Une réponse NOUVELLE se range après les sujets en place.
        second.action_cancel()
        reserve(5, "Valider le budget")
        self.assertEqual(agenda.topic_ids.sorted("sequence").mapped("name"),
                         ["Reprendre le calendrier de migration", "Valider le budget"])
