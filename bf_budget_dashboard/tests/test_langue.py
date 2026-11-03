"""La tuile Budget a une source anglaise et un français.

Les libellés du gabarit OWL sont écrits en anglais ; le français vit dans
`i18n/fr_CA.po`. Odoo ne traduit jamais vers `en_US`, la langue source : une
tuile écrite en français en dur se lisait en français même pour un usager
réglé en anglais, et rien ne le signalait.

Ces essais prouvent deux choses distinctes : que l'extracteur d'Odoo trouve
bien les textes du gabarit (sinon le catalogue est muet sans erreur), et que le
client web les reçoit en français pour `fr_CA`.
"""
import glob
import os

from babel.messages import extract

from odoo.modules.module import get_module_path
from odoo.tests import HttpCase, tagged

ATTENDU = {
    "Budget consumed": "Budget engagé",
    "Revenue booked": "Revenus constatés",
    "flagged line(s)": "ligne(s) en alerte",
    "vs theoretical": "contre le théorique",
    "open budgets": "budgets ouverts",
}


@tagged("post_install", "-at_install")
class TestLangue(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)

    def test_textes_du_gabarit_extraits(self):
        racine = get_module_path("bf_budget_dashboard")
        extraits = set()
        for chemin in sorted(glob.glob(os.path.join(racine, "static/src/xml/*.xml"))):
            with open(chemin, "rb") as f:
                extraits.update(m[1] for m in extract.extract(
                    "odoo.tools.translate:babel_extract_qweb", f, keywords={}))
        self.assertFalse(sorted(set(ATTENDU) - extraits), extraits)

    def test_client_recoit_le_francais(self):
        rep = self.url_open("/web/webclient/translations/essai?lang=fr_CA", timeout=60)
        self.assertEqual(rep.status_code, 200, rep.text[:300])
        propres = {
            m["id"]: " ".join(m["string"].split())
            for m in rep.json()["modules"].get("bf_budget_dashboard", {}).get("messages", [])
        }
        for source, francais in ATTENDU.items():
            with self.subTest(source=source):
                self.assertTrue(
                    propres.get(source, "").startswith(francais), propres.get(source)
                )
