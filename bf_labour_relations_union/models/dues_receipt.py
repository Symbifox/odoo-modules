from odoo import _, api, fields, models


class DuesReceipt(models.Model):
    """Ce que le syndicat a REÇU, contre ce que l'employeur déclare avoir REMIS.

    🔴 L'écart est le seul chiffre qui compte, et c'est la raison d'exister du
    modèle. Enregistrer le reçu sans le rapprocher du déclaré donnerait un
    registre comptable de plus ; c'est le rapprochement qui fait le travail.

    Un écart n'est pas une accusation. Il naît aussi d'un décalage de période,
    d'une personne rattachée en retard ou d'un taux qui a changé en cours de
    période. Le modèle le montre, il ne le juge pas.
    """

    _name = "bf.labour.dues.receipt"
    _description = "Cotisations perçues"
    _inherit = ["mail.thread"]
    _order = "period_end desc, id desc"

    name = fields.Char(
        string="Référence", compute="_compute_name", store=True, readonly=False,
    )
    unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité de négociation", required=True,
        ondelete="restrict", index=True, tracking=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="unit_id.company_id",
        store=True, readonly=True, index=True,
    )
    union_id = fields.Many2one(
        "bf.labour.union", string="Syndicat", related="unit_id.union_id",
        store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )
    period_start = fields.Date(string="Début de la période", required=True)
    period_end = fields.Date(string="Fin de la période", required=True)
    amount_received = fields.Monetary(
        string="Montant reçu", currency_field="currency_id", tracking=True,
    )
    headcount_received = fields.Integer(
        string="Personnes au relevé",
        help="Le nombre de personnes que l'employeur a portées au relevé, "
             "qu'on compare au nombre de couverts.",
    )
    date_received = fields.Date(string="Reçu le", default=fields.Date.context_today)
    remittance_id = fields.Many2one(
        "bf.labour.dues.remittance", string="Remise de l'employeur",
        domain="[('unit_id', '=', unit_id)]", ondelete="set null", tracking=True,
    )
    note = fields.Text(string="Note")

    # ⚠️ NON stockés : ils dépendent de la remise liée, qui bouge de son côté.
    amount_declared = fields.Monetary(
        string="Montant déclaré remis", currency_field="currency_id",
        compute="_compute_reconciliation",
    )
    gap_amount = fields.Monetary(
        string="Écart", currency_field="currency_id", compute="_compute_reconciliation",
    )
    headcount_declared = fields.Integer(
        string="Personnes déclarées", compute="_compute_reconciliation",
    )
    headcount_gap = fields.Integer(
        string="Écart de personnes", compute="_compute_reconciliation",
    )
    covered_count = fields.Integer(
        string="Couverts dans l'unité", compute="_compute_reconciliation",
        help="Ce que le socle connaît de l'unité à la fin de la période. Un "
             "relevé qui compte moins de monde que l'unité n'en couvre mérite "
             "une question.",
    )
    is_reconciled = fields.Boolean(
        string="Rapproché", compute="_compute_reconciliation",
    )

    @api.depends("unit_id.name", "period_end")
    def _compute_name(self):
        for receipt in self:
            if receipt.name:
                continue
            bits = [_("Cotisations reçues")]
            if receipt.unit_id:
                bits.append(receipt.unit_id.name)
            if receipt.period_end:
                bits.append(receipt.period_end.strftime("%Y-%m"))
            receipt.name = " ".join(bits)

    @api.depends("remittance_id.amount_total", "remittance_id.headcount",
                 "amount_received", "headcount_received", "unit_id",
                 "period_end")
    def _compute_reconciliation(self):
        for receipt in self:
            remittance = receipt.remittance_id
            receipt.amount_declared = remittance.amount_total if remittance else 0.0
            receipt.headcount_declared = remittance.headcount if remittance else 0
            receipt.gap_amount = (receipt.amount_received or 0.0) - receipt.amount_declared
            receipt.headcount_gap = (receipt.headcount_received or 0) - receipt.headcount_declared
            reference = receipt.period_end or fields.Date.context_today(receipt)
            receipt.covered_count = len(receipt.unit_id.membership_ids.filtered(
                lambda m: m.covered
                and m.date_start <= reference
                and (not m.date_end or m.date_end >= reference)
            ))
            receipt.is_reconciled = bool(
                remittance and abs(receipt.gap_amount) < 0.005
            )
