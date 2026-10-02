from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCouleurLibre(TransactionCase):
    """La couleur libre de bf_color sur les modèles colorés du module."""

    def _porte_une_couleur_libre(self, model, vals):
        self.assertIn(model, self.env["bf.color.mixin"]._bf_color_model_names())
        record = self.env[model].create(dict(vals, color_hex="#0072b2"))
        self.assertEqual(record.color_hex, "#0072B2")
        self.assertEqual(record.color_resolved, "#0072B2")
        # L'index suit (teinte la plus proche), pour les écrans qui ne lisent que lui.
        self.assertTrue(record.color)
        return record

    def _arch(self, xmlid, view_type):
        view = self.env.ref(xmlid)
        return self.env[view.model].get_view(view.id, view_type)["arch"]

    def test_theme_et_retour(self):
        self._porte_une_couleur_libre("bf.cx.theme", {"name": "Essai couleur"})
        self.assertIn("bf.cx.feedback", self.env["bf.color.mixin"]._bf_color_model_names())
        self.assertIn('name="color_resolved"', self._arch("bf_cx.view_cx_feedback_kanban", "kanban"))
        self.assertIn("'bf_color': True", self._arch("bf_cx.view_cx_feedback_form", "form"))
        self.assertIn("'bf_color': True", self._arch("bf_cx.view_cx_complaint_form", "form"))
