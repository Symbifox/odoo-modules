"""Lot d'ouverture (2.40.0) — la surface offerte aux modules satellites.

Ces tests ne valident pas une fonctionnalité visible : ils verrouillent un
CONTRAT. Un satellite (le sondage de disponibilités, un lien unique) s'appuie
dessus, et surtout le parent doit rester installable SANS aucun satellite.
"""

from datetime import timedelta

import pytz

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_hooks")
class TestExtensionPoints(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": f"All day {d}",
                "dayofweek": str(d),
                "hour_from": 0.0,
                "hour_to": 24.0,
                "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 Hooks",
            "attendance_ids": attendances,
            "tz": "UTC",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Hooks material",
            "calendar_id": cls.calendar.id,
            "resource_type": "material",
            "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Hooks Booker",
            "email": "hooks@test.invalid",
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Hooks Type",
            "duration": 1.0,
            "slot_duration": 1.0,
            "modifications_deadline": 0.0,
            "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id}),
            ],
        })

    # -- (1) grille de créneaux sans réservation persistée ------------------

    def _real_slot(self, n=0):
        """N-ième créneau RÉELLEMENT réservable.

        ⚠️ Ne jamais fabriquer une heure à la main : `_bf_create_booking`
        refuse désormais tout instant hors grille, et il a raison — hors
        disponibilités, OCA n'affecte aucune ressource et la réservation naît
        corrompue. Trois de ces tests fabriquaient leur heure et sont tombés
        le jour où le garde-fou est arrivé.
        """
        import pytz

        debut, fin = self._window(days=30)
        creneaux = self.booking_type._bf_candidate_slots(debut, fin, limit=n + 1)
        self.assertGreater(len(creneaux), n, "pas assez de créneaux disponibles")
        return creneaux[n].astimezone(pytz.utc).replace(tzinfo=None)

    def _window(self, days=3):
        now = fields.Datetime.context_timestamp(
            self.env["resource.booking"], fields.Datetime.now()
        )
        return now + timedelta(hours=1), now + timedelta(days=days)

    def test_candidate_slots_returns_slots(self):
        start, end = self._window()
        slots = self.booking_type._bf_candidate_slots(start, end)
        self.assertTrue(slots, "un calendrier 24/7 doit produire des créneaux")
        self.assertEqual(slots, sorted(slots), "les créneaux sortent triés")

    def test_candidate_slots_persists_nothing(self):
        """Le point entier du crochet : proposer sans réserver."""
        Booking = self.env["resource.booking"]
        before = Booking.search_count([("type_id", "=", self.booking_type.id)])
        start, end = self._window()
        self.booking_type._bf_candidate_slots(start, end)
        after = Booking.search_count([("type_id", "=", self.booking_type.id)])
        self.assertEqual(before, after, "aucune réservation ne doit être écrite")

    def test_candidate_slots_limit(self):
        start, end = self._window()
        full = self.booking_type._bf_candidate_slots(start, end)
        capped = self.booking_type._bf_candidate_slots(start, end, limit=3)
        self.assertEqual(len(capped), min(3, len(full)))
        self.assertEqual(capped, full[:len(capped)], "la limite garde les plus proches")

    def test_candidate_slots_tz_regroups(self):
        """Le fuseau demandé doit gouverner le regroupement, pas celui du serveur."""
        start, end = self._window()
        slots = self.booking_type._bf_candidate_slots(
            start, end, tz="Pacific/Auckland"
        )
        self.assertTrue(slots)
        for slot in slots:
            self.assertIsNotNone(slot.tzinfo, "les créneaux sortent avec leur fuseau")

    # -- (2) fabrique de réservation ---------------------------------------

    def test_create_booking_from_external_source(self):
        start = self._real_slot(0)
        booking = self.booking_type._bf_create_booking(
            start,
            partners=self.partner,
            vals={"bf_source": "test", "bf_source_ref": "res.partner,%d" % self.partner.id},
        )
        self.assertTrue(booking.exists())
        self.assertEqual(booking.type_id, self.booking_type)
        self.assertIn(self.partner, booking.partner_ids)
        self.assertTrue(booking.access_token, "le jeton portail doit être posé")
        self.assertEqual(booking.state, "confirmed")
        self.assertTrue(booking.meeting_id, "la confirmation crée l'événement d'agenda")

    def test_create_booking_pending(self):
        booking = self.booking_type._bf_create_booking(
            self._real_slot(1), partners=self.partner, confirm=False
        )
        self.assertNotEqual(booking.state, "confirmed")
        self.assertTrue(booking.access_token)

    # -- (3) provenance non typée ------------------------------------------

    def test_source_ref_resolves(self):
        booking = self.booking_type._bf_create_booking(
            self._real_slot(2),
            partners=self.partner,
            vals={"bf_source_ref": "res.partner,%d" % self.partner.id},
        )
        self.assertEqual(booking._bf_source_record(), self.partner)
        action = booking.action_bf_source()
        self.assertEqual(action["res_model"], "res.partner")
        self.assertEqual(action["res_id"], self.partner.id)

    def test_source_ref_tolerates_garbage(self):
        """Un satellite désinstallé ne doit jamais casser la fiche."""
        booking = self.booking_type._bf_create_booking(
            self._real_slot(3), partners=self.partner
        )
        for junk in ("", "n_importe_quoi", "modele.absent,4", "res.partner,pas_un_entier",
                     "res.partner,99999999"):
            booking.bf_source_ref = junk
            self.assertFalse(booking._bf_source_record(), junk)
            self.assertFalse(booking.action_bf_source(), junk)

    def test_no_typed_field_towards_satellites(self):
        """🔴 L'invariant qui compte : aucun champ de bf_appointment ne pointe
        vers un modèle satellite.

        Un m2o/m2m typé est résolu au chargement du registre, donc il rend le
        module visé OBLIGATOIRE — et ça ne se voit QUE sur une installation
        neuve, chez le premier locataire qui installe les rendez-vous sans le
        satellite. Un commentaire ne suffit pas à l'empêcher : ce test, oui.
        """
        installed = set(self.env)
        for model_name in ("resource.booking", "resource.booking.type"):
            for fname, field in self.env[model_name]._fields.items():
                comodel = getattr(field, "comodel_name", None)
                if not comodel:
                    continue
                self.assertIn(
                    comodel,
                    installed,
                    f"{model_name}.{fname} pointe vers « {comodel} », absent du "
                    f"registre : dépendance dure introduite.",
                )
                self.assertFalse(
                    comodel.startswith("appointment.poll"),
                    f"{model_name}.{fname} pointe vers « {comodel} » : "
                    f"bf_appointment deviendrait dépendant de son satellite.",
                )

    # -- (4) seau de limitation nommé --------------------------------------

    def test_rate_limit_check_does_not_consume(self):
        """Vérifier n'est pas consommer.

        Un contrôle de jeton doit pouvoir se répéter sans épuiser le quota :
        sinon la personne légitime qui recharge sa page se fait enfermer
        dehors alors que seuls les échecs devraient compter.
        """
        from odoo.addons.bf_appointment.controllers.main import (
            bf_rate_limit,
            bf_rate_limit_record,
        )

        key = "nonconso-%d" % self.partner.id
        for _i in range(20):
            self.assertTrue(
                bf_rate_limit("essai_nc", 2, 60, key=key, consume=False),
                "une vérification ne doit jamais épuiser le seau",
            )
        # Deux échecs inscrits : le seau se ferme alors, et seulement alors.
        bf_rate_limit_record("essai_nc", 60, key=key)
        self.assertTrue(bf_rate_limit("essai_nc", 2, 60, key=key, consume=False))
        bf_rate_limit_record("essai_nc", 60, key=key)
        self.assertFalse(
            bf_rate_limit("essai_nc", 2, 60, key=key, consume=False),
            "deux échecs atteignent le plafond",
        )

    def test_named_rate_limit_bucket(self):
        from odoo.addons.bf_appointment.controllers.main import bf_rate_limit

        key = "test-%d" % self.partner.id
        self.assertTrue(bf_rate_limit("essai", 2, 60, key=key))
        self.assertTrue(bf_rate_limit("essai", 2, 60, key=key))
        self.assertFalse(bf_rate_limit("essai", 2, 60, key=key), "3e appel refusé")
        # Un autre seau, même clé : indépendant.
        self.assertTrue(bf_rate_limit("autre_essai", 2, 60, key=key))
        # Une autre clé, même seau : indépendante.
        self.assertTrue(bf_rate_limit("essai", 2, 60, key=key + "-bis"))


    def test_unavailable_time_is_refused_before_anything_is_written(self):
        """🔴 Une heure hors disponibilités doit être refusée AVANT création.

        Vérifier après coup ne marche pas : lire `combination_id` déclenche la
        validation d'OCA, qui lève avant qu'on puisse dire quoi que ce soit, et
        l'enregistrement corrompu reste dans la transaction pour exploser plus
        loin sur une opération sans rapport.
        """
        from odoo.exceptions import UserError

        Booking = self.env["resource.booking"]
        avant = Booking.search_count([("type_id", "=", self.booking_type.id)])
        bancal = fields.Datetime.now() + timedelta(days=2, minutes=37, seconds=13)
        with self.assertRaises(UserError):
            self.booking_type._bf_create_booking(bancal, partners=self.partner)
        self.assertEqual(
            Booking.search_count([("type_id", "=", self.booking_type.id)]), avant,
            "une réservation a été écrite malgré le refus",
        )
