from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SubscriptionRebillWizard(models.TransientModel):
    _name = 'subscription.rebill.wizard'
    _description = "Rebilling wizard for a subscription managed for a client"

    subscription_id = fields.Many2one(
        'subscription.subscription', required=True, readonly=True,
    )
    vendor_bill_id = fields.Many2one(
        'account.move', string="Vendor bill",
        domain="[('move_type', 'in', ['in_invoice', 'in_refund']), "
               "('subscription_id', '=', subscription_id)]",
        help="Original vendor bill. Optional: leave empty to create a "
             "rebill without an upstream bill (e.g. flat fee).",
    )
    managed_for_id = fields.Many2one(
        'res.partner', required=True, readonly=True,
    )
    currency_id = fields.Many2one(
        'res.currency', readonly=True,
    )
    company_id = fields.Many2one(
        'res.company', readonly=True,
    )

    mode = fields.Selection([
        ('at_cost', "At cost"),
        ('markup_percent', "Cost + markup %"),
        ('fixed_amount', "Fixed amount"),
    ], required=True, default='at_cost')
    cost_amount = fields.Monetary(
        string="Cost (vendor bill)",
        currency_field='currency_id',
        help="Pre-filled from the vendor bill if one is given.",
    )
    markup_percent = fields.Float(string="Markup (%)", default=0.0)
    fixed_amount = fields.Monetary(
        string="Fixed amount", currency_field='currency_id',
    )
    final_amount = fields.Monetary(
        compute='_compute_final_amount', currency_field='currency_id',
        string="Final rebilled amount",
    )

    line_description = fields.Char(
        string="Line description", required=True,
    )
    product_id = fields.Many2one(
        'product.product', string="Product",
        help="Optional: otherwise a free-form line is created.",
    )
    invoice_date = fields.Date(
        string="Invoice date", default=fields.Date.context_today,
    )
    journal_id = fields.Many2one(
        'account.journal', string="Journal",
        domain="[('type', '=', 'sale'), ('company_id', '=', company_id)]",
    )

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        sub_id = self.env.context.get('default_subscription_id') or res.get('subscription_id')
        bill_id = self.env.context.get('default_vendor_bill_id') or res.get('vendor_bill_id')

        # If launched from a vendor bill, infer subscription_id
        if bill_id and not sub_id:
            bill = self.env['account.move'].browse(bill_id)
            if bill.subscription_id:
                sub_id = bill.subscription_id.id

        if sub_id:
            sub = self.env['subscription.subscription'].browse(sub_id)
            if not sub.is_managed_for_client:
                raise UserError(_(
                    "This subscription is not marked as managed for a "
                    "client: first fill in \"Managed for client\" on the "
                    "subscription \"%s\"."
                ) % sub.name)
            if not sub.rebilled:
                raise UserError(_(
                    "Rebilling is not turned on for the subscription \"%s\"."
                ) % sub.name)
            res.update({
                'subscription_id': sub.id,
                'managed_for_id': sub.managed_for_id.id,
                'currency_id': sub.currency_id.id,
                'company_id': sub.company_id.id,
                'mode': sub.rebill_mode or 'at_cost',
                'markup_percent': sub.rebill_markup_percent,
                'fixed_amount': sub.rebill_fixed_amount or sub.cycle_amount,
                # Fallback cost to the subscription's cycle amount; overridden below if a bill is given.
                'cost_amount': sub.cycle_amount,
                # La ligne est lue par le client géré : dans sa langue (la personne peut la retoucher).
                'line_description': sub.with_context(
                    lang=sub.managed_for_id.lang or self.env.lang).env._("Rebilling: %s") % sub.name,
                'product_id': sub.rebill_product_id.id if sub.rebill_product_id else False,
            })

        if bill_id:
            bill = self.env['account.move'].browse(bill_id)
            res.update({
                'vendor_bill_id': bill.id,
                'cost_amount': bill.amount_total,
            })
            if 'managed_for_id' not in res or not res['managed_for_id']:
                sub = bill.subscription_id
                if sub and sub.managed_for_id:
                    res['managed_for_id'] = sub.managed_for_id.id

        return res

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('mode', 'cost_amount', 'markup_percent', 'fixed_amount')
    def _compute_final_amount(self):
        for rec in self:
            if rec.mode == 'fixed_amount':
                rec.final_amount = rec.fixed_amount
            elif rec.mode == 'markup_percent':
                rec.final_amount = rec.cost_amount * (1.0 + (rec.markup_percent or 0.0) / 100.0)
            else:  # at_cost
                rec.final_amount = rec.cost_amount

    @api.onchange('vendor_bill_id')
    def _onchange_vendor_bill(self):
        if self.vendor_bill_id:
            self.cost_amount = self.vendor_bill_id.amount_total

    # ------------------------------------------------------------------
    # Action
    # ------------------------------------------------------------------

    def action_create_invoice(self):
        self.ensure_one()
        if self.final_amount <= 0:
            raise UserError(_("The amount to rebill must be greater than zero."))
        if not self.managed_for_id:
            raise UserError(_("No target client: fill in \"managed_for\" "
                              "on the subscription."))

        journal = self.journal_id or self.env['account.journal'].search([
            ('type', '=', 'sale'),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not journal:
            raise UserError(_("No sales journal found for company %s.") % self.company_id.name)

        line_vals = {
            'name': self.line_description,
            'quantity': 1.0,
            'price_unit': self.final_amount,
        }
        if self.product_id:
            line_vals['product_id'] = self.product_id.id
        if self.subscription_id.analytic_distribution:
            line_vals['analytic_distribution'] = self.subscription_id.analytic_distribution

        # La note de la facture est lue par le client géré : dans sa langue.
        lc = self.with_context(lang=self.managed_for_id.lang or self.env.lang)
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.managed_for_id.id,
            'invoice_date': self.invoice_date,
            'currency_id': self.currency_id.id,
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'subscription_id': self.subscription_id.id,
            'invoice_line_ids': [(0, 0, line_vals)],
            'narration': lc.env._(
                "Rebilling of subscription %(name)s (ref. "
                "%(code)s).%(bill_ref)s"
            ) % {
                'name': self.subscription_id.name,
                'code': self.subscription_id.code or '',
                'bill_ref': (lc.env._(" Source vendor bill: %s.") % self.vendor_bill_id.name)
                            if self.vendor_bill_id and self.vendor_bill_id.name else '',
            },
        })

        # Trace the rebill on the subscription chatter
        self.subscription_id.message_post(
            body=Markup(_(
                "Rebill invoice created: <a href='#' "
                "data-oe-model='account.move' "
                "data-oe-id='%(id)d'>%(name)s</a>, %(amount).2f "
                "%(currency)s (mode: %(mode)s)."
            )) % {
                'id': invoice.id,
                'name': invoice.display_name or _("Draft"),
                'amount': self.final_amount,
                'currency': self.currency_id.name or '',
                'mode': dict(self._fields['mode']._description_selection(self.env)).get(self.mode, self.mode),
            },
        )

        return {
            'type': 'ir.actions.act_window',
            'name': _("Rebill invoice"),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': invoice.id,
            'target': 'current',
        }
