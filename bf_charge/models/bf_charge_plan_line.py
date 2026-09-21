# -*- coding: utf-8 -*-
"""Le grain du plan : une ligne par tâche et par semaine où sa charge tombe.

Le plan répond « combien ». Il ne répond pas « de quoi c'est fait », et sans ça
rien ne se découpe : ni par projet, ni par client, ni par source, ni par motif
de mise à l'écart. Cette table de faits est écrite à chaque calcul et sert de
socle aux vues natives d'Odoo (graphique, tableau croisé, calendrier), plutôt
qu'à un tableau de bord dessiné à la main : une panne de composant ne se voit
dans aucun essai côté serveur.

⚠️ Elle ne stocke que ce qui a été DÉCIDÉ au moment du calcul. Le réel continue
de se relire dans les tâches ; cette table est l'instantané d'un plan, pas une
copie du carnet.
"""
from odoo import fields, models

#: Où une charge atterrit. L'ordre est celui de la lecture : ce qui est posé,
#: puis ce qui ne l'est pas, puis ce qui est hors plan par construction.
SEAUX = [
    ("pose", "Posée sur une semaine"),
    ("retard", "En retard, ramenée sur la première semaine"),
    ("non_placable", "Non plaçable, aucune date"),
    ("au_dela", "Au delà de l'horizon"),
    ("sans_charge", "Vivante, sans charge"),
    ("dormant", "Projet dormant"),
    ("gabarit", "Gabarit"),
    ("ecartee", "Écartée, unité de vente"),
]


class BfChargePlanLine(models.Model):
    _name = "bf.charge.plan.line"
    _description = "Ligne du plan de charge"
    _order = "plan_id, week_start, id"

    plan_id = fields.Many2one("bf.charge.plan", string="Plan", required=True,
                              ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", string="Société",
                                 related="plan_id.company_id", store=True)
    task_id = fields.Many2one("project.task", string="Tâche", ondelete="cascade", index=True)
    task_name = fields.Char(string="Intitulé")
    project_id = fields.Many2one("project.project", string="Projet",
                                 ondelete="cascade", index=True)
    partner_id = fields.Many2one("res.partner", string="Client")
    is_client = fields.Boolean(string="Travail client")
    project_kind = fields.Selection(
        [("vivant", "Vivant"), ("dormant", "Dormant"), ("gabarit", "Gabarit")],
        string="Nature du projet",
    )
    bucket = fields.Selection(SEAUX, string="Sort de la charge", required=True, index=True)
    week_start = fields.Date(string="Semaine", index=True,
                             help="Le lundi de la semaine où cette part de charge tombe. "
                                  "Vide quand la charge n'a pas pu être posée.")
    hours = fields.Float(string="Heures")
    share = fields.Float(string="Part de la tâche",
                         help="Fraction de la tâche qui tombe sur cette semaine. "
                              "Vaut 1 quand la tâche tient dans une seule semaine.")
    source = fields.Selection(
        [
            ("manuel", "Corrigée à la main"),
            ("estime", "Estimée"),
            ("vente", "Quantité vendue"),
            ("vente_ecartee", "Quantité vendue, écartée"),
            ("aucune", "Aucune"),
        ],
        string="Source de la charge",
    )
    date_start = fields.Date(string="Début retenu")
    date_end = fields.Date(string="Fin retenue")
