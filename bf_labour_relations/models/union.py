from odoo import api, fields, models


class Union(models.Model):
    """L'association syndicale.

    Elle est adossée à un partenaire parce qu'on lui écrit, on lui remet des
    cotisations et on la convoque. Le partenaire n'est pas obligatoire : au
    moment d'une requête en accréditation, l'association existe avant qu'on
    ait sa fiche complète.
    """

    _name = "bf.labour.union"
    _description = "Syndicat"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(string="Nom", required=True, tracking=True)
    active = fields.Boolean(string="Actif", default=True)
    partner_id = fields.Many2one(
        "res.partner", string="Partenaire", tracking=True,
        help="La fiche à qui on écrit et à qui la remise est versée.",
    )
    central = fields.Char(
        string="Centrale",
        help="L'organisation à laquelle la section est affiliée, si elle l'est.",
    )
    local_number = fields.Char(string="Section locale")
    contact_ids = fields.Many2many(
        "res.partner", "bf_labour_union_contact_rel", "union_id", "partner_id",
        string="Personnes-ressources",
        help="Les personnes qu'on appelle : conseiller syndical, présidence, "
             "délégués de la section.",
    )
    note = fields.Text(string="Note")

    unit_ids = fields.One2many("bf.labour.unit", "union_id", string="Unités")
    unit_count = fields.Integer(
        string="Nombre d'unités", compute="_compute_unit_count",
    )

    @api.depends("unit_ids")
    def _compute_unit_count(self):
        # `read_group` plutôt qu'un len() par ligne : une centrale qui couvre
        # trente établissements ne doit pas coûter trente requêtes.
        counts = dict(self.env["bf.labour.unit"]._read_group(
            [("union_id", "in", self.ids)], ["union_id"], ["__count"],
        ))
        for union in self:
            union.unit_count = counts.get(union, 0)

    def action_open_units(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Unités de négociation",
            "res_model": "bf.labour.unit",
            "view_mode": "list,form",
            "domain": [("union_id", "=", self.id)],
            "context": {"default_union_id": self.id},
        }
