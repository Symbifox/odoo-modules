from odoo import api, fields, models


class IrModel(models.Model):
    _inherit = "ir.model"

    bf_color_colorable = fields.Boolean(
        string="Carries a free color",
        compute="_compute_bf_color_colorable", search="_search_bf_color_colorable",
        help="The model is wired to bf_color: an automatic rule can color it.",
    )

    def _compute_bf_color_colorable(self):
        names = set(self.env["bf.color.mixin"]._bf_color_model_names())
        for model in self:
            model.bf_color_colorable = model.model in names

    @api.model
    def _search_bf_color_colorable(self, operator, value):
        """Searchable, so the rule form filters its models on the server.

        A computed list of allowed models on the rule only reached the form
        through an onchange, which a new rule never triggered: it offered none.
        """
        if operator not in ("=", "!="):
            raise NotImplementedError(operator)
        wanted = (operator == "=") == bool(value)
        names = self.env["bf.color.mixin"]._bf_color_model_names()
        return [("model", "in" if wanted else "not in", names)]
