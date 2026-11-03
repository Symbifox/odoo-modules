from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestIrregularity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Detector = cls.env["bf.irregularity.detector"]
        cls.Irr = cls.env["bf.irregularity"]
        cls.user = cls.env["res.users"].create({
            "name": "Irr Tester", "login": "irr_tester",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.partner = cls.env["res.partner"].create({"name": "Dossier témoin"})
        cls.today = fields.Date.context_today(cls.Detector)

    def _det(self, xmlid):
        return self.env.ref("bf_irregularity.%s" % xmlid)

    def _open(self, detector):
        return self.Irr.search([("detector_id", "=", detector.id), ("state", "=", "open")])

    def _activity(self, deadline, user):
        return self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("res.partner"),
            "res_id": self.partner.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "summary": "Rappeler",
            "date_deadline": deadline,
            "user_id": user.id,
        })

    def test_activity_overdue_detected_then_resolved(self):
        detector = self._det("detector_activity_overdue")
        fresh = self._activity(self.today - timedelta(days=2), self.user)
        stale = self._activity(self.today - timedelta(days=10), self.user)
        detector._run()
        irr = self._open(detector).filtered(lambda i: i.key == "mail.activity,%s" % stale.id)
        self.assertEqual(len(irr), 1)
        self.assertFalse(self._open(detector).filtered(lambda i: i.key == "mail.activity,%s" % fresh.id))
        self.assertEqual(irr.owner_id, self.user)
        self.assertEqual((irr.res_model, irr.res_id), ("res.partner", self.partner.id))
        self.assertFalse(detector.last_error)
        stale.action_feedback()
        detector._run()
        self.assertEqual(irr.state, "resolved")

    def test_activity_inactive_user(self):
        detector = self._det("detector_activity_inactive_user")
        act = self._activity(self.today + timedelta(days=3), self.user)
        self.user.active = False
        detector._run()
        irr = self._open(detector).filtered(lambda i: i.key == "mail.activity,%s" % act.id)
        self.assertEqual(len(irr), 1)
        self.assertEqual(irr.owner_id, detector.default_owner_id)

    def test_mail_failed(self):
        detector = self._det("detector_mail_failed")
        mail = self.env["mail.mail"].create({"subject": "Relevé", "body_html": "<p>x</p>", "email_to": "a@example.com"})
        mail.write({"state": "exception", "failure_reason": "101\nNetwork is unreachable"})
        detector._run()
        irr = self._open(detector).filtered(lambda i: i.res_id == mail.id)
        self.assertIn("Network is unreachable", irr.name)
        mail.write({"state": "sent"})
        detector._run()
        self.assertEqual(irr.state, "resolved")

    def test_scheduled_message_stuck(self):
        detector = self._det("detector_scheduled_message_stuck")
        msg = self.env["mail.scheduled.message"].create({
            "model": "res.partner", "res_id": self.partner.id,
            "author_id": self.env.user.partner_id.id, "subject": "Suivi",
            "body": "<p>x</p>", "scheduled_date": fields.Datetime.now() + timedelta(days=1),
        })
        # La contrainte native refuse une date passée : on recule en SQL.
        self.env.cr.execute("UPDATE mail_scheduled_message SET scheduled_date = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(hours=3), msg.id))
        msg.invalidate_recordset()
        detector._run()
        irr = self._open(detector).filtered(lambda i: i.key == "mail.scheduled.message,%s" % msg.id)
        self.assertEqual(len(irr), 1)
        self.assertEqual(irr.severity, "critical")

    def test_acknowledge_and_mute_are_kept(self):
        detector = self._det("detector_activity_overdue")
        act_a = self._activity(self.today - timedelta(days=10), self.user)
        act_b = self._activity(self.today - timedelta(days=11), self.user)
        detector._run()
        irr_a = self.Irr.search([("key", "=", "mail.activity,%s" % act_a.id)])
        irr_b = self.Irr.search([("key", "=", "mail.activity,%s" % act_b.id)])
        irr_a.action_acknowledge()
        irr_b.action_mute()
        detector._run()
        self.assertEqual(irr_a.state, "acknowledged")
        self.assertEqual(irr_b.state, "muted")
        irr_b.mute_until = self.today - timedelta(days=1)
        detector._run()
        self.assertEqual(irr_b.state, "open")

    def test_resolved_reopens(self):
        detector = self._det("detector_activity_overdue")
        act = self._activity(self.today - timedelta(days=10), self.user)
        detector._run()
        irr = self.Irr.search([("key", "=", "mail.activity,%s" % act.id)])
        act.date_deadline = self.today
        detector._run()
        self.assertEqual(irr.state, "resolved")
        act.date_deadline = self.today - timedelta(days=9)
        detector._run()
        self.assertEqual(irr.state, "open")
        self.assertFalse(irr.resolved_date)

    def test_checked_keys_limit_resolution(self):
        detector = self.Detector.create({"name": "Fenêtre", "code": "zz_window", "family": "silent"})
        partner = self.partner
        detector._sync([{"record": partner, "key": "k1", "message": "m"}, {"record": partner, "key": "k2", "message": "m"}])
        # k1 n'est plus vérifié (hors fenêtre), k2 l'est et n'est plus constaté.
        detector._sync([], checked_keys={"k2"})
        states = {i.key: i.state for i in self.Irr.search([("detector_id", "=", detector.id)])}
        self.assertEqual(states, {"k1": "open", "k2": "resolved"})

    def test_missing_method_is_reported_not_raised(self):
        detector = self.Detector.create({"name": "Absent", "code": "zz_missing", "family": "late"})
        detector._run()
        self.assertIn("_detect_zz_missing", detector.last_error)

    def test_broken_detector_does_not_block_others(self):
        broken = self.Detector.create({"name": "Cassé", "code": "activity_overdue_broken", "family": "late"})
        type(broken)._detect_activity_overdue_broken = lambda self: 1 / 0
        try:
            self._activity(self.today - timedelta(days=10), self.user)
            self.Detector._cron_run_all()
        finally:
            del type(broken)._detect_activity_overdue_broken
        self.assertIn("division by zero", broken.last_error)
        self.assertTrue(self._open(self._det("detector_activity_overdue")))

    def test_notify_activity(self):
        detector = self.Detector.create({
            "name": "Avis", "code": "zz_notify", "family": "late", "notify_activity": True,
            "default_owner_id": self.user.id,
        })
        detector._sync([{"record": self.partner, "message": "À regarder"}])
        acts = self.env["mail.activity"].search([("res_model", "=", "res.partner"), ("res_id", "=", self.partner.id), ("user_id", "=", self.user.id)])
        self.assertEqual(len(acts), 1)
        detector._sync([{"record": self.partner, "message": "À regarder"}])
        acts = self.env["mail.activity"].search([("res_model", "=", "res.partner"), ("res_id", "=", self.partner.id), ("user_id", "=", self.user.id)])
        self.assertEqual(len(acts), 1, "une irrégularité déjà connue ne replanifie rien")

    def test_record_rule_owner_only(self):
        other = self.env["res.users"].create({"name": "Autre", "login": "irr_other",
                                              "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        detector = self.Detector.create({"name": "Règle", "code": "zz_rule", "family": "late", "default_owner_id": other.id})
        detector._sync([{"record": self.partner, "message": "privé"}])
        self.assertFalse(self.Irr.with_user(self.user).search([("detector_id", "=", detector.id)]))
        self.assertTrue(self.Irr.with_user(other).search([("detector_id", "=", detector.id)]))
