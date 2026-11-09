# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.bf_live_base.models.color_utils import PALETTE, normalize_hex, palette_color, text_color


class BfWheelSegment(models.Model):
    _name = "bf.wheel.segment"
    _description = "Segment d'une roue de fortune"
    _order = "sequence, id"

    wheel_id = fields.Many2one(
        "bf.wheel", string="Roue", required=True, ondelete="cascade", index=True)
    name = fields.Char(string="Étiquette", required=True)
    sequence = fields.Integer(string="Séquence", default=10)
    weight = fields.Integer(
        string="Poids", default=1, required=True,
        help="La part de la roue, et la chance de sortir : un poids de 3 occupe "
             "trois fois plus de place qu'un poids de 1 et sort trois fois plus souvent.")
    color_hex = fields.Char(
        string="Couleur",
        help="Couleur du segment (hex). Laissée vide, elle est prise dans la "
             "palette de la maison au moment de la création, selon le rang du segment.")
    color_display = fields.Char(string="Couleur affichée", compute="_compute_colors")
    text_color = fields.Char(string="Couleur du texte", compute="_compute_colors")
    drawn = fields.Boolean(
        string="Déjà tiré", default=False, copy=False,
        help="Posé par un tirage quand la roue retire son gagnant.")
    company_id = fields.Many2one(related="wheel_id.company_id", store=True, index=True)

    # Une seule garde, et dans la base : un `@api.constrains` sur le même champ ne
    # se déclenche jamais, le CHECK de PostgreSQL lève d'abord (l'ORM écrit, puis
    # valide). Et le CHECK tient aussi contre un UPDATE écrit à la main.
    _sql_constraints = [
        ("weight_positive", "CHECK (weight >= 1)",
         "Le poids d'un segment vaut au moins 1."),
    ]

    @api.depends("color_hex")
    def _compute_colors(self):
        for segment in self:
            # ⚠️ Un segment neuf, pas encore enregistré, n'est jamais passé par
            # `create` : il n'a pas de couleur, et l'écran doit quand même en
            # montrer une.
            color = normalize_hex(segment.color_hex) or PALETTE[0]
            segment.color_display = color
            segment.text_color = text_color(color)

    @api.constrains("color_hex")
    def _check_color_hex(self):
        for segment in self:
            if segment.color_hex and not normalize_hex(segment.color_hex):
                raise ValidationError(_(
                    "« %(value)s » n'est pas une couleur. Attendu : #a1b2c3.",
                    value=segment.color_hex))

    @api.model_create_multi
    def create(self, vals_list):
        """Pose la couleur du rang quand on n'en demande aucune.

        Le rang se compte par roue, et les segments créés dans le même geste se
        suivent dans la palette : une roue bâtie d'un coup, en liste intégrée,
        sort en douze couleurs au lieu de douze fois la même.
        """
        taken = defaultdict(int)
        for vals in vals_list:
            brut = vals.get("color_hex")
            if brut:
                # 🔴 Une couleur illisible ne se remplace pas en silence par celle de
                # la palette : « mauve » passait ainsi pour du bleu, et la contrainte
                # ne voyait jamais la valeur refusée. On garde la valeur telle quelle
                # et on laisse la garde la refuser.
                vals["color_hex"] = normalize_hex(brut) or brut
                continue
            wheel_id = vals.get("wheel_id")
            if wheel_id and wheel_id not in taken:
                taken[wheel_id] = self.search_count([("wheel_id", "=", wheel_id)])
            vals["color_hex"] = palette_color(taken[wheel_id])
            taken[wheel_id] += 1
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("color_hex"):
            vals["color_hex"] = normalize_hex(vals["color_hex"]) or vals["color_hex"]
        return super().write(vals)

    @api.model
    def palette(self):
        return list(PALETTE)
