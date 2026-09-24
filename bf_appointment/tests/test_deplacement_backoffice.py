# -*- coding: utf-8 -*-
"""Déplacer un rendez-vous depuis l'agenda ou depuis Rendez-vous.

Le refus rencontré en production, tel quel :

    Oh mince !
    Cannot schedule these bookings because no resources are selected for them:

Le mécanisme, lu dans `resource_booking` (OCA) : au déplacement d'une
réservation à attribution automatique, `_compute_combination_id` cherche une
ressource libre au nouvel horaire. S'il n'en trouve aucune (hors des
disponibilités, ou déjà prise), il VIDE la ressource, et `_check_scheduling`
refuse ensuite une réservation « sans ressource ». Le motif affiché n'est donc
pas le vrai, et il est en anglais.

Ce que ces essais tiennent en place :

* un gestionnaire des rendez-vous déplace où il veut, par l'agenda comme par
  la fiche, et la ressource reste en place ;
* un utilisateur sans ce rôle est refusé avec la vraie raison, en français ;
* la page publique reste stricte : un client ne sort pas des disponibilités ;
* l'événement d'agenda mène à sa réservation.
"""

from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestDeplacementBackoffice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disponibilités de 9 h à 17 h tous les jours, en UTC : un déplacement
        # à 20 h sort des heures, quel que soit le jour où l'essai tourne.
        attendances = [
            Command.create({
                "name": f"J{d}", "dayofweek": str(d),
                "hour_from": 9.0, "hour_to": 17.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "9-17 deplacement", "attendance_ids": attendances, "tz": "UTC",
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
        cls.gestionnaire = new_test_user(
            cls.env, login="gestionnaire_essai",
            groups="base.group_user,resource_booking.group_manager",
        )
        cls.employe = new_test_user(
            cls.env, login="employe_essai",
            groups="base.group_user,resource_booking.group_user",
        )

    def _jour(self, heure, dans=3):
        return (fields.Datetime.now() + timedelta(days=dans)).replace(
            hour=heure, minute=0, second=0, microsecond=0)

    def _reservation(self, auto=True):
        """Une réservation planifiée à 10 h, dans les heures."""
        resa = self.env["resource.booking"].create({
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([self.partner.id])],
            "combination_auto_assign": auto,
            "combination_id": self.combination.id,
            "start": self._jour(10), "duration": 1.0,
        })
        # L'employé la suit, sinon la règle d'OCA la lui cache : on veut éprouver
        # un refus de planification, pas un refus d'accès.
        resa.message_subscribe(partner_ids=self.employe.partner_id.ids)
        self.assertTrue(resa.meeting_id, "la réservation d'essai n'a pas d'événement")
        self.assertEqual(resa.combination_id, self.combination)
        return resa

    # -- Gestionnaire : les deux portes mènent au même endroit --------------

    def test_gestionnaire_deplace_depuis_la_fiche_hors_des_heures(self):
        resa = self._reservation()
        resa.with_user(self.gestionnaire).write({"start": self._jour(20)})
        self.assertEqual(resa.start, self._jour(20))
        self.assertEqual(resa.combination_id, self.combination,
                         "la ressource a été vidée au déplacement")
        self.assertEqual(resa.meeting_id.start, self._jour(20))

    def test_gestionnaire_deplace_depuis_l_agenda_hors_des_heures(self):
        resa = self._reservation()
        resa.meeting_id.with_user(self.gestionnaire).write({
            "start": self._jour(20), "stop": self._jour(21),
        })
        self.assertEqual(resa.start, self._jour(20))
        self.assertEqual(resa.combination_id, self.combination,
                         "la ressource a été vidée au déplacement")

    def test_gestionnaire_sans_attribution_automatique(self):
        resa = self._reservation(auto=False)
        resa.with_user(self.gestionnaire).write({"start": self._jour(20)})
        self.assertEqual(resa.start, self._jour(20))
        self.assertEqual(resa.combination_id, self.combination)

    def test_dans_les_heures_rien_ne_change(self):
        resa = self._reservation()
        resa.with_user(self.employe).write({"start": self._jour(14)})
        self.assertEqual(resa.start, self._jour(14))
        self.assertEqual(resa.combination_id, self.combination)

    # -- Sans le rôle : refusé, mais pour la vraie raison ---------------------

    def test_employe_refuse_avec_la_vraie_raison_en_francais(self):
        resa = self._reservation()
        with self.assertRaises(ValidationError) as ctx:
            resa.with_user(self.employe).write({"start": self._jour(20)})
        message = str(ctx.exception)
        self.assertNotIn("no resources are selected", message)
        self.assertIn("sort des disponibilités", message)
        self.assertIn("Un gestionnaire des rendez-vous", message)
        self.assertIn("Cliente d'essai - Type d'essai", message)
        self.assertEqual(resa.combination_id, self.combination)

    def test_employe_refuse_depuis_l_agenda_aussi(self):
        resa = self._reservation(auto=False)
        with self.assertRaises(ValidationError) as ctx:
            resa.meeting_id.with_user(self.employe).write({
                "start": self._jour(20), "stop": self._jour(21),
            })
        message = str(ctx.exception)
        self.assertNotIn("Cannot schedule", message)
        self.assertIn("sort des disponibilités", message)

    # -- Ce qui ne doit PAS changer ----------------------------------------

    def test_creation_hors_des_heures_reste_refusee_meme_au_gestionnaire(self):
        # Le passe-droit est celui d'un DÉPLACEMENT. Une création hors des
        # heures reste refusée, comme dans les essais d'OCA, qui tournent en
        # super-utilisateur : les comptes d'automatisation aussi sont
        # gestionnaires, et ils ne sont pas des humains qui décident.
        with self.assertRaises(ValidationError):
            self.env["resource.booking"].with_user(self.gestionnaire).create({
                "type_id": self.booking_type.id,
                "partner_ids": [Command.set([self.partner.id])],
                "combination_auto_assign": False,
                "combination_id": self.combination.id,
                "start": self._jour(20), "duration": 1.0,
            })

    def test_attribution_automatique_passe_encore_a_une_ressource_libre(self):
        # Garder la ressource ne vaut que quand AUCUNE n'est libre : si une
        # autre l'est, l'attribution automatique la prend, comme avant.
        salle_b = self.env["resource.resource"].create({
            "name": "Salle B d'essai", "calendar_id": self.calendar.id,
            "resource_type": "material", "tz": "UTC",
        })
        combinaison_b = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([salle_b.id])],
        })
        self.booking_type.combination_rel_ids = [Command.create({
            "sequence": 1, "combination_id": combinaison_b.id,
        })]
        resa = self._reservation()
        # Quelqu'un occupe la salle A à 14 h.
        self.env["resource.booking"].create({
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([self.partner.id])],
            "combination_auto_assign": False,
            "combination_id": self.combination.id,
            "start": self._jour(14), "duration": 1.0,
        })
        resa.with_user(self.gestionnaire).write({"start": self._jour(14)})
        self.assertEqual(resa.combination_id, combinaison_b)

    # -- La page publique reste stricte ---------------------------------------

    def test_portail_reste_strict_meme_pour_un_gestionnaire(self):
        resa = self._reservation()
        with self.assertRaises(ValidationError):
            resa.with_user(self.gestionnaire).with_context(using_portal=True).write(
                {"start": self._jour(20)})

    # -- L'événement d'agenda mène à sa réservation ---------------------------

    def test_bouton_de_l_evenement_vers_la_reservation(self):
        resa = self._reservation()
        evenement = resa.meeting_id
        self.assertEqual(evenement.bf_booking_count, 1)
        action = evenement.action_bf_open_bookings()
        self.assertEqual(action["res_model"], "resource.booking")
        self.assertEqual(action["res_id"], resa.id)

    def test_bouton_suit_une_reservation_annulee(self):
        # Depuis la 2.57.0, une annulation archive la réservation et GARDE son
        # événement, barré. C'est justement là qu'on veut remonter au dossier.
        resa = self._reservation()
        evenement = resa.meeting_id
        resa.active = False
        evenement.invalidate_recordset(["bf_booking_count"])
        self.assertEqual(evenement.bf_booking_count, 1)
        self.assertEqual(evenement.action_bf_open_bookings()["res_id"], resa.id)

    def test_evenement_ordinaire_sans_bouton(self):
        evenement = self.env["calendar.event"].create({
            "name": "Sans réservation", "start": self._jour(10),
            "stop": self._jour(11),
        })
        self.assertEqual(evenement.bf_booking_count, 0)
