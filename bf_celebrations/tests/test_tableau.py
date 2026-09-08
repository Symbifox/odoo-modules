# -*- coding: utf-8 -*-
"""La surprise, la livraison, et le tableau qu'on retient plutôt qu'envoyer."""

from datetime import date, datetime, timedelta

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_celebrations")
class TestTableau(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fete = cls.env["res.users"].create({
            "name": "Frédérique Ostiguy", "login": "cel_fete@example.test",
            "email": "cel_fete@example.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.organisateur = cls.env["res.users"].create({
            "name": "Ateliers Les Deux Rives", "login": "cel_org@example.test",
            "email": "cel_org@example.test",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_celebrations.group_organizer").id,
            ])],
        })
        cls.employe_fete = cls.env["hr.employee"].create({
            "name": "Frédérique Ostiguy", "user_id": cls.fete.id,
            "work_email": "cel_fete@example.test",
        })
        cls.tableau = cls.env["bf.celebration.board"].create({
            "name": "Bonne fête Frédérique",
            "recipient_employee_id": cls.employe_fete.id,
            "organizer_id": cls.organisateur.id,
            "delivery_date": datetime.now() + timedelta(days=3),
        })

    # ------------------------------------------------------------------

    def test_la_personne_fetee_ne_voit_pas_son_tableau(self):
        """La retenue est dans la RÈGLE, pas dans l'écran.

        Une liste, un export ou une recherche universelle contourneraient un
        `invisible` de vue. Ici l'enregistrement n'existe pas pour elle.
        """
        vu_par_elle = self.env["bf.celebration.board"].with_user(
            self.fete).search([("id", "=", self.tableau.id)])
        self.assertFalse(vu_par_elle, "Le tableau doit lui être invisible.")

        vu_par_org = self.env["bf.celebration.board"].with_user(
            self.organisateur).search([("id", "=", self.tableau.id)])
        self.assertEqual(vu_par_org, self.tableau)

    def test_apres_la_livraison_elle_le_voit(self):
        self.tableau.write({"state": "delivered"})
        vu_par_elle = self.env["bf.celebration.board"].with_user(
            self.fete).search([("id", "=", self.tableau.id)])
        self.assertEqual(vu_par_elle, self.tableau)

    def test_les_mots_suivent_la_meme_retenue(self):
        self.env["bf.celebration.post"].sudo().create({
            "board_id": self.tableau.id,
            "author_name": "Un collègue",
            "body": "<p>Bonne fête !</p>",
        })
        vus = self.env["bf.celebration.post"].with_user(self.fete).search(
            [("board_id", "=", self.tableau.id)])
        self.assertFalse(vus, "Les mots ne doivent pas fuir par le modèle "
                              "des messages.")

    def test_elle_n_est_pas_abonnee_a_son_propre_tableau(self):
        abonnes = self.tableau.sudo().message_partner_ids
        self.assertNotIn(self.fete.partner_id, abonnes,
                         "Un abonné reçoit les notifications du chatter.")

    def test_un_tableau_vide_est_retenu_et_non_livre(self):
        self.tableau.write({
            "state": "open",
            "delivery_date": datetime.now() - timedelta(hours=1),
        })
        livres = self.env["bf.celebration.board"]._cron_livrer()
        self.assertEqual(livres, 0)
        self.assertEqual(
            self.tableau.state, "open",
            "Une carte blanche à son nom est pire qu'aucune carte.")
        activites = self.env["mail.activity"].search([
            ("res_model", "=", "bf.celebration.board"),
            ("res_id", "=", self.tableau.id)])
        self.assertTrue(activites, "La personne qui organise doit être "
                                   "prévenue de la retenue.")

    def test_un_tableau_signe_part(self):
        self.env["bf.celebration.post"].sudo().create({
            "board_id": self.tableau.id,
            "author_name": "Un collègue",
            "body": "<p>Bonne fête !</p>",
        })
        self.tableau.write({
            "state": "open",
            "delivery_date": datetime.now() - timedelta(hours=1),
        })
        self.env["bf.celebration.board"]._cron_livrer()
        self.assertEqual(self.tableau.state, "delivered")
        self.assertTrue(self.tableau.delivered_date)

    def test_le_jeton_se_coupe(self):
        ancien = self.tableau.sudo().access_token
        self.tableau.action_regenerer_jeton()
        self.assertNotEqual(ancien, self.tableau.sudo().access_token)
        self.assertTrue(len(self.tableau.sudo().access_token) > 20)

    def test_sans_tableau_le_bouton_refuse(self):
        occasion = self.env["bf.celebration.occasion"].sudo().create({
            "occasion_type": "birthday",
            "date": date.today() + timedelta(days=5),
            "employee_id": self.employe_fete.id,
            "allow_board": False,
        })
        action = occasion.action_creer_tableau()
        self.assertEqual(action.get("tag"), "display_notification")
        self.assertFalse(occasion.board_ids)
