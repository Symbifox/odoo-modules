# -*- coding: utf-8 -*-
"""« Mon profil » s'ouvre pour qui n'est pas administrateur.

L'onglet Google Calendar du profil affiche `x_google_oauth_connected`, dont le
calcul lit le jeton de rafraîchissement, réservé à base.group_system.
Sans administration, ouvrir « Mon profil » donnait une « Erreur
d'accès » : sur le jeton (usager sans fiche d'employé), ou sur les champs
privés de hr (employé), parce que les champs Google, hors de
SELF_READABLE_FIELDS, empêchaient Odoo de lire le profil en sudo.

Les essais ouvrent le profil comme le client web : action_get, get_views de
la vue que l'action désigne, puis web_read de tous les champs que cette vue
déclare, sous l'usager lui-même. Le jeton, lui, doit rester illisible, même par
onchange, domaine ou tri.

Relecture adverse du correctif :
- les alias Google deviennent inscriptibles par la personne; un alias qui est
  l'adresse d'un AUTRE usager retirerait cette personne de ses événements
  (poussée Google, puis lecture qui remplace les participants) : refusé;
- les champs Google lisibles le restent pour les usagers internes, sur toutes
  les fiches, et ne le sont plus pour le portail (lecture de res.users par RPC).
"""

from lxml import etree

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

CHAMPS_GOOGLE_INTERNES = [
    "x_google_email",
    "x_google_oauth_expiry",
    "x_google_oauth_scope",
    "x_google_oauth_connected",
    "x_google_self_addresses",
]

JETON = "jeton-rafraichissement-essai"
CHAMPS_SECRETS = [
    "x_google_oauth_token_encrypted",
    "x_google_oauth_refresh_token_encrypted",
    "x_google_oauth_state",
    "x_google_oauth_state_date",
    "x_google_pkce_verifier",
]


def champs_de_la_vue(arch):
    """Champs de premier niveau de la vue : ceux que le client lit sur res.users
    (les champs des sous-vues x2many appartiennent à d'autres modèles)."""
    noms = []
    for node in etree.fromstring(arch).iter("field"):
        parent, imbrique = node.getparent(), False
        while parent is not None:
            if parent.tag == "field":
                imbrique = True
                break
            parent = parent.getparent()
        if not imbrique and node.get("name") not in noms:
            noms.append(node.get("name"))
    return noms


def specification(vues, noms):
    """Spécification de web_read à la façon du client (display_name des many2one)."""
    modeles = vues.get("models", {}).get("res.users", {})
    descr = modeles.get("fields", modeles)
    spec = {}
    for nom in noms:
        if descr.get(nom, {}).get("type") == "many2one":
            spec[nom] = {"fields": {"display_name": {}}}
        else:
            spec[nom] = {}
    return spec


