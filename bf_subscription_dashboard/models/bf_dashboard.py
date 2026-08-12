import logging
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class BfDashboard(models.Model):
    _inherit = 'bf.dashboard'

    @api.model
    def get_dashboard_data(self):
        data = super().get_dashboard_data()
        try:
            data['subscription'] = self._get_subscription_summary()
        except Exception:
            _logger.exception("Error loading subscription summary")
            data['subscription'] = None
        return data

    @api.model
    def _get_subscription_summary(self):
        Sub = self.env['subscription.subscription']
        company = self.env.company
        active = Sub.search([('state', '=', 'active'), ('company_id', '=', company.id)])
        today = fields.Date.context_today(self)
        h30 = today + relativedelta(days=30)
        h90 = today + relativedelta(days=90)
        renew30 = active.filtered(lambda s: s.next_billing_date and today <= s.next_billing_date <= h30)
        renew90 = active.filtered(lambda s: s.next_billing_date and today <= s.next_billing_date <= h90)
        dormant = active.filtered('is_dormant')
        managed = active.filtered('managed_for_id')
        monthly = sum(active.mapped('monthly_equivalent'))
        return {
            'active_count': len(active),
            'monthly_spend': round(monthly, 2),
            'annual_spend': round(monthly * 12.0, 2),
            'renewals_30': len(renew30),
            'renewals_90': len(renew90),
            'dormant_count': len(dormant),
            'managed_count': len(managed),
            'currency': company.currency_id.symbol or company.currency_id.name or '',
        }

    @api.model
    def action_open_subscription_dashboard(self):
        return self.env['ir.actions.actions']._for_xml_id(
            'bf_subscription.action_subscription_subscription'
        )

    @api.model
    def action_open_subscription_renewals(self):
        return self.env['ir.actions.actions']._for_xml_id(
            'bf_subscription.action_subscription_renewals_30d'
        )
