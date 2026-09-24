# -*- coding: utf-8 -*-
"""Déplacer un rendez-vous au lieu de l'annuler.

Ce que ces essais tiennent en place, et la mesure derrière chacun :

* le lien « Déplacer » n'apparaît que quand le déplacement MARCHE. Un lien qui
  mène à un refus est pire que pas de lien : la personne a déjà décidé, elle
  clique, elle apprend que non, et elle annule. C'est exactement ce qu'on
  cherche à éviter, et c'est ce que la production montrait : pas un seul vrai
  déplacement parmi les changements de `start` tracés, presque tous des
  annulations ;
* un DÉPLACEMENT ne se dit pas comme une première confirmation. Les deux
  recevaient « voici votre confirmation », sans un mot sur l'heure quittée ;
* l'organisateur recevait « Nouvelle réservation » pour une rencontre qui
  existait déjà, et il ne peut pas l'apprendre par l'avis du demandeur, qui
  l'écarte par construction.
"""

from datetime import timedelta

from odoo import Command, fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDeplacementRdv(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        attendances = [
            Command.create({
                "name": f"J{d}", "dayofweek": str(d),
                "hour_from": 0.0, "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 deplacement", "attendance_ids": attendances, "tz": "UTC",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Salle d'essai", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Cliente d'essai", "email": "cliente@essai.invalid",
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type d'essai", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id}),
            ],
        })

    def _reservation(self, dans=3):
        start = (fields.Datetime.now() + timedelta(days=dans)).replace(
            minute=0, second=0, microsecond=0)
        return self.env["resource.booking"].create({
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([self.partner.id])],
            "combination_id": self.combination.id,
            "combination_auto_assign": False,
            "start": start, "duration": 1.0,
        })

    # -- Le lien, et quand il se retire -------------------------------------

    def test_le_lien_est_offert_sur_un_rdv_a_venir(self):
        resa = self._reservation()
        self.assertTrue(resa.bf_can_reschedule())
        liens = resa.bf_extra_links()
        self.assertTrue(liens, "aucun lien offert sur un rendez-vous déplaçable")
        self.assertIn("/schedule", liens[0]["url"])
        self.assertIn(str(resa.id), liens[0]["url"])
        self.assertIn(resa.access_token, liens[0]["url"])

    def test_aucun_lien_sur_un_rdv_passe(self):
        """Un lien mort est pire que pas de lien.

        ⚠️ La campagne de mutations laisse celle-ci SURVIVRE, et c'est noté
        plutôt que contourné : retirer la garde du passé de `bf_can_reschedule`
        ne change rien, parce que `is_overdue` la couvre déjà
        (`now > start - verrou` est vrai pour toute date passée, dès que le
        verrou est positif, et il vaut 2 h par défaut). Les deux gardes se
        recouvrent PAR CONSTRUCTION.

        On garde quand même la garde explicite, pour une raison lisible : elle
        dit la règle sans obliger le lecteur à aller comprendre `is_overdue`,
        un champ calculé qui dépend d'un réglage de TYPE et se lit sur la
        RÉSERVATION. Fabriquer un verrou négatif pour faire rougir la mutation
        aurait éprouvé une saisie que personne ne fait, pas la règle.
        """
        resa = self._reservation(dans=-3)
        self.assertFalse(resa.bf_can_reschedule())
        self.assertEqual(resa.bf_extra_links(), [])

    def test_aucun_lien_une_fois_le_verrou_passe(self):
        """Le verrou de modification du type retire le lien, sans réglage à tenir."""
        resa = self._reservation()
        self.assertTrue(resa.bf_can_reschedule())
        self.booking_type.modification_lock_hours = 24 * 365
        resa.invalidate_recordset()
        self.assertTrue(resa.is_overdue, "le verrou n'a pas pris")
        self.assertFalse(resa.bf_can_reschedule())
        self.assertEqual(resa.bf_extra_links(), [])

    def test_aucun_lien_sur_un_rdv_annule(self):
        resa = self._reservation()
        resa.action_cancel()
        self.assertFalse(resa.bf_can_reschedule())
        self.assertEqual(resa.bf_extra_links(), [])

    def test_le_lien_survit_au_satellite(self):
        """`bf_extra_links` compose : le satellite appelle super et ajoute."""
        resa = self._reservation()
        self.assertGreaterEqual(len(resa.bf_extra_links()), 1)
        html = resa.bf_extra_cta_html()
        self.assertIn(resa.bf_reschedule_url(), html)

    # -- Ce que le déplacement arme ------------------------------------------

    def test_deplacer_arme_l_avis_sur_l_evenement(self):
        """La mesure qui a supprimé un gabarit du lot.

        Déplacer une réservation écrit sur son événement d'agenda, donc la
        chaîne d'avis posée côté agenda le voit déjà. Pas besoin d'un quinzième
        gabarit pour le demandeur.
        """
        resa = self._reservation()
        evenement = resa.meeting_id
        self.assertTrue(evenement)
        if "bf_change_notice_due" not in evenement._fields:
            self.skipTest("bf_calendar_invite absent de ce locataire")
        depart = resa.start
        resa.write({"start": depart + timedelta(hours=2)})
        evenement.invalidate_recordset()
        self.assertTrue(evenement._bf_change_notice_due())
        libelles = [l["label"] for l in evenement.bf_change_lines()]
        self.assertTrue(libelles, "aucune ligne de changement")

    def test_l_ancienne_heure_se_lit_pour_l_organisateur(self):
        """Un déplacement REMPLACE l'heure : la copie vit sur l'événement."""
        resa = self._reservation()
        if "bf_change_baseline" not in resa.meeting_id._fields:
            self.skipTest("bf_calendar_invite absent de ce locataire")
        depart = resa.start
        resa.write({"start": depart + timedelta(hours=2)})
        date_avant, heure_avant = resa.bf_previous_start_display()
        self.assertTrue(date_avant, "l'ancienne heure est perdue")
        self.assertEqual(
            (date_avant, heure_avant), resa._bf_local_strings(depart),
            "l'heure rendue n'est pas celle que le client tenait",
        )

    def test_sans_deplacement_aucune_ancienne_heure(self):
        """Une première confirmation n'a rien à raconter."""
        resa = self._reservation()
        date_avant, _h = resa.bf_previous_start_display()
        self.assertFalse(date_avant)

    def test_le_gabarit_organisateur_existe_et_se_rend(self):
        resa = self._reservation()
        gabarit = self.env.ref(
            "bf_appointment.mail_template_organizer_reschedule",
            raise_if_not_found=False)
        self.assertTrue(gabarit, "le gabarit de déplacement manque")
        rendu = gabarit._render_field("body_html", resa.ids)[resa.id]
        self.assertNotIn("NoneType", rendu)
        self.assertIn("déplacé", rendu.lower())
