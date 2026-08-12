from markupsafe import escape as _esc
from dateutil.relativedelta import relativedelta

from odoo import _, fields, models


class DailyDigestConfig(models.Model):
    _inherit = "daily.digest.config"

    include_subscription_renewals = fields.Boolean(
        string="Inclure les renouvellements d'abonnements", default=True,
    )
    subscription_renewal_days = fields.Integer(
        string="Horizon renouvellements (jours)", default=30,
    )

    def _render_subscription_renewals(self, user):
        """Build the HTML for upcoming subscription renewals, or '' if none."""
        self.ensure_one()
        if not self.include_subscription_renewals:
            return ""
        Sub = self.env['subscription.subscription'].sudo()
        today = fields.Date.context_today(self)
        horizon = today + relativedelta(days=self.subscription_renewal_days or 30)
        company = user.company_id or self.env.company
        subs = Sub.search([
            ('state', '=', 'active'),
            ('company_id', '=', company.id),
            ('next_billing_date', '>=', today),
            ('next_billing_date', '<=', horizon),
        ], order='next_billing_date')
        if not subs:
            return ""

        accent = company.report_brand_primary or "#29ABE1"
        dark = company.report_brand_dark or "#2D3031"
        rows = ""
        for s in subs:
            cancel_by = s.next_billing_date - relativedelta(days=s.notice_period_days or 0)
            days_left = (s.next_billing_date - today).days
            urgent = days_left <= (s.notice_period_days or 0)
            date_color = "#dc3545" if urgent else "#374151"
            amount = "%s %s" % ('{:,.2f}'.format(s.cycle_amount or 0).replace(',', ' '),
                                s.currency_id.name or '')
            rows += (
                f'<tr>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;font-family:\'Lexend\',Arial,sans-serif;font-size:14px;color:#111827;">{_esc(s.name)}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;font-family:\'Lexend\',Arial,sans-serif;font-size:13px;color:#6B7280;">{_esc(s.vendor_id.name or "")}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;font-family:\'Lexend\',Arial,sans-serif;font-size:13px;color:{date_color};font-weight:600;">{_esc(str(s.next_billing_date))}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;font-family:\'Lexend\',Arial,sans-serif;font-size:13px;color:#6B7280;">{_esc(str(cancel_by))}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;font-family:\'Lexend\',Arial,sans-serif;font-size:13px;color:#374151;text-align:right;">{_esc(amount)}</td>'
                f'</tr>'
            )
        return (
            f'<h3 style="font-family:\'Lexend\',\'Segoe UI\',Arial,sans-serif;font-size:16px;'
            f'font-weight:600;color:{dark};margin:24px 0 8px 0;">'
            f'🔄 {_esc(_("Renouvellements d\'abonnements à venir"))}</h3>'
            f'<table role="presentation" width="100%" style="border:1px solid #e5e7eb;border-radius:8px;'
            f'border-collapse:separate;overflow:hidden;margin-bottom:8px;">'
            f'<thead><tr>'
            f'<th style="padding:8px 10px;text-align:left;background:{accent};color:#fff;font-family:\'Lexend\',Arial,sans-serif;font-size:12px;">{_esc(_("Abonnement"))}</th>'
            f'<th style="padding:8px 10px;text-align:left;background:{accent};color:#fff;font-family:\'Lexend\',Arial,sans-serif;font-size:12px;">{_esc(_("Fournisseur"))}</th>'
            f'<th style="padding:8px 10px;text-align:left;background:{accent};color:#fff;font-family:\'Lexend\',Arial,sans-serif;font-size:12px;">{_esc(_("Renouvellement"))}</th>'
            f'<th style="padding:8px 10px;text-align:left;background:{accent};color:#fff;font-family:\'Lexend\',Arial,sans-serif;font-size:12px;">{_esc(_("À annuler avant"))}</th>'
            f'<th style="padding:8px 10px;text-align:right;background:{accent};color:#fff;font-family:\'Lexend\',Arial,sans-serif;font-size:12px;">{_esc(_("Montant"))}</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>'
        )

    def _generate_html(self, data, user):
        html = super()._generate_html(data, user)
        section = self._render_subscription_renewals(user)
        if section and "<!-- Divider -->" in html:
            html = html.replace("<!-- Divider -->", section + "<!-- Divider -->", 1)
        return html
