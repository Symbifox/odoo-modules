# -*- coding: utf-8 -*-
"""La charge d'une tâche, avec sa source et son placement.

Deux faits mesurés sur une base réelle commandent tout ce fichier.

1. `allocated_hours` n'est pas un estimé : il peut venir d'une quantité vendue.
   Des tâches y portaient des milliers d'heures parce qu'un abonnement annuel
   avait été vendu en unité « Année », que l'unité appartient à la catégorie du
   temps de travail, et qu'Odoo l'a convertie à huit heures par jour.
2. La grande majorité des tâches ouvertes ne portent aucune date. Une charge
   qu'on ne sait pas placer n'est pas une charge nulle : elle se compte à part.
"""
from odoo import _, api, fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    charge_hours_manual = fields.Float(
        string="Charge corrigée (h)",
        help="Pose une charge à la main quand l'estimé est absent ou faux. "
             "Elle prime sur toutes les autres sources.",
    )
    charge_hours = fields.Float(
        string="Charge retenue (h)", compute="_compute_bf_charge", store=False,
        help="Ce que le plan de charge retient pour cette tâche.",
    )
    charge_source = fields.Selection(
        [
            ("manuel", "Corrigée à la main"),
            ("estime", "Estimée"),
            ("vente", "Quantité vendue"),
            ("vente_ecartee", "Quantité vendue, écartée"),
            ("aucune", "Aucune"),
        ],
        string="Source de la charge", compute="_compute_bf_charge", store=False,
    )
    charge_reject_reason = fields.Char(
        string="Motif de l'écart", compute="_compute_bf_charge", store=False,
    )
    charge_date_start = fields.Date(
        string="Début retenu", compute="_compute_bf_charge_dates", store=False,
    )
    charge_date_end = fields.Date(
        string="Fin retenue", compute="_compute_bf_charge_dates", store=False,
    )
    charge_placeable = fields.Boolean(
        string="Plaçable", compute="_compute_bf_charge_dates", store=False,
        help="Faux quand la tâche ne porte aucune date : elle ne tombe alors sur "
             "aucune semaine, et son total est affiché à part.",
    )

    # ------------------------------------------------------------------
    # Charge et source
    # ------------------------------------------------------------------
    # 🔴 `sale_line_id` est ABSENT de cette liste, exprès. Il vient de
    # `sale_project`, qui n'est pas une dépendance : le déclarer fait tomber le
    # registre entier sur un `KeyError: sale_line_id` dès que le module se
    # charge avant lui. Mesuré au banc le 2026-09-21. La lecture du champ se
    # fait quand même à l'exécution, gardée par `_fields`, et le recalcul est
    # déclenché par `allocated_hours`, qu'une confirmation de commande écrit de
    # toute façon en même temps que le lien.
    @api.depends("allocated_hours", "charge_hours_manual")
    def _compute_bf_charge(self):
        heure = self.env.ref("uom.product_uom_hour", raise_if_not_found=False)
        for task in self:
            manuel = task.charge_hours_manual or 0.0
            if manuel:
                task.charge_hours = manuel
                task.charge_source = "manuel"
                task.charge_reject_reason = False
                continue

            heures = task.allocated_hours or 0.0
            ligne = task._bf_charge_sale_line()
            if ligne is not None and ligne:
                uom = ligne.product_uom
                if heure and uom and uom.id != heure.id and uom.category_id == heure.category_id:
                    # Une unité de temps qui n'est pas l'heure a été convertie
                    # en heures par Odoo. Le nombre est juste, ce qu'il décrit ne
                    # l'est pas : une durée d'abonnement n'est pas un effort.
                    task.charge_hours = 0.0
                    task.charge_source = "vente_ecartee"
                    task.charge_reject_reason = _(
                        "Vendue en « %(uom)s » (%(qte).2f), convertie en %(h).0f h. "
                        "Une durée vendue n'est pas un effort estimé."
                    ) % {"uom": uom.name, "qte": ligne.product_uom_qty, "h": heures}
                    continue
                task.charge_hours = heures
                task.charge_source = "vente" if heures else "aucune"
                task.charge_reject_reason = False
                continue

            task.charge_hours = heures
            task.charge_source = "estime" if heures else "aucune"
            task.charge_reject_reason = False

    def _bf_charge_sale_line(self):
        """La ligne de vente de la tâche, ou un recordset vide.

        Passe par `_fields` : `sale_line_id` vient de `sale_project`, qui peut
        ne pas être installé.
        """
        self.ensure_one()
        if "sale_line_id" not in self._fields:
            return None
        # 🔴 `sudo()` : lire la VALEUR du many2one ne demande rien, mais la
        # déréférencer pour atteindre son unité de mesure exige un droit sur
        # `sale.order.line` que le groupe du module n'a pas. Sans ça, bâtir un
        # plan lève un AccessError pour tout le monde sauf un administrateur
        # système. Vu en production le 2026-09-21, dans le rôle visé.
        # ⚠️ On ne lit qu'une unité de mesure, rien de commercial.
        return self.sale_line_id.sudo()

    # ------------------------------------------------------------------
    # Placement
    # ------------------------------------------------------------------
    @api.depends("date_deadline")
    def _compute_bf_charge_dates(self):
        a_un_debut = "planned_date_begin" in self._fields
        for task in self:
            debut = False
            if a_un_debut and task.planned_date_begin:
                debut = fields.Date.to_date(task.planned_date_begin)
            fin = fields.Date.to_date(task.date_deadline) if task.date_deadline else False
            if debut and fin and debut > fin:
                # Un début postérieur à l'échéance ne se répartit pas : on garde
                # la seule date qui reste défendable, l'échéance.
                debut = fin
            task.charge_date_start = debut or fin
            task.charge_date_end = fin or debut
            task.charge_placeable = bool(debut or fin)
