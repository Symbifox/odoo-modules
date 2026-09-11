"""La capacité de créneau, et la non-régression du plafond à 1.

Le calcul de disponibilité est le cœur du module : tout le reste en dépend. Le
premier test de ce fichier ne vérifie donc pas la nouveauté, il vérifie que rien
n'a bougé pour les locataires qui n'en demandaient pas.
"""

from datetime import datetime

import pytz

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_capacite")
class TestCapaciteCreneau(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tz = pytz.timezone("America/Toronto")
        Calendar = cls.env["resource.calendar"]
        cls.cal_ouvert = Calendar.create({
            "name": "Essai ouvert",
            "tz": "America/Toronto",
            "attendance_ids": [(5, 0, 0)] + [
                (0, 0, {
                    "name": "j%s" % j, "dayofweek": str(j),
                    "hour_from": 0.0, "hour_to": 23.98,
                })
                for j in range(7)
            ],
        })
        cls.cal_samedi = Calendar.create({
            "name": "Essai samedi 9-17",
            "tz": "America/Toronto",
            "attendance_ids": [(5, 0, 0), (0, 0, {
                "name": "sam", "dayofweek": "5",
                "hour_from": 9.0, "hour_to": 17.0,
            })],
        })
        cls.ressource = cls.env["resource.resource"].create({
            "name": "Essai salle",
            "resource_type": "material",
            "calendar_id": cls.cal_samedi.id,
            "tz": "America/Toronto",
        })
        cls.combinaison = cls.env["resource.booking.combination"].create({
            "resource_ids": [(6, 0, cls.ressource.ids)],
        })
        cls.debut = cls.tz.localize(datetime(2026, 9, 12, 0, 0))
        cls.fin = cls.tz.localize(datetime(2026, 9, 13, 0, 0))

    def _type(self, capacite=1):
        return self.env["resource.booking.type"].create({
            "name": "Essai type capacité %s" % capacite,
            "duration": 0.5,
            "slot_duration": 0.5,
            "modifications_deadline": 0.0,
            "resource_calendar_id": self.cal_ouvert.id,
            "slot_capacity": capacite,
            "combination_rel_ids": [(0, 0, {"combination_id": self.combinaison.id})],
        })

    def _creneaux(self, type_rdv):
        return [
            s.strftime("%H:%M")
            for s in type_rdv._bf_candidate_slots(
                self.debut, self.fin, tz="America/Toronto"
            )
        ]

    def _reserver(self, type_rdv, heure_utc):
        partenaire = self.env["res.partner"].create({
            "name": "Essai visiteur %s" % heure_utc,
            "email": "essai@example.com",
        })
        return type_rdv._bf_create_booking(heure_utc, partners=partenaire)

    def test_plafond_un_retire_le_creneau(self):
        """Sans capacité déclarée, une réservation ferme le créneau. Inchangé."""
        type_rdv = self._type(capacite=1)
        self.assertIn("13:00", self._creneaux(type_rdv))
        self._reserver(type_rdv, datetime(2026, 9, 12, 17, 0))
        self.assertNotIn(
            "13:00", self._creneaux(type_rdv),
            "Le comportement historique doit tenir : une réservation ferme "
            "le créneau.",
        )

    def test_plafond_trois_garde_le_creneau_ouvert(self):
        type_rdv = self._type(capacite=3)
        self._reserver(type_rdv, datetime(2026, 9, 12, 17, 0))
        self.assertIn(
            "13:00", self._creneaux(type_rdv),
            "Une place prise sur trois laisse le créneau offert.",
        )
        self._reserver(type_rdv, datetime(2026, 9, 12, 17, 0))
        self.assertIn("13:00", self._creneaux(type_rdv))
        self._reserver(type_rdv, datetime(2026, 9, 12, 17, 0))
        self.assertNotIn(
            "13:00", self._creneaux(type_rdv),
            "Le plafond atteint ferme le créneau.",
        )

    def test_plafond_atteint_refuse_la_reservation(self):
        """Le plafond n'est pas qu'un affichage : la troisième est refusée.

        Le refus vient de `_bf_assert_slot_available`, qui contrôle AVANT de
        créer. C'est un `UserError` et pas une `ValidationError` : la
        disponibilité se lit dans la grille, elle ne se découvre pas à la
        validation d'un enregistrement déjà né.
        """
        from odoo.exceptions import UserError

        type_rdv = self._type(capacite=2)
        self._reserver(type_rdv, datetime(2026, 9, 12, 17, 0))
        self._reserver(type_rdv, datetime(2026, 9, 12, 17, 0))
        with self.assertRaises(UserError):
            self._reserver(type_rdv, datetime(2026, 9, 12, 17, 0))

    def test_annulation_rend_la_place(self):
        type_rdv = self._type(capacite=2)
        premiere = self._reserver(type_rdv, datetime(2026, 9, 12, 17, 0))
        self._reserver(type_rdv, datetime(2026, 9, 12, 17, 0))
        self.assertNotIn("13:00", self._creneaux(type_rdv))
        premiere.action_cancel()
        self.assertIn(
            "13:00", self._creneaux(type_rdv),
            "Une place annulée se rend, sinon le créneau se vide au fil des "
            "désistements.",
        )

    def test_candidate_slots_par_combinaison(self):
        """La grille d'UNE combinaison, pas l'union de toutes celles du type."""
        autre_ressource = self.env["resource.resource"].create({
            "name": "Essai autre salle",
            "resource_type": "material",
            "calendar_id": self.cal_ouvert.id,
            "tz": "America/Toronto",
        })
        autre_combinaison = self.env["resource.booking.combination"].create({
            "resource_ids": [(6, 0, autre_ressource.ids)],
        })
        type_rdv = self._type()
        type_rdv.combination_rel_ids = [
            (0, 0, {"combination_id": autre_combinaison.id}),
        ]
        union = self._creneaux(type_rdv)
        self.assertIn(
            "08:00", union,
            "L'union des deux combinaisons ouvre la journée entière.",
        )
        restreint = [
            s.strftime("%H:%M")
            for s in type_rdv._bf_candidate_slots(
                self.debut, self.fin, tz="America/Toronto",
                combination=self.combinaison,
            )
        ]
        self.assertNotIn(
            "08:00", restreint,
            "Restreinte à la salle du samedi, la grille commence à 9 h.",
        )
        self.assertIn("09:00", restreint)
