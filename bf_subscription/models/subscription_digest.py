import base64
import logging
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import subscription_email_template as tpl

_logger = logging.getLogger(__name__)


class SubscriptionDigest(models.Model):
    _name = 'subscription.digest'
    _description = "Subscription digest"
    _order = 'name'

    name = fields.Char(required=True, default=lambda self: _("Subscription digest"))
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    user_ids = fields.Many2many(
        'res.users', 'subscription_digest_user_rel', 'digest_id', 'user_id',
        string="Recipients",
    )
    auto_send = fields.Boolean(
        string="Automatic sending", default=True,
        help="If unchecked, the setup is used only to generate the PDF "
             "report on demand (no scheduled sending).",
    )
    frequency = fields.Selection([
        ('weekly', "Weekly"),
        ('monthly', "Monthly"),
    ], default='weekly', required=True)
    renewal_horizon_days = fields.Integer(
        string="Renewal horizon (days)", default=30,
    )
    include_spend_summary = fields.Boolean(string="Include the spend summary", default=True)
    include_renewals = fields.Boolean(string="Include renewals", default=True)
    include_dormant = fields.Boolean(string="Include dormant subscriptions", default=True)
    include_managed_clients = fields.Boolean(string="Include the cost per "
                                                    "managed client", default=True)
    last_sent = fields.Datetime(readonly=True)

    @api.constrains('auto_send', 'user_ids')
    def _check_recipients(self):
        for rec in self:
            if rec.auto_send and not rec.user_ids:
                raise UserError(_(
                    "Enter at least one recipient, or turn off automatic "
                    "sending (the \"%s\" setup will then be used only for "
                    "the on-demand report)."
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
        # Envoyer aux destinataires est un geste de gestionnaire : sans ce contrôle, un
        # lecteur faisait partir le courriel avant de buter sur l'écriture de last_sent.
        self.check_access('write')
        if not self.user_ids:
            raise UserError(_("No recipient configured."))
        self._send_digest()
        self.last_sent = fields.Datetime.now()
        return self._notify(_("Digest sent to %s.") % ', '.join(self.user_ids.mapped('name')))

    def action_send_test(self):
        self.ensure_one()
        # Le récapitulatif se lit en sudo : vérifier d'abord qu'on a le droit de le voir
        # (règle multi-société), pas seulement de l'appeler.
        self.check_access('read')
        user = self.env.user
        if not user.email:
            raise UserError(_("Your user has no email address."))
        self._send_digest(test_user=user)
        return self._notify(_("Test digest sent to %s.") % user.email)

    def _notify(self, message):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _("Digest"), 'message': message, 'type': 'success', 'sticky': False},
        }

    # ------------------------------------------------------------------
    # On-demand PDF report
    # ------------------------------------------------------------------

    def action_print_report(self):
        """On-demand: render and download the branded PDF report."""
        self.ensure_one()
        self.check_access('read')
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

        # Un rendu par LANGUE de destinataire : le sujet, le corps et le PDF sont lus par chacun dans la
        # sienne, et la tâche planifiée n'en a pas (elle écrirait pour tous dans la langue d'OdooBot).
        # Le PDF est produit une fois par langue, pas une fois par personne.
        par_langue = {}
        for user in recipients:
            if user.email:
                lang = user.lang or self.company_id.partner_id.lang or "en_US"
                par_langue.setdefault(lang, []).append(user)
        for lang, users in par_langue.items():
            lu = self.with_context(lang=lang)
            # Le jour de l'usager, comme l'en-tête du corps : fields.Date.today() lit le jour UTC,
            # et le sujet annonçait la veille ou le lendemain du corps.
            today = fields.Date.context_today(lu)
            attachment_ids = []
            try:
                pdf = lu._get_pdf_binary()
                att = self.env['ir.attachment'].sudo().create({
                    'name': lu.env._("Subscription_report_%s.pdf") % today.isoformat(),
                    'type': 'binary',
                    'datas': base64.b64encode(pdf),
                    'mimetype': 'application/pdf',
                    'res_model': self._name,
                    'res_id': self.id,
                })
                attachment_ids = [att.id]
            except Exception:
                _logger.exception("subscription digest %s: PDF generation failed", self.name)
            subject = lu.env._("Subscription digest: %s") % today.strftime('%Y-%m-%d')
            body = tpl.dress(lu.env, lu._generate_html(data), self.company_id, record=self, lang=lang)
            for user in users:
                mail = self.env['mail.mail'].sudo().create({
                    'subject': subject,
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
        # La date stockée reste au passé une fois l'échéance franchie : sans ce
        # recalcul, un renouvellement passé sortait de l'horizon pour de bon.
        active._refresh_next_billing_date()

        # Spend
        monthly = sum(active.mapped('monthly_equivalent'))
        annual = monthly * 12.0
        # Ce que la société paie pour elle-même, sans les abonnements tenus pour un client.
        own = active.filtered(lambda s: not s.managed_for_id)
        own_monthly = sum(own.mapped('monthly_equivalent'))

        # Renewals within horizon
        today = fields.Date.context_today(self)
        from dateutil.relativedelta import relativedelta
        horizon = today + relativedelta(days=self.renewal_horizon_days or 30)
        renewals = active.filtered(
            lambda s: s.next_billing_date and today <= s.next_billing_date <= horizon
        ).sorted('next_billing_date')

        dormant = active.filtered('is_dormant').sorted('name') if self.include_dormant else Sub.browse()

        # Managed clients aggregation
        # La marge ne compte que les abonnements refacturés : un abonnement géré
        # mais pas refacturé n'a pas de marge, et son coût cumulé s'affichait en
        # marge négative.
        managed = defaultdict(lambda: {'count': 0, 'monthly': 0.0, 'rebilled': 0.0,
                                       'margin': 0.0, 'rebilled_count': 0})
        if self.include_managed_clients:
            for s in active.filtered('managed_for_id'):
                m = managed[s.managed_for_id]
                m['count'] += 1
                m['monthly'] += s.monthly_equivalent
                if s.rebilled:
                    m['rebilled_count'] += 1
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
            'own_monthly': own_monthly, 'own_annual': own_monthly * 12.0,
            'has_managed': len(own) != len(active),
            'renewals': renewals, 'dormant': dormant, 'managed': managed,
            'currency': self.company_id.currency_id,
            'has_content': has_content,
        }

    def _fmt(self, amount, currency):
        return "%s %s" % ('{:,.2f}'.format(amount).replace(',', ' '), currency.name or '')

    def _spend_rows(self, data):
        """(libellé, montant) du sommaire : le total, et la part de la société seule
        dès qu'un abonnement est tenu pour un client."""
        cur = data['currency']
        if not data['has_managed']:
            return [(_("Monthly spend"), self._fmt(data['monthly'], cur)),
                    (_("Annual spend"), self._fmt(data['annual'], cur))]
        name = self.company_id.name
        return [
            (_("Monthly spend, all included"), self._fmt(data['monthly'], cur)),
            (_("Annual spend, all included"), self._fmt(data['annual'], cur)),
            (_("Monthly spend, %s only") % name, self._fmt(data['own_monthly'], cur)),
            (_("Annual spend, %s only") % name, self._fmt(data['own_annual'], cur)),
        ]

    def _fmt_margin(self, m, currency):
        return self._fmt(m['margin'], currency) if m['rebilled_count'] else "—"

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
                'margin': self._fmt_margin(m, cur),
            })
        return {
            'generated_on': fields.Date.context_today(self),
            'active_count': data['active_count'],
            'monthly': self._fmt(data['monthly'], cur),
            'annual': self._fmt(data['annual'], cur),
            'spend': self._spend_rows(data),
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
        parts = [
            tpl.heading(_("Subscription digest"), company),
            tpl.lead(fields.Date.context_today(self).isoformat(), company),
        ]

        if self.include_spend_summary:
            parts.append(tpl.section_title(_("Summary"), company))
            parts.append(tpl.info_card(
                [(_("Active subscriptions"), str(data['active_count']))] + self._spend_rows(data),
                company))

        if self.include_renewals:
            parts.append(tpl.section_title(_("Upcoming renewals (%d days)") % (self.renewal_horizon_days or 30), company))
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
                    [_("Subscription"), _("Vendor"), _("Renewal"), _("Cancel "
                                                                               "before"), _("Amount")],
                    rows, company))
            else:
                parts.append(tpl.empty_note(_("No renewal within the horizon."), company))

        if self.include_dormant:
            parts.append(tpl.section_title(_("Dormant subscriptions"), company))
            if data['dormant']:
                # Hors de la compréhension : avant Python 3.12, _() y perd la langue (piège 41).
                aucune = _("None")
                rows = [[esc(s.name), esc(s.vendor_id.name or ''),
                         esc(str(s.last_vendor_bill_date or aucune))]
                        for s in data['dormant']]
                parts.append(tpl.data_table(
                    [_("Subscription"), _("Vendor"), _("Last bill")], rows, company))
            else:
                parts.append(tpl.empty_note(_("No dormant subscription."), company))

        if self.include_managed_clients and data['managed']:
            parts.append(tpl.section_title(_("Cost per managed client"), company))
            rows = []
            for partner, m in sorted(data['managed'].items(), key=lambda kv: kv[1]['monthly'], reverse=True):
                rows.append([
                    esc(partner.name),
                    esc(str(m['count'])),
                    esc(self._fmt(m['monthly'], cur)),
                    esc(self._fmt_margin(m, cur)),
                ])
            parts.append(tpl.data_table(
                [_("Client"), _("# Subscriptions"), _("Monthly cost"), _("Margin "
                                                                          "(lifetime)")], rows, company))

        parts.append(tpl.empty_note(_("Automatic subscription digest."), company))
        return "".join(parts)
