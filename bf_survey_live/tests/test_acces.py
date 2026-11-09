# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import BfLiveCommon


@tagged("post_install", "-at_install")
class TestAcces(BfLiveCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Usagers = cls.env["res.users"].with_context(
            no_reset_password=True, mail_create_nolog=True)
        groupe_interne = cls.env.ref("base.group_user")
        groupe_sondage = cls.env.ref("survey.group_survey_user")
        cls.hote = Usagers.create({
            "name": "Hôte de séance",
            "login": "hote_bf_live",
            "groups_id": [(6, 0, [groupe_interne.id, groupe_sondage.id])],
        })
        cls.autre_hote = Usagers.create({
            "name": "Autre hôte",
            "login": "autre_hote_bf_live",
            "groups_id": [(6, 0, [groupe_interne.id, groupe_sondage.id])],
        })
        cls.passant = Usagers.create({
            "name": "Passant",
            "login": "passant_bf_live",
            "groups_id": [(6, 0, [groupe_interne.id])],
        })
        cls.equipe = cls.env["bf.live.team"].create({
            "name": "Bleue", "survey_id": cls.sondage.id, "color_key": "bleue"})

    def test_le_groupe_interne_ne_voit_pas_les_equipes(self):
        self.assertFalse(self.passant.has_group("survey.group_survey_user"))
        with mute_logger("odoo.addons.base.models.ir_model"):
            with self.assertRaises(AccessError):
                self.equipe.with_user(self.passant).read(["name"])

    def test_lhote_lit_ecrit_et_cree(self):
        equipe = self.equipe.with_user(self.hote)
        self.assertEqual(equipe.read(["name"])[0]["name"], "Bleue")
        equipe.write({"name": "Bleu ciel"})
        nouvelle = self.env["bf.live.team"].with_user(self.hote).create({
            "name": "Orange", "survey_id": self.sondage.id, "color_key": "orange"})
        self.assertTrue(nouvelle.exists())
        nouvelle.unlink()

    def test_un_sondage_reserve_cache_ses_equipes(self):
        """La règle recopie celle du cœur pour les questions : les équipes
        suivent leur sondage, et un sondage réservé à quelqu'un d'autre ne
        montre pas les siennes."""
        self.sondage.restrict_user_ids = [(6, 0, [self.autre_hote.id])]
        trouvees = self.env["bf.live.team"].with_user(self.hote).search([
            ("survey_id", "=", self.sondage.id)])
        self.assertFalse(trouvees)
        vues = self.env["bf.live.team"].with_user(self.autre_hote).search([
            ("survey_id", "=", self.sondage.id)])
        self.assertEqual(vues, self.equipe)

    def test_le_tirage_demande_le_groupe_sondages(self):
        self.participant("Ana")
        self.sondage.bf_live_team_target = 2
        with self.assertRaises(AccessError):
            self.sondage.with_user(self.passant).action_bf_draw_teams()

    def test_leffacement_dun_sondage_emporte_ses_equipes(self):
        """⚠️ La cascade se fait dans la base : le modèle ne la voit pas passer."""
        identifiant = self.equipe.id
        self.sondage.unlink()
        self.assertFalse(self.env["bf.live.team"].browse(identifiant).exists())
