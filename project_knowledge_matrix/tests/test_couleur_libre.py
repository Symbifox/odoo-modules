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

    def test_type_de_document(self):
        self._porte_une_couleur_libre("project.document.type", {"name": "Essai couleur", "code": "ESSAI_COULEUR"})

    def test_element_prend_la_couleur_de_sa_section(self):
        section = self._porte_une_couleur_libre("project.knowledge.section", {"name": "Essai couleur", "code": "ESSAI_COULEUR"})
        self.assertNotIn("project.knowledge.item", self.env["bf.color.mixin"]._bf_color_model_names())
        self.assertEqual(self.env["project.knowledge.item"]._fields["color_resolved"].related, "section_id.color_resolved")
        arch = self._arch("project_knowledge_matrix.knowledge_item_view_kanban", "kanban")
        self.assertIn('highlight_color="color"', arch)
        self.assertIn('name="color_resolved"', arch)
        self.assertNotIn("kanban_getcolor", arch)
        self.assertTrue(section)

    def test_ma_couleur_a_une_porte(self):
        """« Ma couleur » s'ouvre par la couleur affichée."""
        for xmlid, kind in (('project_knowledge_matrix.view_document_type_tree', 'list'), ('project_knowledge_matrix.view_document_type_form', 'form'), ('project_knowledge_matrix.knowledge_section_view_list', 'list'), ('project_knowledge_matrix.knowledge_section_view_form', 'form'),):
            self.assertIn('widget="bf_color_resolved"', self._arch(xmlid, kind), xmlid)
