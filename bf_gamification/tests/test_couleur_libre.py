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

    def test_categorie_et_niveau(self):
        self._porte_une_couleur_libre("bf.gamification.badge.category", {"name": "Essai couleur"})
        self.assertIn("bf.gamification.level", self.env["bf.color.mixin"]._bf_color_model_names())
        self.assertIn('widget="bf_color"', self._arch("bf_gamification.view_gamification_level_form", "form"))
        self.assertIn('widget="bf_color"', self._arch("bf_gamification.view_badge_category_list", "list"))

    def test_ma_couleur_a_une_porte(self):
        """« Ma couleur » s'ouvre par la couleur affichée."""
        for xmlid, kind in (('bf_gamification.view_gamification_level_list', 'list'), ('bf_gamification.view_gamification_level_form', 'form'), ('bf_gamification.view_badge_category_list', 'list'), ('bf_gamification.view_badge_category_form', 'form'),):
            self.assertIn('widget="bf_color_resolved"', self._arch(xmlid, kind), xmlid)
