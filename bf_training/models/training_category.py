from odoo import fields, models


class BfTrainingCategory(models.Model):
    """Une catégorie de sujet.

    Elle sert à deux choses : regrouper le catalogue, et surtout porter les
    quotas d'heures par sujet, avec leurs exclusions. Le règlement sur les
    services de garde éducatifs à l'enfance, par exemple, exige 6 heures de
    perfectionnement par an « dont au moins 3 heures » sur un sujet précis, et
    dit nommément que le secourisme n'y compte pas. Sans catégorie, cette phrase
    ne se représente pas.
    """

    _name = "bf.training.category"
    _description = "Training category"
    _order = "sequence, name"

    name = fields.Char(string="Category", required=True, translate=True)
    code = fields.Char(
        string="Code", required=True,
        help="Short, stable, and used by the requirements. It is not "
             "translated.")
    sequence = fields.Integer(string="Sequence", default=10)
    color = fields.Integer(string="Colour")
    active = fields.Boolean(string="Active", default=True)
    description = fields.Text(string="Description", translate=True)
    activity_ids = fields.One2many(
        "bf.training.activity", "category_id", string="Activities")
    activity_count = fields.Integer(
        string="Number of activities", compute="_compute_activity_count")

    _sql_constraints = [
        ("code_uniq", "unique (code)", "This category code is already taken."),
    ]

    def _compute_activity_count(self):
        groupes = self.env["bf.training.activity"]._read_group(
            [("category_id", "in", self.ids)], ["category_id"], ["__count"])
        compte = {categorie.id: nombre for categorie, nombre in groupes}
        for rec in self:
            rec.activity_count = compte.get(rec.id, 0)
