from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

# Pure system tracking (stage changes, field audit). These stay on the
# archived original as its historical record; everything else — the real
# conversation — follows the work to the kept task.
_SYSTEM_MESSAGE_TYPES = ("notification",)


class TaskMergeWizard(models.TransientModel):
    _name = "bf.task.merge.wizard"
    _description = "Assistant de regroupement de tâches"

    source_task_ids = fields.Many2many(
        "project.task", string="Tâches à regrouper", required=True
    )
    assignee_ids = fields.Many2many("res.users", string="Personnes assignées")
    target_mode = fields.Selection(
        [
            ("existing", "Vers une tâche existante"),
            ("new", "Vers une nouvelle tâche"),
        ],
        string="Destination",
        default="existing",
        required=True,
    )
    target_task_id = fields.Many2one("project.task", string="Tâche conservée")
    new_task_name = fields.Char(string="Titre de la nouvelle tâche")
    new_project_id = fields.Many2one("project.project", string="Projet")

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        picked = self.env["project.task"].browse(self.env.context.get("active_ids", []))
        if not picked:
            return defaults
        head = picked[:1]
        defaults.update(
            source_task_ids=[Command.set(picked.ids)],
            assignee_ids=[Command.set(picked.user_ids.ids)],
            target_task_id=head.id,
            new_project_id=head.project_id.id,
        )
        return defaults

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def action_merge(self):
        self.ensure_one()
        sources = self.source_task_ids
        if len(sources) < 2:
            raise UserError(_("Choisissez au moins deux tâches à regrouper."))
        # The operator must be allowed to modify every selected task before any
        # elevated (sudo) re-pointing of its sub-records runs.
        sources.check_access("write")

        keeper = self._resolve_keeper()
        keeper.ensure_one()
        absorbed = sources - keeper
        if not absorbed:
            raise UserError(
                _("La destination ne peut pas être la seule tâche sélectionnée.")
            )

        self._absorb_conversation(absorbed, keeper)
        self._absorb_side_records(absorbed, keeper)
        self._leave_breadcrumbs(absorbed, keeper)
        absorbed.write({"active": False})

        return {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "res_id": keeper.id,
            "view_mode": "form",
            "views": [(False, "form")],
        }

    # ------------------------------------------------------------------
    # Destination
    # ------------------------------------------------------------------
    def _resolve_keeper(self):
        sources = self.source_task_ids
        carried = {"tag_ids": [Command.link(tid) for tid in sources.tag_ids.ids]}
        if self.assignee_ids:
            carried["user_ids"] = [Command.set(self.assignee_ids.ids)]

        if self.target_mode == "new":
            if not self.new_task_name or not self.new_project_id:
                raise UserError(
                    _("Indiquez le titre et le projet de la nouvelle tâche.")
                )
            partners = sources.partner_id
            priorities = set(sources.mapped("priority"))
            carried.update(
                name=self.new_task_name,
                project_id=self.new_project_id.id,
                description=self._stitch_descriptions(sources) or False,
                partner_id=partners.id if len(partners) == 1 else False,
                priority=priorities.pop() if len(priorities) == 1 else False,
            )
            return self.env["project.task"].create(carried)

        keeper = self.target_task_id
        if not keeper:
            raise UserError(_("Sélectionnez la tâche à conserver."))
        extra = self._stitch_descriptions(sources - keeper)
        if extra:
            carried["description"] = Markup(keeper.description or "") + extra
        keeper.write(carried)
        return keeper

    def _stitch_descriptions(self, tasks):
        blocks = [
            Markup("<p><b>%s</b></p>%s") % (task.display_name, Markup(task.description))
            for task in tasks
            if task.description
        ]
        return Markup("").join(blocks) if blocks else Markup("")

    # ------------------------------------------------------------------
    # Conversation (messages, activities, followers)
    # ------------------------------------------------------------------
    def _absorb_conversation(self, absorbed, keeper):
        messages = self.env["mail.message"]
        activities = self.env["mail.activity"]
        for task in absorbed:
            messages |= task.message_ids.filtered(
                lambda m: m.message_type not in _SYSTEM_MESSAGE_TYPES
            )
            activities |= task.activity_ids

        followers = absorbed.message_partner_ids.ids
        if followers:
            keeper.message_subscribe(partner_ids=followers)
        # record_name / res_name are stored, non-recomputed copies of the
        # parent's name — refresh them so peripheral views don't show the old
        # task. Both records now belong to a single keeper.
        if messages:
            messages.sudo().exists().write(
                {"res_id": keeper.id, "record_name": keeper.display_name}
            )
        if activities:
            activities.sudo().exists().write(
                {"res_id": keeper.id, "res_name": keeper.display_name}
            )

    # ------------------------------------------------------------------
    # Side records (children, attachments, ratings/meetings, hours, deps)
    # ------------------------------------------------------------------
    def _absorb_side_records(self, absorbed, keeper):
        self._reparent_children(absorbed, keeper)
        self._absorb_attachments(absorbed, keeper)
        self._absorb_ratings_and_meetings(absorbed, keeper)
        self._absorb_timesheets(absorbed, keeper)
        self._rewire_dependencies(absorbed, keeper)

    def _reparent_children(self, absorbed, keeper):
        children = absorbed.child_ids - self.source_task_ids
        if children:
            children.write({"parent_id": keeper.id})

    def _absorb_attachments(self, absorbed, keeper):
        # Attachments uploaded on the task itself (res_model='project.task').
        # Chatter-message attachments ride along with their re-pointed
        # mail.message through the message_attachment_rel link, so they are not
        # searched here.
        attachments = (
            self.env["ir.attachment"]
            .sudo()
            .search([("res_model", "=", "project.task"), ("res_id", "in", absorbed.ids)])
        )
        if attachments:
            attachments.write({"res_id": keeper.id})

    def _absorb_ratings_and_meetings(self, absorbed, keeper):
        if "rating.rating" in self.env:
            ratings = (
                self.env["rating.rating"]
                .sudo()
                .search(
                    [("res_model", "=", "project.task"), ("res_id", "in", absorbed.ids)]
                )
            )
            if ratings:
                ratings.write(
                    {
                        "res_id": keeper.id,
                        "res_model_id": self.env["ir.model"]._get_id("project.task"),
                    }
                )
        if "calendar.event" in self.env:
            events = (
                self.env["calendar.event"]
                .sudo()
                .search(
                    [("res_model", "=", "project.task"), ("res_id", "in", absorbed.ids)]
                )
            )
            if events:
                events.write({"res_id": keeper.id})

    def _absorb_timesheets(self, absorbed, keeper):
        lines_model = self.env["account.analytic.line"]
        if "task_id" not in lines_model._fields:
            return
        lines = lines_model.sudo().search([("task_id", "in", absorbed.ids)])
        if not lines:
            return
        payload = {"task_id": keeper.id}
        if keeper.project_id:
            payload["project_id"] = keeper.project_id.id
        lines.write(payload)

    def _rewire_dependencies(self, absorbed, keeper):
        if "depend_on_ids" not in self.env["project.task"]._fields:
            return
        # The keeper takes over the predecessors of the absorbed tasks. Drop any
        # that already depend on the keeper, which would form a direct cycle.
        predecessors = absorbed.depend_on_ids - keeper - absorbed - keeper.dependent_ids
        if predecessors:
            keeper.write(
                {"depend_on_ids": [Command.link(t.id) for t in predecessors]}
            )
        # Successors that waited on an absorbed task now wait on the keeper, and
        # we drop their stale link to the (archived) source.
        for successor in absorbed.dependent_ids - keeper - absorbed:
            stale = successor.depend_on_ids & absorbed
            successor.write(
                {
                    "depend_on_ids": [Command.link(keeper.id)]
                    + [Command.unlink(t.id) for t in stale]
                }
            )

    # ------------------------------------------------------------------
    # Breadcrumbs
    # ------------------------------------------------------------------
    def _leave_breadcrumbs(self, absorbed, keeper):
        keeper_link = keeper._get_html_link()
        for task in absorbed:
            task.message_post(
                body=Markup(_("Regroupée dans %s.")) % keeper_link,
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        keeper.message_post(
            body=Markup(_("Tâches regroupées ici : %s."))
            % escape(", ".join(absorbed.mapped("display_name"))),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
