from datetime import timedelta

from odoo import api, fields, models


class SchoolAccountPassword(models.TransientModel):
    """Shows a freshly drawn password once, to the person who drew it.

    The value lives in a transient row that only its creator can read (record
    rule) and that the transient vacuum removes; nothing keeps it after that.
    """

    _name = "bf.school.account.password"
    _description = "Student password, shown once"
    _transient_max_hours = 0.1

    account_id = fields.Many2one("bf.school.account", readonly=True, required=True)
    student_id = fields.Many2one(related="account_id.student_id")
    username = fields.Char(related="account_id.username")
    password = fields.Char(readonly=True)
    expired = fields.Boolean(compute="_compute_expired")

    @api.depends("create_date")
    def _compute_expired(self):
        limit = fields.Datetime.now() - timedelta(minutes=5)
        for wizard in self:
            wizard.expired = bool(wizard.create_date and wizard.create_date < limit)

    def _read_format(self, fnames, load="_classic_read"):
        # _read_format serves read(), web_read() and search_read() alike: an
        # override of read() alone left search_read() showing an expired password.
        rows = super()._read_format(fnames, load)
        expired = {w.id for w in self if w.expired}
        for row in rows:
            if row.get("id") in expired and "password" in row:
                row["password"] = False
        return rows

    @api.model
    def _purge_expired(self):
        """Delete shown passwords past their five minutes, so none waits for the
        daily transient vacuum (and for a backup) in the database."""
        limit = fields.Datetime.now() - timedelta(minutes=5)
        self.sudo().search([("create_date", "<", limit)]).unlink()

    def action_done(self):
        self.sudo().unlink()
        return {"type": "ir.actions.act_window_close"}
