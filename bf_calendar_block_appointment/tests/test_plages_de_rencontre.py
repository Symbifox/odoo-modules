# -*- coding: utf-8 -*-
"""Les plages de rencontre ouvrent la prise de rendez-vous."""

from datetime import datetime

import pytz

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged

UTC = pytz.utc


def _aware(*args):
    return UTC.localize(datetime(*args))


@tagged("post_install", "-at_install", "bf_calendar_block_appointment")
class TestPlagesDeRencontre(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC",
                                       no_mail_to_attendees=True, skip_nc_sync=True))
        cls.lundis = cls.env["resource.calendar"].create({
            "name": "Lundis 8-17", "tz": "UTC",
            "attendance_ids": [Command.create({"name": "Lundi", "dayofweek": "0", "hour_from": 8,
                                               "hour_to": 17, "day_period": "morning"})]})
        cls.proprio = new_test_user(cls.env, login="proprio_rdv", groups="base.group_user", tz="UTC")
        cls.ressource = cls.env["resource.resource"].create({
            "name": "Proprio", "resource_type": "user", "user_id": cls.proprio.id,
            "calendar_id": cls.lundis.id, "tz": "UTC"})
        combinaison = cls.env["resource.booking.combination"].create(
            {"resource_ids": [Command.set(cls.ressource.ids)]})
        cls.type_rdv = cls.env["resource.booking.type"].create({
            "name": "Rencontre client", "resource_calendar_id": cls.lundis.id,
            "combination_rel_ids": [Command.create({"sequence": 1, "combination_id": combinaison.id})]})
        cls.plage = cls.env.ref("bf_calendar_block.block_meeting")
        cls.prod = cls.env.ref("bf_calendar_block.block_production")

    def _intervalles(self):
        reservation = self.env["resource.booking"].new({"type_id": self.type_rdv.id})
        return list(reservation._get_intervals(_aware(2026, 10, 24, 0, 0), _aware(2026, 10, 25, 0, 0)))

    def _evenement(self, debut, fin, **vals):
        # Posé par la personne elle-même, comme dans sa grille.
        vals = dict({
            "name": "x", "start": debut, "stop": fin, "user_id": self.proprio.id,
            "partner_ids": [(6, 0, self.proprio.partner_id.ids)]}, **vals)
        auteur = self.env["res.users"].browse(vals["user_id"])
        return self.env["calendar.event"].with_user(auteur).create(vals)

    def test_samedi_ferme_sans_plage(self):
        self.assertEqual(self._intervalles(), [])

    def test_plage_de_rencontre_ouvre_le_samedi(self):
        self._evenement(datetime(2026, 10, 24, 10, 0), datetime(2026, 10, 24, 12, 0),
                        bf_block_type_id=self.plage.id)
        ouverts = [(d, f) for d, f, _r in self._intervalles()]
        self.assertEqual(ouverts, [(_aware(2026, 10, 24, 10, 0), _aware(2026, 10, 24, 12, 0))])

    def test_un_evenement_occupe_referme_une_partie(self):
        self._evenement(datetime(2026, 10, 24, 10, 0), datetime(2026, 10, 24, 12, 0),
                        bf_block_type_id=self.plage.id)
        self._evenement(datetime(2026, 10, 24, 11, 0), datetime(2026, 10, 24, 11, 30), show_as="busy")
        ouverts = [(d, f) for d, f, _r in self._intervalles()]
        self.assertEqual(ouverts, [(_aware(2026, 10, 24, 10, 0), _aware(2026, 10, 24, 11, 0)),
                                   (_aware(2026, 10, 24, 11, 30), _aware(2026, 10, 24, 12, 0))])

    def test_autre_type_n_ouvre_rien(self):
        self._evenement(datetime(2026, 10, 24, 10, 0), datetime(2026, 10, 24, 12, 0),
                        bf_block_type_id=self.prod.id)
        self.assertEqual(self._intervalles(), [])

    def test_plage_toujours_libre(self):
        ev = self._evenement(datetime(2026, 10, 24, 10, 0), datetime(2026, 10, 24, 12, 0),
                             show_as="busy", categ_ids=[(6, 0, self.plage.ids)])
        self.assertEqual(ev.show_as, "free")

    def test_hors_reservation_ce_n_est_pas_une_heure_de_travail(self):
        self._evenement(datetime(2026, 10, 24, 10, 0), datetime(2026, 10, 24, 12, 0),
                        bf_block_type_id=self.plage.id)
        heures = self.lundis._attendance_intervals_batch(
            _aware(2026, 10, 24, 0, 0), _aware(2026, 10, 25, 0, 0), self.ressource)[self.ressource.id]
        self.assertEqual(list(heures), [])

    def test_plage_d_un_autre_n_ouvre_pas_mon_agenda(self):
        autre = new_test_user(self.env, login="autre_rdv", groups="base.group_user", tz="UTC")
        self.env["calendar.event"].with_user(autre).create({
            "name": "x", "start": datetime(2026, 10, 24, 10, 0), "stop": datetime(2026, 10, 24, 12, 0),
            "user_id": autre.id, "bf_block_type_id": self.plage.id})
        self.assertEqual(self._intervalles(), [])

    def test_creneaux_offerts_le_samedi(self):
        self._evenement(datetime(2026, 10, 24, 10, 0), datetime(2026, 10, 24, 12, 0),
                        bf_block_type_id=self.plage.id)
        reservation = self.env["resource.booking"].new({"type_id": self.type_rdv.id})
        creneaux = reservation._get_available_slots(_aware(2026, 10, 24, 0, 0), _aware(2026, 10, 25, 0, 0))
        departs = [d for jour in creneaux.values() for d in jour] if isinstance(creneaux, dict) else list(creneaux)
        self.assertTrue(departs, creneaux)
        self.assertTrue(all(_aware(2026, 10, 24, 10, 0) <= d.astimezone(UTC) < _aware(2026, 10, 24, 12, 0)
                            for d in departs), departs)

    # -- cas limites -----------------------------------------------------------

    def test_m8_plage_journee_entiere_ouvre_sa_journee_locale(self):
        self._evenement(datetime(2026, 10, 24, 8, 0), datetime(2026, 10, 24, 18, 0),
                        allday=True, bf_block_type_id=self.plage.id)
        ouverts = [(d, f) for d, f, _r in self._intervalles()]
        self.assertEqual(ouverts, [(_aware(2026, 10, 24, 0, 0), _aware(2026, 10, 25, 0, 0))])

    def test_m9_pas_de_creneau_fantome_entre_combinaisons(self):
        samedis = self.env["resource.calendar"].create({
            "name": "Samedis", "tz": "UTC",
            "attendance_ids": [Command.create({"name": "Samedi", "dayofweek": "5", "hour_from": 8,
                                               "hour_to": 17, "day_period": "morning"})]})
        b = new_test_user(self.env, login="b_rdv", groups="base.group_user", tz="UTC")
        res_b = self.env["resource.resource"].create({
            "name": "B", "resource_type": "user", "user_id": b.id, "calendar_id": samedis.id, "tz": "UTC"})
        combo_b = self.env["resource.booking.combination"].create({"resource_ids": [Command.set(res_b.ids)]})
        self.type_rdv.combination_rel_ids = [Command.create({"sequence": 2, "combination_id": combo_b.id})]
        # A (proprio) : plage de 10 h à 12 h, occupé de 10 h à 11 h. B : travaille le samedi
        # mais le type est en semaine. 10 h ne doit être offert par personne.
        self._evenement(datetime(2026, 10, 24, 10, 0), datetime(2026, 10, 24, 12, 0),
                        bf_block_type_id=self.plage.id)
        self._evenement(datetime(2026, 10, 24, 10, 0), datetime(2026, 10, 24, 11, 0), show_as="busy")
        ouverts = [(d, f) for d, f, _r in self._intervalles()]
        self.assertEqual(ouverts, [(_aware(2026, 10, 24, 11, 0), _aware(2026, 10, 24, 12, 0))])

    def test_m2_journee_entiere_occupee_ferme_la_journee_locale(self):
        toronto = new_test_user(self.env, login="tor_rdv", groups="base.group_user", tz="America/Toronto")
        ressource = self.env["resource.resource"].create({
            "name": "T", "resource_type": "user", "user_id": toronto.id,
            "calendar_id": self.lundis.id, "tz": "America/Toronto"})
        self.env["calendar.event"].create({
            "name": "Congé", "allday": True, "start": datetime(2026, 10, 26, 8, 0),
            "stop": datetime(2026, 10, 26, 18, 0), "user_id": toronto.id, "show_as": "busy",
            "partner_ids": [(6, 0, toronto.partner_id.ids)]})
        occupes = list(self.env["resource.calendar"]._calendar_event_busy_intervals(
            _aware(2026, 10, 26, 18, 0), _aware(2026, 10, 26, 23, 0), ressource, -1))
        # 15 h à Toronto = 19 h UTC : fermé.
        self.assertTrue(any(d <= _aware(2026, 10, 26, 19, 0) < f for d, f, _r in occupes), occupes)

    # -- 18.0.1.0.1 -----------------------------------------------------------

    def test_plage_posee_par_un_collegue_n_ouvre_rien_avant_acceptation(self):
        collegue = new_test_user(self.env, login="collegue_rdv", groups="base.group_user", tz="UTC")
        ev = self.env["calendar.event"].with_user(collegue).create({
            "name": "x", "start": datetime(2026, 10, 24, 10, 0), "stop": datetime(2026, 10, 24, 12, 0),
            "user_id": self.proprio.id, "partner_ids": [(6, 0, self.proprio.partner_id.ids)],
            "bf_block_type_id": self.plage.id})
        self.assertEqual(self._intervalles(), [])
        ev.attendee_ids.filtered(lambda a: a.partner_id == self.proprio.partner_id).with_user(
            self.proprio).do_accept()
        ouverts = [(d, f) for d, f, _r in self._intervalles()]
        self.assertEqual(ouverts, [(_aware(2026, 10, 24, 10, 0), _aware(2026, 10, 24, 12, 0))])
