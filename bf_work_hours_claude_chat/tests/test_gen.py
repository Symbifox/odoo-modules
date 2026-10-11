from datetime import datetime, timedelta

from odoo.tests import tagged

from odoo.addons.bf_work_hours.tests.common import WorkHoursCase


@tagged("post_install", "-at_install")
class TestGenTraces(WorkHoursCase):

    def prompt(self, user, origin="web", role="user", **extra):
        session = self.env["claude.chat.session"].with_user(user).sudo().create(
            {"user_id": user.id, "name": "WH", "origin": origin})
        return self.env["claude.chat.message"].with_user(user).sudo().create(
            dict({"session_id": session.id, "role": role, "content": "x"}, **extra))

    def gen_items(self, user):
        now = datetime.now()
        return [i for i in self.env["bf.work.collector"]._bf_collect(user, now - timedelta(hours=1),
                                                                     now + timedelta(hours=1))
                if i.source == "gen"]

    def test_a_typed_prompt_is_a_trace(self):
        self.prompt(self.alice)
        self.prompt(self.alice, origin="mobile")
        self.prompt(self.alice, origin="refine_meeting")
        self.assertEqual(len(self.gen_items(self.alice)), 3)

    def test_what_nobody_typed_is_not(self):
        self.prompt(self.alice, role="assistant")
        self.prompt(self.alice, internal=True)
        self.prompt(self.alice, followup=True)
        self.prompt(self.alice, origin="ocr")
        self.prompt(self.alice, origin="title")
        self.prompt(self.bob)
        self.assertFalse(self.gen_items(self.alice))

    def test_described_in_the_notice(self):
        keys = [k for k, _label, _text in self.env["bf.work.collector"]._bf_source_descriptions()]
        self.assertIn("gen", keys)
