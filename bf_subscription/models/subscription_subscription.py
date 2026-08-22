import logging
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


CYCLE_MONTHS = {
    'monthly': 1,
    'quarterly': 3,
    'semi_annual': 6,
    'annual': 12,
    'biennial': 24,
    'triennial': 36,
}


class Subscription(models.Model):
    _name = 'subscription.subscription'
    _description = "Abonnement"
    _inherit = ['mail.thread', 'mail.activity.mixin', 'analytic.mixin']
    _order = 'state, next_billing_date, name'

    # ---- Identity ----
    name = fields.Char(required=True, tracking=True)
    code = fields.Char(
        string="Référence", copy=False, readonly=True, index=True,
        default=lambda self: _("Nouveau"),
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    # ---- Parties ----
    vendor_id = fields.Many2one(
        'res.partner', string="Fournisseur", required=True, tracking=True,
        domain="[('supplier_rank', '>', 0)]",
        help="Le fournisseur qui facture l'abonnement.",
    )
    managed_for_id = fields.Many2one(
        'res.partner', string="Géré pour le client", tracking=True,
        domain="[('is_company', '=', True)]",
        help="Si renseigné : l'abonnement est administré par Blue Fox au nom de ce client.",
    )
    is_managed_for_client = fields.Boolean(
        compute='_compute_is_managed_for_client', store=True,
    )
    responsible_id = fields.Many2one(
        'res.users', string="Responsable", tracking=True,
        default=lambda self: self.env.user,
        domain="[('share', '=', False)]",
    )

    # ---- Classification ----
    category = fields.Selection([
        ('software_saas', 'Logiciel SaaS'),
        ('corporate_membership', 'Membership corporatif'),
        ('infrastructure', 'Infrastructure'),
        ('professional_service', 'Service professionnel récurrent'),
        ('domain_name', 'Nom de domaine'),
        ('certificate', 'Certificat'),
        ('hosting', 'Hébergement'),
        ('other', 'Autre'),
    ], default='software_saas', required=True, tracking=True)
    tag_ids = fields.Many2many('subscription.tag', string="Étiquettes")

    # ---- State ----
    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('active', 'Actif'),
        ('paused', 'Suspendu'),
        ('cancelled', 'Résilié'),
        ('expired', 'Expiré'),
    ], default='draft', required=True, tracking=True, index=True)

    # ---- Cycle & Amount ----
    cycle = fields.Selection([
        ('monthly', 'Mensuel'),
        ('quarterly', 'Trimestriel'),
        ('semi_annual', 'Semestriel'),
        ('annual', 'Annuel'),
        ('biennial', 'Biennal'),
        ('triennial', 'Triennal'),
        ('one_time', 'Unique'),
        ('on_demand', 'À la demande'),
    ], default='monthly', required=True, tracking=True)
    cycle_amount = fields.Monetary(
        string="Montant par cycle", tracking=True,
        currency_field='currency_id',
    )
    monthly_equivalent = fields.Monetary(
        compute='_compute_monthly_equivalent', store=True,
        currency_field='currency_id',
        string="Coût mensualisé",
        help="cycle_amount normalisé en équivalent mensuel pour les agrégats.",
    )

    # ---- Dates ----
    start_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    end_date = fields.Date(string="Fin prévue", tracking=True)
    next_billing_date = fields.Date(
        compute='_compute_next_billing_date', store=True,
        string="Prochain renouvellement", index=True,
    )
    auto_renew = fields.Boolean(default=True, tracking=True)
    notice_period_days = fields.Integer(
        string="Préavis (jours)", default=30,
        help="Combien de jours d'avance pour annuler avant le renouvellement automatique.",
    )

    # ---- Rebilling ----
    rebilled = fields.Boolean(
        string="Refacturé au client", tracking=True,
        help="Si coché : Blue Fox facture le client (managed_for_id) en plus de payer le fournisseur.",
    )
    rebill_mode = fields.Selection([
        ('at_cost', 'Au coût'),
        ('markup_percent', 'Coût + markup %'),
        ('fixed_amount', 'Montant fixe'),
    ], default='at_cost')
    rebill_markup_percent = fields.Float(string="Markup (%)", default=0.0)
    rebill_fixed_amount = fields.Monetary(
        string="Montant fixe refacturé", currency_field='currency_id',
    )
    rebill_product_id = fields.Many2one(
        'product.product', string="Produit de refacturation",
        help="Produit utilisé par défaut lorsque le wizard de refacturation crée une facture client. "
             "Sinon, un libellé générique est utilisé.",
    )
    price_alert_threshold_pct = fields.Float(
        string="Seuil d'alerte hausse (%)", default=15.0,
        help="Si une facture fournisseur liée dépasse le montant par cycle de "
             "ce pourcentage, une alerte est postée sur le fil de discussion.",
    )

    # ---- Invoice correlation ----
    vendor_bill_ids = fields.One2many(
        'account.move', 'subscription_id',
        domain=[('move_type', 'in', ['in_invoice', 'in_refund'])],
        string="Factures fournisseur",
    )
    vendor_bill_count = fields.Integer(
        compute='_compute_invoice_counts', string="# Factures fournisseur",
    )
    customer_invoice_ids = fields.One2many(
        'account.move', 'subscription_id',
        domain=[('move_type', 'in', ['out_invoice', 'out_refund'])],
        string="Factures client",
    )
    customer_invoice_count = fields.Integer(
        compute='_compute_invoice_counts', string="# Factures client",
    )
    total_paid_lifetime = fields.Monetary(
        compute='_compute_totals', currency_field='currency_id',
        string="Total payé (vie)",
    )
    total_rebilled_lifetime = fields.Monetary(
        compute='_compute_totals', currency_field='currency_id',
        string="Total refacturé (vie)",
    )
    rebill_margin = fields.Monetary(
        compute='_compute_margin', currency_field='currency_id',
        string="Marge (vie)",
        help="Total refacturé au client moins total payé au fournisseur.",
    )
    rebill_margin_pct = fields.Float(
        compute='_compute_margin', string="Marge (%)",
    )
    last_vendor_bill_date = fields.Date(
        compute='_compute_last_vendor_bill_date', store=True,
        string="Dernière facture fournisseur",
    )
    is_dormant = fields.Boolean(
        compute='_compute_is_dormant', search='_search_is_dormant',
        string="Dormant",
        help="Abonnement actif à cycle facturable sans facture fournisseur "
             "récente (au-delà de 1,5× la période du cycle).",
    )

    # ---- Misc ----
    # analytic_distribution (+ analytic_precision) provided by analytic.mixin
    external_reference = fields.Char(
        string="Référence fournisseur",
        help="Numéro de contrat, ID compte, ou tout identifiant côté fournisseur.",
    )
    external_url = fields.Char(
        string="URL portail fournisseur",
    )
    payment_method_label = fields.Char(
        string="Méthode de paiement",
        help="Libellé libre : « Visa BF *4242 », « Prélèvement DD compte 1 », etc.",
    )
    notes = fields.Html(string="Notes")

    # ------------------------------------------------------------------
    # SQL constraints
    # ------------------------------------------------------------------

    _sql_constraints = [
        ('code_unique', 'unique(code, company_id)',
         "La référence d'abonnement doit être unique par société."),
    ]

    # ------------------------------------------------------------------
    # Create / Write
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code') or vals['code'] == _("Nouveau"):
                vals['code'] = self.env['ir.sequence'].next_by_code(
                    'subscription.subscription'
                ) or _("Nouveau")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('managed_for_id')
    def _compute_is_managed_for_client(self):
        for rec in self:
            rec.is_managed_for_client = bool(rec.managed_for_id)

    @api.depends('cycle', 'cycle_amount')
    def _compute_monthly_equivalent(self):
        for rec in self:
            months = CYCLE_MONTHS.get(rec.cycle)
            if months and rec.cycle_amount:
                rec.monthly_equivalent = rec.cycle_amount / months
            else:
                rec.monthly_equivalent = 0.0

    @api.depends('start_date', 'cycle', 'state', 'end_date')
    def _compute_next_billing_date(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.start_date or rec.state in ('cancelled', 'expired'):
                rec.next_billing_date = False
                continue
            if rec.cycle == 'on_demand':
                # On-demand billing has no scheduled renewal date.
                rec.next_billing_date = False
                continue
            if rec.cycle in (False, 'one_time'):
                # one_time: next billing = start_date if in future, else nothing
                rec.next_billing_date = rec.start_date if rec.start_date >= today else False
                continue
            months = CYCLE_MONTHS.get(rec.cycle, 1)
            # advance start_date by full cycles until we land on or after today
            nbd = rec.start_date
            # cap iterations to prevent runaway loops on bad data
            for _ in range(120):
                if nbd >= today:
                    break
                nbd = nbd + relativedelta(months=months)
            # respect end_date if set
            if rec.end_date and nbd > rec.end_date:
                rec.next_billing_date = False
            else:
                rec.next_billing_date = nbd

    @api.depends('vendor_bill_ids', 'customer_invoice_ids')
    def _compute_invoice_counts(self):
        for rec in self:
            rec.vendor_bill_count = len(rec.vendor_bill_ids)
            rec.customer_invoice_count = len(rec.customer_invoice_ids)

    @api.depends(
        'vendor_bill_ids.amount_total_signed',
        'vendor_bill_ids.state',
        'customer_invoice_ids.amount_total_signed',
        'customer_invoice_ids.state',
    )
    def _compute_totals(self):
        for rec in self:
            paid = sum(
                m.amount_total_signed
                for m in rec.vendor_bill_ids
                if m.state == 'posted'
            )
            # vendor bill amount_total_signed is negative for in_invoice; flip for display
            rec.total_paid_lifetime = -paid if paid else 0.0
            rec.total_rebilled_lifetime = sum(
                m.amount_total_signed
                for m in rec.customer_invoice_ids
                if m.state == 'posted'
            )

    @api.depends('total_paid_lifetime', 'total_rebilled_lifetime')
    def _compute_margin(self):
        for rec in self:
            rec.rebill_margin = rec.total_rebilled_lifetime - rec.total_paid_lifetime
            rec.rebill_margin_pct = (
                (rec.rebill_margin / rec.total_paid_lifetime * 100.0)
                if rec.total_paid_lifetime else 0.0
            )

    @api.depends('vendor_bill_ids.invoice_date', 'vendor_bill_ids.state')
    def _compute_last_vendor_bill_date(self):
        for rec in self:
            dates = rec.vendor_bill_ids.filtered(
                lambda m: m.state == 'posted' and m.invoice_date
            ).mapped('invoice_date')
            rec.last_vendor_bill_date = max(dates) if dates else False

    # Grace period (days) added on top of one full cycle before a subscription
    # is considered dormant. Absorbs normal accounting-entry lag for vendor bills.
    _DORMANCY_GRACE_DAYS = 45

    def _dormancy_cutoff(self):
        """Date before which a missing bill marks the subscription as dormant."""
        self.ensure_one()
        months = CYCLE_MONTHS.get(self.cycle)
        if not months:
            return None  # one_time / on_demand never dormant
        # One full cycle + a fixed grace window, expressed in whole days.
        days = int(months * 30) + self._DORMANCY_GRACE_DAYS
        return fields.Date.context_today(self) - relativedelta(days=days)

    @api.depends('state', 'cycle', 'last_vendor_bill_date', 'start_date')
    def _compute_is_dormant(self):
        today = fields.Date.context_today(self)
        for rec in self:
            cutoff = rec._dormancy_cutoff() if rec.state == 'active' else None
            if cutoff is None:
                rec.is_dormant = False
                continue
            # Grace: a brand-new subscription started after the cutoff is not dormant.
            if rec.start_date and rec.start_date >= cutoff:
                rec.is_dormant = False
                continue
            ref_date = rec.last_vendor_bill_date or rec.start_date
            rec.is_dormant = bool(ref_date and ref_date < cutoff)

    def _search_is_dormant(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            raise UserError(_("Filtre 'dormant' non supporté."))
        want_dormant = (operator == '=' and value) or (operator == '!=' and not value)
        candidates = self.search([('state', '=', 'active')])
        dormant_ids = candidates.filtered('is_dormant').ids
        if want_dormant:
            return [('id', 'in', dormant_ids)]
        return [('id', 'not in', dormant_ids)]

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------

    @api.onchange('rebilled')
    def _onchange_rebilled(self):
        if self.rebilled and not self.is_managed_for_client:
            return {
                'warning': {
                    'title': _("Refacturation impossible"),
                    'message': _("Il faut d'abord renseigner « Géré pour le client » avant d'activer la refacturation."),
                }
            }

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def action_activate(self):
        for rec in self:
            if not rec.cycle_amount and rec.cycle not in ('on_demand', 'one_time'):
                raise UserError(_("Renseignez le montant par cycle avant d'activer l'abonnement « %s ».") % rec.name)
            rec.state = 'active'
        return True

    def action_pause(self):
        self.write({'state': 'paused'})
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    def action_expire(self):
        self.write({'state': 'expired'})
        return True

    def action_set_draft(self):
        self.write({'state': 'draft'})
        return True

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_view_vendor_bills(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Factures fournisseur"),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.vendor_bill_ids.ids)],
            'context': {
                'default_subscription_id': self.id,
                'default_move_type': 'in_invoice',
                'default_partner_id': self.vendor_id.id,
            },
        }

    def action_view_customer_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Factures client"),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.customer_invoice_ids.ids)],
            'context': {
                'default_subscription_id': self.id,
                'default_move_type': 'out_invoice',
                'default_partner_id': self.managed_for_id.id,
            },
        }

    def action_open_external_url(self):
        self.ensure_one()
        if not self.external_url:
            raise UserError(_("Aucune URL fournisseur configurée."))
        return {
            'type': 'ir.actions.act_url',
            'url': self.external_url,
            'target': 'new',
        }

    # ------------------------------------------------------------------
    # Price-increase alert (called when a vendor bill is linked)
    # ------------------------------------------------------------------

    def _maybe_alert_price_increase(self, bill):
        """Post a chatter alert + activity if `bill` exceeds the recorded
        cycle amount by more than the configured threshold.

        Safe to call repeatedly: keyed on the bill so each bill alerts once.
        """
        self.ensure_one()
        threshold = self.price_alert_threshold_pct or 0.0
        if threshold <= 0 or not self.cycle_amount or not bill or not bill.amount_total:
            return False
        ceiling = self.cycle_amount * (1.0 + threshold / 100.0)
        if bill.amount_total <= ceiling:
            return False
        increase_pct = (bill.amount_total / self.cycle_amount - 1.0) * 100.0
        self.message_post(
            body=_(
                "<b>Hausse de prix détectée</b> sur la facture "
                "<a href='#' data-oe-model='account.move' data-oe-id='%(bid)d'>%(bname)s</a> : "
                "%(amount).2f %(cur)s contre %(cycle).2f %(cur)s au cycle "
                "(+%(pct).1f %%, seuil %(thr).0f %%)."
            ) % {
                'bid': bill.id, 'bname': bill.name or _("Brouillon"),
                'amount': bill.amount_total, 'cycle': self.cycle_amount,
                'cur': self.currency_id.name or '', 'pct': increase_pct,
                'thr': threshold,
            },
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        ActivityType = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        if ActivityType:
            try:
                self.activity_schedule(
                    activity_type_id=ActivityType.id,
                    date_deadline=fields.Date.context_today(self),
                    summary=_("Vérifier la hausse de prix — %s") % self.name,
                    user_id=(self.responsible_id or self.env.user).id,
                )
            except Exception:
                _logger.exception("bf_subscription: price-alert activity failed for %s", self.name)
        return True

    # ------------------------------------------------------------------
    # Cron: renewal alerts
    # ------------------------------------------------------------------

    @api.model
    def _cron_renewal_alerts(self, horizon_days=30, lookback_days=14):
        """Create a "Renouvellement à venir" activity on subscriptions whose
        next_billing_date - notice_period_days falls within `horizon_days`.

        Idempotent: skips if a same-type activity was created within `lookback_days`.
        """
        today = fields.Date.context_today(self)
        Activity = self.env['mail.activity']
        ActivityType = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        if not ActivityType:
            _logger.warning("bf_subscription: mail.mail_activity_data_todo not found, skipping cron")
            return 0

        subs = self.search([
            ('state', '=', 'active'),
            ('next_billing_date', '!=', False),
            ('auto_renew', '=', True),
        ])
        created = 0
        for sub in subs:
            # A renewal alert is only worth raising if it can go quiet for part of
            # the cycle. Once the notice period plus the alert horizon cover the
            # whole cycle, the deadline is permanently in range: the activity comes
            # back at every run, usually already overdue, with nothing left to
            # decide. A monthly subscription with 30 days' notice is the textbook
            # case -- it renews inside the horizon no matter what day it is.
            cycle_days = int((CYCLE_MONTHS.get(sub.cycle) or 0) * 30)
            if cycle_days and (sub.notice_period_days or 0) + horizon_days >= cycle_days:
                continue
            cutoff = sub.next_billing_date - relativedelta(days=sub.notice_period_days or 0)
            if cutoff > today + relativedelta(days=horizon_days):
                continue
            # Idempotence: skip if recent activity exists
            recent = Activity.search([
                ('res_model', '=', 'subscription.subscription'),
                ('res_id', '=', sub.id),
                ('activity_type_id', '=', ActivityType.id),
                ('create_date', '>=', fields.Datetime.now() - relativedelta(days=lookback_days)),
            ], limit=1)
            if recent:
                continue
            try:
                sub.activity_schedule(
                    activity_type_id=ActivityType.id,
                    date_deadline=cutoff,
                    summary=_("Renouvellement à venir — %s") % sub.name,
                    note=_(
                        "L'abonnement <b>%(name)s</b> chez <b>%(vendor)s</b> "
                        "se renouvelle le <b>%(date)s</b> (préavis : %(notice)d jours, "
                        "à décider avant <b>%(cutoff)s</b>).<br/>"
                        "Montant cycle : %(amount)s %(currency)s."
                    ) % {
                        'name': sub.name,
                        'vendor': sub.vendor_id.name,
                        'date': sub.next_billing_date,
                        'notice': sub.notice_period_days or 0,
                        'cutoff': cutoff,
                        'amount': sub.cycle_amount or 0,
                        'currency': sub.currency_id.name or '',
                    },
                    user_id=(sub.responsible_id or self.env.user).id,
                )
                created += 1
            except Exception:
                _logger.exception(
                    "bf_subscription: failed to schedule renewal activity for %s", sub.name,
                )
        _logger.info("bf_subscription: renewal cron created %d activities", created)
        return created
