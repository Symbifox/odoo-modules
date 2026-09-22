# -*- coding: utf-8 -*-
"""Essais du sondage du babillard.

⚠️ Aucun essai ne tourne sous `uid 1` : le superutilisateur n'a pas de groupes,
donc il ne prouve rien sur les règles d'enregistrement. Chaque parcours est joué
sous un compte réel, dans le rôle visé.

🔴 L'anonymat se prouve à trois endroits, et les trois sont ici : ce que le
dépouillement rend, ce que la table des voix laisse LIRE, et ce que la rédaction
peut en tirer. Un essai qui ne regarde que le premier rendrait le même vert à un
module qui publie les votes en clair juste à côté.
"""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestSondage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.g_interne = cls.env.ref("base.group_user")
        cls.g_redaction = cls.env.ref("bf_babillard.group_babillard_redacteur")
        cls.dept_atelier = cls.env["hr.department"].create({"name": "Atelier du sondage"})
        cls.dept_bureau = cls.env["hr.department"].create({"name": "Bureau du sondage"})

        cls.u_redactrice = Users.create({
            "name": "Rédactrice du sondage", "login": "sondage_redactrice",
            "email": "sondage.redaction@exemple.test",
            "groups_id": [(6, 0, [cls.g_interne.id, cls.g_redaction.id])]})
        cls.votants = Users.create([{
            "name": "Votant %s" % n, "login": "sondage_votant_%s" % n,
            "email": "sondage.votant%s@exemple.test" % n,
            "groups_id": [(6, 0, [cls.g_interne.id])],
        } for n in range(1, 5)])
        cls.u1, cls.u2, cls.u3, cls.u4 = cls.votants

        Employe = cls.env["hr.employee"]
        for user in cls.votants:
            Employe.create({"name": user.name, "user_id": user.id,
                            "department_id": cls.dept_atelier.id})
        cls.e_bureau = Employe.create({
            "name": "Personne du bureau", "user_id": cls.u_redactrice.id,
            "department_id": cls.dept_bureau.id})

    # --- outillage --------------------------------------------------------

    def _sondage(self, choix=("Pizza", "Sushis"), **kw):
        vals = {
            "name": "Le repas de l'équipe, c'est quoi ?",
            "type_publication": "sondage",
            "audience": "tous",
            "state": "publie",
            "option_ids": [(0, 0, {"name": libelle, "sequence": 10 + i})
                           for i, libelle in enumerate(choix)],
        }
        vals.update(kw)
        return self.env["bf.babillard.post"].with_user(self.u_redactrice).create(vals)

    def _vue(self, post, user):
        return post.with_user(user).sondage

    def _option(self, post, libelle):
        return post.sudo().option_ids.filtered(lambda o: o.name == libelle)

    # --- voter ------------------------------------------------------------

    def test_voter_puis_se_reprendre(self):
        post = self._sondage()
        pizza = self._option(post, "Pizza")
        post.with_user(self.u1).action_basculer_vote(pizza.id)
        vue = self._vue(post, self.u1)
        self.assertEqual([o["nb"] for o in vue["options"]], [1, 0])
        self.assertTrue(vue["options"][0]["par_moi"])
        self.assertEqual(vue["nb_votants"], 1)

        post.with_user(self.u1).action_basculer_vote(pizza.id)
        vue = self._vue(post, self.u1)
        self.assertEqual([o["nb"] for o in vue["options"]], [0, 0])
        self.assertFalse(vue["options"][0]["par_moi"])

    def test_choix_unique_remplace(self):
        post = self._sondage()
        post.with_user(self.u1).action_basculer_vote(self._option(post, "Pizza").id)
        post.with_user(self.u1).action_basculer_vote(self._option(post, "Sushis").id)
        vue = self._vue(post, self.u1)
        self.assertEqual([o["nb"] for o in vue["options"]], [0, 1],
                         "un seul choix : le second remplace le premier")
        self.assertEqual(vue["nb_votants"], 1)

    def test_choix_multiple_cumule(self):
        post = self._sondage(sondage_choix_multiple=True)
        post.with_user(self.u1).action_basculer_vote(self._option(post, "Pizza").id)
        post.with_user(self.u1).action_basculer_vote(self._option(post, "Sushis").id)
        vue = self._vue(post, self.u1)
        self.assertEqual([o["nb"] for o in vue["options"]], [1, 1])
        self.assertEqual(vue["nb_votants"], 1,
                         "🔴 le seuil compte des personnes, pas des voix")

    def test_hors_audience_ne_vote_pas(self):
        post = self._sondage(audience="departements",
                             department_ids=[(6, 0, [self.dept_bureau.id])])
        with self.assertRaises(AccessError):
            post.with_user(self.u1).action_basculer_vote(
                self._option(post, "Pizza").id)

    def test_meme_la_redaction_ne_vote_pas_hors_audience(self):
        """🔴 Le trou que la règle d'enregistrement ne bouche pas.

        Une personne ordinaire hors audience est arrêtée par la règle : elle ne
        LIT même pas la publication. La rédaction, elle, lit tout
        (`rule_post_redacteur`), et seul le contrôle d'audience dans la méthode
        l'empêche de voter sur un sondage qui ne lui est pas adressé. Une
        mutation l'a démasqué : le premier jeu d'essais restait vert sans ce
        contrôle.
        """
        post = self._sondage(audience="departements",
                             department_ids=[(6, 0, [self.dept_atelier.id])],
                             sondage_ajout_ouvert=True)
        self.assertTrue(post.with_user(self.u_redactrice).name,
                        "la rédaction lit bien la publication")
        with self.assertRaises(AccessError):
            post.with_user(self.u_redactrice).action_basculer_vote(
                self._option(post, "Pizza").id)
        with self.assertRaises(AccessError):
            post.with_user(self.u_redactrice).action_ajouter_option("Poutine")

    def test_sondage_ferme_ou_non_publie(self):
        post = self._sondage(state="brouillon")
        pizza = self._option(post, "Pizza")
        with self.assertRaises(UserError):
            post.with_user(self.u1).action_basculer_vote(pizza.id)

        post.sudo().write({"state": "publie"})
        post.with_user(self.u_redactrice).action_fermer_sondage()
        with self.assertRaises(UserError):
            post.with_user(self.u1).action_basculer_vote(pizza.id)

        post.with_user(self.u_redactrice).action_rouvrir_sondage()
        post.with_user(self.u1).action_basculer_vote(pizza.id)
        self.assertEqual(self._vue(post, self.u1)["options"][0]["nb"], 1)

    def test_option_dun_autre_sondage_refusee(self):
        """🔴 L'identifiant arrive du navigateur : il ne vaut rien tant qu'il
        n'a pas été confronté aux choix de CETTE publication."""
        post = self._sondage()
        ailleurs = self._sondage(choix=("Ailleurs",), name="Un autre sondage")
        with self.assertRaisesRegex(UserError, "n'existe pas sur cette publication"):
            post.with_user(self.u1).action_basculer_vote(
                self._option(ailleurs, "Ailleurs").id)

    def test_voter_au_nom_dun_autre_refuse(self):
        post = self._sondage()
        with self.assertRaises(AccessError):
            self.env["bf.babillard.vote"].with_user(self.u1).create({
                "post_id": post.id, "option_id": self._option(post, "Pizza").id,
                "user_id": self.u2.id})

    def test_la_garde_du_modele_tient_si_les_droits_souvrent(self):
        """🔴 La garde de `create` est aujourd'hui couverte par les droits :
        personne n'a le droit de créer une voix, donc elle ne se déclenche
        jamais. Une mutation l'a démasquée en survivant. Cet essai lui donne
        les conditions où elle sert, celles de `bf.babillard.geste`, qui LUI
        porte déjà un droit de création.
        """
        post = self._sondage()
        self.env["ir.model.access"].sudo().create({
            "name": "Voix : création ouverte, le temps de cet essai",
            "model_id": self.env["ir.model"]._get_id("bf.babillard.vote"),
            "group_id": self.g_interne.id,
            "perm_read": True, "perm_create": True,
        })
        self.env.registry.clear_cache()
        with self.assertRaises(AccessError):
            self.env["bf.babillard.vote"].with_user(self.u1).create({
                "post_id": post.id, "option_id": self._option(post, "Pizza").id,
                "user_id": self.u2.id})

    def test_creer_une_voix_par_rpc_refuse(self):
        """L'audience n'écrit pas dans la table : tout passe par la méthode."""
        post = self._sondage()
        with self.assertRaises(AccessError):
            self.env["bf.babillard.vote"].with_user(self.u1).create({
                "post_id": post.id, "option_id": self._option(post, "Pizza").id,
                "user_id": self.u1.id})

    # --- anonymat ---------------------------------------------------------

    def test_nominatif_montre_les_noms(self):
        post = self._sondage()
        post.with_user(self.u1).action_basculer_vote(self._option(post, "Pizza").id)
        vue = self._vue(post, self.u2)
        self.assertEqual(vue["options"][0]["noms"], [self.u1.name])
        self.assertFalse(vue["anonyme"])

    def test_anonyme_sous_le_seuil_ne_rend_aucun_chiffre(self):
        post = self._sondage(sondage_anonyme=True)
        pizza = self._option(post, "Pizza")
        for user in (self.u1, self.u2):
            post.with_user(user).action_basculer_vote(pizza.id)
        vue = self._vue(post, self.u3)
        self.assertTrue(vue["masque"])
        self.assertEqual(vue["nb_votants"], 0)
        self.assertEqual([o["nb"] for o in vue["options"]], [0, 0])
        self.assertEqual([o["part"] for o in vue["options"]], [0, 0])
        self.assertTrue(vue["message"])
        self.assertIsNone(vue["options"][0]["noms"])

    def test_anonyme_au_seuil_rend_les_chiffres_sans_les_noms(self):
        post = self._sondage(sondage_anonyme=True)
        pizza, sushis = self._option(post, "Pizza"), self._option(post, "Sushis")
        post.with_user(self.u1).action_basculer_vote(pizza.id)
        post.with_user(self.u2).action_basculer_vote(pizza.id)
        post.with_user(self.u3).action_basculer_vote(sushis.id)
        vue = self._vue(post, self.u4)
        self.assertFalse(vue["masque"])
        self.assertEqual(vue["nb_votants"], 3)
        self.assertEqual([o["nb"] for o in vue["options"]], [2, 1])
        self.assertEqual([o["part"] for o in vue["options"]], [67, 33])
        self.assertTrue(all(o["noms"] is None for o in vue["options"]))

    def test_anonyme_la_redaction_ne_lit_pas_les_voix(self):
        """🔴 La promesse ne tient pas si l'auteur peut lire la table.

        Sans la condition sur `rule_vote_redacteur`, la rédaction lisait les
        quatre voix nominativement, et le masque du dépouillement n'était qu'un
        décor.
        """
        post = self._sondage(sondage_anonyme=True)
        pizza = self._option(post, "Pizza")
        for user in (self.u1, self.u2, self.u3):
            post.with_user(user).action_basculer_vote(pizza.id)

        lues = self.env["bf.babillard.vote"].with_user(self.u_redactrice).search(
            [("post_id", "=", post.id)])
        self.assertFalse(lues, "aucune voix d'un sondage anonyme ne se lit")

        # Et l'audience non plus, sauf la sienne.
        vues_par_u1 = self.env["bf.babillard.vote"].with_user(self.u1).search(
            [("post_id", "=", post.id)])
        self.assertEqual(vues_par_u1.mapped("user_id"), self.u1,
                         "on lit sa propre voix, et rien d'autre")

    def test_nominatif_la_redaction_lit_les_voix(self):
        post = self._sondage()
        post.with_user(self.u1).action_basculer_vote(self._option(post, "Pizza").id)
        lues = self.env["bf.babillard.vote"].with_user(self.u_redactrice).search(
            [("post_id", "=", post.id)])
        self.assertEqual(len(lues), 1)

    def test_avertissement_quand_laudience_est_trop_petite(self):
        post = self._sondage(audience="departements",
                             department_ids=[(6, 0, [self.dept_bureau.id])],
                             sondage_anonyme=True)
        self.assertTrue(post.sondage_avertissement,
                        "une audience d'une personne ne tient pas la promesse")
        post.sudo().sondage_anonyme = False
        post.invalidate_recordset(["sondage_avertissement"])
        self.assertFalse(post.sondage_avertissement)

    # --- ajouter un choix -------------------------------------------------

    def test_ajout_ferme_par_defaut(self):
        post = self._sondage()
        self.assertFalse(self._vue(post, self.u1)["peut_ajouter"])
        with self.assertRaises(UserError):
            post.with_user(self.u1).action_ajouter_option("Poutine")

    def test_ajout_ouvert_et_plafond_par_personne(self):
        post = self._sondage(sondage_ajout_ouvert=True, sondage_max_ajouts=2)
        post.with_user(self.u1).action_ajouter_option("Poutine")
        post.with_user(self.u1).action_ajouter_option("Tacos")
        self.assertEqual(len(post.sudo().option_ids), 4)
        self.assertFalse(self._vue(post, self.u1)["peut_ajouter"])
        with self.assertRaises(UserError):
            post.with_user(self.u1).action_ajouter_option("Sandwichs")
        # Le plafond est PAR PERSONNE : la suivante en a encore deux.
        post.with_user(self.u2).action_ajouter_option("Sandwichs")
        self.assertEqual(len(post.sudo().option_ids), 5)

    def test_ajout_refuse_le_doublon_et_le_vide(self):
        post = self._sondage(sondage_ajout_ouvert=True)
        with self.assertRaises(UserError):
            post.with_user(self.u1).action_ajouter_option("   ")
        with self.assertRaises(UserError):
            post.with_user(self.u1).action_ajouter_option("  pizza  ")
        self.assertEqual(len(post.sudo().option_ids), 2)

    def test_ajout_hors_audience_refuse(self):
        post = self._sondage(sondage_ajout_ouvert=True, audience="departements",
                             department_ids=[(6, 0, [self.dept_bureau.id])])
        with self.assertRaises(AccessError):
            post.with_user(self.u1).action_ajouter_option("Poutine")

    def test_qui_a_propose_ne_sort_pas_dun_sondage_anonyme(self):
        post = self._sondage(sondage_ajout_ouvert=True, sondage_anonyme=True)
        post.with_user(self.u1).action_ajouter_option("Poutine")
        vue = self._vue(post, self.u2)
        self.assertTrue(all(o["propose_par"] is None for o in vue["options"]),
                        "🔴 sur un sondage anonyme, qui propose est un demi-vote")
        post.sudo().sondage_anonyme = False
        post.invalidate_recordset(["sondage"])
        vue = self._vue(post, self.u2)
        self.assertEqual(vue["options"][2]["propose_par"], self.u1.name)

    def test_le_champ_qui_propose_ne_se_lit_pas_sans_la_redaction(self):
        post = self._sondage(sondage_ajout_ouvert=True)
        post.with_user(self.u1).action_ajouter_option("Poutine")
        option = self._option(post, "Poutine")
        lu = option.with_user(self.u2).read(["name"])
        self.assertEqual(lu[0]["name"], "Poutine")
        with self.assertRaises(AccessError):
            option.with_user(self.u2).read(["propose_par_id"])

    def test_anonyme_le_seuil_compte_des_personnes_pas_des_voix(self):
        """🔴 En choix multiple, deux personnes posent trois voix. Un seuil
        posé sur les VOIX aurait dévoilé un résultat que deux personnes se
        partagent."""
        post = self._sondage(choix=("Pizza", "Sushis", "Poutine"),
                             sondage_anonyme=True, sondage_choix_multiple=True)
        pizza = self._option(post, "Pizza")
        sushis = self._option(post, "Sushis")
        post.with_user(self.u1).action_basculer_vote(pizza.id)
        post.with_user(self.u1).action_basculer_vote(sushis.id)
        post.with_user(self.u2).action_basculer_vote(pizza.id)
        vue = self._vue(post, self.u3)
        self.assertTrue(vue["masque"], "trois voix, mais deux personnes")
        self.assertEqual([o["nb"] for o in vue["options"]], [0, 0, 0])

    def test_plafond_global_des_choix(self):
        post = self._sondage(sondage_ajout_ouvert=True, sondage_max_ajouts=30)
        self.env["bf.babillard.option"].sudo().create([
            {"post_id": post.id, "name": "Choix %s" % n} for n in range(18)])
        self.assertEqual(len(post.sudo().option_ids), 20)
        with self.assertRaises(UserError):
            post.with_user(self.u1).action_ajouter_option("Le vingt-et-unième")

    # --- garde-fous -------------------------------------------------------

    def test_une_option_ne_saccroche_qua_un_sondage(self):
        post = self.env["bf.babillard.post"].with_user(self.u_redactrice).create({
            "name": "Une nouvelle ordinaire", "type_publication": "nouvelle",
            "audience": "tous"})
        with self.assertRaises(ValidationError):
            self.env["bf.babillard.option"].with_user(self.u_redactrice).create({
                "post_id": post.id, "name": "Pizza"})

    def test_une_voix_ne_peut_pas_porter_le_choix_dun_autre_sondage(self):
        """La contrainte de base, celle qui tient même en superutilisateur :
        la méthode est le premier rempart, elle n'est pas le seul."""
        post = self._sondage()
        ailleurs = self._sondage(choix=("Ailleurs",), name="Un autre sondage")
        with self.assertRaises(AccessError):
            self.env["bf.babillard.vote"].sudo().create({
                "post_id": post.id,
                "option_id": self._option(ailleurs, "Ailleurs").id,
                "user_id": self.u1.id})

    def test_fermer_nest_pas_pour_tout_le_monde(self):
        post = self._sondage()
        with self.assertRaises(AccessError):
            post.with_user(self.u1).action_fermer_sondage()

    def test_la_porte_dentree_des_sondages(self):
        """🔴 Le défaut qui a mené à cette action : un sondage est un TYPE de
        publication, et rien à l'écran ne le disait. La personne qui voulait en
        poser un n'a pas trouvé comment. L'action doit donc ne montrer que les
        sondages ET en créer un du bon type.
        """
        action = self.env.ref("bf_babillard.action_babillard_sondages")
        self.assertEqual(
            safe_eval(action.domain), [("type_publication", "=", "sondage")])
        self.assertEqual(
            safe_eval(action.context).get("default_type_publication"), "sondage")

        # Le contexte fait vraiment naître un sondage, pas une annonce.
        post = self.env["bf.babillard.post"].with_user(self.u_redactrice).with_context(
            **safe_eval(action.context)).create({"name": "Un sondage né du menu"})
        self.assertEqual(post.type_publication, "sondage")

    def test_le_menu_des_sondages_est_reserve_a_la_redaction(self):
        menu = self.env.ref("bf_babillard.menu_babillard_sondages")
        self.assertIn(self.g_redaction, menu.groups_id,
                      "le menu ne s'offre qu'à qui peut publier")

    def test_une_publication_ordinaire_na_pas_de_sondage(self):
        post = self.env["bf.babillard.post"].with_user(self.u_redactrice).create({
            "name": "Une nouvelle ordinaire", "type_publication": "nouvelle",
            "audience": "tous", "state": "publie"})
        self.assertEqual(self._vue(post, self.u1), {"est_sondage": False})
