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
    _description = "Pulse question"
    _rec_name = "title"
    _order = "sequence, id"

    title = fields.Char(string="Question", required=True, translate=True)
    metric_id = fields.Many2one(
        "bf.ex.pulse.metric", string="Dimension measured", required=True,
        ondelete="restrict",
    )
    question_kind = fields.Selection(
        [("scale", "Scale of 0 to 10"), ("text", "Free-text comment")],
        string="Format", required=True, default="scale",
    )
    is_enps = fields.Boolean(
        string="eNPS question",
        help="The recommendation question, read on the promoters, "
             "passives and detractors scale rather than as an average.",
    )
    hint = fields.Char(
        string="Hint", translate=True,
        help="One line under the question. It is there to frame what is "
             "being asked, never to steer the answer.",
    )
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(string="Active", default=True)

    @api.constrains("question_kind", "is_enps")
    def _check_enps_is_a_scale(self):
        for rec in self:
            if rec.is_enps and rec.question_kind != "scale":
                raise ValidationError(
                    "Une question eNPS se répond sur une échelle de 0 à 10. "
                    "Un commentaire libre ne se range pas en promoteurs et "
                    "en détracteurs."
                )
