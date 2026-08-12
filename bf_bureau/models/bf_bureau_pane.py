import ast

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .bf_bureau_desk import LAYOUT_SLOTS


VIEW_TYPES = [
    ("kanban", "Kanban"),
    ("list", "Liste"),
    ("form", "Fiche"),
    ("pivot", "Tableau croisé"),
    ("graph", "Graphique"),
    ("calendar", "Calendrier"),
    ("activity", "Activité"),
]


class BfBureauPane(models.Model):
    _name = "bf.bureau.pane"
    _description = "BF Bureau — panneau dans un bureau"
    _order = "desk_id, slot"

    desk_id = fields.Many2one(
        "bf.bureau.desk",
        required=True,
        ondelete="cascade",
        index=True,
    )
    slot = fields.Selection(
        [
            ("full", "Plein écran"),
            ("left_full", "Colonne gauche"),
            ("right_full", "Colonne droite"),
            ("top_full", "Rangée du haut"),
            ("top_left", "Haut-gauche"),
            ("top_right", "Haut-droite"),
            ("bottom_full", "Rangée du bas"),
            ("bottom_left", "Bas-gauche"),
            ("bottom_right", "Bas-droite"),
            ("row_1", "Rangée 1"),
            ("row_2", "Rangée 2"),
            ("row_3", "Rangée 3"),
        ],
        required=True,
    )
    action_id = fields.Many2one(
        "ir.actions.act_window",
        required=True,
        ondelete="restrict",
        domain="[('type','=','ir.actions.act_window')]",
    )
    view_type = fields.Selection(
        VIEW_TYPES,
        default="kanban",
        required=True,
    )
    name_override = fields.Char()
    weight = fields.Integer(
        default=1,
        help="Poids relatif (1 = normal, 2 = grand, 3 = très grand). "
             "Influence la taille du panneau dans la grille.",
    )
    domain_override = fields.Char(
        help="Expression Python qui sera ajoutée (AND) au domaine de "
             "l'action. Ex.: [('priority','=','1')]. Laissez vide pour "
             "utiliser le domaine de l'action tel quel.",
    )
    context_override = fields.Char(
        help="Dictionnaire Python fusionné par-dessus le contexte de "
             "l'action. Ex.: {'search_default_my_filter': 1}. Laissez "
             "vide pour utiliser le contexte de l'action tel quel.",
    )

    _sql_constraints = [
        (
            "desk_slot_unique",
            "UNIQUE (desk_id, slot)",
            "Un seul panneau par emplacement dans un bureau.",
        ),
        (
            "weight_positive",
            "CHECK (weight >= 1 AND weight <= 4)",
            "Le poids doit être entre 1 et 4.",
        ),
    ]

    @api.constrains("slot", "desk_id")
    def _check_slot_layout(self):
        for pane in self:
            allowed = LAYOUT_SLOTS.get(pane.desk_id.layout, ())
            if pane.slot not in allowed:
                raise ValidationError(
                    f"L'emplacement « {pane.slot} » n'est pas valide pour la "
                    f"mise en page « {pane.desk_id.layout} ». "
                    f"Emplacements permis : {', '.join(allowed)}."
                )

    @api.constrains("view_type", "action_id")
    def _check_view_type_in_action(self):
        for pane in self:
            modes = (pane.action_id.view_mode or "").split(",")
            if pane.view_type not in [m.strip() for m in modes]:
                raise ValidationError(
                    f"Le type de vue « {pane.view_type} » n'est pas supporté par "
                    f"l'action « {pane.action_id.name} » (modes disponibles : "
                    f"{pane.action_id.view_mode})."
                )

    @api.constrains("domain_override")
    def _check_domain_override_parses(self):
        for pane in self:
            if not pane.domain_override:
                continue
            try:
                value = ast.literal_eval(pane.domain_override)
            except (SyntaxError, ValueError) as exc:
                raise ValidationError(
                    f"Domaine invalide : {exc}. Doit être une liste Python, "
                    f"ex. [('priority','=','1')]."
                )
            if not isinstance(value, list):
                raise ValidationError(
                    "Domaine invalide : doit être une liste Python."
                )

    @api.constrains("context_override")
    def _check_context_override_parses(self):
        for pane in self:
            if not pane.context_override:
                continue
            try:
                value = ast.literal_eval(pane.context_override)
            except (SyntaxError, ValueError) as exc:
                raise ValidationError(
                    f"Contexte invalide : {exc}. Doit être un dictionnaire "
                    f"Python, ex. {{'search_default_my_filter': 1}}."
                )
            if not isinstance(value, dict):
                raise ValidationError(
                    "Contexte invalide : doit être un dictionnaire Python."
                )
