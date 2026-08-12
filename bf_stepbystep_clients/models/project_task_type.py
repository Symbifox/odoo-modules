from odoo import fields, models


class ProjectTaskType(models.Model):
    _inherit = "project.task.type"

    progression_step_number = fields.Integer(
        string="Numéro d'étape de progression",
        help="Numéro de l'étape dans la progression linéaire du mandat. "
             "Laisser à 0 (par défaut) pour exclure ce stage de la "
             "visualisation Step-by-Step.",
    )
    progression_step_name = fields.Char(
        string="Nom d'étape de progression",
        translate=True,
        help="Libellé court affiché dans la visualisation linéaire "
             "(ex: « Démarrage », « Audit initial »). Si vide, le nom du "
             "stage est utilisé.",
    )
    progression_client_visible = fields.Boolean(
        string="Visible portail client",
        default=True,
        help="Cocher pour afficher cette étape sur le portail public du "
             "client. Décocher pour les étapes internes (ex: revue interne, "
             "facturation).",
    )
    progression_client_action_hint = fields.Text(
        string="Consigne client",
        translate=True,
        help="Instruction affichée au client sur le portail lorsque le "
             "projet est rendu à cette étape "
             "(ex: « Téléversez vos formulaires X et Y »).",
    )
