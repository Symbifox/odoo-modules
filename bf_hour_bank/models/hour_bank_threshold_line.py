from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.misc import get_lang


class HourBankThresholdLine(models.Model):
    _name = 'hour.bank.threshold.line'
    _description = "Notification threshold of an hour bank"
    _order = 'bank_id, value'

    bank_id = fields.Many2one(
        'hour.bank.client', required=True, ondelete='cascade',
        string="Hour bank", index=True,
    )
    mode = fields.Selection(
        related='bank_id.threshold_mode', store=True, readonly=True,
        string="Mode",
    )
    value = fields.Float(
        required=True, string="Threshold",
        help="Hours for the 'Unbilled' and 'Remaining balance' modes. "
             "Percentage (0-100) for the '% of budget' mode. In "
             "'Remaining balance', a negative value sets the threshold "
             "below zero: that is the useful form on a postpaid account, "
             "where hours pile up as a debt between two invoices.",
    )
    name = fields.Char(
        compute='_compute_name', store=True, string="Label",
    )
    active = fields.Boolean(default=True)
    last_fired_date = fields.Datetime(
        readonly=True, string="Last triggered",
    )
    last_fired_period_key = fields.Char(
        readonly=True, string="Period key",
        help="Idempotency key: as long as it does not change, the "
             "threshold does not fire again. Reset depends on the mode (a "
             "new invoice, a budget change, or the balance rising back).",
    )
    is_armed = fields.Boolean(
        compute='_compute_is_armed', string="Armed",
        help="True if the threshold is ready to fire (current period key "
             "different from the last one).",
    )

    _sql_constraints = [
        (
            'unique_bank_value',
            'UNIQUE(bank_id, value)',
            "A threshold with this value already exists on this bank.",
        ),
    ]

    @api.depends('mode', 'value')
    def _compute_name(self):
        # Sans langue au contexte (RPC, travail planifié), la langue de la
        # maison, comme le faisait `_()` : `env._` y rendrait la source.
        lang = self.env.lang or get_lang(self.env).code
        for rec in self:
            rec.name = rec.with_context(lang=lang)._bf_label()

    def _bf_label(self):
        """Le libellé du palier dans la langue du contexte.

        `name` est stocké, donc écrit une fois dans la langue de qui a créé le
        palier : un courriel d'alerte le relit par ici, dans la langue de son
        lecteur."""
        self.ensure_one()
        env = self.env
        if self.mode == 'budget_pct':
            return env._("%(value)s %% of budget",
                         value=int(self.value) if self.value == int(self.value) else self.value)
        if self.mode == 'unbilled':
            return env._("%(value)sh unbilled", value=self.value)
        if self.mode == 'balance_floor':
            if self.value < 0:
                # Compte postpayé : le solde plonge sous zéro, le palier
                # se lit comme une dette d'heures et non comme un reste.
                return env._("Debt of %(value)sh", value=abs(self.value))
            return env._("Balance below %(value)sh", value=self.value)
        return env._("%(value)s", value=self.value)

    @api.depends('last_fired_period_key', 'bank_id.threshold_mode', 'bank_id.threshold_budget_hours')
    def _compute_is_armed(self):
        for rec in self:
            if not rec.bank_id or rec.bank_id.threshold_mode == 'disabled':
                rec.is_armed = False
                continue
            current_key = rec.bank_id._current_period_key()
            rec.is_armed = (rec.last_fired_period_key or '') != current_key

    @api.constrains('value', 'mode')
    def _check_value(self):
        for rec in self:
            if rec.mode == 'budget_pct':
                if rec.value <= 0 or rec.value > 100:
                    raise ValidationError(_(
                        "For the '%% of budget' mode, the threshold must "
                        "be strictly greater than 0 and at most 100."
                    ))
            elif rec.mode == 'balance_floor':
                # Une valeur négative est légitime sur un compte postpayé, où
                # les heures s'accumulent en dette avant d'être facturées : le
                # palier se pose alors sous zéro. Zéro reste refusé, sinon un
                # palier créé sans valeur alerterait dès le premier solde nul.
                if rec.value == 0:
                    raise ValidationError(_(
                        "The remaining balance threshold cannot be zero."
                    ))
            else:
                if rec.value <= 0:
                    raise ValidationError(_(
                        "The threshold must be strictly positive."
                    ))
