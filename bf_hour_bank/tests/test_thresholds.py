from datetime import date, timedelta

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'bf_hour_bank')
class TestHourBankThresholds(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Threshold Co',
            'email': 'client@example.test',
        })
        cls.recipient = cls.env['res.partner'].create({
            'name': 'Recipient Default',
            'email': 'recipient@example.test',
        })
        cls.alt_recipient = cls.env['res.partner'].create({
            'name': 'Recipient Alert Only',
            'email': 'alert@example.test',
        })
        cls.project = cls.env['project.project'].create({
            'name': 'Test Threshold Project',
            'allow_timesheets': True,
            'partner_id': cls.partner.id,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Heures de service',
            'type': 'service',
            'invoice_policy': 'order',
            'list_price': 100.0,
        })
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.env.company.id)],
            limit=1,
        )

    def _new_bank(self, mode='disabled', budget=0.0, recipient_ids=None, alert_recipient_ids=None):
        return self.env['hour.bank.client'].create({
            'partner_id': self.partner.id,
            'project_ids': [(6, 0, [self.project.id])],
            'threshold_mode': mode,
            'threshold_budget_hours': budget,
            'recipient_ids': [(6, 0, (recipient_ids if recipient_ids is not None else [self.recipient.id]))],
            'alert_recipient_ids': [(6, 0, (alert_recipient_ids or []))],
            'notify_internal_followers': False,
        })

    def _add_timesheet(self, hours, days_ago=0):
        return self.env['account.analytic.line'].create({
            'name': 'TS %sh' % hours,
            'date': date.today() - timedelta(days=days_ago),
            'unit_amount': hours,
            'project_id': self.project.id,
            'employee_id': self.env.ref('hr.employee_admin').id
            if self.env.ref('hr.employee_admin', raise_if_not_found=False)
            else self.env['hr.employee'].create({'name': 'Test Emp'}).id,
        })

    def _post_invoice(self, qty, days_ago=0):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'invoice_date': date.today() - timedelta(days=days_ago),
            'journal_id': self.journal.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'quantity': qty,
                'price_unit': 100.0,
            })],
        })
        invoice.action_post()
        return invoice

    # ------------------------------------------------------------------
    # unbilled
    # ------------------------------------------------------------------

    def test_unbilled_fires_when_threshold_reached(self):
        bank = self._new_bank(mode='unbilled')
        self.env['hour.bank.threshold.line'].create({'bank_id': bank.id, 'value': 5.0})
        self._add_timesheet(6.0)
        fired = bank._check_thresholds()
        self.assertEqual(fired, 1)
        self.assertEqual(len(bank.threshold_event_ids), 1)
        event = bank.threshold_event_ids
        self.assertEqual(event.mode_snapshot, 'unbilled')
        self.assertAlmostEqual(event.value_snapshot, 5.0)
        self.assertAlmostEqual(event.measured_value, 6.0, places=2)

    def test_unbilled_no_double_fire_same_period(self):
        bank = self._new_bank(mode='unbilled')
        self.env['hour.bank.threshold.line'].create({'bank_id': bank.id, 'value': 5.0})
        self._add_timesheet(6.0)
        self.assertEqual(bank._check_thresholds(), 1)
        # Second call without state change → no fire
        self.assertEqual(bank._check_thresholds(), 0)
        self.assertEqual(len(bank.threshold_event_ids), 1)

    def test_unbilled_rearms_after_new_posted_invoice(self):
        bank = self._new_bank(mode='unbilled')
        self.env['hour.bank.threshold.line'].create({'bank_id': bank.id, 'value': 5.0})
        self._add_timesheet(6.0, days_ago=2)
        bank._check_thresholds()
        # Post invoice (1 day ago) covering the unbilled hours → resets unbilled to 0
        self._post_invoice(6.0, days_ago=1)
        # New timesheet today, after the invoice → counts as unbilled again
        self._add_timesheet(6.0, days_ago=0)
        self.assertEqual(bank._check_thresholds(), 1)
        self.assertEqual(len(bank.threshold_event_ids), 2)

    # ------------------------------------------------------------------
    # budget_pct
    # ------------------------------------------------------------------

    def test_budget_pct_fires_only_crossed_thresholds(self):
        bank = self._new_bank(mode='budget_pct', budget=20.0)
        for v in (25.0, 50.0, 75.0, 100.0):
            self.env['hour.bank.threshold.line'].create({'bank_id': bank.id, 'value': v})
        self._add_timesheet(5.0)  # 25%
        fired = bank._check_thresholds()
        self.assertEqual(fired, 1)
        self.assertAlmostEqual(bank.threshold_event_ids.value_snapshot, 25.0)
        # Add 6 more hours (total 11h = 55%) → fire 50 only (25 already fired)
        self._add_timesheet(6.0)
        self.assertEqual(bank._check_thresholds(), 1)
        latest = bank.threshold_event_ids.sorted(lambda e: e.id, reverse=True)[:1]
        self.assertAlmostEqual(latest.value_snapshot, 50.0)

    def test_budget_pct_rearms_when_budget_changes(self):
        bank = self._new_bank(mode='budget_pct', budget=20.0)
        line = self.env['hour.bank.threshold.line'].create({'bank_id': bank.id, 'value': 25.0})
        self._add_timesheet(6.0)  # 30% → fire 25
        bank._check_thresholds()
        self.assertTrue(line.last_fired_period_key)
        first_key = line.last_fired_period_key
        # Change budget → key changes → re-armed
        bank.threshold_budget_hours = 40.0
        # 6h / 40h = 15% — below 25%, no fire even though re-armed
        self.assertEqual(bank._check_thresholds(), 0)
        # Add more hours to cross 25% of new budget (10h = 25%)
        self._add_timesheet(4.0)
        self.assertEqual(bank._check_thresholds(), 1)
        self.assertNotEqual(line.last_fired_period_key, first_key)

    # ------------------------------------------------------------------
    # balance_floor
    # ------------------------------------------------------------------

    def test_balance_floor_fires_when_balance_drops_below(self):
        bank = self._new_bank(mode='balance_floor')
        self.env['hour.bank.threshold.line'].create({'bank_id': bank.id, 'value': 5.0})
        self._post_invoice(10.0)  # +10h credit → balance 10
        self.assertEqual(bank._check_thresholds(), 0)  # 10 > 5, no fire
        self._add_timesheet(6.0)  # balance 4 < 5 → fire
        self.assertEqual(bank._check_thresholds(), 1)

    def test_balance_floor_rearms_with_hysteresis(self):
        bank = self._new_bank(mode='balance_floor')
        line = self.env['hour.bank.threshold.line'].create({'bank_id': bank.id, 'value': 5.0})
        self._post_invoice(10.0)
        self._add_timesheet(6.0)  # balance 4
        bank._check_thresholds()
        self.assertEqual(len(bank.threshold_event_ids), 1)
        # Add credit bringing balance to 6.0 (above 5 + 0.5 hysteresis) → silent re-arm
        self.env['hour.bank.adjustment'].create({
            'hour_bank_id': bank.id,
            'date': date.today(),
            'hours': 2.0,
            'description': 'recharge',
        })
        bank.invalidate_recordset(['current_balance'])
        self.assertEqual(bank._check_thresholds(), 0)
        # last_fired_period_key should be reset to empty (armed)
        self.assertFalse(line.last_fired_period_key)
        # Drop balance back below 5 → fire again
        self._add_timesheet(3.0)  # balance 6 - 3 = 3
        self.assertEqual(bank._check_thresholds(), 1)
        self.assertEqual(len(bank.threshold_event_ids), 2)

    def test_balance_floor_accepts_negative_value_for_postpaid(self):
        """Compte postpayé : le palier se pose sous zéro et se lit en dette."""
        bank = self._new_bank(mode='balance_floor')
        line = self.env['hour.bank.threshold.line'].create({
            'bank_id': bank.id, 'value': -12.0,
        })
        self.assertEqual(line.name, "Dette de 12.0h")

    def test_balance_floor_rejects_zero_value(self):
        bank = self._new_bank(mode='balance_floor')
        with self.assertRaises(ValidationError):
            self.env['hour.bank.threshold.line'].create({
                'bank_id': bank.id, 'value': 0.0,
            })

    def test_balance_floor_negative_fires_and_rearms_on_invoice(self):
        """Le solde plonge sous -12h, alerte; la facture le remonte, réarmement."""
        bank = self._new_bank(mode='balance_floor')
        line = self.env['hour.bank.threshold.line'].create({
            'bank_id': bank.id, 'value': -12.0,
        })
        self._add_timesheet(10.0, days_ago=2)  # solde -10, au-dessus du palier
        self.assertEqual(bank._check_thresholds(), 0)
        self._add_timesheet(3.0, days_ago=2)  # solde -13 → déclenche
        bank.invalidate_recordset(['current_balance'])
        self.assertEqual(bank._check_thresholds(), 1)
        event = bank.threshold_event_ids
        self.assertEqual(event.mode_snapshot, 'balance_floor')
        self.assertAlmostEqual(event.measured_value, -13.0, places=2)
        # La facture ramène le solde à -1 (au-dessus de -12 + 0.5) → réarme
        self._post_invoice(12.0, days_ago=1)
        bank.invalidate_recordset(['current_balance'])
        self.assertEqual(bank._check_thresholds(), 0)
        self.assertFalse(line.last_fired_period_key)
        # Les heures repartent et repassent sous le palier → alerte de nouveau
        self._add_timesheet(12.0)
        bank.invalidate_recordset(['current_balance'])
        self.assertEqual(bank._check_thresholds(), 1)
        self.assertEqual(len(bank.threshold_event_ids), 2)

    # ------------------------------------------------------------------
    # Recipients & attachment
    # ------------------------------------------------------------------

    def test_alert_recipients_fallback_to_recipient_ids_when_empty(self):
        bank = self._new_bank(mode='unbilled', recipient_ids=[self.recipient.id])
        self.env['hour.bank.threshold.line'].create({'bank_id': bank.id, 'value': 1.0})
        self._add_timesheet(2.0)
        bank._check_thresholds()
        event = bank.threshold_event_ids
        self.assertIn(self.recipient, event.recipient_ids)
        self.assertNotIn(self.alt_recipient, event.recipient_ids)

    def test_alert_recipients_override_recipient_ids_when_set(self):
        bank = self._new_bank(
            mode='unbilled',
            recipient_ids=[self.recipient.id],
            alert_recipient_ids=[self.alt_recipient.id],
        )
        self.env['hour.bank.threshold.line'].create({'bank_id': bank.id, 'value': 1.0})
        self._add_timesheet(2.0)
        bank._check_thresholds()
        event = bank.threshold_event_ids
        self.assertIn(self.alt_recipient, event.recipient_ids)
        self.assertNotIn(self.recipient, event.recipient_ids)

    def test_event_has_attachment_xlsx(self):
        bank = self._new_bank(mode='unbilled')
        self.env['hour.bank.threshold.line'].create({'bank_id': bank.id, 'value': 1.0})
        self._add_timesheet(2.0)
        bank._check_thresholds()
        event = bank.threshold_event_ids
        self.assertTrue(event.attachment_id, "event should have xlsx attachment")
        self.assertEqual(
            event.attachment_id.mimetype,
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
