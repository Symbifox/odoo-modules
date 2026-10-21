"""La tuile « Adhésion aux avantages » a une source anglaise et un français.

Le module n'a pas de correctif de code : son gabarit OWL (static/src/xml/tile.xml)
passe d'un texte français en dur à une source anglaise, et le français vit dans
`i18n/fr_CA.po`. Ces essais prouvent que l'extracteur d'Odoo trouve les trois
textes du gabarit et que le client web les reçoit en français pour fr_CA (en
anglais, il lit la source).
"""
import glob
import os

from babel.messages import extract

from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, tagged

ATTENDU = {
    "Benefit uptake (": "Adhésion aux avantages",
    "that nobody uses": "que personne ne prend",
    "per year": "par année",
}


@tagged("post_install", "-at_install")
class TestLangue(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)

    def test_textes_du_gabarit_extraits(self):
        racine = get_module_path("bf_employee_experience_dashboard")
        extraits = set()
        for chemin in sorted(glob.glob(os.path.join(racine, "static/src/xml/*.xml"))):
            with open(chemin, "rb") as f:
                extraits.update(m[1] for m in extract.extract(
                    "odoo.tools.translate:babel_extract_qweb", f, keywords={}))
        self.assertFalse(sorted(set(ATTENDU) - extraits), extraits)

    def test_client_recoit_le_francais(self):
        rep = self.url_open("/web/webclient/translations/essai?lang=fr_CA", timeout=60)
        self.assertEqual(rep.status_code, 200, rep.text[:300])
        propres = {m["id"]: " ".join(m["string"].split()) for m in rep.json()["modules"].get(
            "bf_employee_experience_dashboard", {}).get("messages", [])}
        for source, francais in ATTENDU.items():
            with self.subTest(source=source):
                self.assertTrue(propres.get(source, "").startswith(francais), propres.get(source))
