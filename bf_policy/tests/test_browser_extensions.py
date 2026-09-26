"""Extensions de navigateur imposees par la politique.

Le bloc `browser.extensions` de la politique, la regle de conflit (le retrait
l'emporte), la forme des identifiants, et la route publique update.xml que
Brave interroge pour les extensions Symbifox hors boutique."""

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from ..controllers import main as ctrl

SIGNETS = "nhekfepjhpbbnpjocfhflekddijjfmjm"
TOKENS = "ccpcbipmbbnlmpoaailjgfffhbbpmecn"
BITWARDEN = "nngceckbapebfimnlniiiahkandclblb"


@tagged("post_install", "-at_install")
class TestBrowserExtensions(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Foxy Ext"})
        cls.org = cls.env["bf.policy.org"].create({
            "company_id": cls.company.id, "domain": "ext.example"})
        cls.user = cls.env["res.users"].create({
            "name": "Sam", "login": "sam@ext.example",
            "company_id": cls.company.id, "company_ids": [(4, cls.company.id)]})
        Ext = cls.env["bf.policy.extension"].with_context(active_test=False)
        # Le catalogue de depart est semé par le module ; on s'en sert tel quel.
        cls.signets = Ext.search([("extension_id", "=", SIGNETS)])
        cls.tokens = Ext.search([("extension_id", "=", TOKENS)])
        cls.bitwarden = Ext.search([("extension_id", "=", BITWARDEN)])

    def _extensions(self):
        return self.org.get_policy_json(self.user)["browser"]["extensions"]

    def test_catalogue_seme(self):
        self.assertEqual(len(self.signets | self.tokens | self.bitwarden), 3)
        self.assertEqual(self.signets.source, "symbifox")
        self.assertEqual(self.bitwarden.source, "store")

    def test_rien_d_office(self):
        # Aucune organisation n'impose quoi que ce soit sans l'avoir choisi.
        self.assertEqual(self._extensions(), [])

    def test_bloc_de_politique(self):
        self.org.extension_ids = [(6, 0, (self.signets | self.bitwarden).ids)]
        par_id = {e["id"]: e for e in self._extensions()}
        self.assertEqual(set(par_id), {SIGNETS, BITWARDEN})
        self.assertEqual(par_id[BITWARDEN]["update_url"],
                         "https://clients2.google.com/service/update2/crx")
        self.assertEqual(par_id[SIGNETS]["update_url"],
                         "https://ext.example/bf_policy/extensions/update.xml")
        # Signets recoit l'instance ; Bitwarden, rien.
        self.assertEqual(par_id[SIGNETS]["managed"], {"instance": "https://ext.example"})
        self.assertNotIn("managed", par_id[BITWARDEN])

    def test_tokens_ne_recoit_pas_l_instance(self):
        # Tokens refuse par conception tout stockage et tout hote d'office.
        self.org.extension_ids = [(6, 0, self.tokens.ids)]
        self.assertNotIn("managed", self._extensions()[0])

    def test_ajout_et_retrait_par_personne(self):
        self.org.extension_ids = [(6, 0, (self.signets | self.bitwarden).ids)]
        self.env["bf.policy.user"].create({
            "user_id": self.user.id, "company_id": self.company.id,
            "extension_ids": [(6, 0, self.tokens.ids)],
            "extension_remove_ids": [(6, 0, self.bitwarden.ids)]})
        self.assertEqual({e["id"] for e in self._extensions()}, {SIGNETS, TOKENS})

    def test_le_retrait_l_emporte_sur_l_ajout(self):
        self.env["bf.policy.user"].create({
            "user_id": self.user.id, "company_id": self.company.id,
            "extension_ids": [(6, 0, self.tokens.ids)],
            "extension_remove_ids": [(6, 0, self.tokens.ids)]})
        self.assertEqual(self._extensions(), [])

    def test_extension_archivee_ne_part_pas(self):
        self.org.extension_ids = [(6, 0, self.bitwarden.ids)]
        self.bitwarden.active = False
        self.assertEqual(self._extensions(), [])

    def test_identifiant_mal_forme_refuse(self):
        Ext = self.env["bf.policy.extension"]
        for mauvais in ("abc", "z" * 32, SIGNETS.upper(), SIGNETS + "a",
                        "https://chromewebstore.google.com/detail/x/" + SIGNETS):
            with self.subTest(mauvais=mauvais), self.assertRaises(ValidationError):
                Ext.create({"name": "X", "extension_id": mauvais})


@tagged("post_install", "-at_install")
class TestUpdateXml(TransactionCase):
    """La fabrique du fichier update.xml, sans HTTP."""

    def test_forme_gupdate(self):
        xml = ctrl._extensions_update_xml("ext.example", {SIGNETS: "0.4.0"})
        self.assertIn("<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>", xml)
        self.assertIn(f"<app appid='{SIGNETS}'>", xml)
        self.assertIn(f"codebase='https://ext.example/bf_policy/static/extensions/{SIGNETS}.crx'", xml)
        self.assertIn("version='0.4.0'", xml)

    def test_domaine_echappe(self):
        xml = ctrl._extensions_update_xml("a'b.example", {SIGNETS: "1.0"})
        self.assertNotIn("a'b", xml)

    def test_paquets_servis_par_le_module(self):
        # Les deux paquets deposes par bf_policy_publish_extensions.py.
        servis = ctrl._hosted_extensions()
        self.assertEqual(set(servis), {SIGNETS, TOKENS})


@tagged("post_install", "-at_install")
class TestUpdateXmlRoute(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Org = cls.env["bf.policy.org"]
        Org.search([]).write({"active": False})
        company = cls.env["res.company"].create({"name": "Route Ext"})
        Org.create({"company_id": company.id, "domain": "route.example"})
        cls.env.flush_all()

    def test_route_publique(self):
        resp = self.url_open("/bf_policy/extensions/update.xml",
                             headers={"Host": "route.example"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("xml", resp.headers["Content-Type"])
        self.assertIn(f"codebase='https://route.example/bf_policy/static/extensions/{SIGNETS}.crx'",
                      resp.text)

    def test_paquet_servi_en_statique(self):
        resp = self.url_open(f"/bf_policy/static/extensions/{SIGNETS}.crx",
                             headers={"Host": "route.example"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content[:4], b"Cr24")
