# -*- coding: utf-8 -*-
from odoo.addons.bf_org_chart.moteur import palette
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

    def test_le_compte_qui_decide_du_bouton(self):
        """🔴 Ce compte décide si le bouton s'affiche sur une fiche de SOCIÉTÉ.
        La mutation qui le forçait à zéro ne cassait aucun essai : le bouton
        pouvait disparaître sans un mot."""
        self.assertEqual(self.societe.org_chart_people_count, 3,
                         "la société compte ses trois personnes liées")
        muette = self.env["res.partner"].create({"name": "Sans chaîne", "is_company": True})
        self.env["res.partner"].create({"name": "Isolé", "parent_id": muette.id})
        self.assertEqual(muette.org_chart_people_count, 0,
                         "sans supérieur inscrit, le bouton n'a rien à montrer")
        self.assertEqual(self.dti.org_chart_people_count, 1,
                         "une personne dans une chaîne porte le bouton")

    def test_le_compte_et_le_dessin_voient_la_meme_chose(self):
        """Le compte et la carte doivent avoir la MÊME portée, sinon le bouton
        se cache devant un dessin qui, lui, aurait du contenu."""
        carte = self.societe._org_chart_carte("personnes")
        internes = [b for b in carte.boites if b.teinte != "ambre"]
        self.assertEqual(len(internes), self.societe.org_chart_people_count)

    def test_une_societe_sous_une_autre_ne_fausse_pas_le_compte(self):
        """Le cas holding : une société fille pend sous la société mère."""
        P = self.env["res.partner"]
        mere = P.create({"name": "Groupe mère", "is_company": True})
        self.societe.parent_id = mere
        self.societe.invalidate_recordset()
        mere.invalidate_recordset()
        carte = mere._org_chart_carte("personnes")
        internes = [b for b in carte.boites if b.teinte != "ambre"]
        self.assertEqual(
            len(internes), mere.org_chart_people_count,
            "le compte de la mère et son dessin divergent : le bouton ment")

    def test_une_societe_ne_peut_pas_etre_un_superieur(self):
        """🔴 Sans cette garde, une société posée en supérieur par RPC entrait
        dans l'organigramme des personnes comme un supérieur externe."""
        with self.assertRaises(ValidationError):
            self.analyste.manager_id = self.societe

    def test_une_societe_n_a_pas_de_superieur(self):
        with self.assertRaises(ValidationError):
            self.societe.manager_id = self.pdg

    def test_un_superieur_hors_perimetre_ne_fait_pas_tomber_la_page(self):
        """🔴 `gens` est filtré par les règles, `manager_id` ne l'est par rien :
        lire le nom d'un supérieur d'une autre société levait AccessError, et
        c'est justement le cas que le module revendique de savoir traiter."""
        voisine = self.env["res.company"].create({"name": "Voisine organigramme"})
        voisin = self.env["res.users"].create({
            "name": "Voisin", "login": "voisin_orgchart_people",
            "company_id": voisine.id, "company_ids": [(6, 0, [voisine.id])],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        dehors_soc = self.env["res.partner"].create({
            "name": "Société d'ailleurs", "is_company": True,
            "company_id": self.env.company.id})
        dehors = self.env["res.partner"].create({
            "name": "Patron d'ailleurs", "parent_id": dehors_soc.id,
            "company_id": self.env.company.id})
        self.dti.manager_id = dehors
        societe_voisine = self.societe.with_user(voisin)
        carte = societe_voisine._org_chart_carte("personnes")
        self.assertTrue(carte.boites, "la carte doit se rendre malgré tout")
        self.assertNotIn("Patron d'ailleurs", [b.titre for b in carte.boites])

    def test_le_pied_ne_porte_pas_la_marque_de_la_maison(self):
        """Le module est distribué : ce pied s'imprime chez un tiers."""
        carte = self.societe._org_chart_carte("personnes")
        self.assertNotIn("Blue Fox", carte.pied)

    def test_l_action_epingle_sa_vue_de_recherche(self):
        """🔴 `search_default_...` est ignoré EN SILENCE quand la vue de
        recherche retenue ne porte pas le filtre. Vu sur la démonstration : deux
        vues racines à la même priorité sur les contacts, et l'écran s'ouvrait
        sur tous les contacts au lieu de la chaîne."""
        action = self.env.ref("bf_org_chart_people.action_org_chart_personnes")
        self.assertTrue(action.search_view_id,
                        "l'action doit épingler la vue de recherche")
        arch = action.search_view_id.get_combined_arch()
        self.assertIn("filter_avec_lien", arch,
                      "la vue épinglée doit porter le filtre que le contexte demande")
        import ast
        ctx = ast.literal_eval(action.context)
        for nom in ctx:
            if nom.startswith("search_default_"):
                self.assertIn(nom[len("search_default_"):], arch,
                              "filtre par défaut absent de la vue épinglée : %s" % nom)


@tagged("post_install", "-at_install", "bf_org_chart")
class TestPaletteDeLaSociete(TransactionCase):
    """Le dessin figeait les couleurs de Blue Fox, donc un AUTRE locataire
    repartait avec notre bleu sur son propre organigramme."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Source = cls.env["res.partner"]
        cls.societe = cls.Source.create({"name": "Teinte inc.", "is_company": True})
        cls.pdg = cls.Source.create({"name": "Aline Roy", "function": "Présidente",
                                     "parent_id": cls.societe.id})
        cls.Source.create({"name": "Karim Haddad", "function": "Adjoint",
                           "parent_id": cls.societe.id, "manager_id": cls.pdg.id})

    def test_la_resolution_de_champ_va_du_precis_au_general(self):
        """Testé sur un double : la chaîne de champs doit se lire même sur une
        base où aucun module de marque n'est installé, donc sans dépendre de la
        présence du champ dans CETTE base."""
        class Fausse:
            _fields = {"secondary_color": object()}
            def __getitem__(self, nom):
                return {"secondary_color": "#123456"}[nom]

        lue = self.Source._org_chart_couleur(
            Fausse(), ("report_brand_dark", "secondary_color"))
        self.assertEqual(lue, "#123456")
        self.assertIsNone(self.Source._org_chart_couleur(Fausse(), ("absent",)))

    def test_un_champ_vide_ne_compte_pas_comme_une_couleur(self):
        class Vide:
            _fields = {"report_brand_dark": object(), "secondary_color": object()}
            def __getitem__(self, nom):
                return {"report_brand_dark": False, "secondary_color": "#123456"}[nom]

        self.assertEqual(
            self.Source._org_chart_couleur(
                Vide(), ("report_brand_dark", "secondary_color")),
            "#123456")

    def test_la_palette_suit_la_societe(self):
        """Les deux branches affirment : sur une base sans module de marque, le
        repli EST le comportement attendu, pas une raison de sauter l'essai."""
        societe = self.env.company
        champ = next((n for n in self.Source._ORG_CHART_CHAMPS_BLEU
                      if n in societe._fields), None)
        if champ:
            societe.write({champ: "#B4005A"})
            self.assertEqual(self.pdg._org_chart_palette().bleu, "#B4005A")
        else:
            self.assertEqual(self.pdg._org_chart_palette().bleu,
                             palette.BLEU_DEFAUT)

    def test_la_palette_de_la_societe_arrive_jusqu_au_dessin(self):
        societe = self.env.company
        champ = next((n for n in self.Source._ORG_CHART_CHAMPS_BLEU
                      if n in societe._fields), None)
        attendu = "#B4005A" if champ else palette.BLEU_DEFAUT
        if champ:
            societe.write({champ: attendu})
        dessin = self.societe._org_chart_svg("personnes")
        self.assertIn(attendu, dessin)

    def test_le_dessin_reste_lisible_quelle_que_soit_la_societe(self):
        pal = self.pdg._org_chart_palette()
        self.assertGreaterEqual(
            palette.contraste(pal.encre, pal.papier), 4.5)

