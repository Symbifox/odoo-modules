from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRiskScore(TransactionCase):

    def _make_result(self, difficulty, fail_state):
        template = self.env["bf.phishing.template"].create({
            "name": "T-%s" % difficulty, "subject": "S", "difficulty": difficulty})
        partner = self.env["res.partner"].create({
            "name": "P-%s-%s" % (difficulty, fail_state),
            "email": "%s.%s@example.com" % (difficulty, fail_state)})
        campaign = self.env["bf.phishing.campaign"].create({
            "name": "C", "template_id": template.id,
            "auto_assign_training": False,
            "recipient_partner_ids": [(6, 0, partner.ids)]})
        campaign.action_prepare()
        res = campaign.result_ids[0]
        res.register_sent()
        if fail_state in ("opened", "clicked", "submitted"):
            res.register_open()
        if fail_state in ("clicked", "submitted"):
            res.register_click()
        if fail_state == "submitted":
            res.register_submit()
        return res.profile_id

    def test_submitted_easy_is_critical(self):
        profile = self._make_result("easy", "submitted")
        profile.action_recompute()
        self.assertEqual(profile.risk_score, 100.0)
        self.assertEqual(profile.risk_level, "critical")

    def test_clicked_medium_is_high(self):
        profile = self._make_result("medium", "clicked")
        profile.action_recompute()
        # raw = 60 * 0.8 * recency / (0.8 * recency) = 60 -> high
        self.assertEqual(profile.risk_score, 60.0)
        self.assertEqual(profile.risk_level, "high")

    def test_opened_hard_is_moderate(self):
        profile = self._make_result("hard", "opened")
        profile.action_recompute()
        self.assertEqual(profile.risk_score, 20.0)
        self.assertEqual(profile.risk_level, "moderate")

    def test_sent_not_opened_is_low(self):
        profile = self._make_result("easy", "sent")
        profile.action_recompute()
        self.assertEqual(profile.risk_score, 0.0)
        self.assertEqual(profile.risk_level, "low")

    def test_report_reduces_score(self):
        profile = self._make_result("easy", "clicked")
        # Mark the single result as reported -> weight_reported (-40) applies.
        profile.result_ids.register_report()
        profile.action_recompute()
        # raw = (60 - 40) * 1.0 / 1.0 = 20 -> moderate
        self.assertEqual(profile.risk_score, 20.0)
        self.assertEqual(profile.risk_level, "moderate")
