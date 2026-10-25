"""Le récapitulatif sous la mise en page commune, et une date de renouvellement qui avance.

* Le corps n'a plus de coquille à lui : le courriel est habillé par
  `bf_onboarding_base.bf_mail_layout`, comme les autres courriels maison.
* La date de renouvellement stockée restait au passé une fois l'échéance
  franchie : le récapitulatif et la tâche des relances la recalculent.
* Un abonnement géré mais pas refacturé n'a pas de marge : la colonne dit « — »
  au lieu de son coût cumulé en négatif.

Rien ne sort : `mail.mail.send` est neutralisé.
"""
from unittest.mock import patch

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged

from ..models import subscription_email_template as tpl


@tagged('post_install', '-at_install')
class TestRecapitulatif(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Rita Recap', 'login': 'rita_recap_essai', 'email': 'rita@recap.invalid',
            'groups_id': [Command.set([cls.env.ref('base.group_user').id])],
        })
        cls.vendor = cls.env['res.partner'].create({'name': 'Fournisseur essai', 'supplier_rank': 1})
        cls.client = cls.env['res.partner'].create({'name': 'Client géré essai', 'is_company': True})
        cls.digest = cls.env['subscription.digest'].create({
            'name': 'Récap essai', 'company_id': cls.company.id,
            'user_ids': [Command.set([cls.user.id])],
        })

    def _sub(self, **vals):
        base = {
            'name': 'Abonnement essai', 'vendor_id': self.vendor.id, 'company_id': self.company.id,
            'cycle': 'monthly', 'cycle_amount': 10.0, 'state': 'active',
            'start_date': fields.Date.today() - relativedelta(months=3),
        }
        base.update(vals)
        return self.env['subscription.subscription'].create(base)

    def test_courriel_habille_par_la_mise_en_page_commune(self):
        self._sub()
        with patch.object(type(self.env['mail.mail']), 'send', lambda *a, **k: None):
            self.digest._send_digest()
        mail = self.env['mail.mail'].search([('email_to', '=', self.user.email)], order='id desc', limit=1)
        self.assertTrue(mail)
        layout = self.env.ref(tpl.MAIL_LAYOUT, raise_if_not_found=False)
        self.assertTrue(layout, "bf_onboarding_base doit livrer la mise en page")
        # Le bandeau de la mise en page (logo par la route de marque), et plus la coquille
        # d'avant (logo par /web/image de la société, DOCTYPE à elle).
        self.assertIn('/brand/logo/%d' % self.company.id, mail.body_html)
        self.assertNotIn('/web/image/res.company/', mail.body_html)
        self.assertEqual(mail.body_html.count('<body'), 1)

    def test_corps_sans_coquille(self):
        html = self.digest._generate_html(self.digest._gather_data())
        self.assertNotIn('<body', html)
        self.assertNotIn('<html', html)
        # Chaque cellule porte sa taille, son interligne et sa couleur (mode quirks).
        self.assertIn('line-height:', html)

    def test_date_de_renouvellement_avance(self):
        sub = self._sub(start_date=fields.Date.today() - relativedelta(months=2, days=5))
        juste = sub.next_billing_date
        self.env.flush_all()
        self.assertGreaterEqual(juste, fields.Date.today())
        # La date stockée reste au passé quand le jour franchit l'échéance.
        self.env.cr.execute(
            "UPDATE subscription_subscription SET next_billing_date = %s WHERE id = %s",
            (juste - relativedelta(months=1), sub.id))
        sub.invalidate_recordset(['next_billing_date'])
        self.assertLess(sub.next_billing_date, fields.Date.today())
        self.digest._gather_data()
        self.assertEqual(sub.next_billing_date, juste)

    def test_relances_recalculent_la_date(self):
        sub = self._sub(cycle='annual', cycle_amount=120.0,
                        start_date=fields.Date.today() - relativedelta(years=1, days=-10))
        juste = sub.next_billing_date
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE subscription_subscription SET next_billing_date = %s WHERE id = %s",
            (juste - relativedelta(years=1), sub.id))
        sub.invalidate_recordset(['next_billing_date'])
        self.assertLess(sub.next_billing_date, fields.Date.today())
        self.env['subscription.subscription']._cron_renewal_alerts()
        self.assertEqual(sub.next_billing_date, juste)

    def test_marge_seulement_si_refacture(self):
        self._sub(managed_for_id=self.client.id, rebilled=False)
        payload = self.digest._get_report_payload()
        ligne = [m for m in payload['managed'] if m['client'] == self.client.name]
        self.assertEqual(ligne[0]['margin'], '—')
