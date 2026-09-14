"""Les axes de mesure du pulse.

Un axe n'est pas une question. Il porte plusieurs questions qui tournent d'une
vague à l'autre, ce qui évite qu'une équipe apprenne la question par cœur et
finisse par répondre à la question plutôt qu'à elle-même.
"""

from odoo import fields, models


class PulseMetric(models.Model):
    _name = "bf.ex.pulse.metric"
    _description = "Axe de mesure du pulse"
    _order = "sequence, id"

    name = fields.Char(string="Axe", required=True, translate=True)
    code = fields.Char(
        string="Code",
        required=True,
        help="Identifiant stable, employé par les agrégats. Le renommer coupe "
             "la continuité des scores historiques.",
    )
    sequence = fields.Integer(string="Séquence", default=10)
    description = fields.Text(
        string="Ce que l'axe mesure",
        translate=True,
        help="Lisible par la personne qui répond. Un axe qu'on ne sait pas "
             "définir produit des réponses qui ne veulent rien dire.",
    )
    question_ids = fields.One2many(
        "bf.ex.pulse.question", "metric_id", string="Questions",
    )
    question_count = fields.Integer(
        string="Nombre de questions", compute="_compute_question_count",
    )
    active = fields.Boolean(string="Actif", default=True)

    _sql_constraints = [
        ("code_uniq", "UNIQUE(code)", "Ce code d'axe existe déjà."),
    ]

    def _compute_question_count(self):
        data = self.env["bf.ex.pulse.question"]._read_group(
            [("metric_id", "in", self.ids)], ["metric_id"], ["__count"],
        )
        counts = {metric.id: count for metric, count in data}
        for rec in self:
            rec.question_count = counts.get(rec.id, 0)
