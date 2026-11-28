from odoo.tests import TransactionCase, tagged


# `post_install` : sur une grosse base, les essais `at_install` tournent sur un
# registre PARTIEL et les surcharges des autres modules du projet ne sont pas
# encore chargées : les vues que ce module hérite s'assemblent là, ou pas.
@tagged("post_install", "-at_install")
class TestBeneficiaire(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Projet = self.env["project.project"]
        self.Partenaire = self.env["res.partner"]
        # Des fiches à nous, jamais celles de la base : l'essai tourne aussi
        # bien sur un clone de la production que sur une base neuve, et il
        # n'affirme rien sur les projets d'un vrai client.
        self.payeur = self.Partenaire.create({"name": "Payeur d'essai", "is_company": True})
        self.recevant = self.Partenaire.create({"name": "Recevant d'essai", "is_company": True})
        self.contact_recevant = self.Partenaire.create(
            {"name": "Contact d'essai", "parent_id": self.recevant.id}
        )

    def _projet(self, **vals):
        return self.Projet.create({"name": "Essai bénéficiaire", **vals})

    # ── le champ et son calcul ───────────────────────────────────────────────

    def test_le_beneficiaire_nomme_est_celui_qui_recoit(self):
        projet = self._projet(
            partner_id=self.payeur.id, bf_beneficiary_id=self.recevant.id
        )
        self.assertEqual(projet.bf_beneficiary_effective_id, self.recevant)
        # Témoin : le donneur d'ordre ne bouge pas, c'est lui qui porte la facture.
        self.assertEqual(projet.partner_id, self.payeur)

    def test_sans_beneficiaire_c_est_le_donneur_d_ordre(self):
        projet = self._projet(partner_id=self.payeur.id)
        self.assertFalse(projet.bf_beneficiary_id)
        self.assertEqual(projet.bf_beneficiary_effective_id, self.payeur)

    def test_un_contact_designe_sa_societe(self):
        """Le partenaire COMMERCIAL, et non le contact : la page est lue par
        l'entreprise, pas par la personne dont le nom est au dossier."""
        projet = self._projet(
            partner_id=self.payeur.id, bf_beneficiary_id=self.contact_recevant.id
        )
        self.assertEqual(projet.bf_beneficiary_effective_id, self.recevant)
        # Témoin : le champ saisi garde le contact, c'est le calcul qui remonte.
        self.assertEqual(projet.bf_beneficiary_id, self.contact_recevant)

    def test_un_projet_sans_client_du_tout_ne_casse_rien(self):
        projet = self._projet()
        self.assertFalse(projet.partner_id)
        self.assertFalse(projet.bf_beneficiary_effective_id)

    def test_le_calcul_suit_l_ecriture_dans_les_deux_sens(self):
        """Sans les bonnes dépendances, le calcul reste figé sur sa première
        valeur et la page nomme encore le payeur après la correction."""
        projet = self._projet(partner_id=self.payeur.id)
        self.assertEqual(projet.bf_beneficiary_effective_id, self.payeur)
        projet.bf_beneficiary_id = self.recevant
        self.assertEqual(projet.bf_beneficiary_effective_id, self.recevant)
        projet.bf_beneficiary_id = False
        self.assertEqual(projet.bf_beneficiary_effective_id, self.payeur)

    def test_renommer_la_societe_du_contact_suit(self):
        """La dépendance porte sur `commercial_partner_id`, pas sur le seul id :
        rattacher le contact à une autre société change ce que la page nomme."""
        projet = self._projet(
            partner_id=self.payeur.id, bf_beneficiary_id=self.contact_recevant.id
        )
        self.assertEqual(projet.bf_beneficiary_effective_id, self.recevant)
        autre = self.Partenaire.create({"name": "Autre société d'essai", "is_company": True})
        self.contact_recevant.parent_id = autre
        self.assertEqual(projet.bf_beneficiary_effective_id, autre)

    def test_la_duplication_garde_le_beneficiaire(self):
        """Les projets modèles sont copiés pour chaque mandat :
        un bénéficiaire déjà nommé doit suivre la copie."""
        projet = self._projet(
            partner_id=self.payeur.id, bf_beneficiary_id=self.recevant.id
        )
        self.assertEqual(projet.copy().bf_beneficiary_id, self.recevant)

    def test_le_beneficiaire_suit_le_domaine_de_societe_du_donneur_d_ordre(self):
        """Un partenaire réservé à une autre société n'est pas proposé : la page
        publique de cette société-ci le nommerait. Le domaine est ÉVALUÉ, pas
        seulement relu, avec un témoin des deux côtés."""
        from odoo.tools.safe_eval import safe_eval

        autre = self.env["res.company"].create({"name": "Autre société du banc"})
        a_l_autre = self.Partenaire.create(
            {"name": "Réservé à l'autre", "company_id": autre.id})
        a_tous = self.Partenaire.create({"name": "Partagé", "company_id": False})
        a_la_notre = self.Partenaire.create(
            {"name": "Réservé à la nôtre", "company_id": self.env.company.id})
        # La société POSÉE : un projet sans société laisse passer tout partenaire
        # (la branche `=?` du domaine), exactement comme pour le donneur d'ordre.
        projet = self._projet(partner_id=self.payeur.id, company_id=self.env.company.id)
        self.assertEqual(projet.company_id, self.env.company)
        champ = self.Projet._fields["bf_beneficiary_id"]
        self.assertEqual(champ.domain, self.Projet._fields["partner_id"].domain)
        domaine = safe_eval(champ.domain, {"company_id": projet.company_id.id})
        proposes = self.Partenaire.search(
            domaine + [("id", "in", (a_l_autre | a_tous | a_la_notre).ids)])
        self.assertEqual(proposes, a_tous | a_la_notre)

    # ── la vue groupée ───────────────────────────────────────────────────────

    def test_le_regroupement_par_donneur_d_ordre_rassemble_les_mandats(self):
        """Le chemin de données de la vue groupée, lu sans l'écran."""
        autre_payeur = self.Partenaire.create({"name": "Autre payeur", "is_company": True})
        a = self._projet(partner_id=self.payeur.id, bf_beneficiary_id=self.recevant.id)
        b = self._projet(partner_id=self.payeur.id)
        c = self._projet(partner_id=autre_payeur.id)
        groupes = self.Projet.read_group(
            [("id", "in", (a | b | c).ids)], ["id"], ["partner_id"]
        )
        compte = {g["partner_id"][0]: g["partner_id_count"] for g in groupes}
        self.assertEqual(compte.get(self.payeur.id), 2)
        self.assertEqual(compte.get(autre_payeur.id), 1)

    def test_le_regroupement_par_beneficiaire_isole_ceux_sans_beneficiaire(self):
        a = self._projet(partner_id=self.payeur.id, bf_beneficiary_id=self.recevant.id)
        b = self._projet(partner_id=self.payeur.id)
        groupes = self.Projet.read_group(
            [("id", "in", (a | b).ids)], ["id"], ["bf_beneficiary_id"]
        )
        par_cle = {
            (g["bf_beneficiary_id"] or (False,))[0]: g["bf_beneficiary_id_count"]
            for g in groupes
        }
        self.assertEqual(par_cle.get(self.recevant.id), 1)
        # Le projet sans bénéficiaire n'est pas rangé sous le payeur : il forme
        # son propre groupe « vide », ce qui est la lecture juste du cahier.
        self.assertEqual(par_cle.get(False), 1)

    def test_la_recherche_par_beneficiaire_trouve_par_la_societe(self):
        """`child_of` : taper le nom de l'entreprise doit trouver le projet
        rattaché à l'un de ses contacts, sinon l'écran montre la société et la
        recherche rend zéro."""
        projet = self._projet(
            partner_id=self.payeur.id, bf_beneficiary_id=self.contact_recevant.id
        )
        trouves = self.Projet.search(
            [("id", "=", projet.id), ("bf_beneficiary_id", "child_of", self.recevant.id)]
        )
        self.assertEqual(trouves, projet)

    # ── ce que les écrans montrent ───────────────────────────────────────────

    def test_les_trois_vues_portent_le_champ(self):
        """Une colonne ajoutée par xpath peut s'assembler sans erreur et ne rien
        montrer : on lit l'arbre rendu, pas le fichier source."""
        for type_vue in ("form", "list", "search"):
            arch = self.Projet.get_view(view_type=type_vue)["arch"]
            self.assertIn("bf_beneficiary_id", arch, type_vue)

    def test_le_beneficiaire_ne_propose_que_des_clients(self):
        """Le MÊME contexte que le champ qu'il double.

        ⚠️ Ce contexte TRIE, il ne filtre pas : avec `account`, les fiches qui
        ont déjà été clientes remontent en tête de liste (`customer_rank`), et
        une fiche créée depuis le champ naît cliente. Sans `account`, il est
        sans effet. Il ne retire donc ni employé ni fournisseur de la liste.
        """
        from lxml import etree

        arbre = etree.fromstring(self.Projet.get_view(view_type="form")["arch"])
        contextes = {
            champ: (arbre.xpath("//field[@name='%s']" % champ)[0].get("context") or "")
            for champ in ("partner_id", "bf_beneficiary_id")
        }
        self.assertIn("'res_partner_search_mode': 'customer'",
                      contextes["bf_beneficiary_id"])
        # Témoin : là où le donneur d'ordre porte un contexte de recherche
        # (`project_account` le lui donne, et `sale_project` par lui), c'est
        # bien le même. Sur une base qui n'a que `project`, il n'en porte
        # aucun ; le bénéficiaire garde le sien, qui y est sans effet.
        if contextes["partner_id"]:
            self.assertEqual(contextes["bf_beneficiary_id"], contextes["partner_id"])

    def test_le_libelle_du_donneur_d_ordre_remplace_Client(self):
        arch = self.Projet.get_view(view_type="form")["arch"]
        self.assertIn('string="Ordering customer"', arch)

    def test_l_action_s_ouvre_deja_groupee_par_donneur_d_ordre(self):
        action = self.env.ref(
            "bf_project_beneficiary.action_project_by_ordering_customer"
        )
        self.assertEqual(action.res_model, "project.project")
        self.assertIn("'group_by': ['partner_id']", action.context)

    def test_les_deux_regroupements_sont_offerts_a_la_recherche(self):
        arch = self.Projet.get_view(view_type="search")["arch"]
        self.assertIn("bf_group_by_ordering_customer", arch)
        self.assertIn("bf_group_by_beneficiary", arch)
