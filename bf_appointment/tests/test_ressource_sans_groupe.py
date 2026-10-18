# -*- coding: utf-8 -*-
"""Une ressource se modifie sans le groupe Réservations.

La contrainte `_check_bookings_scheduling` d'OCA cherchait les réservations
au nom de l'usager courant : sans droit de lecture sur `resource.booking`,
créer un employé, changer le fuseau d'une ressource ou créer un horaire
levait une AccessError. Ces essais tiennent en place :

* un usager qui a le droit d'écrire les ressources, sans aucun groupe
  Réservations, crée une ressource humaine et change son fuseau ;
* le contrôle n'est pas éteint pour autant : un vrai conflit est refusé ;
* le refus ne nomme ni le client ni la réservation à qui ne peut pas la lire,
  et reste détaillé pour un gestionnaire des rendez-vous ;
* un administrateur sans groupe Réservations crée un horaire de travail (même
  contrainte d'OCA sur `resource.calendar`).
"""

from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged

_GROUPES_RESERVATIONS = (
    "resource_booking.group_user",
    "resource_booking.group_manager",
)


@tagged("post_install", "-at_install")
class TestRessourceSansGroupe(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disponibilités de 9 h à 17 h tous les jours. Une réservation à 10 h
        # UTC sort de ces heures dès que la ressource passe à Auckland.
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "9-17 ressource sans groupe", "tz": "UTC",
            "attendance_ids": [
                Command.create({
                    "name": f"J{d}", "dayofweek": str(d),
                    "hour_from": 9.0, "hour_to": 17.0, "day_period": "morning",
                })
                for d in range(7)
            ],
        })
        # Le droit d'écrire les ressources, sans rien des rendez-vous : c'est
        # ce que `hr` donne à ses agents (`access_hr_employee_resource_user`).
        cls.groupe_ressources = cls.env["res.groups"].create({
            "name": "Ressources (essai, sans réservations)",
        })
        cls.env["ir.model.access"].create({
            "name": "resource.resource essai sans réservations",
            "model_id": cls.env.ref("resource.model_resource_resource").id,
            "group_id": cls.groupe_ressources.id,
            "perm_read": True, "perm_write": True,
            "perm_create": True, "perm_unlink": True,
        })
        cls.rh = new_test_user(
            cls.env, login="rh_sans_reservations", groups="base.group_user",
        )
        cls.rh.groups_id = [Command.link(cls.groupe_ressources.id)]
        cls.admin_sans_groupe = new_test_user(
            cls.env, login="admin_sans_reservations",
            groups="base.group_user,base.group_system",
        )
        cls.gestionnaire = new_test_user(
            cls.env, login="gestionnaire_ressource",
            groups="base.group_user,resource_booking.group_manager",
        )
        cls.conseillere = new_test_user(
            cls.env, login="conseillere_ressource", groups="base.group_user",
        )
        cls.ressource = cls.env["resource.resource"].create({
            "name": "Conseillère d'essai", "resource_type": "user",
            "user_id": cls.conseillere.id, "calendar_id": cls.calendar.id,
            "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.ressource.id])],
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Zéphyrine Cachemaille", "email": "zephyrine@essai.invalid",
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type ressource sans groupe", "duration": 1.0,
            "slot_duration": 1.0, "modifications_deadline": 0.0,
            "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id}),
            ],
        })

    def _sans_reservations(self, user):
        """L'essai ne vaut rien si une base l'inscrit quand même au groupe."""
        for xmlid in _GROUPES_RESERVATIONS:
            if user.has_group(xmlid):
                self.skipTest("%s hérite de %s sur cette base" % (user.login, xmlid))

    def _reservation(self):
        """Une réservation planifiée à 10 h UTC, dans trois jours."""
        start = (fields.Datetime.now() + timedelta(days=3)).replace(
            hour=10, minute=0, second=0, microsecond=0)
        resa = self.env["resource.booking"].create({
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([self.partner.id])],
            "combination_auto_assign": False,
            "combination_id": self.combination.id,
            "start": start, "duration": 1.0,
        })
        self.assertTrue(resa.meeting_id, "la réservation d'essai n'a pas d'événement")
        return resa

    # -- Sans groupe : plus d'AccessError -----------------------------------

    def test_ressource_humaine_creee_et_fuseau_change_sans_groupe(self):
        self._sans_reservations(self.rh)
        lie = new_test_user(self.env, login="lie_sans_reservations")
        ressource = self.env["resource.resource"].with_user(self.rh).create({
            "name": "Ressource créée par RH", "resource_type": "user",
            "user_id": lie.id, "calendar_id": self.calendar.id, "tz": "UTC",
        })
        ressource.write({"tz": "America/Toronto"})
        self.assertEqual(ressource.tz, "America/Toronto")
        # La ressource déjà réservée, sans conflit : 10 h UTC tient encore
        # dans 9 h-17 h à Londres.
        self._reservation()
        self.ressource.with_user(self.rh).write({"tz": "Europe/London"})
        self.assertEqual(self.ressource.tz, "Europe/London")

    def test_employe_cree_par_un_agent_rh_sans_groupe(self):
        if "hr.employee" not in self.env:
            self.skipTest("hr n'est pas installé sur cette base")
        agent = new_test_user(
            self.env, login="agent_rh_sans_reservations",
            groups="base.group_user,hr.group_hr_user",
        )
        self._sans_reservations(agent)
        employe = self.env["hr.employee"].with_user(agent).create({
            "name": "Employé d'essai", "resource_calendar_id": self.calendar.id,
        })
        employe.write({"tz": "America/Toronto"})
        self.assertEqual(employe.resource_id.tz, "America/Toronto")

    def test_horaire_cree_par_un_administrateur_sans_groupe(self):
        self._sans_reservations(self.admin_sans_groupe)
        horaire = self.env["resource.calendar"].with_user(
            self.admin_sans_groupe).create({"name": "Horaire créé sans groupe"})
        horaire.write({"tz": "America/Toronto"})
        self.assertEqual(horaire.tz, "America/Toronto")

    # -- Le contrôle tient toujours ------------------------------------------

    def test_conflit_refuse_sans_nommer_la_reservation(self):
        self._sans_reservations(self.rh)
        self._reservation()
        with self.assertRaises(ValidationError) as refus:
            # 10 h UTC tombe la nuit à Auckland, hors de 9 h-17 h.
            self.ressource.with_user(self.rh).write({"tz": "Pacific/Auckland"})
        message = str(refus.exception)
        self.assertNotIn("Zéphyrine", message)
        self.assertNotIn("Cachemaille", message)
        self.assertNotIn(self.booking_type.name, message)
        self.assertNotIn("Ces rendez-vous ne peuvent pas", message)
        self.assertIn("Demandez à un gestionnaire des rendez-vous", message)

    def test_conflit_detaille_pour_un_gestionnaire(self):
        self._reservation()
        with self.assertRaises(ValidationError) as refus:
            self.ressource.with_user(self.gestionnaire).write(
                {"tz": "Pacific/Auckland"})
        message = str(refus.exception)
        self.assertIn("Ces rendez-vous ne peuvent pas être placés", message)
        self.assertIn("Zéphyrine Cachemaille - Type ressource sans groupe", message)
