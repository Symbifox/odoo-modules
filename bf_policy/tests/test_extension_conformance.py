# -*- coding: utf-8 -*-
"""Conformite des extensions : ce qu'elle VOIT, et surtout ce qu'elle ne peut pas voir.

🔴 Deux garanties sont eprouvees ici, parce que ce sont les deux qui ont ete arbitrees :
rien n'est jamais bloque (un administrateur, comme n'importe qui, installe ce qu'il veut),
et le signalement ne porte QUE sur le catalogue impose — y compris quand l'extension, ou un
appel RPC forge, essaie de rapporter autre chose.
"""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

SIGNETS = "nhekfepjhpbbnpjocfhflekddijjfmjm"
TOKENS = "ccpcbipmbbnlmpoaailjgfffhbbpmecn"
BITWARDEN = "nngceckbapebfimnlniiiahkandclblb"
CLE = "profil-essai-01"
AUTRE_CLE = "profil-essai-02"


@tagged("post_install", "-at_install")
class TestExtensionConformance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Foxy Conf"})
        cls.org = cls.env["bf.policy.org"].create({
            "company_id": cls.company.id, "domain": "conf.example"})
        cls.moi = cls.env["res.users"].create({
            "name": "Sam", "login": "sam@conf.example",
            "company_id": cls.company.id, "company_ids": [(4, cls.company.id)],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])]})
        cls.autre = cls.env["res.users"].create({
            "name": "Alex", "login": "alex@conf.example",
            "company_id": cls.company.id, "company_ids": [(4, cls.company.id)],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])]})
        Ext = cls.env["bf.policy.extension"].with_context(active_test=False)
        cls.signets = Ext.search([("extension_id", "=", SIGNETS)])
        cls.tokens = Ext.search([("extension_id", "=", TOKENS)])
        cls.bitwarden = Ext.search([("extension_id", "=", BITWARDEN)])
        # ⚠️ Le montage se VÉRIFIE : sans ça, une base où le catalogue manque fait
        # échouer les essais sur un « lists differ » au lieu de dire ce qui manque.
        for nom, rec in (("Signets", cls.signets), ("Tokens", cls.tokens),
                         ("Bitwarden", cls.bitwarden)):
            assert len(rec) == 1, f"le catalogue de base n'a pas {nom} : essai illisible"
        cls.org.extension_ids = [(6, 0, (cls.signets | cls.bitwarden).ids)]

    def profils(self, usager=None):
        return (self.env["bf.policy.browser.profile"]
                .with_user(usager or self.moi)
                .with_company(self.company))

    # ------------------------------------------------------------------ le catalogue
    def test_le_serveur_dit_quoi_verifier_et_rien_de_plus(self):
        a_verifier = self.profils().conformite_a_verifier()
        self.assertEqual(sorted(e["id"] for e in a_verifier["extensions"]),
                         sorted([SIGNETS, BITWARDEN]))
        self.assertNotIn(TOKENS, [e["id"] for e in a_verifier["extensions"]],
                         "une extension non imposee ne doit pas etre demandee")

    def test_le_catalogue_suit_les_ajouts_et_retraits_de_la_personne(self):
        self.env["bf.policy.user"].create({
            "user_id": self.moi.id, "company_id": self.company.id,
            "extension_ids": [(6, 0, self.tokens.ids)],
            "extension_remove_ids": [(6, 0, self.bitwarden.ids)]})
        ids = [e["id"] for e in self.profils().conformite_a_verifier()["extensions"]]
        self.assertIn(TOKENS, ids, "son ajout n'est pas verifie")
        self.assertNotIn(BITWARDEN, ids, "son retrait est encore verifie")

    def test_sans_politique_rien_n_est_demande(self):
        autre_societe = self.env["res.company"].create({"name": "Sans politique"})
        self.moi.write({"company_ids": [(4, autre_societe.id)]})
        a_verifier = (self.env["bf.policy.browser.profile"].with_user(self.moi)
                      .with_company(autre_societe).conformite_a_verifier())
        self.assertEqual(a_verifier["extensions"], [])

    # ------------------------------------------------------------------ le rapport
    def test_un_rapport_se_range_et_se_relit(self):
        r = self.profils().conformite_rapporter(
            CLE, {SIGNETS: "presente", BITWARDEN: "absente"},
            navigateur="Brave 1.88", plateforme="Linux")
        self.assertEqual(r["enregistres"], 2)
        self.assertEqual(r["ecartes"], 0)
        self.assertEqual(r["inconnues"], 0)
        profil = self.profils().browse(r["profil"])
        self.assertEqual(profil.user_id, self.moi)
        self.assertEqual(profil.navigateur, "Brave 1.88")
        self.assertEqual(profil.manquantes, 1)
        self.assertTrue(profil.vu_le)

    def test_un_deuxieme_rapport_met_a_jour_sans_doubler(self):
        p = self.profils()
        p.conformite_rapporter(CLE, {SIGNETS: "absente", BITWARDEN: "presente"})
        r = p.conformite_rapporter(CLE, {SIGNETS: "presente", BITWARDEN: "presente"})
        profil = p.browse(r["profil"])
        self.assertEqual(len(profil.state_ids), 2, "les lignes se sont doublees")
        self.assertEqual(set(profil.state_ids.mapped("etat")), {"presente"})
        self.assertEqual(profil.manquantes, 0)

    def test_une_extension_hors_catalogue_est_ECARTEE_jamais_ecrite(self):
        """🔴 La garantie de portee, cote serveur : meme si l'extension change d'avis."""
        r = self.profils().conformite_rapporter(
            CLE, {SIGNETS: "presente",
                  TOKENS: "presente",                 # imposee a personne ici
                  "aaaabbbbccccddddeeeeffffgggghhhh": "presente",   # inconnue
                  "gestionnaire-de-mots-de-passe": "presente"})
        self.assertEqual(r["enregistres"], 1)
        self.assertEqual(r["ecartes"], 3)
        profil = self.profils().browse(r["profil"])
        etats = self.env["bf.policy.extension.state"].sudo().search(
            [("profile_id", "=", profil.id)])
        # Les deux imposées, et RIEN d'autre : Bitwarden est « pas rapportée ».
        self.assertEqual(etats.extension_id, self.signets | self.bitwarden,
                         "quelque chose d'autre que le catalogue a ete ecrit")
        self.assertEqual(
            {e.extension_id.extension_id: e.etat for e in etats},
            {SIGNETS: "presente", BITWARDEN: "inconnu"})

    def test_un_etat_inconnu_est_ecarte(self):
        r = self.profils().conformite_rapporter(
            CLE, {SIGNETS: "espionnee", BITWARDEN: "presente"})
        self.assertEqual((r["enregistres"], r["ecartes"]), (1, 1))
        self.assertEqual(r["inconnues"], 1, "l'état refusé doit devenir « pas rapportée »")

    def test_une_extension_retiree_du_catalogue_perd_son_etat(self):
        p = self.profils()
        r = p.conformite_rapporter(CLE, {SIGNETS: "presente", BITWARDEN: "presente"})
        self.org.extension_ids = [(6, 0, self.signets.ids)]
        r = p.conformite_rapporter(CLE, {SIGNETS: "presente"})
        profil = p.browse(r["profil"])
        self.assertEqual(profil.state_ids.extension_id, self.signets)

    # ------------------------------------------------------------------ les bornes
    def test_une_cle_invalide_refuse(self):
        # ⚠️ `assertRaises` d'Odoo n'accepte pas un TUPLE d'exceptions (il teste
        # `issubclass(exception, AccessError)`) : un essai ecrit ainsi leve une
        # TypeError et ne mesure rien.
        for mauvaise in ("", "court", "a" * 65, "clé-avec-accent", "../../etc/passwd"):
            with self.assertRaises(UserError, msg=mauvaise):
                self.profils().conformite_rapporter(mauvaise, {SIGNETS: "presente"})

    def test_une_cle_invalide_refuse_aussi_a_l_ecriture_directe(self):
        # ⚠️ Personne n'a le droit de créer un profil à la main : la contrainte se
        # mesure donc sur le chemin qui écrit VRAIMENT,
        # le `sudo` de la méthode. C'est lui qu'elle protège.
        with self.assertRaises(ValidationError):
            self.profils().sudo().create({"user_id": self.moi.id, "cle": "court"})

    def test_un_rapport_trop_gros_refuse(self):
        with self.assertRaises(UserError):
            self.profils().conformite_rapporter(
                CLE, {f"ext{i:04d}": "presente" for i in range(301)})

    def test_un_identifiant_demesure_est_ecarte_pas_ecrit(self):
        # ⚠️ Le plafond ne comptait que les ENTRÉES : deux cents clés d'un mégaoctet
        # passaient le contrôle avant d'être écartées une à une.
        r = self.profils().conformite_rapporter(
            CLE, {SIGNETS: "presente", "x" * 5000: "presente"})
        self.assertEqual(r["ecartes"], 1)
        profil = self.profils().browse(r["profil"])
        self.assertNotIn("x" * 5000,
                         profil.state_ids.extension_id.mapped("extension_id"))

    def test_un_catalogue_plus_grand_que_le_plafond_n_est_pas_condamne(self):
        # Écrit en dur à 200, le plafond refusait POUR TOUJOURS le client honnête d'une
        # organisation qui impose davantage.
        Ext = self.env["bf.policy.extension"]
        grosses = Ext.browse()
        for i in range(210):
            # ⚠️ Un identifiant d'extension Chromium, c'est 32 lettres de « a » à « p »
            # — le module le contrôle. Des chiffres, ou 26 identifiants recyclés, font
            # échouer l'essai sur autre chose que ce qu'il mesure.
            alphabet = "abcdefghijklmnop"
            suffixe = (alphabet[i // 16] + alphabet[i % 16])
            grosses |= Ext.create({"name": f"Ext {i}",
                                   "extension_id": "a" * 30 + suffixe})
        self.org.extension_ids = [(6, 0, grosses.ids)]
        etats = {e.extension_id: "presente" for e in grosses}
        r = self.profils().conformite_rapporter(CLE, etats)
        self.assertGreater(r["enregistres"], 200)

    def test_un_rapport_qui_n_est_pas_un_dictionnaire_refuse(self):
        with self.assertRaises(UserError):
            self.profils().conformite_rapporter(CLE, [SIGNETS])

    def test_un_nom_de_navigateur_est_borne_et_nettoye(self):
        r = self.profils().conformite_rapporter(
            CLE, {SIGNETS: "presente"}, navigateur="Brave\n\x00 " + "x" * 200)
        profil = self.profils().browse(r["profil"])
        self.assertLessEqual(len(profil.navigateur), 64)
        self.assertNotIn("\n", profil.navigateur)
        self.assertNotIn("\x00", profil.navigateur)

    # ------------------------------------------------------------------ qui voit quoi
    def test_le_profil_est_toujours_celui_de_l_appelant(self):
        """🔴 Un appel forge ne pose pas un profil au nom de quelqu'un d'autre."""
        r = self.profils().with_context(default_user_id=self.autre.id) \
            .conformite_rapporter(CLE, {SIGNETS: "presente"})
        self.assertEqual(self.profils().browse(r["profil"]).user_id, self.moi)

    def test_personne_ne_voit_les_profils_d_autrui(self):
        r = self.profils().conformite_rapporter(CLE, {SIGNETS: "presente"})
        vus = self.profils(self.autre).search([])
        self.assertNotIn(r["profil"], vus.ids, "le profil d'autrui est visible")
        with self.assertRaises(AccessError):
            self.profils(self.autre).browse(r["profil"]).read(["navigateur"])

    def test_deux_profils_de_la_meme_personne_coexistent(self):
        p = self.profils()
        un = p.conformite_rapporter(CLE, {SIGNETS: "presente"}, navigateur="Brave")
        deux = p.conformite_rapporter(AUTRE_CLE, {SIGNETS: "absente"},
                                      navigateur="Firefox")
        self.assertNotEqual(un["profil"], deux["profil"])
        self.assertEqual(len(p.search([])), 2)

    def test_l_administration_voit_tout(self):
        r = self.profils().conformite_rapporter(CLE, {SIGNETS: "presente"})
        admin = self.env["bf.policy.browser.profile"].with_user(
            self.env.ref("base.user_admin"))
        self.assertIn(r["profil"], admin.search([]).ids)

    # ------------------------------------------------------------------ qui entre
    def test_un_compte_EXTERNE_ne_peut_rien_du_tout(self):
        """🔴 Les trois methodes travaillent en `sudo`, donc HORS des droits d'acces.

        Un `@api.model` public est appelable par toute session authentifiee, portail
        compris (`/web/dataset/call_kw` est en `auth="user"` et ne controle aucun
        modele). Sans controle d'entree, un client du portail LISAIT le catalogue
        impose de l'organisation — identifiants et noms — et se faisait ECRIRE un
        profil a son nom. Mesure avant correctif : trois extensions rendues, une
        ligne creee.
        """
        externe = self.env["res.users"].create({
            "name": "Cliente externe", "login": "externe@conf.example",
            "company_id": self.company.id, "company_ids": [(4, self.company.id)],
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        self.assertTrue(externe.share, "le garnissage n'est pas un compte externe")
        Profils = self.profils(externe)
        with self.assertRaises(AccessError):
            Profils.conformite_a_verifier()
        with self.assertRaises(AccessError):
            Profils.conformite_rapporter(CLE, {SIGNETS: "presente"})
        with self.assertRaises(AccessError):
            Profils.conformite_oublier(CLE)
        self.assertFalse(
            self.env["bf.policy.browser.profile"].sudo()
            .search_count([("user_id", "=", externe.id)]),
            "un compte externe a laisse une ligne en base")

    def test_la_surcharge_se_lit_sur_la_societe_de_l_organisation(self):
        """🔴 La meme regle que la politique servie aux postes, et pas une autre.

        `get_policy_json` cherche la surcharge personnelle sur la societe de
        l'ORGANISATION. Lue sur celle de la SESSION, elle est introuvable des que les
        deux diffèrent — exactement le repli a une seule organisation — et l'ecran
        verifie alors une extension que la politique retire a cette personne.
        """
        # ⚠️ Le repli ne sert QU'AVEC une seule organisation active : sur une base qui
        # en porte plusieurs il ne se declenche pas, et l'essai mesurerait alors le cas
        # banal au lieu de celui qu'il annonce. On monte donc le monde du repli.
        Org = self.env["bf.policy.org"].sudo()
        (Org.search([]) - self.org.sudo()).write({"active": False})
        self.assertEqual(Org.search_count([]), 1, "le repli ne peut pas se declencher")
        ailleurs = self.env["res.company"].create({"name": "Societe de la session"})
        self.moi.write({"company_ids": [(4, ailleurs.id)], "company_id": ailleurs.id})
        self.env["bf.policy.user"].create({
            "user_id": self.moi.id, "company_id": self.company.id,
            "extension_ids": [(6, 0, self.tokens.ids)],
            "extension_remove_ids": [(6, 0, self.bitwarden.ids)]})
        vus = [e["id"] for e in (self.env["bf.policy.browser.profile"]
                                 .with_user(self.moi).with_company(ailleurs)
                                 .conformite_a_verifier()["extensions"])]
        # Ce que le POSTE recevrait, pour comparer a la meme chose.
        servies = [e["id"] for e in
                   self.org.get_policy_json(self.moi)["browser"]["extensions"]]
        self.assertEqual(sorted(vus), sorted(servies),
                         "l'ecran et le poste ne jugent pas le meme catalogue")
        self.assertIn(TOKENS, vus, "son ajout est perdu")
        self.assertNotIn(BITWARDEN, vus, "son retrait est ignore")

    # ------------------------------------------------------------------ la decision
    def test_la_politique_ne_bloque_jamais_rien(self):
        """🔴 La regle du module : voir, jamais empecher.

        La politique rendue aux postes ne porte qu'une liste d'IMPOSITION. Aucune liste de
        refus, aucune liste d'autorisation, aucun `ExtensionSettings` : un administrateur —
        comme n'importe qui — installe l'extension qu'il veut. Si quelqu'un ajoute un
        blocage un jour, c'est ICI que ca se verra.
        """
        self.env["bf.policy.user"].create({
            "user_id": self.moi.id, "company_id": self.company.id,
            "extension_ids": [(6, 0, self.tokens.ids)]})
        charge = self.org.get_policy_json(self.moi)
        navigateur = charge.get("browser", {})
        self.assertTrue(navigateur.get("extensions"), "la politique n'impose plus rien")
        plat = str(charge)
        for interdit in ("Blocklist", "blocklist", "Allowlist", "allowlist",
                         "ExtensionSettings", "blocked", "bloquee"):
            self.assertNotIn(interdit, plat,
                             f"la politique porte « {interdit} » : ce n'est plus un "
                             "signalement, c'est un verrou")

    # ------------------------------------------------------------------ les droits
    def test_un_employe_ne_peut_PAS_deplacer_son_profil_chez_un_collegue(self):
        """🔴 Le trou que les droits ferment, et pourquoi un controle ne suffit pas.

        Odoo vérifie la règle d'écriture sur l'état d'AVANT et ne la revérifie jamais
        après : avec un droit d'écriture, `write({"user_id": <collègue>})` passait le
        contrôle puis sortait la ligne de la portée de son auteur. L'admin voyait alors,
        au nom du collègue, un navigateur et des états choisis par quelqu'un d'autre.
        La parade n'est pas un contrôle de plus : c'est de ne donner le droit à personne.
        """
        r = self.profils().conformite_rapporter(CLE, {SIGNETS: "presente"})
        profil = self.profils().browse(r["profil"])
        with self.assertRaises(AccessError):
            profil.write({"user_id": self.autre.id})
        with self.assertRaises(AccessError):
            profil.write({"navigateur": "Autre chose"})
        self.assertEqual(self.profils().browse(r["profil"]).user_id, self.moi)

    def test_un_employe_ne_peut_PAS_creer_un_profil_a_la_main(self):
        # Deux champs de texte libre écrits sous son nom, sans borne : c'était le canal
        # par lequel « toutes mes extensions » pouvait entrer par morceaux.
        with self.assertRaises(AccessError):
            self.profils().create({"user_id": self.moi.id, "cle": "a" * 12,
                                   "navigateur": "x" * 5000})

    def test_un_employe_ne_peut_PAS_ecrire_un_etat_dans_le_profil_d_un_autre(self):
        # Même mécanique, et en plus la contrainte d'unicité servait d'oracle : elle
        # disait si un collègue rapporte déjà telle extension.
        r = self.profils().conformite_rapporter(CLE, {SIGNETS: "presente"})
        ligne = self.profils().browse(r["profil"]).state_ids[0]
        with self.assertRaises(AccessError):
            ligne.write({"etat": "absente"})
        with self.assertRaises(AccessError):
            self.env["bf.policy.extension.state"].with_user(self.autre).create({
                "profile_id": r["profil"], "extension_id": self.signets.id,
                "etat": "absente"})

    def test_chacun_peut_retirer_SON_profil(self):
        """Le geste de retrait : éteindre le signalement efface ce qu'il avait laissé."""
        r = self.profils().conformite_rapporter(CLE, {SIGNETS: "presente"})
        self.assertEqual(self.profils().conformite_oublier(CLE), {"oublie": 1})
        self.assertFalse(self.profils().browse(r["profil"]).exists())
        # Rejouable, et muet quand il n'y a rien.
        self.assertEqual(self.profils().conformite_oublier(CLE), {"oublie": 0})

    def test_oublier_ne_touche_pas_le_profil_d_un_autre(self):
        self.profils().conformite_rapporter(CLE, {SIGNETS: "presente"})
        self.assertEqual(self.profils(self.autre).conformite_oublier(CLE), {"oublie": 0})
        self.assertEqual(len(self.profils().search([])), 1)

    # ------------------------------------------------------------------ les comptes
    def test_les_comptes_distinguent_le_defaut_de_l_absence_de_mesure(self):
        p = self.profils()
        r = p.conformite_rapporter(CLE, {SIGNETS: "absente"})
        profil = p.browse(r["profil"])
        self.assertEqual((profil.manquantes, profil.inconnues), (1, 1),
                         "Bitwarden n'a rien dit : ce n'est pas un défaut, c'est un trou")
        r = p.conformite_rapporter(CLE, {SIGNETS: "presente", BITWARDEN: "presente"})
        profil = p.browse(r["profil"])
        self.assertEqual((profil.manquantes, profil.inconnues), (0, 0))

    def test_les_comptes_se_cherchent_car_ils_sont_stockes(self):
        # 🔴 La facette « Conformes » était fausse : écrite sur l'état des lignes, elle
        # voulait dire « au moins une ligne va bien » et montrait les profils incomplets.
        p = self.profils()
        bon = p.conformite_rapporter(AUTRE_CLE,
                                     {SIGNETS: "presente", BITWARDEN: "presente"})
        mauvais = p.conformite_rapporter(CLE, {SIGNETS: "presente",
                                               BITWARDEN: "absente"})
        conformes = p.search([("manquantes", "=", 0), ("inconnues", "=", 0)]).ids
        self.assertIn(bon["profil"], conformes)
        self.assertNotIn(mauvais["profil"], conformes,
                         "un profil incomplet passe pour conforme")
        self.assertEqual(p.search([("manquantes", ">", 0)]).ids, [mauvais["profil"]])

    # ------------------------------------------------------------------ l'organisation
    def test_une_seule_organisation_sert_toutes_les_societes(self):
        """🔴 Sinon l'écran affirme « rien n'est imposé » pendant que le poste reçoit la
        liste imposée : le poste choisit son organisation par le HOST, pas par la société
        active. Avec une seule organisation, c'est elle, comme `_select_for_host`."""
        autre_societe = self.env["res.company"].create({"name": "Societe sans politique"})
        self.moi.write({"company_ids": [(4, autre_societe.id)]})
        vu = (self.env["bf.policy.browser.profile"].with_user(self.moi)
              .with_company(autre_societe).conformite_a_verifier())
        orgs = self.env["bf.policy.org"].sudo().search_count([])
        if orgs == 1:
            self.assertTrue(vu["extensions"], "le repli mono-organisation ne joue pas")
        else:
            self.assertEqual(vu["extensions"], [],
                             "avec plusieurs organisations, ne rien servir est la regle")
