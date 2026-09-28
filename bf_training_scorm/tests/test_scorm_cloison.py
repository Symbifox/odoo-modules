"""La cloison du contenu SCORM.

🔴 Un paquet est du code arbitraire déposé par un gestionnaire. Servi depuis
le domaine d'Odoo sans cloison, il tournait avec la session de qui l'ouvrait —
administrateur compris — et pouvait appeler `/web/dataset/call_kw` en son nom.
"""
import re

from odoo.tests import HttpCase, tagged

from .test_training_scorm import MANIFESTE_12, _zip


@tagged("post_install", "-at_install", "bf_training_scorm")
class TestScormCloison(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.paquet = cls.env["bf.scorm.package"].create({
            "name": "Paquet cloisonné",
            "archive": _zip({
                "imsmanifest.xml": MANIFESTE_12,
                "index.html": b"<html><head><title>x</title></head>"
                              b"<body>contenu</body></html>",
                "app.js": b"alert(1)",
            }),
        })
        canal = cls.env["slide.channel"].create({
            "name": "Cours cloisonné", "is_published": True,
            "visibility": "public", "enroll": "public"})
        cls.diapo = cls.env["slide.slide"].create({
            "name": "Leçon cloisonnée", "channel_id": canal.id,
            "slide_category": "scorm", "scorm_package_id": cls.paquet.id,
            "is_published": True})
        cls.paquet.slide_id = cls.diapo

    def _url_contenu(self):
        self.authenticate("admin", "admin")
        r = self.url_open(f"/bf_training_scorm/launch/{self.paquet.id}")
        self.assertEqual(r.status_code, 200, "la page du lecteur doit se charger")
        cadre = re.search(r"<iframe[^>]*>", r.text).group(0)
        self.assertIn('sandbox="allow-scripts allow-forms allow-popups"', cadre)
        self.assertNotIn("allow-same-origin", cadre)
        return re.search(r'src="([^"]+)"', cadre).group(1).replace("&amp;", "&")

    def test_le_contenu_est_servi_dans_une_cloison(self):
        url = self._url_contenu()
        self.assertTrue(url.startswith(f"/bf_training_scorm/sco/{self.paquet.id}/"))
        r = self.url_open(url)
        self.assertEqual(r.status_code, 200)
        csp = r.headers.get("Content-Security-Policy", "")
        self.assertIn("sandbox allow-scripts allow-forms allow-popups", csp)
        self.assertNotIn("allow-same-origin", csp)
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        # L'API est posée DANS la page, avant tout le code du paquet.
        self.assertIn("window.__bfScorm", r.text)
        self.assertIn("API_1484_11", r.text)
        self.assertLess(r.text.index("window.__bfScorm"), r.text.index("contenu"))

    def test_les_fichiers_annexes_sont_cloisonnes_aussi(self):
        url = self._url_contenu().rsplit("/", 1)[0] + "/app.js"
        r = self.url_open(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn("sandbox", r.headers.get("Content-Security-Policy", ""))
        self.assertNotIn("__bfScorm", r.text, "on n'injecte que dans le HTML")

    def test_le_contenu_se_sert_sans_la_session(self):
        """Une origine opaque n'envoie pas le témoin : le jeton doit suffire."""
        url = self._url_contenu()
        self.logout()
        self.assertEqual(self.url_open(url).status_code, 200)

    def test_un_jeton_falsifie_ou_d_un_autre_paquet_est_refuse(self):
        url = self._url_contenu()
        prefixe, jeton, fichier = url.rsplit("/", 2)
        faux = jeton[:-4] + ("0000" if not jeton.endswith("0000") else "1111")
        self.assertEqual(self.url_open(f"{prefixe}/{faux}/{fichier}").status_code, 404)
        autre = prefixe.replace(f"/sco/{self.paquet.id}", f"/sco/{self.paquet.id + 999}")
        self.assertEqual(self.url_open(f"{autre}/{jeton}/{fichier}").status_code, 404)

    def test_l_ancienne_adresse_sans_cloison_n_existe_plus(self):
        self.authenticate("admin", "admin")
        r = self.url_open(f"/bf_training_scorm/content/{self.paquet.id}/index.html")
        self.assertEqual(r.status_code, 404)