class _ProfilCommun:

    @classmethod
    def _preparer(cls, env):
        cls.a_hr = "hr.employee" in env
        cls.employe = new_test_user(
            env, login="employe_essai", password="employe_essai",
            groups="base.group_user")
        cls.usager = new_test_user(
            env, login="usager_essai", password="usager_essai",
            groups="base.group_user")
        cls.admin = new_test_user(
            env, login="admin_essai", password="admin_essai",
            groups="base.group_user,base.group_system")
        if cls.a_hr:
            env["hr.employee"].create({
                "name": cls.employe.name, "user_id": cls.employe.id})
            env["hr.employee"].create({
                "name": cls.admin.name, "user_id": cls.admin.id})
        # L'employée a connecté son agenda Google; l'usager, non : c'est le
        # cas du signalement (le jeton vide est justement celui qui plantait,
        # un jeton déjà présent en cache masquait le défaut).
        cls.employe.sudo().write({
            "x_google_oauth_refresh_token_encrypted": JETON,
            "x_google_email": "employe_essai@exemple.test",
        })


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestMonProfil(_ProfilCommun, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._preparer(cls.env)

    def _ouvrir_profil(self, user, froid=False):
        Users = self.env["res.users"].with_user(user)
        action = Users.action_get()
        vues = Users.get_views(action["views"], {})
        arch = vues["views"]["form"]["arch"]
        noms = champs_de_la_vue(arch)
        self.env.invalidate_all()
        if froid:
            # Caches du registre vidés (groupes de l'usager compris) : la
            # lecture des groupes repasse alors par SELF_READABLE_FIELDS.
            self.env.registry.clear_cache()
        try:
            [valeurs] = Users.browse(user.id).web_read(specification(vues, noms))
        except AccessError as erreur:
            self.fail("« Mon profil » refusé à %s : %s" % (user.login, erreur))
        return action, noms, valeurs

    def test_profil_usager_sans_fiche_d_employe(self):
        action, noms, valeurs = self._ouvrir_profil(self.usager)
        self.assertEqual(action.get("xml_id"), "base.action_res_users_my")
        self.assertIn("x_google_oauth_connected", noms,
                      "l'onglet Google Calendar a quitté le profil")
        self.assertFalse(valeurs["x_google_oauth_connected"])
        for secret in CHAMPS_SECRETS:
            self.assertNotIn(secret, valeurs)
        # Une fois l'agenda connecté, le profil s'ouvre toujours et le dit.
        self.usager.sudo().write({
            "x_google_oauth_refresh_token_encrypted": JETON,
            "x_google_email": "usager_essai@exemple.test",
        })
        action, noms, valeurs = self._ouvrir_profil(self.usager)
        self.assertTrue(valeurs["x_google_oauth_connected"])
        self.assertEqual(valeurs["x_google_email"], "usager_essai@exemple.test")

    def test_profil_employe(self):
        if not self.a_hr:
            self.skipTest("hr n'est pas installé : pas de profil d'employé")
        action, noms, valeurs = self._ouvrir_profil(self.employe)
        self.assertEqual(action.get("xml_id"), "hr.res_users_action_my")
        self.assertIn("x_google_oauth_connected", noms)
        self.assertTrue(valeurs["x_google_oauth_connected"])
        for secret in CHAMPS_SECRETS:
            self.assertNotIn(secret, valeurs)

    def test_profil_a_cache_froid(self):
        """La garde de `_google_self_fields` tient quand le cache des groupes
        est froid (sans elle : récursion sans fin)."""
        for user in [self.usager, self.admin] + ([self.employe] if self.a_hr else []):
            with self.subTest(user=user.login):
                self.env.registry.clear_cache()
                lisibles = self.env["res.users"].with_user(user).SELF_READABLE_FIELDS
                self.assertIn("x_google_oauth_connected", lisibles)
                self._ouvrir_profil(user, froid=True)

    def test_jetons_restent_illisibles(self):
        """Le correctif n'ouvre aucun secret, ni sur soi ni sur autrui."""
        for user in (self.employe, self.usager):
            Users = self.env["res.users"].with_user(self.employe)
            for secret in CHAMPS_SECRETS:
                self.env.invalidate_all()
                with self.assertRaises(AccessError, msg=secret):
                    Users.browse(user.id).read([secret])
                with self.assertRaises(AccessError, msg=secret):
                    Users.search_read([("id", "=", user.id)], [secret])
        champs = self.env["res.users"].with_user(self.employe).fields_get()
        for secret in CHAMPS_SECRETS:
            self.assertNotIn(secret, champs)

    def test_etat_google_d_un_collegue_sans_erreur(self):
        """Lire le booléen sur une autre fiche (sans le raccourci sudo du
        profil) ne passe plus par le jeton avec les droits de l'usager."""
        self.env.invalidate_all()
        [valeurs] = self.env["res.users"].with_user(self.employe).browse(
            self.usager.id).web_read({"name": {}, "x_google_oauth_connected": {}})
        self.assertFalse(valeurs["x_google_oauth_connected"])
        self.env.invalidate_all()
        [valeurs] = self.env["res.users"].with_user(self.usager).browse(
            self.employe.id).web_read({"name": {}, "x_google_oauth_connected": {}})
        self.assertTrue(valeurs["x_google_oauth_connected"])

    def test_alias_enregistres_depuis_le_profil(self):
        moi = self.env["res.users"].with_user(self.employe).browse(self.employe.id)
        moi.write({"x_google_self_addresses": "alias@exemple.test"})
        self.assertEqual(self.employe.x_google_self_addresses, "alias@exemple.test")
        with self.assertRaises(AccessError):
            self.env["res.users"].with_user(self.employe).browse(
                self.usager.id).write({"x_google_self_addresses": "x@exemple.test"})
        with self.assertRaises(AccessError):
            moi.write({"x_google_oauth_refresh_token_encrypted": "faux"})

    def test_administrateur_garde_tout(self):
        Users = self.env["res.users"].with_user(self.admin)
        vue = self.env.ref("base.view_users_form")
        arch = Users.get_views([(vue.id, "form")], {})["views"]["form"]["arch"]
        noms = champs_de_la_vue(arch)
        for nom in ("x_google_oauth_connected", "x_google_email",
                    "x_google_oauth_expiry", "x_google_oauth_scope",
                    "x_google_self_addresses"):
            self.assertIn(nom, noms)
        self.env.invalidate_all()
        [valeurs] = Users.browse(self.employe.id).read(
            ["x_google_oauth_refresh_token_encrypted", "x_google_oauth_connected"])
        self.assertEqual(valeurs["x_google_oauth_refresh_token_encrypted"], JETON)
        self.assertTrue(valeurs["x_google_oauth_connected"])
        self._ouvrir_profil(self.admin)


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestMonProfilHttp(_ProfilCommun, HttpCase):
    """Le même parcours par /web/dataset/call_kw, dans une session réelle."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._preparer(cls.env)

    def _rpc(self, methode, args, kwargs=None):
        return self.make_jsonrpc_request(
            "/web/dataset/call_kw/res.users/%s" % methode,
            {"model": "res.users", "method": methode, "args": args,
             "kwargs": kwargs or {}})

    def _ouvrir_profil(self, user):
        self.authenticate(user.login, user.login)
        action = self._rpc("action_get", [])
        vues = self._rpc("get_views", [], {"views": action["views"], "options": {}})
        noms = champs_de_la_vue(vues["views"]["form"]["arch"])
        # Le serveur de test partage la transaction (donc le cache) avec
        # l'essai; un vrai serveur repart d'un cache vide à chaque requête.
        # Même ainsi, sur le code d'avant #essai, cette voie ne tombe que pour
        # le profil d'employé (hr) : le profil sans fiche d'employé, lui, est
        # tenu par TestMonProfil.test_profil_usager_sans_fiche_d_employe.
        self.env.invalidate_all()
        [valeurs] = self._rpc("web_read", [[user.id]], {
            "specification": specification(vues, noms)})
        return noms, valeurs

    def test_mon_profil_par_rpc(self):
        users = [self.usager, self.admin] + ([self.employe] if self.a_hr else [])
        for user in users:
            with self.subTest(user=user.login):
                noms, valeurs = self._ouvrir_profil(user)
                self.assertIn("x_google_oauth_connected", noms)
                for secret in CHAMPS_SECRETS:
                    self.assertNotIn(secret, valeurs)


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestJetonHorsDeLecture(_ProfilCommun, TransactionCase):
    """Ni onchange, ni domaine, ni tri ne lisent le jeton pour qui n'est pas
    administrateur, sur sa propre fiche comme sur celle d'autrui."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._preparer(cls.env)

    def test_onchange_sur_soi_avec_le_jeton(self):
        moi = self.env["res.users"].with_user(self.employe).browse(self.employe.id)
        for changement, champs in (({}, []), (
                {"x_google_self_addresses": "moi.perso@exemple.test"},
                ["x_google_self_addresses"])):
            self.env.invalidate_all()
            with self.assertRaises(AccessError):
                moi.onchange(changement, champs, {
                    "name": {}, "x_google_self_addresses": {},
                    "x_google_oauth_refresh_token_encrypted": {}})

    def test_domaine_et_tri_sur_le_jeton(self):
        Users = self.env["res.users"].with_user(self.employe)
        with self.assertRaises(AccessError):
            Users.search([("x_google_oauth_refresh_token_encrypted", "!=", False)])
        with self.assertRaises(AccessError):
            Users.search([("x_google_oauth_refresh_token_encrypted", "=ilike", "j%")])
        with self.assertRaises(AccessError):
            Users.search_count([("x_google_oauth_refresh_token_encrypted", "!=", False)])
        with self.assertRaises(AccessError):
            Users.search([], order="x_google_oauth_refresh_token_encrypted", limit=1)


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestPortail(_ProfilCommun, TransactionCase):
    """Le portail ne lit aucun champ Google : ni sur sa fiche, ni sur celle
    d'un autre compte portail de la même entreprise cliente (les seules que les
    règles de res.partner lui ouvrent), ni sur une fiche interne. Un usager
    interne les lit toujours sur toutes les fiches."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._preparer(cls.env)
        client = cls.env["res.partner"].create({
            "name": "Client essai", "is_company": True})
        cls.portail = new_test_user(
            cls.env, login="portail_essai", groups="base.group_portal",
            email="portail.essai@exemple.test")
        cls.portail2 = new_test_user(
            cls.env, login="portail2_essai", groups="base.group_portal",
            email="portail2.essai@exemple.test")
        (cls.portail.partner_id | cls.portail2.partner_id).parent_id = client
        cls.portail2.sudo().write({
            "x_google_email": "portail2.google@exemple.test",
            "x_google_self_addresses": "portail2.alias@exemple.test",
        })

    def test_portail_ne_lit_pas_les_champs_google(self):
        Users = self.env["res.users"].with_user(self.portail)
        # La fiche voisine est bien lisible : seul le champ est refusé.
        self.env.invalidate_all()
        self.assertEqual(
            Users.browse(self.portail2.id).read(["login"])[0]["login"],
            "portail2_essai")
        for fiche in (self.portail2, self.portail, self.employe):
            for champ in CHAMPS_GOOGLE_INTERNES:
                self.env.invalidate_all()
                with self.subTest(fiche=fiche.login, champ=champ), \
                        self.assertRaises(AccessError):
                    Users.browse(fiche.id).read([champ])
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            Users.search_read([("id", "=", self.portail2.id)], ["x_google_email"])
        with self.assertRaises(AccessError):
            Users.search([("x_google_email", "!=", False)])
        champs = Users.fields_get()
        for champ in CHAMPS_GOOGLE_INTERNES + CHAMPS_SECRETS:
            self.assertNotIn(champ, champs)

    def test_portail_get_views_sans_onglet_google(self):
        Users = self.env["res.users"].with_user(self.portail)
        for xmlid in ("base.view_users_form", "base.view_users_form_simple_modif"):
            vue = self.env.ref(xmlid)
            arch = Users.get_views([(vue.id, "form")], {})["views"]["form"]["arch"]
            with self.subTest(vue=xmlid):
                self.assertNotIn("x_google", arch)
                self.assertNotIn('name="google_calendar', arch)
        # Témoin : un usager interne voit bien l'onglet du profil.
        vue = self.env.ref("base.view_users_form_simple_modif")
        arch = self.env["res.users"].with_user(self.usager).get_views(
            [(vue.id, "form")], {})["views"]["form"]["arch"]
        self.assertIn('name="google_calendar_preferences"', arch)

    def test_portail_a_cache_froid(self):
        self.env.registry.clear_cache()
        lisibles = self.env["res.users"].with_user(self.portail).SELF_READABLE_FIELDS
        for champ in CHAMPS_GOOGLE_INTERNES:
            self.assertNotIn(champ, lisibles)

    def test_portail_n_ecrit_pas_ses_alias(self):
        with self.assertRaises(AccessError):
            self.env["res.users"].with_user(self.portail).browse(
                self.portail.id).write({"x_google_self_addresses": "x@exemple.test"})

    def test_interne_lit_toujours_les_fiches_des_collegues(self):
        self.env.invalidate_all()
        [valeurs] = self.env["res.users"].with_user(self.usager).browse(
            self.employe.id).read(CHAMPS_GOOGLE_INTERNES)
        self.assertEqual(valeurs["x_google_email"], "employe_essai@exemple.test")
        self.assertTrue(valeurs["x_google_oauth_connected"])


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestAliasGoogle(TransactionCase):
    """Un alias Google ne peut pas être l'adresse d'un AUTRE usager."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.moi = new_test_user(
            cls.env, login="moi_essai@exemple.test", groups="base.group_user",
            email="moi.essai@exemple.test")
        cls.collegue = new_test_user(
            cls.env, login="collegue_essai@exemple.test",
            groups="base.group_user", email="collegue.perso.essai@exemple.test")
        cls.client = new_test_user(
            cls.env, login="client_essai", groups="base.group_portal",
            email="client.essai@exemple.test")
        cls.ancien = new_test_user(
            cls.env, login="ancien_essai@exemple.test",
            groups="base.group_user", email="ancien.essai@exemple.test")
        cls.ancien.active = False
        cls.voisin = new_test_user(
            cls.env, login="ab1.essai@exemple.test", groups="base.group_user",
            email="ab1.essai@exemple.test")
        cls.admin = new_test_user(
            cls.env, login="admin_alias_essai", groups="base.group_user,base.group_system",
            email="admin.alias.essai@exemple.test")

    def _ecrire(self, valeur, qui=None, fiche=None):
        qui, fiche = qui or self.moi, fiche or self.moi
        self.env["res.users"].with_user(qui).browse(fiche.id).write(
            {"x_google_self_addresses": valeur})

    def test_adresse_d_un_collegue_refusee(self):
        for valeur in (
            "collegue_essai@exemple.test",              # connexion
            "collegue.perso.essai@exemple.test",        # courriel (partenaire)
            "Collegue.Perso.Essai@Exemple.TEST",        # casse
            "moi.perso.essai@exemple.test\ncollegue_essai@exemple.test",
            "moi.perso.essai@exemple.test; collegue.perso.essai@exemple.test",
            "Collègue <collegue_essai@exemple.test>",        # nom + connexion
            "Collègue <collegue.perso.essai@exemple.test>",  # nom + courriel
        ):
            with self.subTest(valeur=valeur), self.assertRaises(ValidationError):
                self._ecrire(valeur)

    def test_portail_et_compte_archive_refuses(self):
        for valeur in ("client.essai@exemple.test", "client_essai",
                       "ancien_essai@exemple.test", "ancien.essai@exemple.test"):
            with self.subTest(valeur=valeur), self.assertRaises(ValidationError):
                self._ecrire(valeur)

    def test_administrateur_aussi_refuse(self):
        with self.assertRaises(ValidationError):
            self._ecrire("collegue_essai@exemple.test", qui=self.admin)

    def test_propres_adresses_permises(self):
        valeur = "moi.perso.essai@exemple.test, moi@autre-domaine-essai.test"
        self._ecrire(valeur)
        self.assertEqual(self.moi.x_google_self_addresses, valeur)
        self._ecrire("moi_essai@exemple.test\nmoi.essai@exemple.test")
        self.assertEqual(
            self.moi._get_google_self_addresses() >= {
                "moi_essai@exemple.test", "moi.essai@exemple.test"}, True)

    def test_jokers_sql_sans_effet(self):
        """`_` et `%` ne sont pas des jokers : « ab_ » n'est pas « ab1 »."""
        self._ecrire("ab_.essai@exemple.test")
        self._ecrire("%.essai@exemple.test")
        self.assertEqual(self.moi.x_google_self_addresses, "%.essai@exemple.test")

    def test_message_traduit(self):
        if not self.env["res.lang"].search([("code", "=", "fr_CA")]):
            self.skipTest("fr_CA n'est pas installé")
        with self.assertRaisesRegex(
                ValidationError,
                "collegue_essai@exemple.test est l'adresse d'un autre compte"):
            self.env["res.users"].with_user(self.moi).with_context(
                lang="fr_CA").browse(self.moi.id).write(
                    {"x_google_self_addresses": "collegue_essai@exemple.test"})
