import base64
import logging
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import subscription_email_template as tpl

_logger = logging.getLogger(__name__)


class SubscriptionDigest(models.Model):
    _name = 'subscription.digest'
    _description = "Récapitulatif des abonnements"
    _order = 'name'

    name = fields.Char(required=True, default="Récapitulatif des abonnements")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    user_ids = fields.Many2many(
        'res.users', 'subscription_digest_user_rel', 'digest_id', 'user_id',
        string="Destinataires",
    )
    auto_send = fields.Boolean(
        string="Envoi automatique", default=True,
        help="Si décoché, la configuration sert uniquement à générer le rapport "
             "PDF sur demande (aucun envoi planifié).",
    )
    frequency = fields.Selection([
        ('weekly', 'Hebdomadaire'),
        ('monthly', 'Mensuel'),
    ], default='weekly', required=True)
    renewal_horizon_days = fields.Integer(
        string="Horizon renouvellements (jours)", default=30,
    )
    include_spend_summary = fields.Boolean(string="Inclure le sommaire de dépense", default=True)
    include_renewals = fields.Boolean(string="Inclure les renouvellements", default=True)
    include_dormant = fields.Boolean(string="Inclure les abonnements dormants", default=True)
    include_managed_clients = fields.Boolean(string="Inclure le coût par client géré", default=True)
    last_sent = fields.Datetime(readonly=True)

    @api.constrains('auto_send', 'user_ids')
    def _check_recipients(self):
        for rec in self:
            if rec.auto_send and not rec.user_ids:
                raise UserError(_(
                    "Renseignez au moins un destinataire, ou désactivez l'envoi automatique "
                    "(la configuration « %s » servira alors uniquement au rapport sur demande)."
                ) % rec.name)

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def _cron_send_digests(self):
        today = fields.Date.today()
        now = fields.Datetime.now()
        for digest in self.search([('active', '=', True), ('auto_send', '=', True)]):
            send = False
            if not digest.last_sent:
                send = True
            else:
                last = digest.last_sent.date()
                if digest.frequency == 'weekly':
                    send = (today - last).days >= 7
                elif digest.frequency == 'monthly':
                    send = (today - last).days >= 30 or today.month != last.month
            if send:
                try:
                    digest._send_digest()
                    digest.last_sent = now
                except Exception:
                    _logger.exception("subscription digest %s failed", digest.name)

    # ------------------------------------------------------------------
    # Manual actions
    # ------------------------------------------------------------------

    def action_send_now(self):
        self.ensure_one()
        if not self.user_ids:
            raise UserError(_("Aucun destinataire configuré."))
        self._send_digest()
        self.last_sent = fields.Datetime.now()
        return self._notify(_("Récapitulatif envoyé à %s.") % ', '.join(self.user_ids.mapped('name')))

    def action_send_test(self):
        self.ensure_one()
        user = self.env.user
        if not user.email:
            raise UserError(_("Votre utilisateur n'a pas d'adresse courriel."))
        self._send_digest(test_user=user)
        return self._notify(_("Récapitulatif test envoyé à %s.") % user.email)

    def _notify(self, message):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _("Récapitulatif"), 'message': message, 'type': 'success', 'sticky': False},
        }

    # ------------------------------------------------------------------
    # On-demand PDF report
    # ------------------------------------------------------------------

    def action_print_report(self):
        """On-demand: render and download the branded PDF report."""
        self.ensure_one()
        return self.env.ref('bf_subscription.action_report_subscription').report_action(self)

    def _get_pdf_binary(self):
        self.ensure_one()
        pdf_content, _ct = self.env['ir.actions.report']._render_qweb_pdf(
            'bf_subscription.action_report_subscription', self.ids,
        )
        return pdf_content

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    def _send_digest(self, test_user=None):
        self.ensure_one()
        recipients = test_user or self.user_ids
        data = self._gather_data()
        if not data['has_content']:
            _logger.info("subscription digest %s: nothing to report", self.name)
            if not test_user:
                return
        sender = self.company_id.email or self.env.user.email_formatted

        # Generate the branded PDF once and attach it to every email.
        attachment_ids = []
        try:
            pdf = self._get_pdf_binary()
            att = self.env['ir.attachment'].sudo().create({
                'name': "Rapport_abonnements_%s.pdf" % fields.Date.today().isoformat(),
                'type': 'binary',
                'datas': base64.b64encode(pdf),
                'mimetype': 'application/pdf',
                'res_model': self._name,
                'res_id': self.id,
            })
            attachment_ids = [att.id]
        except Exception:
            _logger.exception("subscription digest %s: PDF generation failed", self.name)

        for user in recipients:
            if not user.email:
                continue
            body = self._generate_html(data)
            mail = self.env['mail.mail'].sudo().create({
                'subject': _("Récapitulatif des abonnements — %s") % fields.Date.today().strftime('%Y-%m-%d'),
                'email_from': sender,
                'email_to': user.email,
                'body_html': body,
                'attachment_ids': [(6, 0, attachment_ids)] if attachment_ids else False,
            })
            mail.send()

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _gather_data(self):
        self.ensure_one()
        Sub = self.env['subscription.subscription'].sudo()
        company_domain = [('company_id', '=', self.company_id.id)]
        active = Sub.search(company_domain + [('state', '=', 'active')])

        # Spend
        monthly = sum(active.mapped('monthly_equivalent'))
        annual = monthly * 12.0

        # Renewals within horizon
        today = fields.Date.context_today(self)
        from dateutil.relativedelta import relativedelta
        horizon = today + relativedelta(days=self.renewal_horizon_days or 30)
        renewals = active.filtered(
            lambda s: s.next_billing_date and today <= s.next_billing_date <= horizon
        ).sorted('next_billing_date')

        dormant = active.filtered('is_dormant').sorted('name') if self.include_dormant else Sub.browse()

        # Managed clients aggregation
        managed = defaultdict(lambda: {'count': 0, 'monthly': 0.0, 'rebilled': 0.0, 'margin': 0.0})
        if self.include_managed_clients:
            for s in active.filtered('managed_for_id'):
                m = managed[s.managed_for_id]
                m['count'] += 1
                m['monthly'] += s.monthly_equivalent
                m['rebilled'] += s.total_rebilled_lifetime
                m['margin'] += s.rebill_margin

        has_content = bool(
            (self.include_spend_summary and active)
            or (self.include_renewals and renewals)
            or (self.include_dormant and dormant)
            or (self.include_managed_clients and managed)
        )
        return {
            'active_count': len(active),
            'monthly': monthly, 'annual': annual,
            'renewals': renewals, 'dormant': dormant, 'managed': managed,
            'currency': self.company_id.currency_id,
            'has_content': has_content,
        }

    def _fmt(self, amount, currency):
        return "%s %s" % ('{:,.2f}'.format(amount).replace(',', ' '), currency.name or '')

    def _get_report_payload(self):
        """Flat, QWeb-friendly payload for the PDF report template."""
        self.ensure_one()
        from dateutil.relativedelta import relativedelta
        data = self._gather_data()
        cur = data['currency']
        renewals = []
        for s in data['renewals']:
            renewals.append({
                'name': s.name,
                'vendor': s.vendor_id.name or '',
                'next_date': s.next_billing_date,
                'cancel_by': s.next_billing_date - relativedelta(days=s.notice_period_days or 0),
                'amount': self._fmt(s.cycle_amount, cur),
            })
        dormant = [{
            'name': s.name, 'vendor': s.vendor_id.name or '',
            'last_bill': s.last_vendor_bill_date or False,
        } for s in data['dormant']]
        managed = []
        for partner, m in sorted(data['managed'].items(), key=lambda kv: kv[1]['monthly'], reverse=True):
            managed.append({
                'client': partner.name,
                'count': m['count'],
                'monthly': self._fmt(m['monthly'], cur),
                'margin': self._fmt(m['margin'], cur),
            })
        return {
            'generated_on': fields.Date.context_today(self),
            'active_count': data['active_count'],
            'monthly': self._fmt(data['monthly'], cur),
            'annual': self._fmt(data['annual'], cur),
            'renewals': renewals,
            'dormant': dormant,
            'managed': managed,
            'horizon_days': self.renewal_horizon_days or 30,
        }

    def _generate_html(self, data):
        self.ensure_one()
        from markupsafe import escape as esc
        company = self.company_id
        cur = data['currency']
        parts = []

        if self.include_spend_summary:
            parts.append(tpl.section_title(_("Sommaire"), company))
            parts.append(tpl.info_card([
                (_("Abonnements actifs"), str(data['active_count'])),
                (_("Dépense mensualisée"), self._fmt(data['monthly'], cur)),
                (_("Dépense annualisée"), self._fmt(data['annual'], cur)),
            ], company))

        if self.include_renewals:
            parts.append(tpl.section_title(_("Renouvellements à venir (%d j)") % (self.renewal_horizon_days or 30), company))
            if data['renewals']:
                rows = []
                from dateutil.relativedelta import relativedelta
                for s in data['renewals']:
                    cancel_by = s.next_billing_date - relativedelta(days=s.notice_period_days or 0)
                    rows.append([
                        esc(s.name),
                        esc(s.vendor_id.name or ''),
                        esc(str(s.next_billing_date)),
                        esc(str(cancel_by)),
                        esc(self._fmt(s.cycle_amount, cur)),
                    ])
                parts.append(tpl.data_table(
                    [_("Abonnement"), _("Fournisseur"), _("Renouvellement"), _("À annuler avant"), _("Montant")],
                    rows, company))
            else:
                parts.append(tpl.empty_note(_("Aucun renouvellement dans l'horizon.")))

        if self.include_dormant:
            parts.append(tpl.section_title(_("Abonnements dormants"), company))
            if data['dormant']:
                rows = [[esc(s.name), esc(s.vendor_id.name or ''),
                         esc(str(s.last_vendor_bill_date or _("aucune")))]
                        for s in data['dormant']]
                parts.append(tpl.data_table(
                    [_("Abonnement"), _("Fournisseur"), _("Dernière facture")], rows, company))
            else:
                parts.append(tpl.empty_note(_("Aucun abonnement dormant.")))

        if self.include_managed_clients and data['managed']:
            parts.append(tpl.section_title(_("Coût par client géré"), company))
            rows = []
            for partner, m in sorted(data['managed'].items(), key=lambda kv: kv[1]['monthly'], reverse=True):
                rows.append([
                    esc(partner.name),
                    esc(str(m['count'])),
                    esc(self._fmt(m['monthly'], cur)),
                    esc(self._fmt(m['margin'], cur)),
                ])
            parts.append(tpl.data_table(
                [_("Client"), _("# Abonnements"), _("Coût mensualisé"), _("Marge (vie)")], rows, company))

        return tpl.wrapper(_("Récapitulatif des abonnements"), "".join(parts), company)
