# -*- coding: utf-8 -*-
"""La date qui manquait.

Odoo 18 Community ne porte aucune date de début sur `project.task` :
`date_assign` dit quand on a assigné la tâche, `create_date` quand on l'a
saisie, et ni l'une ni l'autre ne dit quand le travail doit commencer.
`planned_date_begin` existe, mais dans `project_enterprise`.

Ce champ-ci est donc déclaré ici, sous le même nom, exprès : le jour où une base
passe en Enterprise, le champ est déjà rempli et rien ne se perd.

⚠️ C'est un champ stocké ordinaire, sans calcul. Un calcul stocké avec
`readonly=False` accepte les valeurs explicites mais se fait écraser au premier
recalcul de ses dépendances, et la planification serait alors effacée sans bruit.
Le repli sur `date_assign` se fait à l'affichage, dans `bf.gantt.source`, jamais
en base.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProjectTask(models.Model):
    _inherit = "project.task"

    planned_date_begin = fields.Datetime(
        string="Début planifié",
        index=True,
        tracking=True,
        help="Quand le travail doit commencer. Laissé vide, l'échéancier retombe "
             "sur la date d'assignation, puis sur la date de création, et le dit.",
    )
    bf_gantt_start_origin = fields.Selection(
        selection=[
            ("planifie", "Début planifié"),
            ("assignation", "Date d'assignation"),
            ("creation", "Date de création"),
            ("repli", "Reconstituée depuis l'échéance"),
        ],
        string="Origine du début",
        compute="_compute_bf_gantt_start_origin",
        help="D'où vient la date de début affichée dans l'échéancier. "
             "Tout ce qui n'est pas « Début planifié » est une approximation.",
    )

    @api.depends("planned_date_begin", "date_assign", "create_date", "date_deadline")
    def _compute_bf_gantt_start_origin(self):
        for tache in self:
            if tache.planned_date_begin:
                tache.bf_gantt_start_origin = "planifie"
            elif tache.date_assign:
                tache.bf_gantt_start_origin = "assignation"
            elif tache.create_date:
                tache.bf_gantt_start_origin = "creation"
            else:
                tache.bf_gantt_start_origin = "repli"

    @api.constrains("planned_date_begin", "date_deadline")
    def _check_bf_gantt_dates(self):
        for tache in self:
            if (tache.planned_date_begin and tache.date_deadline
                    and tache.planned_date_begin > tache.date_deadline):
                raise ValidationError(_(
                    "Le début planifié de « %(tache)s » tombe après son échéance.",
                    tache=tache.display_name,
                ))

    def action_bf_gantt_from_task(self):
        """Ouvre l'échéancier du projet de la tâche, sur la tâche."""
        self.ensure_one()
        if not self.project_id:
            return False
        return self.project_id.action_bf_gantt()
