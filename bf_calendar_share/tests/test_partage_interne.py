# -*- coding: utf-8 -*-
"""Partage interne de l'agenda par niveau (lot 4)."""

from datetime import datetime

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_calendar_share")
class TestPartageInterne(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       no_mail_to_attendees=True, skip_nc_sync=True))
        cls.proprio = new_test_user(cls.env, login="proprio_n232s", groups="base.group_user")
        cls.collegue = new_test_user(cls.env, login="collegue_n232s", groups="base.group_user")
        cls.tiers = new_test_user(cls.env, login="tiers_n232s", groups="base.group_user")
        cls.robot = new_test_user(cls.env, login="robot_n232s",
                                  groups="base.group_user,bf_calendar_share.group_calendar_see_all")
        cls.Share = cls.env["bf.calendar.share"]
        cls.rencontre = cls.env["calendar.event"].with_user(cls.proprio).create({
            "name": "Audit chez Client X",
            "location": "Bureau du client",
            "description": "Ordre du jour confidentiel",
            "start": datetime(2026, 10, 21, 14, 0), "stop": datetime(2026, 10, 21, 15, 0),
            "partner_ids": [(6, 0, (cls.proprio.partner_id | cls.tiers.partner_id).ids)],
        })

    def _lu_par(self, user, champs=("name", "location", "description", "partner_ids", "start")):
        self.env.invalidate_all()
        return self.rencontre.with_user(user).read(list(champs))[0]

    def _partager(self, avec, niveau):
        return self.Share.with_user(self.proprio).create({"grantee_id": avec.id, "level": niveau})

    # -- défaut : rien ne change ----------------------------------------------

    def test_defaut_tous_les_details_comme_le_coeur(self):
        self.assertEqual(self.proprio.bf_calendar_share_default, "details")
        vu = self._lu_par(self.collegue)
        self.assertEqual(vu["name"], "Audit chez Client X")
        self.assertEqual(vu["location"], "Bureau du client")

    # -- niveaux --------------------------------------------------------------

    def test_disponibilites_seulement(self):
        self._partager(self.collegue, "freebusy")
        vu = self._lu_par(self.collegue)
        self.assertNotEqual(vu["name"], "Audit chez Client X")
        self.assertFalse(vu["location"])
        self.assertFalse(vu["description"])
        self.assertFalse(vu["partner_ids"])
        self.assertEqual(vu["start"], datetime(2026, 10, 21, 14, 0))
        self.env.invalidate_all()
        self.assertNotIn("Client X", self.rencontre.with_user(self.collegue).display_name)

    def test_titres_et_lieux(self):
        self._partager(self.collegue, "titles")
        vu = self._lu_par(self.collegue)
        self.assertEqual(vu["name"], "Audit chez Client X")
        self.assertEqual(vu["location"], "Bureau du client")
        self.assertFalse(vu["description"])
        self.assertFalse(vu["partner_ids"])

    def test_defaut_restreint_partage_explicite_plus_large(self):
        self.proprio.with_user(self.proprio).write({"bf_calendar_share_default": "freebusy"})
        self._partager(self.collegue, "details")
        self.assertIn("Ordre du jour confidentiel", self._lu_par(self.collegue)["description"])
        self.assertNotEqual(self._lu_par(self.tiers.copy({"login": "autre_n232s"}))["name"],
                            "Audit chez Client X")

    def test_invite_voit_toujours(self):
        self.proprio.with_user(self.proprio).write({"bf_calendar_share_default": "freebusy"})
        self.assertIn("Ordre du jour confidentiel", self._lu_par(self.tiers)["description"])

    def test_organisateur_voit_tout(self):
        self.proprio.with_user(self.proprio).write({"bf_calendar_share_default": "freebusy"})
        self.assertEqual(self._lu_par(self.proprio)["name"], "Audit chez Client X")

    def test_prive_reste_occupe_meme_au_niveau_modifier(self):
        self.rencontre.with_user(self.proprio).write({"privacy": "private"})
        self._partager(self.collegue, "edit")
        self.assertNotEqual(self._lu_par(self.collegue)["name"], "Audit chez Client X")
        self.env.invalidate_all()
        self.assertFalse(self.rencontre.with_user(self.collegue).user_can_edit)

    def test_compte_technique_voit_tout(self):
        self.proprio.with_user(self.proprio).write({"bf_calendar_share_default": "freebusy"})
        self.assertIn("Ordre du jour confidentiel", self._lu_par(self.robot)["description"])

    # -- écrire ---------------------------------------------------------------

    def test_modifier_donne_la_main(self):
        self.env.invalidate_all()
        self.assertFalse(self.rencontre.with_user(self.collegue).user_can_edit)
        self._partager(self.collegue, "edit")
        self.env.invalidate_all()
        self.assertTrue(self.rencontre.with_user(self.collegue).user_can_edit)
        self.rencontre.with_user(self.collegue).write({"location": "Salle B"})
        self.assertEqual(self.rencontre.location, "Salle B")

    def test_niveau_restreint_n_ecrit_pas_a_l_aveugle(self):
        self._partager(self.collegue, "freebusy")
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.rencontre.with_user(self.collegue).write({"name": "Écrasé"})
        with self.assertRaises(AccessError):
            self.rencontre.with_user(self.collegue).unlink()

    # -- regrouper ------------------------------------------------------------

    def test_regrouper_par_participants_ecarte_l_agenda_restreint(self):
        self._partager(self.collegue, "freebusy")
        groupes = self.env["calendar.event"].with_user(self.collegue).read_group(
            [("id", "=", self.rencontre.id)], ["partner_ids"], ["partner_ids"])
        self.assertFalse(groupes)
        groupes = self.env["calendar.event"].with_user(self.tiers).read_group(
            [("id", "=", self.rencontre.id)], ["partner_ids"], ["partner_ids"])
        self.assertTrue(groupes)

    # -- les partages eux-mêmes ------------------------------------------------

    def test_pas_de_partage_avec_soi_meme(self):
        with self.assertRaises(ValidationError):
            self._partager(self.proprio, "edit")

    def test_je_ne_gere_que_mon_agenda(self):
        partage = self._partager(self.collegue, "titles")
        with self.assertRaises(AccessError):
            partage.with_user(self.collegue).write({"level": "edit"})
        with self.assertRaises(AccessError):
            self.Share.with_user(self.collegue).create({
                "owner_id": self.proprio.id, "grantee_id": self.tiers.id, "level": "edit"})
        self.assertEqual(partage.with_user(self.collegue).level, "titles")
        self.assertFalse(partage.with_user(self.tiers).search([("id", "=", partage.id)]))

    def test_mon_profil_s_ouvre_et_s_ecrit(self):
        self.env.invalidate_all()
        moi = self.collegue.with_user(self.collegue)
        moi.read(["bf_calendar_share_default"])
        moi.write({"bf_calendar_share_default": "titles"})
        self.assertEqual(self.collegue.bf_calendar_share_default, "titles")
