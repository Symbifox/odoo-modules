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

    def test_plage_horaire(self):
        self._porte_une_couleur_libre("bf.time.of.day", {"name": "Essai couleur", "code": "essai_couleur"})

    def test_pastille_de_la_tache(self):
        slot = self._porte_une_couleur_libre("bf.time.of.day", {"name": "Essai pastille", "code": "essai_pastille"})
        project = self.env["project.project"].create({"name": "Essai pastille"})
        task = self.env["project.task"].create({"name": "Essai", "project_id": project.id, "time_of_day_id": slot.id})
        self.assertEqual(task.time_of_day_color_resolved, "#0072B2")
        self.assertEqual(task.time_of_day_color_text, slot.color_text)
        arch = self._arch("project.view_task_kanban", "kanban")
        self.assertIn("time_of_day_color_resolved", arch)
        self.assertIn("--background-color:", arch)

    def test_ma_couleur_a_une_porte(self):
        """« Ma couleur » s'ouvre par la couleur affichée."""
        for xmlid, kind in (('bf_time_of_day.bf_time_of_day_view_list', 'list'), ('bf_time_of_day.bf_time_of_day_view_form', 'form'),):
            self.assertIn('widget="bf_color_resolved"', self._arch(xmlid, kind), xmlid)
