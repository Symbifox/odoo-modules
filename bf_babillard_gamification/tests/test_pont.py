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

    def test_la_carte_est_signee_par_qui_remet(self):
        """🔴 La carte portait le compte qui écrit, souvent un compte technique :
        « Administrateur » félicitait à la place de la personne."""
        ligne = self.env["gamification.badge.user"].create({
            "badge_id": self.badge.id, "user_id": self.a_qui.id,
            "sender_id": self.qui.id})
        carte = self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "gamification.badge.user"),
            ("source_res_id", "=", ligne.id)])
        self.assertEqual(carte.auteur_user_id, self.qui)
        self.assertNotEqual(carte.auteur_user_id, self.env.user)

    def test_la_carte_porte_le_visage_de_qui_est_reconnu(self):
        employe = self.env["hr.employee"].create({
            "name": "Qui reçoit", "user_id": self.a_qui.id})
        ligne = self.env["gamification.badge.user"].create({
            "badge_id": self.badge.id, "user_id": self.a_qui.id,
            "sender_id": self.qui.id})
        carte = self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "gamification.badge.user"),
            ("source_res_id", "=", ligne.id)])
        self.assertEqual(carte.personne_id.id, employe.id)

    def test_la_carte_prend_l_image_du_badge(self):
        # Un PNG d'un pixel, transparent : le contenu ne compte pas, sa reprise si.
        image = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42m"
                 "NkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
        self.badge.image_1920 = image
        ligne = self.env["gamification.badge.user"].create({
            "badge_id": self.badge.id, "user_id": self.a_qui.id,
            "sender_id": self.qui.id})
        carte = self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "gamification.badge.user"),
            ("source_res_id", "=", ligne.id)])
        self.assertTrue(carte.image_couverture)

    def test_l_image_survit_a_un_contexte_d_ecran(self):
        """🔴 `bin_size` fait rendre « 12.34 Kb » à la place du binaire.

        Un badge remis depuis un formulaire arrive avec ce drapeau au contexte :
        la carte portait alors une chaîne de taille en guise d'image, et rien
        ne le disait.
        """
        image = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42m"
                 "NkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
        self.badge.image_1920 = image
        ligne = self.env["gamification.badge.user"].with_context(
            bin_size=True).create({
                "badge_id": self.badge.id, "user_id": self.a_qui.id,
                "sender_id": self.qui.id})
        carte = self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "gamification.badge.user"),
            ("source_res_id", "=", ligne.id)])
        couverture = carte.with_context(bin_size=False).image_couverture
        self.assertTrue(couverture)
        self.assertNotIn(b"Kb", couverture if isinstance(couverture, bytes)
                         else couverture.encode())

    def test_le_visage_se_trouve_meme_depuis_une_autre_societe(self):
        """🔴 `user_id.employee_id` cherche l'employé dans `env.company`.

        C'est la société de QUI REMET, pas de qui reçoit. Dans le cas même que
        le pont prend soin de gérer pour la société de la carte, le visage
        disparaissait en silence.
        """
        autre = self.env["res.company"].create({"name": "Autre société"})
        self.a_qui.write({"company_ids": [(4, autre.id)], "company_id": autre.id})
        employe = self.env["hr.employee"].create({
            "name": "Qui reçoit", "user_id": self.a_qui.id,
            "company_id": autre.id})
        ligne = self.env["gamification.badge.user"].with_company(
            self.env.ref("base.main_company")).create({
                "badge_id": self.badge.id, "user_id": self.a_qui.id,
                "sender_id": self.qui.id})
        carte = self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "gamification.badge.user"),
            ("source_res_id", "=", ligne.id)])
        self.assertEqual(carte.personne_id.id, employe.id)

    def test_un_badge_sans_image_ne_pose_pas_de_couverture(self):
        ligne = self.env["gamification.badge.user"].create({
            "badge_id": self.badge.id, "user_id": self.a_qui.id,
            "sender_id": self.qui.id})
        carte = self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "gamification.badge.user"),
            ("source_res_id", "=", ligne.id)])
        self.assertFalse(carte.image_couverture)

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
