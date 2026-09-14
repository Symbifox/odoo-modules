"""La banque de questions.

Deux formes seulement : une échelle bornée de 0 à 10, et un commentaire libre.
L'échelle est bornée là parce que c'est la forme d'un eNPS, et parce qu'Odoo
contraint déjà son propre type `scale` aux mêmes bornes. Garder une seule
grille de lecture évite d'avoir à en expliquer deux.
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PulseQuestion(models.Model):
    _name = "bf.ex.pulse.question"
    _description = "Question de pulse"
    _rec_name = "title"
    _order = "sequence, id"

    title = fields.Char(string="Question", required=True, translate=True)
    metric_id = fields.Many2one(
        "bf.ex.pulse.metric", string="Axe mesuré", required=True,
        ondelete="restrict",
    )
    question_kind = fields.Selection(
        [("scale", "Échelle de 0 à 10"), ("text", "Commentaire libre")],
        string="Forme", required=True, default="scale",
    )
    is_enps = fields.Boolean(
        string="Question eNPS",
        help="La question de recommandation, lue sur la grille "
             "promoteurs, passifs et détracteurs plutôt qu'en moyenne.",
    )
    hint = fields.Char(
        string="Précision", translate=True,
        help="Une ligne sous la question. Sert à borner ce qu'on demande, "
             "jamais à orienter la réponse.",
    )
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(string="Actif", default=True)

    @api.constrains("question_kind", "is_enps")
    def _check_enps_is_a_scale(self):
        for rec in self:
            if rec.is_enps and rec.question_kind != "scale":
                raise ValidationError(
                    "Une question eNPS se répond sur une échelle de 0 à 10. "
                    "Un commentaire libre ne se range pas en promoteurs et "
                    "en détracteurs."
                )
