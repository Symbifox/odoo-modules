# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPontBadges(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.badge = cls.env["gamification.badge"].create({"name": "Coup de main"})
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.qui = Users.create({"name": "Qui remet", "login": "babillard_badge_qui",
                                "email": "qui@exemple.test",
                                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])]})
        cls.a_qui = Users.create({"name": "Qui reçoit", "login": "babillard_badge_recoit",
                                  "email": "recoit@exemple.test",
                                  "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])]})

    def test_badge_remis_par_une_personne_devient_une_carte(self):
        ligne = self.env["gamification.badge.user"].create({
            "badge_id": self.badge.id, "user_id": self.a_qui.id,
            "sender_id": self.qui.id, "comment": "Merci pour le quart de nuit."})
        carte = self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "gamification.badge.user"),
            ("source_res_id", "=", ligne.id)])
        self.assertEqual(len(carte), 1)
        self.assertIn("Qui reçoit", carte.name)
        self.assertIn("Qui remet", carte.name)
        self.assertIn("quart de nuit", carte.corps_html)
        self.assertEqual(carte.type_publication, "reconnaissance")

    def test_badge_de_machine_ne_publie_rien(self):
        """Un badge sans personne qui remet vient d'un défi : ce n'est pas une
        reconnaissance entre collègues."""
        ligne = self.env["gamification.badge.user"].create({
            "badge_id": self.badge.id, "user_id": self.a_qui.id})
        self.assertFalse(self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "gamification.badge.user"),
            ("source_res_id", "=", ligne.id)]))

    def test_le_titre_est_dans_la_langue_de_la_maison(self):
        """🔴 Une carte écrite dans la langue de qui déclenche arrive en anglais
        sur un babillard français dès qu'un compte technique passe par là."""
        self.env.company.partner_id.lang = "fr_CA"
        self.badge.with_context(lang="fr_CA").name = "Coup de main"
        self.badge.with_context(lang="en_US").name = "Helping hand"
        ligne = self.env["gamification.badge.user"].with_context(lang="en_US").create({
            "badge_id": self.badge.id, "user_id": self.a_qui.id,
            "sender_id": self.qui.id})
        carte = self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "gamification.badge.user"),
            ("source_res_id", "=", ligne.id)])
        self.assertIn("Coup de main", carte.name)
        self.assertNotIn("Helping hand", carte.name)
