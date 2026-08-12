from odoo import api, fields, models
from odoo.exceptions import ValidationError


class BfTimerStopWizard(models.TransientModel):
    _name = "bf.timer.stop.wizard"
    _description = "Assistant d'arrêt du timer"

    timer_id = fields.Many2one("bf.timer", required=True, ondelete="cascade")
    project_name = fields.Char(readonly=True)
    task_name = fields.Char(readonly=True)
    elapsed_display = fields.Char(string="Durée brute", readonly=True)
    hours = fields.Integer(string="Heures", default=0)
    minutes = fields.Integer(string="Minutes", default=5)
    description = fields.Char(string="Description")
    preset_id = fields.Many2one(
        "bf.timer.description.preset",
        string="Preset",
        domain=[("active", "=", True)],
    )

    @api.onchange("preset_id")
    def _onchange_preset_id(self):
        if self.preset_id:
            self.description = self.preset_id.text

    def _check_timer_ownership(self):
        """Verify the current user owns the timer."""
        timer = self.timer_id
        if not timer.exists():
            raise ValidationError("Timer introuvable.")
        if timer.user_id.id != self.env.uid:
            raise ValidationError("Vous ne pouvez pas modifier le timer d'un autre utilisateur.")
        return timer

    def action_confirm(self):
        """Create timesheet and delete timer."""
        self.ensure_one()
        timer = self._check_timer_ownership()
        total_minutes = self.hours * 60 + self.minutes
        if total_minutes <= 0:
            raise ValidationError("La durée doit être supérieure à 0.")
        ICP = self.env["ir.config_parameter"].sudo()
        mode = ICP.get_param("bf_timer.rounding_mode", "round_all")
        increment = int(ICP.get_param("bf_timer.rounding_increment", "5"))
        if mode != "none" and total_minutes < increment:
            total_minutes = increment
        duration_hours = round(total_minutes / 60.0, 2)
        self.env["account.analytic.line"].create({
            "name": self.description or timer.task_id.name,
            "date": timer.start_time.date(),
            "unit_amount": duration_hours,
            "task_id": timer.task_id.id,
            "project_id": timer.project_id.id,
            "employee_id": timer.employee_id.id,
        })
        timer.unlink()
        return {"type": "ir.actions.act_window_close"}

    def action_discard(self):
        """Delete timer without creating a timesheet."""
        self.ensure_one()
        timer = self._check_timer_ownership()
        timer.unlink()
        return {"type": "ir.actions.act_window_close"}

    def action_cancel(self):
        """Reactivate the timer (user changed their mind)."""
        self.ensure_one()
        timer = self._check_timer_ownership()
        timer.write({"is_active": True, "claimed_at": False})
        return {"type": "ir.actions.act_window_close"}
