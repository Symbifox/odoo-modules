from odoo import _, api, fields, models


class BfNoteActivityWizard(models.TransientModel):
    _name = "bf.note.activity.wizard"
    _description = "Convert a note to an activity"

    note_id = fields.Many2one("bf.note", required=True, ondelete="cascade")
    activity_type_id = fields.Many2one(
        "mail.activity.type",
        string="Type",
        required=True,
        default=lambda self: self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False),
    )
    date_deadline = fields.Date(
        string="Due date",
        required=True,
        default=fields.Date.context_today,
    )
    summary = fields.Char(string="Summary", required=True)
    note_html = fields.Html(string="Details", sanitize=True)
    user_id = fields.Many2one(
        "res.users",
        string="Assigned to",
        required=True,
        default=lambda self: self.env.user,
    )
    needs_target = fields.Boolean(compute="_compute_needs_target")
    link_target_ref = fields.Reference(
        string="Target",
        selection="_selection_target_model",
    )

    @api.model
    def _selection_target_model(self):
        return self.env["bf.note"]._selection_target_model()

    @api.depends("note_id")
    def _compute_needs_target(self):
        for w in self:
            w.needs_target = bool(w.note_id) and not w.note_id.link_ids

    @api.onchange("note_id")
    def _onchange_note_id(self):
        if self.note_id:
            self.summary = self.note_id.name
            self.note_html = self.note_id.body

    def action_create(self):
        self.ensure_one()
        target_ref = self.link_target_ref if self.needs_target else False
        new_activities = self.note_id._create_activities_for_links(
            activity_type_id=self.activity_type_id.id,
            custom_deadline=self.date_deadline,
            link_target_ref=target_ref,
        )
        # override summary/note avec les valeurs choisies dans le wizard
        if new_activities:
            new_activities.write({
                "summary": self.summary,
                "note": self.note_html or "",
                "user_id": self.user_id.id,
            })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Activity created"),
                "message": _("%s activity(ies).", len(new_activities)),
                "type": "success",
            },
        }
