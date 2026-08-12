from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext

_DESCRIPTION_MAX_LEN = 500


class ProjectTask(models.Model):
    _inherit = "project.task"

    @api.model
    def _bf_chatter_resolve_employee(self):
        """Return the hr.employee for the current user, or raise UserError."""
        employee = self.env.user.employee_id or self.env["hr.employee"].search(
            [
                ("user_id", "=", self.env.uid),
                ("company_id", "in", self.env.companies.ids),
            ],
            limit=1,
        )
        if not employee:
            raise UserError(_("Aucun employé n'est associé à votre compte utilisateur."))
        return employee

    def action_bf_create_chatter_timesheet(self, duration_hours, body_html=""):
        """Create a timesheet line tied to this task from a chatter note.

        Called via RPC from the OWL Composer patch after the internal note
        has been posted. Strips HTML from `body_html` to build the timesheet
        description; falls back to the task name when the body is empty.
        """
        self.ensure_one()

        try:
            duration_hours = float(duration_hours or 0)
        except (TypeError, ValueError):
            raise ValidationError(_("Durée invalide."))
        if duration_hours <= 0:
            raise ValidationError(_("La durée doit être supérieure à 0."))

        if not self.project_id:
            raise UserError(_("Cette tâche n'a pas de projet."))
        if not self.allow_timesheets:
            raise UserError(
                _("Les feuilles de temps ne sont pas activées sur cette tâche.")
            )

        employee = self._bf_chatter_resolve_employee()

        description = (html2plaintext(body_html or "") or "").strip() or self.name
        if len(description) > _DESCRIPTION_MAX_LEN:
            description = description[: _DESCRIPTION_MAX_LEN - 1].rstrip() + "…"

        line = self.env["account.analytic.line"].create({
            "name": description,
            "date": fields.Date.context_today(self),
            "unit_amount": duration_hours,
            "task_id": self.id,
            "project_id": self.project_id.id,
            "employee_id": employee.id,
        })
        return {
            "id": line.id,
            "name": line.name,
            "unit_amount": line.unit_amount,
        }
