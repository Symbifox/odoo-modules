"""What the notices say, and who they seem to come from.

Found by the real sends of 18.0.1.0.1: after a refusal, the next person's
activity was "assigned" by the colleague who had just refused, which revealed
the refusal; the activity email mixed two languages; the late-change notice
did not say that an answer was expected.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import ShiftCase


@tagged("post_install", "-at_install")
class TestNotices(ShiftCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_french()
        cls.env.company.partner_id.email = "company@example.com"

    def _offer(self, responsible=None):
        pool = self.env["bf.shift.pool"].create({
            "name": "List", "agreement_id": self.agreement.id,
            "member_ids": [(0, 0, {"employee_id": e.id}) for e in (self.e1, self.e2, self.e3)]})
        sched = self.schedule()
        if responsible is not None:
            sched.user_id = responsible
        open_shift = self.shift(sched, False, self.sunday + timedelta(days=3))
        self.publish(sched)
        offer = self.env["bf.shift.offer"].with_user(self.u_mgr).create({
            "assignment_id": open_shift.id, "pool_id": pool.id})
        offer.action_start()
        return offer

    def _refuse(self, offer, user):
        line = offer.line_ids.filtered(lambda l: l.state == "offered")
        self.assertEqual(line.employee_id.user_id, user)
        self.env["bf.shift.refuse.wizard"].with_user(user).create(
            {"offer_line_id": line.id, "reason": "Personal"}).action_confirm()

    def _notices(self, offer, user):
        return self.env["mail.message"].sudo().search([
            ("model", "=", "bf.shift.offer"), ("res_id", "=", offer.id),
            ("message_type", "=", "user_notification"),
            ("partner_ids", "in", user.partner_id.ids)], order="id")

    def setUp(self):
        super().setUp()
        self.sent = []
        IrMailServer = type(self.env["ir.mail_server"])

        def send_email(server, message, *args, **kwargs):
            self.sent.append(message)
            return message["Message-Id"]
        self.patch(IrMailServer, "send_email", send_email)

    def _html(self, user):
        """The HTML of the last email sent to ``user``, as it left."""
        for message in reversed(self.sent):
            if user.email in message["To"]:
                for part in message.walk():
                    if part.get_content_type() == "text/html":
                        return part.get_content()
        self.fail("no email to %s" % user.email)

    def test_next_activity_is_given_by_the_responsible(self):
        offer = self._offer(responsible=self.u_mgr)
        self._refuse(offer, self.u1)   # Ana refuses: Bea is next
        activity = offer.activity_ids.filtered(lambda a: a.user_id == self.u2)
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.create_uid, self.u_mgr, "attributed to the responsible, not to Ana")
        notice = self._notices(offer, self.u2)
        self.assertEqual(len(notice), 1)
        self.assertEqual(notice.author_id, self.u_mgr.partner_id)
        self.assertNotIn("Ana", notice.body)
        self.assertNotIn("Ana", notice.subject)
        self.assertFalse(offer.activity_ids.filtered(lambda a: a.user_id == self.u1),
                         "Ana's activity is gone")
        self.assertFalse(self.env["mail.message"].sudo().search([
            ("model", "=", "bf.shift.offer"), ("res_id", "=", offer.id),
            ("mail_activity_type_id", "!=", False)]),
            "removed, not marked done: no 'activity done' message signed by Ana")

    def test_exhausted_list_is_signed_by_the_company(self):
        offer = self._offer(responsible=self.u_mgr)
        for user in (self.u1, self.u2, self.u3):
            self._refuse(offer, user)
        self.assertEqual(offer.state, "unfilled")
        activity = offer.activity_ids.filtered(lambda a: a.user_id == self.u_mgr)
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.create_uid, self.u_mgr, "not Cyd, who refused last")
        notice = self._notices(offer, self.u_mgr)
        self.assertEqual(len(notice), 1, "told, even though the activity is their own")
        self.assertEqual(notice.author_id, self.env.company.partner_id)
        self.assertNotIn("Cyd", notice.body)
        # The message alone is not the email: Odoo drops the acting user from
        # the recipients, and the activity is handed out in the responsible's name.
        self.assertIn("est épuisée" if self.u_mgr.lang == "fr_CA" else "is exhausted",
                      self._html(self.u_mgr), "the email left too")

    def test_without_responsible_the_company_signs(self):
        offer = self._offer(responsible=False)
        self._refuse(offer, self.u1)
        activity = offer.activity_ids.filtered(lambda a: a.user_id == self.u2)
        self.assertNotIn(activity.create_uid, self.u1 | self.u2 | self.u3)
        notice = self._notices(offer, self.u2)
        self.assertEqual(notice.author_id, self.env.company.partner_id)

    def test_activity_email_in_one_language(self):
        """The subject, the Activity / Deadline header and the summary all
        follow the person offered, never the one who acts."""
        self.u_mgr.lang = "fr_CA"
        self.u1.lang = "fr_CA"
        self.u2.lang = "en_US"
        offer = self._offer(responsible=self.u_mgr)
        self._refuse(offer.with_context(lang="fr_CA"), self.u1)
        activity = offer.activity_ids.filtered(lambda a: a.user_id == self.u2)
        self.assertIn("Answer by", activity.summary)
        notice = self._notices(offer, self.u2)
        self.assertIn("Open shift offered", notice.subject)
        html = self._html(self.u2)
        self.assertIn("Activity:", html)
        self.assertIn("Deadline:", html)
        for french in ("Activité", "Échéance", "Répondre", "Quart", "Offre"):
            self.assertNotIn(french, html, "English email with French: %s" % french)
        # And the other way round.
        self.u2.lang = "fr_CA"
        self.u3.lang = "fr_CA"
        self.u_mgr.lang = "en_US"
        self._refuse(offer.with_user(self.u_mgr).with_context(lang="en_US"), self.u2)
        notice = self._notices(offer, self.u3)
        self.assertIn("Quart à combler offert", notice.subject)
        html = self._html(self.u3)
        self.assertIn("Activité", html)
        for english in ("Activity:", "Deadline:", "Answer by", "Open shift"):
            self.assertNotIn(english, html, "French email with English: %s" % english)

    def test_late_change_asks_for_an_answer(self):
        tomorrow = fields.Date.context_today(self.env["bf.shift.schedule"]) + timedelta(days=1)
        sched = self.schedule(start=tomorrow, days=3)
        a = self.shift(sched, self.e1, tomorrow)
        self.publish(sched)
        a.with_user(self.u_mgr).write({"employee_id": self.e2.id})
        change = a.change_ids
        self.assertEqual(change.consent, "pending")
        to_bea = self.env["mail.message"].sudo().search([
            ("model", "=", "bf.shift.assignment"), ("res_id", "=", a.id),
            ("message_type", "=", "user_notification"),
            ("partner_ids", "in", self.u2.partner_id.ids)])
        self.assertEqual(len(to_bea), 1)
        self.assertIn("answer is expected", to_bea.body)
        self.assertIn("/mail/view?model=bf.shift.change&amp;res_id=%s" % change.id, to_bea.body)
        self.assertIn("Answer expected", to_bea.subject)
        to_ana = self.env["mail.message"].sudo().search([
            ("model", "=", "bf.shift.assignment"), ("res_id", "=", a.id),
            ("message_type", "=", "user_notification"),
            ("partner_ids", "in", self.u1.partner_id.ids)])
        self.assertEqual(len(to_ana), 1)
        self.assertNotIn("answer is expected", to_ana.body, "Ana has nothing to answer")
        # A change far enough ahead asks nothing.
        sched2 = self.schedule(start=self.sunday + timedelta(days=14))
        b = self.shift(sched2, self.e1, self.sunday + timedelta(days=15))
        self.publish(sched2)
        b.with_user(self.u_mgr).write({"employee_id": self.e2.id})
        msg = self.env["mail.message"].sudo().search([
            ("model", "=", "bf.shift.assignment"), ("res_id", "=", b.id),
            ("message_type", "=", "user_notification"),
            ("partner_ids", "in", self.u2.partner_id.ids)])
        self.assertNotIn("answer is expected", msg.body)
