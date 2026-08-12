from datetime import datetime, time

from odoo import api, fields, models


class BfNoteTaskWizard(models.TransientModel):
    _name = "bf.note.task.wizard"
    _description = "Convertir une note en tâche"

    note_id = fields.Many2one("bf.note", required=True, ondelete="cascade")

    name = fields.Char(string="Titre", required=True)
    description = fields.Html(string="Description", sanitize=True)

    project_id = fields.Many2one("project.project", string="Projet", required=True)
    stage_id = fields.Many2one(
        "project.task.type",
        string="Étape",
        domain="[('project_ids', '=', project_id)]",
    )
    parent_id = fields.Many2one(
        "project.task",
        string="Tâche parente",
        domain="[('project_id', '=', project_id)]",
        help="Optionnel : créer la nouvelle tâche comme sous-tâche.",
    )
    user_ids = fields.Many2many(
        "res.users",
        string="Assignée à",
        default=lambda self: self.env.user,
    )
    date_deadline = fields.Datetime(string="Échéance")
    tag_ids = fields.Many2many("project.tags", string="Étiquettes")
    partner_id = fields.Many2one("res.partner", string="Client")

    link_back = fields.Boolean(
        string="Lier la note à la tâche",
        default=True,
        help="Ajoute la nouvelle tâche aux liens de la note pour garder une trace.",
    )
    archive_note = fields.Boolean(
        string="Archiver la note",
        default=False,
        help="Archive la note après création de la tâche (recommandé si elle ne sert plus).",
    )
    open_task = fields.Boolean(
        string="Ouvrir la tâche",
        default=True,
    )

    @api.onchange("note_id")
    def _onchange_note_id(self):
        if not self.note_id:
            return
        note = self.note_id
        self.name = note.name
        self.description = note.body or ""

        if note.deadline_date:
            self.date_deadline = datetime.combine(note.deadline_date, time(hour=17, minute=0))

        self._prefill_project_from_links()

    def _prefill_project_from_links(self):
        """Inspect the note's primary link to pre-select project / parent / partner."""
        self.ensure_one()
        if not self.note_id:
            return
        for link in self.note_id.link_ids:
            model, rid = link.res_model, link.res_id
            if not (model and rid and model in self.env):
                continue
            if model == "project.task":
                task = self.env["project.task"].browse(rid).exists()
                if task:
                    self.project_id = task.project_id
                    self.parent_id = task
                    if task.partner_id:
                        self.partner_id = task.partner_id
                    return
            if model == "project.project":
                project = self.env["project.project"].browse(rid).exists()
                if project:
                    self.project_id = project
                    if project.partner_id:
                        self.partner_id = project.partner_id
                    return
            if model == "res.partner":
                partner = self.env["res.partner"].browse(rid).exists()
                if partner and not self.partner_id:
                    self.partner_id = partner

    @api.onchange("project_id")
    def _onchange_project_id(self):
        if self.stage_id and self.stage_id.project_ids and self.project_id not in self.stage_id.project_ids:
            self.stage_id = False
        if self.parent_id and self.parent_id.project_id != self.project_id:
            self.parent_id = False
        if self.project_id and self.project_id.partner_id and not self.partner_id:
            self.partner_id = self.project_id.partner_id

    def action_create(self):
        self.ensure_one()
        Task = self.env["project.task"]
        vals = {
            "name": self.name,
            "description": self.description or "",
            "project_id": self.project_id.id,
            "user_ids": [(6, 0, self.user_ids.ids)] if self.user_ids else [(5, 0, 0)],
            "tag_ids": [(6, 0, self.tag_ids.ids)] if self.tag_ids else [(5, 0, 0)],
        }
        if self.stage_id:
            vals["stage_id"] = self.stage_id.id
        if self.parent_id:
            vals["parent_id"] = self.parent_id.id
        if self.date_deadline:
            vals["date_deadline"] = self.date_deadline
        if self.partner_id:
            vals["partner_id"] = self.partner_id.id

        task = Task.create(vals)

        if self.link_back:
            existing = self.note_id.link_ids.filtered(
                lambda l: l.res_model == "project.task" and l.res_id == task.id
            )
            if not existing:
                self.env["bf.note.link"].create({
                    "note_id": self.note_id.id,
                    "res_model": "project.task",
                    "res_id": task.id,
                })

        self.note_id.tracked_task_ids = [(4, task.id)]

        if self.archive_note:
            self.note_id.active = False

        if self.open_task:
            return {
                "type": "ir.actions.act_window",
                "res_model": "project.task",
                "res_id": task.id,
                "views": [(False, "form")],
                "target": "current",
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Tâche créée",
                "message": task.display_name,
                "type": "success",
                "sticky": False,
            },
        }
