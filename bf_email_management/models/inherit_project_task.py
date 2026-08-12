"""project.task name_search override for bf.email.reroute.

When the wizard is open, the target_reference Reference field passes
``bf_email_reroute_search=True`` in the dropdown context. We use that flag
to:
  * Accept a raw integer typed by the user (``22299``) and resolve it to
    ``[('id', '=', 22299)]`` (some workflows reference tasks by their ID
    in URLs).
  * Return enriched display labels ``#{id} — {display_name}`` so the
    confirmation dropdown shows the full task title alongside its ID.

The override is gated on the context flag, so default behavior is
preserved everywhere else in Odoo.
"""

from odoo import api, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    @api.model
    def name_search(self, name="", args=None, operator="ilike", limit=100):
        if self.env.context.get("bf_email_reroute_search") and name:
            stripped = name.strip()
            if stripped.isdigit():
                try:
                    task_id = int(stripped)
                except ValueError:
                    task_id = None
                if task_id and self.browse(task_id).exists():
                    rec = self.browse(task_id)
                    return [(rec.id, f"#{rec.id} — {rec.display_name}")]
            # Otherwise: enrich the standard label with the ID prefix.
            results = super().name_search(
                name=name, args=args, operator=operator, limit=limit,
            )
            return [(rid, f"#{rid} — {label}") for rid, label in results]
        return super().name_search(
            name=name, args=args, operator=operator, limit=limit,
        )
