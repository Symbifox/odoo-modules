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

    def test_groupe_et_etiquette(self):
        partner = self.env["res.partner"].create({"name": "Essai couleur"})
        self._porte_une_couleur_libre("hosting.endpoint.group", {"name": "Essai couleur", "partner_id": partner.id})
        self._porte_une_couleur_libre("hosting.service.tag", {"name": "Essai couleur"})
        self.assertIn("'bf_color': True", self._arch("hosting_management.hosting_service_view_form", "form"))
        self.assertIn("'bf_color': True", self._arch("hosting_management.hosting_endpoint_view_form", "form"))
