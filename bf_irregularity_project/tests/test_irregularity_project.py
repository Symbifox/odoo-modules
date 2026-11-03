from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestIrregularityProject(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Irr = cls.env["bf.irregularity"]
        cls.user = cls.env["res.users"].create({
            "name": "Chargé", "login": "irr_charge",
            "groups_id": [(6, 0, [cls.env.ref("project.group_project_user").id,
                                  cls.env.ref("hr_timesheet.group_hr_timesheet_user").id])],
        })
        cls.project = cls.env["project.project"].create({"name": "Projet témoin", "user_id": cls.user.id})

    def _det(self, code):
        return self.env["bf.irregularity.detector"].with_context(active_test=False).search([("code", "=", code)])

    def _keys(self, detector):
        return set(self.Irr.search([("detector_id", "=", detector.id), ("state", "=", "open")]).mapped("key"))

    def test_waiting_too_long_uses_state_tracking(self):
        detector = self._det("task_waiting_too_long")
        old = self.env["project.task"].create({"name": "Vieille attente", "project_id": self.project.id, "user_ids": [(6, 0, [self.user.id])]})
        new = self.env["project.task"].create({"name": "Attente récente", "project_id": self.project.id})
        # Odoo ne suit pas un enregistrement créé dans la même passe de pré-commit.
        self.env.cr.precommit.run()
        (old | new).write({"state": "05_waiting_client"})
        # Le suivi des champs s'écrit au pré-commit, que le test déclenche lui-même.
        self.env.cr.precommit.run()
        # Recule l'entrée en attente de la vieille tâche.
        self.env.cr.execute("""
            UPDATE mail_message SET date = %s
             WHERE model = 'project.task' AND res_id = %s
               AND id IN (SELECT mail_message_id FROM mail_tracking_value)
        """, (fields.Datetime.now() - timedelta(days=50), old.id))
        detector._run()
        self.assertFalse(detector.last_error)
        keys = self._keys(detector)
        self.assertIn("project.task,%s" % old.id, keys)
        self.assertNotIn("project.task,%s" % new.id, keys)
        irr = self.Irr.search([("key", "=", "project.task,%s" % old.id), ("detector_id", "=", detector.id)])
        self.assertEqual(irr.severity, "critical")
        self.assertEqual(irr.owner_id, self.user)
        old.state = "01_in_progress"
        self.env.cr.precommit.run()
        detector._run()
        self.assertEqual(irr.state, "resolved")

    def test_waiting_with_follow_up_within_30_days_is_skipped(self):
        detector = self._det("task_waiting_too_long")
        now = fields.Datetime.now()
        today = fields.Date.context_today(detector)
        Task = self.env["project.task"]
        names = ["échéance proche", "échéance lointaine", "échéance dépassée", "activité proche", "activité dépassée"]
        tasks = {n: Task.create({"name": n, "project_id": self.project.id}) for n in names}
        tasks["échéance proche"].date_deadline = now + timedelta(days=10)
        tasks["échéance lointaine"].date_deadline = now + timedelta(days=45)
        tasks["échéance dépassée"].date_deadline = now - timedelta(days=3)
        todo = self.env.ref("mail.mail_activity_data_todo")
        tasks["activité proche"].activity_schedule(activity_type_id=todo.id, date_deadline=today + timedelta(days=20))
        tasks["activité dépassée"].activity_schedule(activity_type_id=todo.id, date_deadline=today - timedelta(days=2))
        self.env.cr.precommit.run()
        ids = tuple(t.id for t in tasks.values())
        Task.browse(ids).write({"state": "06_waiting_external"})
        self.env.cr.precommit.run()
        self.env.cr.execute("""
            UPDATE mail_message SET date = %s
             WHERE model = 'project.task' AND res_id IN %s
               AND id IN (SELECT mail_message_id FROM mail_tracking_value)
        """, (now - timedelta(days=40), ids))
        detector._run()
        self.assertFalse(detector.last_error)
        keys = self._keys(detector)
        flagged = {n for n, t in tasks.items() if "project.task,%s" % t.id in keys}
        self.assertEqual(flagged, {"échéance lointaine", "échéance dépassée", "activité dépassée"})

    def test_task_in_archived_project(self):
        detector = self._det("task_in_archived_project")
        task = self.env["project.task"].create({"name": "Oubliée", "project_id": self.project.id})
        done = self.env["project.task"].create({"name": "Finie", "project_id": self.project.id, "state": "1_done"})
        self.project.active = False
        detector._run()
        keys = self._keys(detector)
        self.assertIn("project.task,%s" % task.id, keys)
        self.assertNotIn("project.task,%s" % done.id, keys)

    def test_unassigned_detector_ships_inactive(self):
        self.assertFalse(self._det("task_unassigned").active)
        detector = self._det("task_unassigned")
        task = self.env["project.task"].create({"name": "Personne", "project_id": self.project.id, "user_ids": False})
        detector._run()
        irr = self.Irr.search([("key", "=", "project.task,%s" % task.id), ("detector_id", "=", detector.id)])
        self.assertEqual(irr.owner_id, self.user)

    def test_timesheet_missing_day(self):
        detector = self._det("timesheet_missing_day")
        detector.threshold = 5
        employee = self.env["hr.employee"].create({"name": "Chargé", "user_id": self.user.id})
        self.env.cr.execute("UPDATE hr_employee SET create_date = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(days=30), employee.id))
        employee.invalidate_recordset()
        today = fields.Date.context_today(detector)
        days, day = [], today - timedelta(days=1)
        while len(days) < 5:
            if employee.resource_calendar_id._works_on_date(day):
                days.append(day)
            day -= timedelta(days=1)
        self.env["account.analytic.line"].create({
            "name": "Travail", "project_id": self.project.id, "employee_id": employee.id,
            "date": days[0], "unit_amount": 2,
        })
        detector._run()
        self.assertFalse(detector.last_error)
        irrs = self.Irr.search([("detector_id", "=", detector.id), ("res_id", "=", employee.id), ("state", "=", "open")])
        self.assertEqual(set(irrs.mapped("key")), {"hr.employee,%s,%s" % (employee.id, d) for d in days[1:]})
        self.assertEqual(irrs.owner_id, self.user)
        # Un jour sorti de la fenêtre reste ouvert.
        detector.threshold = 1
        detector._run()
        self.assertEqual(len(irrs.filtered(lambda i: i.state == "open")), 4)
