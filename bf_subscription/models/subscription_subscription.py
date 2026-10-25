import logging
from dateutil.relativedelta import relativedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


RENEWAL_ACTIVITY_XMLID = 'bf_subscription.mail_activity_type_renewal'

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
    _description = "Subscription"
    _inherit = ['mail.thread', 'mail.activity.mixin', 'analytic.mixin']
    _order = 'state, next_billing_date, name'

    # ---- Identity ----
    name = fields.Char(required=True, tracking=True)
    code = fields.Char(
        string="Reference", copy=False, readonly=True, index=True,
        default=lambda self: _("New"),
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
        'res.partner', string="Vendor", required=True, tracking=True,
        domain="[('supplier_rank', '>', 0)]",
        help="The vendor that bills the subscription.",
    )
    managed_for_id = fields.Many2one(
        'res.partner', string="Managed for client", tracking=True,
        domain="[('is_company', '=', True)]",
        help="If set: the subscription is administered by Blue Fox on "
             "behalf of this client.",
    )
    is_managed_for_client = fields.Boolean(
        compute='_compute_is_managed_for_client', store=True,
    )
    responsible_id = fields.Many2one(
        'res.users', string="Assignee", tracking=True,
        default=lambda self: self.env.user,
        domain="[('share', '=', False)]",
    )

    # ---- Classification ----
    category = fields.Selection([
        ('software_saas', "SaaS software"),
        ('corporate_membership', "Corporate membership"),
        ('infrastructure', 'Infrastructure'),
        ('professional_service', "Recurring professional service"),
        ('domain_name', "Domain name"),
        ('certificate', "Certificate"),
        ('hosting', "Hosting"),
        ('other', "Other"),
    ], default='software_saas', required=True, tracking=True)
    tag_ids = fields.Many2many('subscription.tag', string="Tags")

    # ---- State ----
    state = fields.Selection([
        ('draft', "Draft"),
        ('active', "Active"),
        ('paused', "Paused"),
        ('cancelled', "Canceled"),
        ('expired', "Expired"),
    ], default='draft', required=True, tracking=True, index=True)

    # ---- Cycle & Amount ----
    cycle = fields.Selection([
        ('monthly', "Monthly"),
        ('quarterly', "Quarterly"),
        ('semi_annual', "Semiannual"),
        ('annual', "Annual"),
        ('biennial', "Biennial"),
        ('triennial', "Triennial"),
        ('one_time', "One-time"),
        ('on_demand', "On demand"),
    ], default='monthly', required=True, tracking=True)
    cycle_amount = fields.Monetary(
        string="Amount per cycle", tracking=True,
        currency_field='currency_id',
    )
    monthly_equivalent = fields.Monetary(
        compute='_compute_monthly_equivalent', store=True,
        currency_field='currency_id',
        string="Monthly cost",
        help="cycle_amount normalized to a monthly equivalent for aggregates.",
    )

    # ---- Dates ----
    start_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    end_date = fields.Date(string="Planned end", tracking=True)
    next_billing_date = fields.Date(
        compute='_compute_next_billing_date', store=True,
        string="Next renewal", index=True,
    )
    auto_renew = fields.Boolean(default=True, tracking=True)
    notice_period_days = fields.Integer(
        string="Notice period (days)", default=30,
        help="How many days ahead to cancel before the automatic renewal.",
    )
    renewal_decision_date = fields.Date(
        string="Renewal confirmed for", copy=False, readonly=True, tracking=True,
        help="Renewal for which the \"keep it\" decision was made. As "
             "long as it targets the next renewal, no reminder is raised; "
             "at the next cycle the question comes up again by itself.",
    )
    renewal_decision_needed = fields.Boolean(
        compute='_compute_renewal_decision_needed',
        string="Renewal decision needed",
    )

    # ---- Rebilling ----
    rebilled = fields.Boolean(
        string="Rebilled to client", tracking=True,
        help="If checked: Blue Fox bills the client (managed_for_id) on "
             "top of paying the vendor.",
    )
    rebill_mode = fields.Selection([
        ('at_cost', "At cost"),
        ('markup_percent', "Cost + markup %"),
        ('fixed_amount', "Fixed amount"),
    ], default='at_cost')
    rebill_markup_percent = fields.Float(string="Markup (%)", default=0.0)
    rebill_fixed_amount = fields.Monetary(
        string="Fixed amount rebilled", currency_field='currency_id',
    )
    rebill_product_id = fields.Many2one(
        'product.product', string="Rebilling product",
        help="Product used by default when the rebilling wizard creates a "
             "customer invoice. Otherwise, a generic label is used.",
    )
    price_alert_threshold_pct = fields.Float(
        string="Price increase alert threshold (%)", default=15.0,
        help="If a linked vendor bill exceeds the amount per cycle by "
             "this percentage, an alert is posted in the chatter.",
    )

    # ---- Invoice correlation ----
    vendor_bill_ids = fields.One2many(
        'account.move', 'subscription_id',
        domain=[('move_type', 'in', ['in_invoice', 'in_refund'])],
        string="Vendor bills",
    )
    vendor_bill_count = fields.Integer(
        compute='_compute_invoice_counts', string="# Vendor bills",
    )
    customer_invoice_ids = fields.One2many(
        'account.move', 'subscription_id',
        domain=[('move_type', 'in', ['out_invoice', 'out_refund'])],
        string="Customer invoices",
    )
    customer_invoice_count = fields.Integer(
        compute='_compute_invoice_counts', string="# Customer invoices",
    )
    total_paid_lifetime = fields.Monetary(
        compute='_compute_totals', currency_field='currency_id',
        string="Total paid (lifetime)",
    )
    total_rebilled_lifetime = fields.Monetary(
        compute='_compute_totals', currency_field='currency_id',
        string="Total rebilled (lifetime)",
    )
    rebill_margin = fields.Monetary(
        compute='_compute_margin', currency_field='currency_id',
        string="Margin (lifetime)",
        help="Total rebilled to the client minus total paid to the vendor.",
    )
    rebill_margin_pct = fields.Float(
        compute='_compute_margin', string="Margin (%)",
    )
    last_vendor_bill_date = fields.Date(
        compute='_compute_last_vendor_bill_date', store=True,
        string="Last vendor bill",
    )
    is_dormant = fields.Boolean(
        compute='_compute_is_dormant', search='_search_is_dormant',
        string="Dormant",
        help="Active subscription with a billable cycle and no recent "
             "vendor bill (beyond 1.5× the cycle period).",
    )

    # ---- Misc ----
    # analytic_distribution (+ analytic_precision) provided by analytic.mixin
    external_reference = fields.Char(
        string="Vendor reference",
        help="Contract number, account ID, or any identifier on the "
             "vendor side.",
    )
    external_url = fields.Char(
        string="Vendor portal URL",
    )
    payment_method_label = fields.Char(
        string="Payment method",
        help="Free-form label: \"Visa BF *4242\", \"Direct debit account "
             "1\", etc.",
    )
    notes = fields.Html(string="Notes")

    # ------------------------------------------------------------------
    # SQL constraints
    # ------------------------------------------------------------------

    _sql_constraints = [
        ('code_unique', 'unique(code, company_id)',
         "The subscription reference must be unique per company."),
    ]

    # ------------------------------------------------------------------
    # Create / Write
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code') or vals['code'] == _("New"):
                vals['code'] = self.env['ir.sequence'].next_by_code(
                    'subscription.subscription'
                ) or _("New")
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

    def _refresh_next_billing_date(self):
        """Recalculer la date de renouvellement stockée.

        Elle dépend du jour, qu'aucun `depends` ne voit : Odoo ne la recalcule qu'à
        l'écriture d'une dépendance. Une fois l'échéance passée, elle restait au passé,
        et le renouvellement suivant n'entrait plus dans aucun horizon. La tâche des relances
        et le récapitulatif l'appellent avant de lire la date.
        """
        subs = self.filtered(lambda s: s.state not in ('cancelled', 'expired'))
        if subs:
            self.env.add_to_compute(self._fields['next_billing_date'], subs)
            subs._recompute_recordset(['next_billing_date'])

    @api.depends('state', 'auto_renew', 'next_billing_date', 'renewal_decision_date')
    def _compute_renewal_decision_needed(self):
        for rec in self:
            rec.renewal_decision_needed = bool(
                rec.state == 'active'
                and rec.auto_renew
                and rec.next_billing_date
                and rec.renewal_decision_date != rec.next_billing_date
            )

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
            raise UserError(_("The 'dormant' filter is not supported."))
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
                    'title': _("Rebilling not possible"),
                    'message': _("First fill in \"Managed for client\" "
                                 "before turning on rebilling."),
                }
            }

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def action_activate(self):
        for rec in self:
            if not rec.cycle_amount and rec.cycle not in ('on_demand', 'one_time'):
                raise UserError(_("Enter the amount per cycle before "
                                  "activating the subscription \"%s\".") % rec.name)
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
    # Renewal decision
    # ------------------------------------------------------------------

    def action_confirm_renewal(self):
        """Noter « on garde » pour le renouvellement en cours et fermer sa relance.

        La décision porte sur UNE date, pas sur l'abonnement : au cycle suivant,
        `next_billing_date` avance, la décision ne le vise plus, et la relance se
        réarme toute seule.
        """
        for rec in self:
            if not rec.next_billing_date:
                raise UserError(
                    _("The subscription \"%s\" has no upcoming renewal to "
                      "confirm.") % rec.name
                )
            rec.renewal_decision_date = rec.next_billing_date
        self.activity_feedback(
            [RENEWAL_ACTIVITY_XMLID], feedback=_("Renewal confirmed."),
        )
        return True

    def action_reopen_renewal(self):
        """Revenir sur la décision : la relance repart au prochain passage du cron."""
        self.write({'renewal_decision_date': False})
        return True

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_view_vendor_bills(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Vendor bills"),
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
            'name': _("Customer invoices"),
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
            raise UserError(_("No vendor URL configured."))
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
        # Écrite pour le responsable, qui devra agir : dans sa langue (un lien de facture peut venir d'un
        # courriel entrant ou d'une automatisation, sans personne derrière).
        lu = self.with_context(lang=self.responsible_id.lang or self.company_id.partner_id.lang or self.env.lang)
        # Markup : le corps porte du HTML (sinon message_post l'échappe et le fil montre les balises) ;
        # les valeurs, dont le nom de la facture, sont échappées par `%`.
        lu.message_post(
            body=Markup(lu.env._(
                "<b>Price increase detected</b> on bill <a href='#' "
                "data-oe-model='account.move' "
                "data-oe-id='%(bid)d'>%(bname)s</a>: %(amount).2f %(cur)s "
                "against %(cycle).2f %(cur)s per cycle (+%(pct).1f %%, "
                "threshold %(thr).0f %%)."
            )) % {
                'bid': bill.id, 'bname': bill.name or lu.env._("Draft"),
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
                lu.activity_schedule(
                    activity_type_id=ActivityType.id,
                    date_deadline=fields.Date.context_today(self),
                    summary=lu.env._("Check the price increase: %s") % self.name,
                    user_id=(self.responsible_id or self.env.user).id,
                )
            except Exception:
                _logger.exception("bf_subscription: price-alert activity failed for %s", self.name)
        return True

    # ------------------------------------------------------------------
    # Cron: renewal alerts
    # ------------------------------------------------------------------

    @api.model
    def _cron_renewal_alerts(self, horizon_days=30):
        """Create a "Renouvellement à venir" activity on subscriptions whose
        next_billing_date - notice_period_days falls within `horizon_days`.

        Two things keep it quiet, and neither is a rolling window:
        * a live alert of the same type already standing on the record;
        * a renewal decision (`renewal_decision_date`) naming the cycle in flight.
        """
        today = fields.Date.context_today(self)
        Activity = self.env['mail.activity']
        ActivityType = self.env.ref(RENEWAL_ACTIVITY_XMLID, raise_if_not_found=False) \
            or self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        if not ActivityType:
            _logger.warning("bf_subscription: no activity type available, skipping cron")
            return 0

        self.search([('state', '=', 'active')])._refresh_next_billing_date()
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
            # The decision names a date, so it expires by itself: once the cycle
            # rolls over, `next_billing_date` moves and the alert comes back.
            if sub.renewal_decision_date and sub.renewal_decision_date == sub.next_billing_date:
                continue
            # A live alert already holds the file. Deduplicating on existence
            # rather than on a 14-day window is what stops a second copy from
            # appearing beside an alert nobody has answered yet.
            standing = Activity.search([
                ('res_model', '=', 'subscription.subscription'),
                ('res_id', '=', sub.id),
                ('activity_type_id', '=', ActivityType.id),
            ], limit=1)
            if standing:
                continue
            # Le travail planifié n'a pas de langue : la relance part au responsable, dans la sienne.
            lu = sub.with_context(lang=sub.responsible_id.lang or sub.company_id.partner_id.lang or 'en_US')
            try:
                lu.activity_schedule(
                    activity_type_id=ActivityType.id,
                    date_deadline=cutoff,
                    summary=lu.env._("Upcoming renewal: %s") % sub.name,
                    note=lu.env._(
                        "The subscription <b>%(name)s</b> with "
                        "<b>%(vendor)s</b> renews on <b>%(date)s</b> "
                        "(notice: %(notice)d days, decide before "
                        "<b>%(cutoff)s</b>).<br/>Cycle amount: %(amount)s "
                        "%(currency)s.<br/><br/>Keep it? Click <b>Renewal "
                        "confirmed</b> on the record: the reminder stays "
                        "quiet until the next cycle. Cancel it? Uncheck "
                        "<b>Auto Renew</b>."
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
