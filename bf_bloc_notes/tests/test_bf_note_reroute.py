from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("bf_bloc_notes")
class TestBfNoteReroute(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Note = cls.env["bf.note"]
        cls.Link = cls.env["bf.note.link"]
        cls.Reroute = cls.env["bf.note.reroute"]
        cls.partner = cls.env["res.partner"].create({"name": "Reroute Partner"})
        cls.other_partner = cls.env["res.partner"].create({"name": "Reroute Partner 2"})
        cls.project = cls.env["project.project"].create({"name": "Reroute Project"})
        cls.task = cls.env["project.task"].create({
            "name": "Reroute Task",
            "project_id": cls.project.id,
        })

    def _note_on(self, model, res_id, **extra):
        vals = {
            "body": "<p>Note à re-router</p>",
            "link_ids": [(0, 0, {"res_model": model, "res_id": res_id})],
        }
        vals.update(extra)
        return self.Note.create(vals)

    # ------------------------------------------------------------------
    # Sélection des modèles compatibles
    # ------------------------------------------------------------------
    def test_selection_covers_every_thread_model(self):
        """Toute fiche à chatter est une cible — pas seulement les 10 historiques."""
        selection = dict(self.Note._selection_target_model())
        self.assertIn("res.partner", selection)
        self.assertIn("project.task", selection)
        # bf.note porte mail.thread et ne figurait pas dans la liste historique.
        self.assertIn("bf.note", selection)
        self.assertGreater(len(selection), 10)

    def test_selection_excludes_non_thread_models(self):
        selection = dict(self.Note._selection_target_model())
        self.assertNotIn("ir.ui.view", selection)

    def test_selection_priority_models_come_first(self):
        selection = self.Note._selection_target_model()
        self.assertEqual(selection[0][0], "res.partner")

    def test_selection_honours_allowlist_param(self):
        """Le paramètre reste une liste blanche pour qui veut restreindre."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_bloc_notes.reference_models", "res.partner,project.task",
        )
        selection = dict(self.Note._selection_target_model())
        self.assertEqual(set(selection), {"res.partner", "project.task"})

    # ------------------------------------------------------------------
    # Re-routage
    # ------------------------------------------------------------------
    def test_reroute_replace_moves_the_link(self):
        note = self._note_on("res.partner", self.partner.id)
        wizard = self.Reroute.create({
            "note_ids": [(6, 0, note.ids)],
            "target_reference": f"project.task,{self.task.id}",
            "mode": "replace",
        })
        wizard.action_confirm()
        self.assertEqual(len(note.link_ids), 1)
        self.assertEqual(note.res_model, "project.task")
        self.assertEqual(note.res_id, self.task.id)

    def test_reroute_add_keeps_the_previous_link(self):
        note = self._note_on("res.partner", self.partner.id)
        wizard = self.Reroute.create({
            "note_ids": [(6, 0, note.ids)],
            "target_reference": f"project.task,{self.task.id}",
            "mode": "add",
        })
        wizard.action_confirm()
        self.assertEqual(len(note.link_ids), 2)
        self.assertEqual(
            {(link.res_model, link.res_id) for link in note.link_ids},
            {("res.partner", self.partner.id), ("project.task", self.task.id)},
        )

    def test_reroute_to_model_outside_the_legacy_list(self):
        """Une note peut être routée vers un modèle absent des 10 historiques."""
        host = self.Note.create({"body": "<p>Note hôte</p>"})
        note = self._note_on("res.partner", self.partner.id)
        wizard = self.Reroute.create({
            "note_ids": [(6, 0, note.ids)],
            "target_reference": f"bf.note,{host.id}",
        })
        wizard.action_confirm()
        self.assertEqual(note.res_model, "bf.note")
        self.assertEqual(note.res_id, host.id)
        # Et la fiche redevient visible dans « Lien primaire ».
        self.assertEqual(note.res_ref, host)

    def test_reroute_is_idempotent_on_the_same_target(self):
        note = self._note_on("project.task", self.task.id)
        wizard = self.Reroute.create({
            "note_ids": [(6, 0, note.ids)],
            "target_reference": f"project.task,{self.task.id}",
            "mode": "replace",
        })
        wizard.action_confirm()
        self.assertEqual(len(note.link_ids), 1)

    def test_reroute_bulk(self):
        notes = self._note_on("res.partner", self.partner.id)
        notes |= self._note_on("res.partner", self.other_partner.id)
        wizard = self.Reroute.create({
            "note_ids": [(6, 0, notes.ids)],
            "target_reference": f"project.task,{self.task.id}",
        })
        result = wizard.action_confirm()
        for note in notes:
            self.assertEqual(note.res_model, "project.task")
            self.assertEqual(note.res_id, self.task.id)
        self.assertEqual(wizard.state, "done")
        self.assertIn("2/2", wizard.result_text)
        self.assertEqual(result["res_model"], "bf.note.reroute")

    def test_reroute_an_archived_note(self):
        """Une note archivée doit rester re-routable (m2m + active_test)."""
        note = self._note_on("res.partner", self.partner.id)
        note.active = False
        wizard = self.Reroute.create({
            "note_ids": [(6, 0, note.ids)],
            "target_reference": f"project.task,{self.task.id}",
        })
        self.assertEqual(wizard.note_ids, note)
        wizard.action_confirm()
        self.assertEqual(note.res_model, "project.task")
        self.assertEqual(note.res_id, self.task.id)

    def test_reroute_without_target_raises(self):
        note = self._note_on("res.partner", self.partner.id)
        wizard = self.Reroute.create({"note_ids": [(6, 0, note.ids)]})
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_reroute_defaults_from_active_ids(self):
        note = self._note_on("res.partner", self.partner.id)
        wizard = self.Reroute.with_context(
            active_ids=note.ids, active_model="bf.note",
        ).create({})
        self.assertEqual(wizard.note_ids, note)
        self.assertEqual(wizard.note_count, 1)
        self.assertEqual(wizard.current_target, self.partner.display_name)

    # ------------------------------------------------------------------
    # Lien rapide
    # ------------------------------------------------------------------
    def test_quick_paste_technical_reference(self):
        target = self.Reroute._resolve_quick_paste(f"project.task:{self.task.id}")
        self.assertEqual(target, self.task)

    def test_quick_paste_alias(self):
        target = self.Reroute._resolve_quick_paste(f"task:{self.task.id}")
        self.assertEqual(target, self.task)
        target = self.Reroute._resolve_quick_paste(f"contact:{self.partner.id}")
        self.assertEqual(target, self.partner)

    def test_quick_paste_bare_id(self):
        target = self.Reroute._resolve_quick_paste(str(self.task.id))
        self.assertEqual(target, self.task)

    def test_quick_paste_odoo_18_url_uses_action_path(self):
        target = self.Reroute._resolve_quick_paste(
            f"https://example.invalid/odoo/contacts/{self.partner.id}"
        )
        self.assertEqual(target, self.partner)

    def test_quick_paste_legacy_web_url(self):
        target = self.Reroute._resolve_quick_paste(
            f"https://example.invalid/web#id={self.partner.id}&model=res.partner&view_type=form"
        )
        self.assertEqual(target, self.partner)

    def test_quick_paste_project_task_url(self):
        target = self.Reroute._resolve_quick_paste(
            f"https://example.invalid/odoo/project/{self.project.id}/{self.task.id}"
        )
        self.assertEqual(target, self.task)

    def test_quick_paste_rejects_incompatible_model(self):
        view = self.env["ir.ui.view"].search([], limit=1)
        self.assertFalse(
            self.Reroute._resolve_quick_paste(f"ir.ui.view:{view.id}")
        )

    def test_quick_paste_unknown_returns_none(self):
        self.assertFalse(self.Reroute._resolve_quick_paste("n'importe quoi"))
        self.assertFalse(self.Reroute._resolve_quick_paste(""))

    def test_onchange_quick_paste_sets_target(self):
        note = self._note_on("res.partner", self.partner.id)
        wizard = self.Reroute.create({"note_ids": [(6, 0, note.ids)]})
        wizard.quick_paste = f"task:{self.task.id}"
        wizard._onchange_quick_paste()
        self.assertEqual(wizard.target_reference, self.task)

    # ------------------------------------------------------------------
    # Sélecteur de fiche sur la ligne de lien
    # ------------------------------------------------------------------
    def test_link_target_ref_create(self):
        note = self.Note.create({"body": "<p>Sans lien</p>"})
        link = self.Link.create({
            "note_id": note.id,
            "target_ref": f"project.task,{self.task.id}",
        })
        self.assertEqual(link.res_model, "project.task")
        self.assertEqual(link.res_id, self.task.id)
        self.assertEqual(link.target_ref, self.task)

    def test_link_target_ref_write(self):
        note = self._note_on("res.partner", self.partner.id)
        link = note.link_ids
        link.target_ref = f"project.task,{self.task.id}"
        self.assertEqual(link.res_model, "project.task")
        self.assertEqual(link.res_id, self.task.id)

    def test_link_target_ref_false_when_outside_selection(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_bloc_notes.reference_models", "project.task",
        )
        note = self._note_on("res.partner", self.partner.id)
        note.link_ids.invalidate_recordset(["target_ref"])
        self.assertFalse(note.link_ids.target_ref)

    # ------------------------------------------------------------------
    # Activités : garde-fou sur les modèles sans mail.activity.mixin
    # ------------------------------------------------------------------
    def test_activity_skips_models_without_activity_mixin(self):
        event = self.env["calendar.event"].create({
            "name": "Rencontre test",
            "start": "2026-08-10 14:00:00",
            "stop": "2026-08-10 15:00:00",
        })
        # calendar.event expose un `activity_ids` (les activités qui ont
        # engendré la rencontre) sans porter mail.activity.mixin — d'où le test
        # sur `is_mail_activity` plutôt que sur la présence du champ.
        self.assertFalse(
            self.env["ir.model"].sudo().search([
                ("model", "=", "calendar.event"), ("is_mail_activity", "=", True),
            ])
        )
        note = self._note_on("calendar.event", event.id)
        note.action_create_activity_quick()
        activity = note.tracked_activity_ids
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.res_model, "bf.note")
        self.assertEqual(activity.res_id, note.id)
