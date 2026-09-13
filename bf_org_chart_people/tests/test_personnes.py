# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_org_chart")
class TestOrganigrammePersonnes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        P = cls.env["res.partner"]
        cls.societe = P.create({"name": "Souci Plastique inc.", "is_company": True,
                                "street": "99 boulevard de la Filiale"})
        cls.pdg = P.create({"name": "Jeanne Tremblay", "function": "Présidente",
                            "parent_id": cls.societe.id})
        cls.dti = P.create({"name": "Marc Bélisle", "function": "Directeur TI",
                            "parent_id": cls.societe.id, "manager_id": cls.pdg.id})
        cls.analyste = P.create({"name": "Li Wei", "function": "Analyste",
                                 "parent_id": cls.societe.id,
                                 "manager_id": cls.dti.id})

    def test_le_champ_superieur_ne_touche_pas_a_l_adresse(self):
        """La raison d'être du champ : `parent_id` écrase l'adresse, pas lui."""
        holding = self.env["res.partner"].create({
            "name": "Groupe Souci", "is_company": True,
            "street": "1 rue du Holding", "city": "Québec"})
        patron = self.env["res.partner"].create({
            "name": "Robert Gagnon", "parent_id": holding.id})
        avant = (self.dti.street, self.dti.city)
        self.dti.manager_id = patron
        self.assertEqual((self.dti.street, self.dti.city), avant,
                         "le lien hiérarchique ne doit pas déménager la fiche")

    def test_boucle_hierarchique_refusee(self):
        with self.assertRaises(ValidationError):
            self.pdg.manager_id = self.analyste

    def test_relever_de_soi_meme_refuse(self):
        with self.assertRaises(ValidationError):
            self.pdg.manager_id = self.pdg

    def test_compte_des_subordonnes(self):
        self.assertEqual(self.pdg.subordinate_count, 1)
        self.assertEqual(self.dti.subordinate_count, 1)
        self.assertEqual(self.analyste.subordinate_count, 0)

    def test_la_carte_porte_les_trois_personnes(self):
        carte = self.societe._org_chart_carte("personnes")
        cles = {b.cle for b in carte.boites}
        self.assertEqual(cles, {"p%s" % p.id for p in
                                (self.pdg, self.dti, self.analyste)})
        self.assertEqual(len(carte.aretes), 2)

    def test_le_superieur_externe_est_montre_et_teinte(self):
        autre = self.env["res.partner"].create({"name": "Groupe Souci", "is_company": True})
        dti_groupe = self.env["res.partner"].create({
            "name": "Aïcha Benali", "function": "DTI du groupe", "parent_id": autre.id})
        self.dti.manager_id = dti_groupe
        carte = self.societe._org_chart_carte("personnes")
        boite = [b for b in carte.boites if b.cle == "p%s" % dti_groupe.id]
        self.assertTrue(boite, "le supérieur d'une autre société doit apparaître")
        self.assertEqual(boite[0].teinte, "ambre")
        self.assertEqual(boite[0].note, "Groupe Souci")

    def test_le_plan_se_calcule_depuis_la_carte(self):
        plan = self.societe._org_chart_plan("personnes")
        self.assertEqual(plan.mode, "arbre")
        self.assertEqual(len(plan.boites), 3)

    def test_genre_inconnu_refuse(self):
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.societe._org_chart_carte("ce-genre-n-existe-pas")

    def test_les_deux_genres_coexistent(self):
        """Deux satellites équipent res.partner : aucun n'efface l'autre."""
        codes = {g["code"] for g in self.societe._org_chart_genres()}
        self.assertIn("personnes", codes)
        if "bf_org_chart_ownership" in self.env["ir.module.module"]._installed():
            self.assertIn("detention", codes)

    def test_action_ouvre_une_url(self):
        action = self.societe.action_org_chart_personnes()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertIn("/bf/organigramme/res.partner/%s/personnes" % self.societe.id,
                      action["url"])
