from odoo.tests.common import TransactionCase


class TestBfProjectTaskMerge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        cls.Message = cls.env["mail.message"]
        cls.Activity = cls.env["mail.activity"]
        cls.note_subtype = cls.env.ref("mail.mt_note").id
        cls.mtypes = set(
            dict(
                cls.Message._fields["message_type"]._description_selection(cls.env)
            ).keys()
        )
        # Reuse an existing project: project.project may carry custom required
        # columns on this tenant. TransactionCase rolls back, so nothing leaks.
        cls.project = cls.env["project.project"].search([], limit=1)
        if not cls.project:
            cls.project = cls.env["project.project"].create({"name": "Merge QA"})
        cls.dst = cls.Task.create({"name": "Cible", "project_id": cls.project.id})
        cls.src = cls.Task.create({"name": "Source", "project_id": cls.project.id})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _msg(self, task, mtype, body, parent=None):
        vals = {
            "model": "project.task",
            "res_id": task.id,
            "message_type": mtype,
            "subtype_id": self.note_subtype,
            "body": body,
        }
        if parent:
            vals["parent_id"] = parent.id
        return self.Message.create(vals)

    def _merge_into(self, dst, *sources):
        rs = dst
        for t in sources:
            rs |= t
        wizard = self.env["bf.task.merge.wizard"].create(
            {
                "source_task_ids": [(6, 0, rs.ids)],
                "target_mode": "existing",
                "target_task_id": dst.id,
            }
        )
        return wizard.action_merge()

    def _merge_new(self, name, project, *sources):
        rs = self.env["project.task"]
        for t in sources:
            rs |= t
        wizard = self.env["bf.task.merge.wizard"].create(
            {
                "source_task_ids": [(6, 0, rs.ids)],
                "target_mode": "new",
                "new_task_name": name,
                "new_project_id": project.id,
            }
        )
        return wizard.action_merge()

    def _new_task(self, name, project=None):
        return self.Task.create(
            {"name": name, "project_id": (project or self.project).id}
        )

    # ------------------------------------------------------------------
    # Core behavior
    # ------------------------------------------------------------------
    def test_action_returns_destination(self):
        res = self._merge_into(self.dst, self.src)
        self.assertEqual(res.get("res_id"), self.dst.id)
        self.assertEqual(res.get("res_model"), "project.task")

    def test_sources_are_archived(self):
        self._merge_into(self.dst, self.src)
        self.assertFalse(self.src.active)
        self.assertTrue(self.dst.active)

    def test_guard_requires_two_tasks(self):
        from odoo.exceptions import UserError

        wizard = self.env["bf.task.merge.wizard"].create(
            {
                "source_task_ids": [(6, 0, self.src.ids)],
                "target_mode": "existing",
                "target_task_id": self.src.id,
            }
        )
        with self.assertRaises(UserError):
            wizard.action_merge()

    # ------------------------------------------------------------------
    # Chatter — every conversation type moves; system messages stay
    # ------------------------------------------------------------------
    def test_all_conversation_types_move(self):
        # user_notification is intentionally excluded: it is out of the
        # message_ids domain (so the merge never touches it) and a raw create
        # of one trips an unrelated Odoo reply_to quirk.
        moving = {}
        for mtype in ("comment", "email", "email_outgoing", "auto_comment"):
            if mtype in self.mtypes:
                moving[mtype] = self._msg(self.src, mtype, f"<p>{mtype}</p>")
        notif = self._msg(self.src, "notification", "<p>étape</p>")

        self._merge_into(self.dst, self.src)

        for mtype, msg in moving.items():
            self.assertEqual(
                msg.res_id, self.dst.id, f"{mtype} must move to destination"
            )
        self.assertEqual(notif.res_id, self.src.id, "notification stays on source")

    def test_tombstone_note_stays_on_source(self):
        self._merge_into(self.dst, self.src)
        bodies = " ".join(self.src.message_ids.mapped("body") or [])
        self.assertIn("regroupée dans", bodies.lower())

    def test_destination_gets_summary_note(self):
        self._merge_into(self.dst, self.src)
        bodies = " ".join(self.dst.message_ids.mapped("body") or [])
        self.assertIn("regroupées ici", bodies.lower())

    def test_parent_id_preserved_when_parent_stays(self):
        parent = self._msg(self.src, "notification", "<p>parent système</p>")
        child = self._msg(self.src, "comment", "<p>réponse</p>", parent=parent)
        # must not raise even though parent stays on source while child moves
        self._merge_into(self.dst, self.src)
        self.assertEqual(child.res_id, self.dst.id)
        self.assertEqual(parent.res_id, self.src.id)
        self.assertEqual(child.parent_id, parent)  # FK intact, only cosmetic

    # ------------------------------------------------------------------
    # Activities
    # ------------------------------------------------------------------
    def test_activities_are_reassigned(self):
        activity = self.src.activity_schedule(
            "mail.mail_activity_data_todo", summary="À faire"
        )
        self._merge_into(self.dst, self.src)
        self.assertEqual(activity.res_id, self.dst.id)

    # ------------------------------------------------------------------
    # Attachments — task-level re-pointed; message-level follow the message
    # ------------------------------------------------------------------
    def test_task_attachment_repointed(self):
        att = self.env["ir.attachment"].create(
            {"name": "tache.txt", "res_model": "project.task", "res_id": self.src.id}
        )
        self._merge_into(self.dst, self.src)
        self.assertEqual(att.res_id, self.dst.id)
        self.assertEqual(att.res_model, "project.task")

    def test_message_attachment_follows_message(self):
        msg = self._msg(self.src, "comment", "<p>avec pièce</p>")
        att = self.env["ir.attachment"].create(
            {"name": "msg.txt", "res_model": "mail.message", "res_id": msg.id}
        )
        msg.attachment_ids = [(4, att.id)]
        self._merge_into(self.dst, self.src)
        self.assertEqual(msg.res_id, self.dst.id)
        self.assertIn(att, msg.attachment_ids)
        self.assertEqual(att.res_id, msg.id)  # still linked to its message

    # ------------------------------------------------------------------
    # Followers — dedup-safe, no UNIQUE collision
    # ------------------------------------------------------------------
    def test_followers_dedup_no_collision(self):
        partners = self.env["res.partner"].search([], limit=2)
        if len(partners) < 2:
            self.skipTest("need two partners")
        shared, only_src = partners[0], partners[1]
        self.dst.message_subscribe(partner_ids=shared.ids)
        self.src.message_subscribe(partner_ids=(shared + only_src).ids)
        # must not raise on the shared follower
        self._merge_into(self.dst, self.src)
        followers = self.dst.message_partner_ids
        self.assertIn(shared, followers)
        self.assertIn(only_src, followers)
        # the shared follower exists exactly once on the destination
        count = self.env["mail.followers"].search_count(
            [
                ("res_model", "=", "project.task"),
                ("res_id", "=", self.dst.id),
                ("partner_id", "=", shared.id),
            ]
        )
        self.assertEqual(count, 1)

    # ------------------------------------------------------------------
    # Ratings & calendar events
    # ------------------------------------------------------------------
    def test_rating_repointed(self):
        if "rating.rating" not in self.env:
            self.skipTest("rating not installed")
        try:
            rating = self.env["rating.rating"].create(
                {
                    "res_model": "project.task",
                    "res_model_id": self.env["ir.model"]._get_id("project.task"),
                    "res_id": self.src.id,
                    "rating": 5,
                }
            )
        except Exception:
            self.skipTest("rating.rating create constraints not satisfiable here")
        self._merge_into(self.dst, self.src)
        self.assertEqual(rating.res_id, self.dst.id)

    def test_calendar_event_repointed(self):
        event = self.env["calendar.event"].create(
            {
                "name": "Réunion",
                "start": "2026-01-02 10:00:00",
                "stop": "2026-01-02 11:00:00",
                # res_model is a stored-related of res_model_id — set the m2o
                "res_model_id": self.env["ir.model"]._get_id("project.task"),
                "res_id": self.src.id,
            }
        )
        self.assertEqual(event.res_model, "project.task")
        self._merge_into(self.dst, self.src)
        self.assertEqual(event.res_id, self.dst.id)

    # ------------------------------------------------------------------
    # Timesheets
    # ------------------------------------------------------------------
    def _make_timesheet(self, task, project):
        employee = self.env["hr.employee"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        if not employee:
            employee = self.env["hr.employee"].create({"name": "QA Employé"})
        return self.env["account.analytic.line"].create(
            {
                "name": "Travail",
                "task_id": task.id,
                "project_id": project.id,
                "employee_id": employee.id,
                "unit_amount": 1.5,
            }
        )

    def test_timesheets_are_reassigned(self):
        # NB: under `-u bf_project_merge --test-enable` the test registry is
        # scoped to this module's dependency closure (project), so hr_timesheet
        # fields are absent and this skips. On the live server the field exists
        # and the re-pointing runs (validated by the persisted shell E2E).
        if "task_id" not in self.env["account.analytic.line"]._fields:
            self.skipTest("hr_timesheet not in test registry")
        line = self._make_timesheet(self.src, self.project)
        self._merge_into(self.dst, self.src)
        self.assertEqual(line.task_id, self.dst)

    def test_timesheets_cross_project_align(self):
        if "task_id" not in self.env["account.analytic.line"]._fields:
            self.skipTest("hr_timesheet not in test registry")
        projects = self.env["project.project"].search(
            [("allow_timesheets", "=", True)], limit=2
        )
        if len(projects) < 2:
            self.skipTest("need two timesheet projects")
        src = self._new_task("Src TS", projects[0])
        dst = self._new_task("Dst TS", projects[1])
        line = self._make_timesheet(src, projects[0])
        self._merge_into(dst, src)
        self.assertEqual(line.task_id, dst)
        self.assertEqual(line.project_id, projects[1])

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------
    def test_dependency_upstream_inherited(self):
        if "depend_on_ids" not in self.Task._fields:
            self.skipTest("task dependencies not available")
        upstream = self._new_task("Amont")
        self.src.depend_on_ids = [(4, upstream.id)]
        self._merge_into(self.dst, self.src)
        self.assertIn(upstream, self.dst.depend_on_ids)

    def test_dependency_downstream_repointed(self):
        if "depend_on_ids" not in self.Task._fields:
            self.skipTest("task dependencies not available")
        downstream = self._new_task("Aval")
        downstream.depend_on_ids = [(4, self.src.id)]
        self._merge_into(self.dst, self.src)
        self.assertIn(self.dst, downstream.depend_on_ids)

    def test_dependency_no_self_reference(self):
        if "depend_on_ids" not in self.Task._fields:
            self.skipTest("task dependencies not available")
        # source depends on the destination itself
        self.src.depend_on_ids = [(4, self.dst.id)]
        self._merge_into(self.dst, self.src)
        self.assertNotIn(self.dst, self.dst.depend_on_ids)

    # ------------------------------------------------------------------
    # Sub-tasks, multi-source, create-new
    # ------------------------------------------------------------------
    def test_subtask_reparented(self):
        child = self._new_task("Sous-tâche")
        child.parent_id = self.src.id
        self._merge_into(self.dst, self.src)
        self.assertEqual(child.parent_id, self.dst)

    def test_three_sources(self):
        s2 = self._new_task("Source 2")
        s3 = self._new_task("Source 3")
        m1 = self._msg(self.src, "comment", "<p>m1</p>")
        m2 = self._msg(s2, "comment", "<p>m2</p>")
        m3 = self._msg(s3, "comment", "<p>m3</p>")
        self._merge_into(self.dst, self.src, s2, s3)
        self.assertEqual(m1.res_id, self.dst.id)
        self.assertEqual(m2.res_id, self.dst.id)
        self.assertEqual(m3.res_id, self.dst.id)
        self.assertFalse(s2.active)
        self.assertFalse(s3.active)

    def test_create_new_task_moves_all(self):
        s2 = self._new_task("Source B")
        m1 = self._msg(self.src, "comment", "<p>a</p>")
        m2 = self._msg(s2, "email", "<p>b</p>")
        res = self._merge_new("Tâche fusionnée", self.project, self.src, s2)
        new_task = self.Task.browse(res["res_id"])
        self.assertTrue(new_task.exists())
        self.assertTrue(new_task.active)
        self.assertEqual(m1.res_id, new_task.id)
        self.assertEqual(m2.res_id, new_task.id)
        self.assertFalse(self.src.active)
        self.assertFalse(s2.active)
