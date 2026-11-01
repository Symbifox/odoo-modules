from odoo import fields, models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _validate_session(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        result = super()._validate_session(balancing_account, amount_to_balance, bank_payment_method_diffs)
        self._bf_host_close()
        return result

    def _bf_host_close(self):
        """The service is over: the parties still waiting have left, the names and notes are erased."""
        for session in self.filtered(lambda s: s.config_id.bf_host and s.state == "closed"):
            waits = self.env["bf.host.wait"].sudo().search(
                [("config_id", "=", session.config_id.id), ("state", "=", "waiting")])
            waits.write({"state": "left"})
            config = [("config_id", "=", session.config_id.id), ("seated_at", "<=", fields.Datetime.now())]
            seated = self.env["bf.host.seating"].sudo().search(config + [("state", "=", "seated")])
            seated.write({"state": "left", "cleared_at": fields.Datetime.now()})
            self.env["bf.host.seating"].sudo().search(config + [("state", "=", "to_clear")]).write(
                {"state": "done", "cleared_at": fields.Datetime.now()})
            self.env["bf.host.seating"]._bf_purge(config)
            self.env["bf.host.wait"]._bf_purge([("config_id", "=", session.config_id.id)])
