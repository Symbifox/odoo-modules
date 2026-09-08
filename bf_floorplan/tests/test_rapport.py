# -*- coding: utf-8 -*-
from odoo.tests import tagged

from .commun import CasPlan


@tagged("post_install", "-at_install")
class TestRapport(CasPlan):

    def _html(self):
        rapport = self.env.ref("bf_floorplan.action_report_plan")
        html, _genre = rapport.with_user(self.gestion)._render_qweb_html(
            "bf_floorplan.report_plan", self.plan.ids)
        return html.decode("utf-8")

    def test_le_rapport_trace_le_plan(self):
        html = self._html()
        self.assertIn("<svg", html)
        self.assertIn('viewBox="0 0 1000.0 800.0"', html)
        # ⚠️ wkhtmltopdf rend un SVG sans width/height à zéro : on les exige.
        # 1000 × 800 est plus profond que 2:3 : calé sur la hauteur (640)
        self.assertIn('width="800"', html)
        self.assertIn('height="640"', html)
        for nom in ("Aire ouverte (1/4)", "Local technique", "P-01", "SW-01", "AP-01",
                    "Personne du banc", "Inventaire par zone", "Commutateur réseau"):
            self.assertIn(nom, html)
        self.assertNotIn("<image", html)
        self.assertIn("Banc (Atelier, étage 1)", html)
        self.assertIn(self.plan.company_id.name, html)

    def test_le_rapport_incorpore_le_fond(self):
        self.plan.fond = self.fond_b64()
        html = self._html()
        self.assertIn("<image", html)
        self.assertIn("data:image/png;base64,", html)

    def test_plan_profond_se_cale_sur_la_hauteur(self):
        self.plan.write({"largeur": 500.0, "profondeur": 1000.0})
        html = self._html()
        self.assertIn('width="320"', html)   # 640 / 2
        self.assertIn('height="640"', html)

    def test_plan_large_prend_toute_la_largeur(self):
        self.plan.write({"largeur": 2000.0, "profondeur": 1000.0})
        html = self._html()
        self.assertIn('width="960"', html)
        self.assertIn('height="480"', html)

    def test_action_imprimer(self):
        action = self.plan.action_imprimer()
        self.assertEqual(action["type"], "ir.actions.report")
        self.assertEqual(action["report_name"], "bf_floorplan.report_plan")
