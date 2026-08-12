from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SubscriptionRebillWizard(models.TransientModel):
    _name = 'subscription.rebill.wizard'
    _description = "Wizard de refacturation d'un abonnement géré pour client"

    subscription_id = fields.Many2one(
        'subscription.subscription', required=True, readonly=True,
    )
    vendor_bill_id = fields.Many2one(
        'account.move', string="Facture fournisseur",
        domain="[('move_type', 'in', ['in_invoice', 'in_refund']), "
               "('subscription_id', '=', subscription_id)]",
        help="Facture fournisseur d'origine. Optionnel : laisser vide pour "
             "créer une refacturation sans facture amont (ex. forfait fixe).",
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
        ('at_cost', 'Au coût'),
        ('markup_percent', 'Coût + markup %'),
        ('fixed_amount', 'Montant fixe'),
    ], required=True, default='at_cost')
    cost_amount = fields.Monetary(
        string="Coût (facture fournisseur)",
        currency_field='currency_id',
        help="Pré-rempli depuis la facture fournisseur si fournie.",
    )
    markup_percent = fields.Float(string="Markup (%)", default=0.0)
    fixed_amount = fields.Monetary(
        string="Montant fixe", currency_field='currency_id',
    )
    final_amount = fields.Monetary(
        compute='_compute_final_amount', currency_field='currency_id',
        string="Montant final refacturé",
    )

    line_description = fields.Char(
        string="Description de ligne", required=True,
    )
    product_id = fields.Many2one(
        'product.product', string="Produit",
        help="Optionnel — sinon une ligne libre est créée.",
    )
    invoice_date = fields.Date(
        string="Date de facturation", default=fields.Date.context_today,
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
                    "Cet abonnement n'est pas marqué comme géré pour un client : "
                    "renseignez d'abord « Géré pour le client » sur l'abonnement « %s »."
                ) % sub.name)
            if not sub.rebilled:
                raise UserError(_(
                    "L'abonnement « %s » n'a pas la refacturation activée."
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
                'line_description': _("Refacturation – %s") % sub.name,
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
            raise UserError(_("Le montant à refacturer doit être supérieur à zéro."))
        if not self.managed_for_id:
            raise UserError(_("Aucun client cible — renseignez « managed_for » sur l'abonnement."))

        journal = self.journal_id or self.env['account.journal'].search([
            ('type', '=', 'sale'),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not journal:
            raise UserError(_("Aucun journal de vente trouvé pour la société %s.") % self.company_id.name)

        line_vals = {
            'name': self.line_description,
            'quantity': 1.0,
            'price_unit': self.final_amount,
        }
        if self.product_id:
            line_vals['product_id'] = self.product_id.id
        if self.subscription_id.analytic_distribution:
            line_vals['analytic_distribution'] = self.subscription_id.analytic_distribution

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.managed_for_id.id,
            'invoice_date': self.invoice_date,
            'currency_id': self.currency_id.id,
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'subscription_id': self.subscription_id.id,
            'invoice_line_ids': [(0, 0, line_vals)],
            'narration': _(
                "Refacturation de l'abonnement %(name)s (réf. %(code)s)."
                "%(bill_ref)s"
            ) % {
                'name': self.subscription_id.name,
                'code': self.subscription_id.code or '',
                'bill_ref': (_(" Facture fournisseur source : %s.") % self.vendor_bill_id.name)
                            if self.vendor_bill_id and self.vendor_bill_id.name else '',
            },
        })

        # Trace the rebill on the subscription chatter
        self.subscription_id.message_post(
            body=_(
                "Facture de refacturation créée : <a href='#' data-oe-model='account.move' "
                "data-oe-id='%(id)d'>%(name)s</a> — %(amount).2f %(currency)s "
                "(mode : %(mode)s)."
            ) % {
                'id': invoice.id,
                'name': invoice.display_name or _("Brouillon"),
                'amount': self.final_amount,
                'currency': self.currency_id.name or '',
                'mode': dict(self._fields['mode'].selection).get(self.mode, self.mode),
            },
        )

        return {
            'type': 'ir.actions.act_window',
            'name': _("Facture de refacturation"),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': invoice.id,
            'target': 'current',
        }
