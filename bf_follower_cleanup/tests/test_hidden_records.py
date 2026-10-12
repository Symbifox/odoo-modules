from odoo.tests import TransactionCase, tagged

from ..models.mail_followers import PARAM_ALWAYS_REMOVE, PARAM_BATCH


@tagged("post_install", "-at_install")
class TestHiddenRecords(TransactionCase):
    """The crons run as the administrator, not as the superuser. Where
    bf_record_privacy is installed, a follower line of a record the
    administrator cannot read is hidden from them: the unlink must not read it
    under their name."""

    def test_the_cron_removes_lines_of_records_its_user_cannot_read(self):
        admin = self.env.ref("base.user_admin")
        self.env["ir.rule"].create({
            "name": "FC test: hidden contacts",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain_force": "[('ref', '!=', 'FC-HIDDEN')]",
            "groups": [(4, self.env.ref("base.group_user").id)],
        })
        hidden = self.env["res.partner"].create({"name": "FC hidden contact", "ref": "FC-HIDDEN"})
        outsider = self.env["res.partner"].create({"name": "FC outsider"})
        hidden.message_subscribe(partner_ids=outsider.ids)
        line = self.env["mail.followers"].search(
            [("res_model", "=", "res.partner"), ("res_id", "=", hidden.id), ("partner_id", "=", outsider.id)])
        self.assertTrue(line)
        if self.env["mail.followers"].with_user(admin).search_count([("id", "=", line.id)]):
            self.skipTest("bf_record_privacy is not installed: the administrator sees the line")
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param(PARAM_ALWAYS_REMOVE, str(outsider.id))
        ICP.set_param(PARAM_BATCH, "1000000")
        # A cron starts with an empty cache: the unlink then reads the line under its user's name.
        self.env.invalidate_all()
        self.env["mail.followers"].with_user(admin)._cron_remove_non_internal_followers()
        self.assertFalse(line.exists())
