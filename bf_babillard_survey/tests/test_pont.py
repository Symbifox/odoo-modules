# -*- coding: utf-8 -*-
"""Essais du pont Babillard ↔ Sondages Odoo."""
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPontSondages(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.g_interne = cls.env.ref("base.group_user")
        cls.g_redaction = cls.env.ref("bf_babillard.group_babillard_redacteur")
        cls.u_redactrice = Users.create({
            "name": "Rédactrice du pont", "login": "pont_survey_redactrice",
            "email": "pont.survey@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id, cls.g_redaction.id,
                                  cls.env.ref("survey.group_survey_user").id])]})
        cls.u_simple = Users.create({
            "name": "Personne ordinaire", "login": "pont_survey_simple",
            "email": "pont.survey.simple@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id,
                                  cls.env.ref("survey.group_survey_user").id])]})

    def _sondage(self, **kw):
        vals = {"title": "Ce qu'on garde du 5 à 7", "access_mode": "public"}
        vals.update(kw)
        return self.env["survey.survey"].with_user(self.u_redactrice).create(vals)

    def _carte(self, sondage):
        return self.env["bf.babillard.post"].sudo().with_context(
            active_test=False).search([
                ("source_model", "=", "survey.survey"),
                ("source_res_id", "=", sondage.id)])

    def test_annonce_pose_une_carte_avec_le_lien(self):
        sondage = self._sondage()
        sondage.with_user(self.u_redactrice).action_annoncer_au_babillard()
        carte = self._carte(sondage)
        self.assertEqual(len(carte), 1)
        self.assertEqual(carte.name, sondage.title)
        self.assertEqual(carte.state, "publie")
        self.assertIn(sondage.access_token, carte.corps_html)

    def test_annoncer_deux_fois_ne_pose_quune_carte(self):
        sondage = self._sondage()
        sondage.with_user(self.u_redactrice).action_annoncer_au_babillard()
        sondage.with_user(self.u_redactrice).action_annoncer_au_babillard()
        self.assertEqual(len(self._carte(sondage)), 1)

    def test_sondage_sur_invitation_refuse(self):
        """🔴 Le lien de départ d'un sondage à jeton ne mène nulle part sans
        l'invitation nominative : une carte lue par toute la maison ne peut pas
        le porter."""
        sondage = self._sondage(access_mode="token")
        with self.assertRaises(UserError):
            sondage.with_user(self.u_redactrice).action_annoncer_au_babillard()
        self.assertFalse(self._carte(sondage))

    def test_sondage_archive_refuse(self):
        sondage = self._sondage()
        sondage.sudo().active = False
        with self.assertRaises(UserError):
            sondage.with_user(self.u_redactrice).action_annoncer_au_babillard()

    def test_sans_la_redaction_personne_ne_publie(self):
        """🔴 La méthode est publique : le `groups=` du bouton ne garde que
        l'écran."""
        sondage = self._sondage()
        with self.assertRaises(AccessError):
            sondage.with_user(self.u_simple).action_annoncer_au_babillard()
        self.assertFalse(self._carte(sondage))
