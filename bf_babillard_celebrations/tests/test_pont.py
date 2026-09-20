# -*- coding: utf-8 -*-
from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPontCelebrations(TransactionCase):
    """⚠️ Éprouvé par une LIVRAISON réelle.

    La première version de ces essais vérifiait le module d'origine de
    `_livrer` (`__module__`). C'est un contrôle de forme : il passe même si le
    crochet ne pose aucune carte, et il rougit pour une raison sans rapport dès
    qu'un autre module surcharge la même méthode après nous.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employe = cls.env["hr.employee"].create({
            "name": "Personne fêtée", "work_email": "fetee@exemple.test"})

    def _tableau(self, nom="Bonne fête"):
        return self.env["bf.celebration.board"].create({
            "name": nom,
            "recipient_employee_id": self.employe.id,
            "delivery_date": fields.Date.context_today(self.env.user),
            "state": "open",
        })

    def _carte(self, tableau):
        return self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "bf.celebration.board"),
            ("source_res_id", "=", tableau.id)])

    def test_une_carte_livree_paraît_au_babillard(self):
        tableau = self._tableau()
        tableau._livrer()
        self.assertEqual(tableau.state, "delivered")
        carte = self._carte(tableau)
        self.assertEqual(len(carte), 1)
        self.assertEqual(carte.type_publication, "celebration")
        self.assertIn("Personne fêtée", carte.name)
        self.assertEqual(carte.state, "publie")

    def test_la_carte_porte_le_visage_de_qui_est_fete(self):
        """Une célébration sans visage se lit comme une ligne de journal."""
        tableau = self._tableau()
        tableau._livrer()
        self.assertEqual(self._carte(tableau).personne_id.id, self.employe.id)

    def test_un_tableau_non_livre_ne_paraît_pas(self):
        """La garde ne se prouve qu'en APPELANT la livraison.

        🔴 La première version créait un tableau et vérifiait l'absence de carte
        sans jamais appeler `_livrer` : la garde n'était pas atteinte, et une
        mutation qui la supprime passait au vert.
        """
        tableau = self._tableau("Carte encore en brouillon")
        tableau.write({"state": "draft"})
        tableau._livrer()
        self.assertEqual(tableau.state, "draft", "rien ne devait partir")
        self.assertFalse(self._carte(tableau))

    def test_deux_livraisons_ne_font_pas_deux_cartes(self):
        tableau = self._tableau()
        tableau._livrer()
        tableau._livrer()
        self.assertEqual(len(self._carte(tableau)), 1)

    def test_le_babillard_ne_publie_pas_la_date_de_naissance(self):
        """Le pont ne publie que ce que les célébrations rendent déjà public."""
        tableau = self._tableau()
        tableau._livrer()
        carte = self._carte(tableau)
        self.assertNotIn("naissance", (carte.corps_html or "").lower())
        self.assertFalse(carte.sudo().source_res_id != tableau.id)
