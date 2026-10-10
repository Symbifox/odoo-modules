"""Nom neutre et abonnés fermés : les deux fuites du cœur d'Odoo 18 sur les fiches santé.

En mode debug, l'erreur d'accès d'une règle nommait la fiche refusée ; ``mail.followers``
disait à tout interne qui a des fiches santé. Chaque porte est prouvée à part, par son
propre message. Données inventées.
"""
from unittest.mock import MagicMock, patch

from odoo.addons.base.models import res_users
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

MEDICAMENT = "MEDICAMENT-SECRET-XYZ"
CONDITION = "CONDITION-SECRETE"
ALIMENT = "ALIMENT-SECRET"


@tagged("post_install", "-at_install")
class TestNomsPrives(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = "base.group_user,bf_health.group_health_user"
        cls.alice = new_test_user(cls.env, login="np_alice", groups=groupes)
        cls.carol = new_test_user(cls.env, login="np_carol", groups=groupes)
        env_alice = cls.env(user=cls.alice)
        cls.med = env_alice["health.medication"].create({"name": MEDICAMENT})
        cls.condition = env_alice["health.condition"].create({"name": CONDITION})
        cls.aliment = env_alice["health.food"].create({"name": ALIMENT})

    def test_en_debug_l_erreur_d_acces_ne_nomme_pas_la_fiche(self):
        # Le mode debug : membre de base.group_no_one ET session en debug.
        self.carol.sudo().write({"groups_id": [(4, self.env.ref("base.group_no_one").id)]})
        session = MagicMock()
        session.session.debug = "1"
        for fiche, secret in ((self.med, MEDICAMENT), (self.condition, CONDITION), (self.aliment, ALIMENT)):
            with patch.object(res_users, "request", session), self.assertRaises(AccessError) as refus:
                fiche.with_user(self.carol).read(["id"])
            message = str(refus.exception)
            self.assertIn("%s: %d" % (fiche._name, fiche.id), message,
                          "précondition : en debug, le message liste les fiches refusées")
            self.assertNotIn(secret, message)
            self.assertIn("Fiche santé privée", message)

    def test_la_personne_et_le_superutilisateur_lisent_le_vrai_nom(self):
        self.assertEqual(self.med.with_user(self.alice).display_name, MEDICAMENT)
        self.assertEqual(self.med.with_user(self.alice).sudo().display_name, MEDICAMENT)
        self.assertEqual(self.med.sudo().display_name, MEDICAMENT)

    def test_les_abonnes_d_une_fiche_sante_ne_se_cherchent_pas(self):
        Abonne = self.env["mail.followers"]
        lignes = Abonne.sudo().search([("res_model", "=", "health.medication"), ("res_id", "=", self.med.id)])
        self.assertTrue(lignes, "précondition : Alice suit son médicament")
        self.assertFalse(Abonne.with_user(self.carol).search([("res_model", "=", "health.medication")]))
        self.assertFalse(Abonne.with_user(self.carol).read_group(
            [("res_model", "in", ("health.medication", "health.condition"))], ["res_id:count"], ["partner_id"]))
        self.assertEqual(Abonne.with_user(self.alice).search(
            [("res_model", "=", "health.medication"), ("res_id", "=", self.med.id)]), lignes)
        with self.assertRaises(AccessError):
            lignes.with_user(self.carol).read(["res_id", "partner_id"])

    def test_les_abonnes_d_une_fiche_sante_ne_se_lisent_pas_par_id(self):
        lignes = self.env["mail.followers"].sudo().search(
            [("res_model", "=", "health.medication"), ("res_id", "=", self.med.id)])
        with self.assertRaises(AccessError) as refus:
            lignes.with_user(self.carol).check_access("read")
        self.assertIn("abonnés d'une fiche santé", str(refus.exception))
        self.assertFalse(lignes.with_user(self.carol).has_access("read"))
        self.assertTrue(lignes.with_user(self.alice).has_access("read"))
