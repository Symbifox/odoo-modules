from markupsafe import Markup

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.privacy_breach_notice.tests.common import BreachNoticeCase


@tagged("post_install", "-at_install")
class TestPasDeSms(BreachNoticeCase):

    def _note_au_client(self, notice):
        return notice.with_user(self.manager).message_post(
            body=Markup("<p>Note</p>"), message_type="comment", subtype_xmlid="mail.mt_comment")

    def test_aucun_sms_ne_se_rattache_au_fil(self):
        notice = self._breach()
        notice.action_send()
        message = self._note_au_client(notice)
        for qui in (self.reader, self.manager):
            with self.assertRaisesRegex(UserError, "ne part pas par SMS", msg=qui.name):
                self.env["sms.sms"].with_user(qui).sudo().create({
                    "number": "+15145550100", "body": "x", "mail_message_id": message.id})
        ailleurs = self.env["sms.sms"].with_user(self.reader).sudo().create({"number": "+15145550100", "body": "x"})
        with self.assertRaisesRegex(UserError, "ne part pas par SMS"):
            ailleurs.write({"mail_message_id": message.id})

    def test_le_renvoi_sms_ne_part_pas(self):
        """Par l'assistant réel : la notification existante est réécrite, puis le SMS créé."""
        notice = self._breach()
        notice.action_send()
        message = self._note_au_client(notice)
        notification = self.env["mail.notification"].create({
            "mail_message_id": message.id, "res_partner_id": self.reader.partner_id.id,
            "notification_type": "sms", "notification_status": "exception", "sms_number": "+15145550100"})
        wizard = self.env["sms.resend"].with_user(self.reader).with_context(
            default_mail_message_id=message.id).create({})
        wizard.recipient_ids.write({"sms_number": "+15145550199", "resend": True})
        with self.assertRaises(UserError):  # AccessError en hérite
            wizard.action_resend()
        self.assertFalse(self.env["sms.sms"].search([("mail_message_id", "=", message.id)]))
        self.assertEqual(notification.sms_number, "+15145550100")
