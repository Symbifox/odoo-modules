from datetime import datetime, timedelta

from odoo.tests import tagged

from odoo.addons.bf_work_hours.tests.common import WorkHoursCase


@tagged("post_install", "-at_install")
class TestEmailTraces(WorkHoursCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.accounts = {
            user: cls.env["bf.email.account"].sudo().create({
                "name": f"WH {user.login}", "user_id": user.id, "host": "imap.test.invalid", "port": 993,
                "login": f"{user.login}@test.invalid", "password": "x", "state": "connected",
            })
            for user in (cls.alice, cls.bob)
        }

    def email(self, user, folder="Sent", **vals):
        values = {"user_id": user.id, "account_id": self.accounts[user].id, "direction": "out",
                  "source": "imap", "imap_folder": folder, "subject": "WH",
                  "email_from": f"{user.login}@test.invalid", "date": datetime.now() - timedelta(minutes=10)}
        values.update(vals)
        email = self.env["bf.email"].sudo().create(values)
        email.sudo().write({"is_from_me": vals.get("is_from_me", True)})
        return email

    def email_items(self, user):
        now = datetime.now()
        return [i for i in self.env["bf.work.collector"]._bf_collect(user, now - timedelta(hours=1),
                                                                     now + timedelta(hours=1))
                if i.source == "email"]

    def test_sent_from_the_mail_client(self):
        self.email(self.alice)
        self.assertEqual(len(self.email_items(self.alice)), 1)
        self.assertEqual(self.env["bf.work.collector"]._bf_point_minutes()["email"], 5)

    def test_not_from_the_chatter_not_received_not_someone_else(self):
        self.email(self.alice, source="chatter")
        self.email(self.alice, direction="in")
        self.email(self.alice, is_from_me=False)
        self.email(self.bob)
        self.assertFalse(self.email_items(self.alice))

    def test_only_out_of_the_sent_folder(self):
        """🟡 The person's address in From, landed in the inbox (forged sender, shared alias): not theirs."""
        self.email(self.alice, folder="INBOX")
        self.assertFalse(self.email_items(self.alice))
