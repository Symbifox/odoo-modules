"""Les axes de mesure du pulse.

Un axe n'est pas une question. Il porte plusieurs questions qui tournent d'une
vague à l'autre, ce qui évite qu'une équipe apprenne la question par cœur et
finisse par répondre à la question plutôt qu'à elle-même.
"""

from odoo import fields, models


class PulseMetric(models.Model):
    _name = "bf.ex.pulse.metric"
    _description = "Pulse measurement dimension"
    _order = "sequence, id"

    name = fields.Char(string="Dimension", required=True, translate=True)
    code = fields.Char(
        string="Code",
        required=True,
        help="A stable identifier, used by the aggregates. Renaming it "
             "breaks the continuity of historical scores.",
    )
    sequence = fields.Integer(string="Sequence", default=10)
    description = fields.Text(
        string="What this dimension measures",
        translate=True,
        help="Read by the person answering. A dimension you cannot define "
             "produces answers that mean nothing.",
    )
    question_ids = fields.One2many(
        "bf.ex.pulse.question", "metric_id", string="Questions",
    )
    question_count = fields.Integer(
        string="Number of questions", compute="_compute_question_count",
    )
    active = fields.Boolean(string="Active", default=True)

    _sql_constraints = [
        ("code_uniq", "UNIQUE(code)", "This dimension code already exists."),
    ]

    def _compute_question_count(self):
        data = self.env["bf.ex.pulse.question"]._read_group(
            [("metric_id", "in", self.ids)], ["metric_id"], ["__count"],
        )
        counts = {metric.id: count for metric, count in data}
        for rec in self:
            rec.question_count = counts.get(rec.id, 0)
