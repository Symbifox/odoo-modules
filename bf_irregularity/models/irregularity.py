from datetime import timedelta

from odoo import _, api, fields, models

from .irregularity_detector import SEVERITIES

SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


class BfIrregularity(models.Model):
    _name = "bf.irregularity"
    _description = "Irregularity"
    _order = "state_rank, severity_rank, first_seen"
    _rec_name = "name"

    name = fields.Char(string="Irregularity", required=True, readonly=True)
    detector_id = fields.Many2one("bf.irregularity.detector", required=True, readonly=True, ondelete="cascade", index=True)
    family = fields.Selection(related="detector_id.family", store=True)
    severity = fields.Selection(SEVERITIES, required=True, readonly=True)
    severity_rank = fields.Integer(compute="_compute_ranks", store=True)
    key = fields.Char(required=True, readonly=True, index=True)
    res_model = fields.Char(string="Model", readonly=True, index=True)
    res_id = fields.Many2oneReference(string="Record ID", model_field="res_model", readonly=True)
    res_name = fields.Char(string="Record", compute="_compute_res_name")
    owner_id = fields.Many2one("res.users", string="Owner", index=True)
    state = fields.Selection([
        ("open", "Open"),
        ("acknowledged", "Acknowledged"),
        ("muted", "Muted"),
        ("resolved", "Resolved"),
    ], default="open", required=True, index=True)
    state_rank = fields.Integer(compute="_compute_ranks", store=True)
    first_seen = fields.Datetime(readonly=True)
    last_seen = fields.Datetime(readonly=True)
    resolved_date = fields.Datetime(readonly=True)
    mute_until = fields.Date()
    age_days = fields.Integer(string="Age (days)", compute="_compute_age_days")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)

    _sql_constraints = [
        ("detector_key_unique", "unique(detector_id, key)", "An irregularity is unique per detector and key."),
    ]

    @api.depends("severity", "state")
    def _compute_ranks(self):
        for irr in self:
            irr.severity_rank = SEVERITY_RANK.get(irr.severity, 9)
            irr.state_rank = 1 if irr.state == "resolved" else 0

    @api.depends("res_model", "res_id")
    def _compute_res_name(self):
        for irr in self:
            name = False
            if irr.res_model in self.env and irr.res_id:
                record = self.env[irr.res_model].sudo().browse(irr.res_id).exists()
                name = record.display_name if record else _("(deleted)")
            irr.res_name = name

    def _compute_age_days(self):
        now = fields.Datetime.now()
        for irr in self:
            irr.age_days = (now - irr.first_seen).days if irr.first_seen else 0

    def _notify_new(self):
        for irr in self.filtered(lambda i: i.detector_id.notify_activity and i.owner_id):
            if irr.res_model == "mail.activity" or irr.res_model not in self.env:
                continue
            record = self.env[irr.res_model].browse(irr.res_id).exists()
            if not record or not hasattr(record, "activity_schedule"):
                continue
            record.sudo().activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("Irregularity: %s", irr.detector_id.name),
                note=irr.name,
                user_id=irr.owner_id.id,
            )

    # -------------------------------------------------------------- actions

    def action_open_record(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
            "target": "current",
        }

    def action_acknowledge(self):
        self.write({"state": "acknowledged"})

    def action_mute(self):
        self.write({"state": "muted", "mute_until": fields.Date.context_today(self) + timedelta(days=30)})

    def action_reopen(self):
        self.write({"state": "open", "mute_until": False})
