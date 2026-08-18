from odoo import api, fields, models


class ProjectTask(models.Model):
    """Côté tâche du lien avec la Messagerie.

    Le lien conversation ↔ tâche n'existait que dans un sens : on pouvait rattacher un fil
    à une tâche, mais rien sur la tâche ne le montrait, et il fallait retourner dans la
    Messagerie pour retrouver de quoi on parlait."""

    _inherit = "project.task"

    sms_thread_ids = fields.Many2many(
        comodel_name="sms.archive.thread",
        relation="sms_thread_task_rel",
        column1="task_id",
        column2="thread_id",
        string="Conversations SMS",
    )
    sms_thread_count = fields.Integer(
        string="Conversations",
        compute="_compute_sms_thread_count",
    )

    @api.depends("sms_thread_ids")
    def _compute_sms_thread_count(self):
        # read_group plutôt qu'un len() par tâche : le bouton statistique est calculé sur
        # toute la liste des tâches, pas seulement sur la fiche ouverte.
        counts = {}
        if self.ids:
            self.env.cr.execute(
                "SELECT task_id, COUNT(thread_id) FROM sms_thread_task_rel "
                "WHERE task_id IN %s GROUP BY task_id",
                (tuple(self.ids),),
            )
            counts = dict(self.env.cr.fetchall())
        for task in self:
            task.sms_thread_count = counts.get(task.id, 0)

    def action_open_sms_threads(self):
        """Ouvre les conversations rattachées à cette tâche."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Conversations — {self.name}",
            "res_model": "sms.archive.thread",
            "domain": [("task_ids", "in", self.id)],
            "view_mode": "list,form",
            "context": {"default_task_ids": [(4, self.id, 0)]},
        }
