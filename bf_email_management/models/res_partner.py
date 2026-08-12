from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_email_count = fields.Integer(
        string="Courriels",
        compute="_compute_bf_email_count",
    )

    def _compute_bf_email_count(self):
        if not self.ids:
            self.bf_email_count = 0
            return
        # Raw SQL bypasses record rules: scope to the current user so the
        # smart button matches what the drill-through action will show.
        self.env.cr.execute("""
            SELECT partner_id, COUNT(*) AS cnt
            FROM bf_email
            WHERE partner_id IN %s AND active = TRUE AND user_id = %s
            GROUP BY partner_id
        """, [tuple(self.ids), self.env.uid])
        counts = dict(self.env.cr.fetchall())
        for rec in self:
            rec.bf_email_count = counts.get(rec.id, 0)

    def action_view_bf_emails(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Courriels",
            "res_model": "bf.email",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }
