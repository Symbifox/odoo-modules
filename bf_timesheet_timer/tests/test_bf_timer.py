from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("bf_timesheet_timer", "bf_timer")
class TestBfTimer(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Timer = cls.env["bf.timer"]

        cls.user = cls.env["res.users"].create({
            "name": "Timer User",
            "login": "timer_user",
            "email": "timer@example.com",
        })
        cls.employee = cls.env["hr.employee"].create({
            "name": "Timer User",
            "user_id": cls.user.id,
        })
        cls.project = cls.env["project.project"].create({
            "name": "Timer Test Project",
            "allow_timesheets": True,
        })
        cls.task = cls.env["project.task"].create({
            "name": "Timer Task",
            "project_id": cls.project.id,
        })

    def _timer_env(self):
        return self.Timer.with_user(self.user)

    # --- _compute_suggested_minutes (rounding) ---

    def test_rounding_none_returns_ceil_minutes(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_timer.rounding_mode", "none"
        )
        # 61 seconds → ceil(1.016...) → 2
        result = self.Timer._compute_suggested_minutes(61)
        self.assertEqual(result, 2)

    def test_rounding_all_snaps_up_to_increment(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_timer.rounding_mode", "round_all")
        ICP.set_param("bf_timer.rounding_increment", "5")
        # 6 minutes of work → rounded up to next 5-minute boundary = 10
        result = self.Timer._compute_suggested_minutes(6 * 60)
        self.assertEqual(result, 10)

    def test_rounding_all_minimum_is_increment(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_timer.rounding_mode", "round_all")
        ICP.set_param("bf_timer.rounding_increment", "5")
        # 30 seconds < 5 minutes → minimum = 5
        result = self.Timer._compute_suggested_minutes(30)
        self.assertEqual(result, 5)

    def test_rounding_below_threshold_only_short_sessions(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_timer.rounding_mode", "round_below_threshold")
        ICP.set_param("bf_timer.rounding_increment", "5")
        ICP.set_param("bf_timer.rounding_threshold", "30")
        # 15 minutes < 30 threshold → round up to 15 (already on boundary)
        short = self.Timer._compute_suggested_minutes(15 * 60)
        self.assertEqual(short, 15)
        # 45 minutes > 30 threshold → don't round to 5, just ceil
        long = self.Timer._compute_suggested_minutes(45 * 60 + 10)
        self.assertEqual(long, 46)

    # --- get_rounding_settings ---

    def test_get_rounding_settings_returns_keys(self):
        s = self.Timer.get_rounding_settings()
        self.assertIn("mode", s)
        self.assertIn("increment", s)
        self.assertIn("threshold", s)

    # --- start_timer ---

    def test_start_timer_creates_active_record(self):
        result = self._timer_env().start_timer(self.task.id)
        self.assertIn("id", result)
        timer = self.Timer.browse(result["id"])
        self.assertTrue(timer.is_active)
        self.assertEqual(timer.task_id, self.task)

    def test_start_timer_rejects_duplicate_for_same_task(self):
        self._timer_env().start_timer(self.task.id)
        with self.assertRaises(UserError):
            self._timer_env().start_timer(self.task.id)

    def test_start_timer_rejects_non_timesheet_task(self):
        no_ts_project = self.env["project.project"].create({
            "name": "No TS",
            "allow_timesheets": False,
        })
        task = self.env["project.task"].create({
            "name": "No TS task",
            "project_id": no_ts_project.id,
        })
        with self.assertRaises(UserError):
            self._timer_env().start_timer(task.id)

    # --- pause / resume ---

    def test_pause_accumulates_seconds(self):
        result = self._timer_env().start_timer(self.task.id)
        timer = self.Timer.browse(result["id"])
        self._timer_env().pause_timer(timer.id)
        timer.invalidate_recordset()
        self.assertTrue(timer.is_paused)
        # accumulated_seconds may be ~0 for fast tests, but flag must flip
        self.assertGreaterEqual(timer.accumulated_seconds, 0)

    def test_pause_twice_raises(self):
        result = self._timer_env().start_timer(self.task.id)
        timer = self.Timer.browse(result["id"])
        self._timer_env().pause_timer(timer.id)
        with self.assertRaises(UserError):
            self._timer_env().pause_timer(timer.id)

    def test_resume_unpauses(self):
        result = self._timer_env().start_timer(self.task.id)
        timer = self.Timer.browse(result["id"])
        self._timer_env().pause_timer(timer.id)
        self._timer_env().resume_timer(timer.id)
        timer.invalidate_recordset()
        self.assertFalse(timer.is_paused)

    def test_resume_not_paused_raises(self):
        result = self._timer_env().start_timer(self.task.id)
        timer = self.Timer.browse(result["id"])
        with self.assertRaises(UserError):
            self._timer_env().resume_timer(timer.id)

    # --- stop / confirm / discard ---

    def test_stop_timer_deactivates_and_returns_data(self):
        result = self._timer_env().start_timer(self.task.id)
        stop_data = self._timer_env().stop_timer(result["id"])
        self.assertIn("suggested_hours", stop_data)
        self.assertIn("suggested_minutes", stop_data)
        self.assertFalse(self.Timer.browse(result["id"]).is_active)

    def test_discard_timer_removes_record(self):
        result = self._timer_env().start_timer(self.task.id)
        tid = result["id"]
        self._timer_env().discard_timer(tid)
        self.assertFalse(self.Timer.browse(tid).exists())

    def test_confirm_timesheet_rejects_zero_duration(self):
        result = self._timer_env().start_timer(self.task.id)
        self._timer_env().stop_timer(result["id"])
        with self.assertRaises(ValidationError):
            self._timer_env().confirm_timesheet(result["id"], 0, "desc")

    def test_confirm_timesheet_creates_analytic_line(self):
        result = self._timer_env().start_timer(self.task.id)
        self._timer_env().stop_timer(result["id"])
        self._timer_env().confirm_timesheet(result["id"], 1.5, "Test work")
        line = self.env["account.analytic.line"].search([
            ("task_id", "=", self.task.id),
            ("name", "=", "Test work"),
        ], limit=1)
        self.assertTrue(line)
        self.assertEqual(line.unit_amount, 1.5)
        # Timer should have been removed
        self.assertFalse(self.Timer.browse(result["id"]).exists())

    # --- pin / unpin ---

    def test_pin_and_unpin_task(self):
        self._timer_env().pin_task(self.task.id)
        pinned = self.env["bf.timer.pinned.task"].search([
            ("user_id", "=", self.user.id),
            ("task_id", "=", self.task.id),
        ])
        self.assertEqual(len(pinned), 1)
        # Pinning twice should not duplicate
        self._timer_env().pin_task(self.task.id)
        self.assertEqual(
            self.env["bf.timer.pinned.task"].search_count([
                ("user_id", "=", self.user.id),
                ("task_id", "=", self.task.id),
            ]),
            1,
        )
        self._timer_env().unpin_task(self.task.id)
        self.assertFalse(self.env["bf.timer.pinned.task"].search([
            ("user_id", "=", self.user.id),
            ("task_id", "=", self.task.id),
        ]))
