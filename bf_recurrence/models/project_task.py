from odoo import api, fields, models

from .project_task_recurrence import ANCHOR_HELP, ANCHOR_SELECTION


class ProjectTask(models.Model):
    _inherit = "project.task"

    # Miroir non stocké du champ porté par project.task.recurrence, à l'image
    # des repeat_* du noyau : même compute, même compute_sudo, même readonly.
    repeat_anchor = fields.Selection(
        ANCHOR_SELECTION,
        string="Calculer la prochaine échéance",
        default="deadline",
        compute="_compute_repeat",
        compute_sudo=True,
        readonly=False,
        help=ANCHOR_HELP,
    )

    @api.model
    def _get_recurrence_fields(self):
        """Un seul ajout couvre les trois chemins du noyau.

        Cette liste pilote à la fois la création de la récurrence dans
        ``create()``, sa mise à jour dans ``write()`` et le miroir
        ``_compute_repeat()``.
        """
        return super()._get_recurrence_fields() + ["repeat_anchor"]
