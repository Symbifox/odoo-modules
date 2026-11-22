from datetime import datetime, time

from odoo import _, api, fields, models


class BfNoteTaskWizard(models.TransientModel):
    _name = "bf.note.task.wizard"
    _description = "Convert a note to a task"

    note_id = fields.Many2one("bf.note", required=True, ondelete="cascade")

    name = fields.Char(string="Title", required=True)
    description = fields.Html(string="Description", sanitize=True)

    project_id = fields.Many2one("project.project", string="Project", required=True)
    stage_id = fields.Many2one(
        "project.task.type",
        string="Stage",
        domain="[('project_ids', '=', project_id)]",
    )
    parent_id = fields.Many2one(
        "project.task",
        string="Parent task",
        domain="[('project_id', '=', project_id)]",
        help="Optional: create the new task as a subtask.",
    )
    user_ids = fields.Many2many(
        "res.users",
        string="Assigned to",
        default=lambda self: self.env.user,
    )
    date_deadline = fields.Datetime(string="Due date")
    tag_ids = fields.Many2many("project.tags", string="Tags")
    partner_id = fields.Many2one("res.partner", string="Client")

    link_back = fields.Boolean(
        string="Link the note to the task",
        default=True,
        help="Adds the new task to the note's links to keep a trace.",
    )
    archive_note = fields.Boolean(
        string="Archive the note",
        default=False,
        help="Archives the note once the task is created (recommended if "
             "it is no longer needed).",
    )
    open_task = fields.Boolean(
        string="Open the task",
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

        # les fichiers de la note (photo, PDF partagés depuis le
        # téléphone) suivent la tâche. Copiés, pas déplacés : la note garde
        # les siens, archivée ou non, et peut encore servir ailleurs.
        #
        # 🔴 Seulement quand le geste est de
        # l'AUTEUR. Sinon, un destinataire en écriture emportait les fichiers de
        # l'auteur, et ceux des messages du fil (voir `_mobile_attachments`),
        # dans un projet qu'il choisit. Le droit de LIRE ne change pas : c'est
        # le déplacement qu'on ferme. La tâche garde le lien retour, le titre et
        # le contenu, et les fichiers restent sur la note.
        #
        # ⚠️ La lecture est « l'auteur, ou `env.su` », et PAS `peut_gerer` :
        # conséquence assumée de l'arbitrage, un compte `base.group_system` qui
        # fait une tâche depuis la note d'un collègue n'emporte plus les
        # fichiers non plus. Le code en superutilisateur (passes de Gen, action
        # serveur) les emporte, lui, parce qu'il agit POUR l'auteur.
        #
        # ⚠️ C'est le SEUL chemin du module qui copie une pièce d'une note :
        # mesuré, un seul `.copy(` dans tout le module. « En faire
        # une activité » et le re-routage ne touchent aucune pièce, et le geste
        # du téléphone passe par CET assistant (`_mobile_make_task`).
        if self.env.su or self.note_id.user_id.id == self.env.uid:
            for piece in self.note_id._mobile_attachments():
                piece.copy({"res_model": "project.task", "res_id": task.id})

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
                "title": _("Task created"),
                "message": task.display_name,
                "type": "success",
                "sticky": False,
            },
        }
