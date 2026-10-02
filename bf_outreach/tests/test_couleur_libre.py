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

    def test_campagne_et_etiquette(self):
        self._porte_une_couleur_libre("bf.outreach.tag", {"name": "Essai couleur"})
        self._porte_une_couleur_libre("bf.outreach.campaign", {"name": "Essai couleur"})
        self.assertIn('name="color_resolved"', self._arch("bf_outreach.view_bf_outreach_campaign_kanban", "kanban"))
        for xmlid, kind in (("bf_outreach.view_bf_outreach_target_list", "list"),
                            ("bf_outreach.view_bf_outreach_target_kanban", "kanban"),
                            ("bf_outreach.view_bf_outreach_target_form", "form")):
            self.assertIn("'bf_color': True", self._arch(xmlid, kind), xmlid)
