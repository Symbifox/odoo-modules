from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Les champs qu'une ligne confirmée ne laisse plus toucher. Une ligne d'usage
# appartient à la date où elle a eu lieu ; la corriger après coup, c'est
# réécrire l'histoire du budget.
FROZEN_FIELDS = {"employee_id", "benefit_id", "date", "quantity", "amount", "source"}


class Usage(models.Model):
    _name = "bf.ex.usage"
    _description = "Usage d'un avantage"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    employee_id = fields.Many2one(
        "hr.employee", string="Employé", required=True, ondelete="cascade", index=True,
    )
    benefit_id = fields.Many2one(
        "bf.ex.benefit", string="Avantage", required=True, ondelete="restrict", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="benefit_id.company_id",
        store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )
    date = fields.Date(
        string="Date", required=True, default=fields.Date.context_today, tracking=True,
    )
    quantity = fields.Float(string="Quantité", default=1.0)
    amount = fields.Monetary(
        string="Coût réel", currency_field="currency_id",
        help="Laissé à zéro, le coût est estimé depuis le modèle de l'avantage.",
    )
    source = fields.Selection(
        [
            ("manual", "Saisi par le gestionnaire"),
            ("portal", "Déclaré par la personne"),
            ("expense", "Repris d'une note de frais"),
            ("import", "Importé en lot"),
        ],
        string="Origine", required=True, default="manual",
    )
    note = fields.Text(string="Note")
    state = fields.Selection(
        [("draft", "Brouillon"), ("confirmed", "Confirmé")],
        string="État", default="draft", required=True, tracking=True,
    )
    entitled = fields.Boolean(
        string="Avait le droit", compute="_compute_entitled", store=True,
        help="La personne avait-elle un droit ouvert à cet avantage ce jour-là? "
             "Un usage sans droit n'est pas bloqué : il est signalé.",
    )

    @api.depends("employee_id", "benefit_id", "date")
    def _compute_entitled(self):
        Entitlement = self.env["bf.ex.entitlement"].sudo()
        for line in self:
            if not (line.employee_id and line.benefit_id and line.date):
                line.entitled = False
                continue
            line.entitled = bool(Entitlement.search_count([
                ("employee_id", "=", line.employee_id.id),
                ("benefit_id", "=", line.benefit_id.id),
                ("date_start", "<=", line.date),
                "|", ("date_end", "=", False), ("date_end", ">=", line.date),
            ]))

    @api.constrains("quantity")
    def _check_quantity(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError(_("Une quantité d'usage doit être positive."))

    def action_confirm(self):
        for line in self:
            if line.state != "draft":
                continue
            line.state = "confirmed"
            # `_message_log` et non `message_post` : une note automatique ne doit
            # pas dépendre de l'adresse courriel de l'auteur, qu'un compte peut
            # très bien ne pas avoir.
            line._message_log(body=_("Usage confirmé. La ligne est désormais figée."))
        return True

    def action_reset_draft(self):
        raise UserError(_(
            "Une ligne d'usage confirmée ne revient pas au brouillon. "
            "Si elle est fausse, elle se supprime et se ressaisit, ce qui laisse "
            "une trace."
        ))

    def write(self, vals):
        touched = FROZEN_FIELDS & set(vals)
        if touched:
            frozen = self.filtered(lambda line: line.state == "confirmed")
            if frozen:
                raise UserError(_(
                    "Ligne d'usage confirmée : « %(fields)s » ne se modifie plus.",
                    fields=", ".join(sorted(touched)),
                ))
        return super().write(vals)
