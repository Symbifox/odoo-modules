from odoo import api, fields, models


class ProjectTask(models.Model):
    """Côté tâche du lien avec la Messagerie.

    Le lien conversation ↔ tâche n'existait que dans un sens : on pouvait rattacher un fil
    à une tâche, mais rien sur la tâche ne le montrait, et il fallait retourner dans la
    Messagerie pour retrouver de quoi on parlait.

    Depuis la 5.13.0 la tâche compte aussi les **messages** qui lui ont été
    rattachés un par un. Les deux compteurs ne disent pas la même chose et
    cohabitent : une conversation entière peut concerner la tâche sans qu'aucun
    message précis n'y ait été posé, et l'inverse est tout aussi courant : trois
    SMS d'un fil qui, lui, appartient à un autre dossier."""

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
    sms_auto_thread_ids = fields.Many2many(
        comodel_name="sms.archive.thread",
        relation="sms_thread_auto_task_rel",
        column1="task_id",
        column2="thread_id",
        string="Conversations suivies",
        help="Conversations dont chaque nouveau message est relayé "
             "automatiquement au chatter de cette tâche.",
    )
    sms_link_count = fields.Integer(
        string="SMS rattachés",
        compute="_compute_sms_link_count",
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

    def _compute_sms_link_count(self):
        """Messages et appels rattachés à cette tâche précise.

        Compté sur `res_ref` et non sur le couple (`res_model`, `res_id`) : deux
        conditions séparées se seraient appliquées indépendamment et auraient
        compté le ticket portant le même numéro."""
        counts = {}
        refs = {f"project.task,{tid}": tid for tid in self.ids if tid}
        if refs:
            rows = self.env["sms.archive.link"].sudo()._read_group(
                [("res_ref", "in", list(refs))], groupby=["res_ref"],
                aggregates=["__count"],
            )
            counts = {refs[ref]: count for ref, count in rows if ref in refs}
        for task in self:
            task.sms_link_count = counts.get(task.id, 0)

    def action_open_sms_threads(self):
        """Ouvre les conversations rattachées à cette tâche."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Conversations ({self.name})",
            "res_model": "sms.archive.thread",
            "domain": [("task_ids", "in", self.id)],
            "view_mode": "list,form",
            "context": {"default_task_ids": [(4, self.id, 0)]},
        }

    def action_open_sms_links(self):
        """Ouvre les messages et appels rattachés à cette tâche."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"SMS rattachés ({self.name})",
            "res_model": "sms.archive.link",
            "domain": [("res_ref", "=", f"project.task,{self.id}")],
            "view_mode": "list",
            "context": {"create": False},
        }
