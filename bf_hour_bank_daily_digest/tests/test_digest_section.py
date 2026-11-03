from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'bf_hour_bank_daily_digest')
class TestHourBankDigestSection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Une société à nous : une base qui porte de vraies banques d'heures
        # ferait paraître la section là où ces essais attendent le silence (vu
        # sur un banc monté depuis un clone de production).
        cls.company = cls.env['res.company'].create({'name': "Banque d'heures - essais"})
        cls.user = cls.env['res.users'].create({
            'name': 'Digest Reader',
            'login': 'digest.reader@example.test',
            'email': 'digest.reader@example.test',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
        })
        cls.digest = cls.env['daily.digest.config'].create({
            'name': 'Test digest',
            'user_ids': [(6, 0, [cls.user.id])],
            # No outbound call to the weather API from a test.
            'include_weather': False,
            'include_quote': False,
            'company_id': cls.company.id,
        })

    # -- helpers ------------------------------------------------------------

    def _bank(self, name, mode='balance_floor', line_value=5.2, line_active=True):
        partner = self.env['res.partner'].create({'name': name})
        bank = self.env['hour.bank.client'].create({
            'partner_id': partner.id,
            'company_id': self.company.id,
            'threshold_mode': mode,
        })
        if line_value is not None and mode != 'disabled':
            self.env['hour.bank.threshold.line'].create({
                'bank_id': bank.id,
                'value': line_value,
                'active': line_active,
            })
        return bank

    def _event(self, bank, days_ago=0, mode='balance_floor', value=5.2, measured=-6.78):
        return self.env['hour.bank.threshold.event'].create({
            'bank_id': bank.id,
            'mode_snapshot': mode,
            'value_snapshot': value,
            'measured_value': measured,
            'fired_date': fields.Datetime.now() - timedelta(days=days_ago),
        })

    def _section(self):
        return self.digest._render_hour_bank_thresholds(self.user)

    # -- tests --------------------------------------------------------------

    def test_quiet_when_nothing_to_say(self):
        """No event and no unwatched bank renders nothing at all."""
        self._bank('Watched Co')
        self.assertEqual(self._section(), "")

    def test_toggle_off_silences_the_section(self):
        bank = self._bank('Watched Co')
        self._event(bank)
        self.digest.include_hour_bank_thresholds = False
        self.assertEqual(self._section(), "")

    def test_fired_event_is_listed_with_snapshot_label(self):
        bank = self._bank('Alerted Co')
        self._event(bank)
        html = self._section()
        self.assertIn('Alerted Co', html)
        # Label comes from the event snapshot, not from the live line.
        self.assertIn('Solde sous 5,2 h', html)
        self.assertIn('-6,78 h', html)

    def test_negative_floor_reads_as_a_debt(self):
        bank = self._bank('Postpaid Co', line_value=-12)
        self._event(bank, value=-12, measured=-12.41)
        html = self._section()
        self.assertIn('Dette de 12 h', html)
        self.assertIn('-12,41 h', html)

    def test_budget_pct_event_is_a_percentage(self):
        bank = self._bank('Budget Co', mode='budget_pct', line_value=80)
        self._event(bank, mode='budget_pct', value=80, measured=83.5)
        html = self._section()
        self.assertIn('80 % du budget', html)
        self.assertIn('83,5 %', html)

    def test_event_outside_horizon_is_dropped(self):
        bank = self._bank('Old Co')
        self._event(bank, days_ago=30)
        self.digest.hour_bank_threshold_days = 7
        self.assertEqual(self._section(), "")

    def test_horizon_is_configurable(self):
        bank = self._bank('Old Co')
        self._event(bank, days_ago=30)
        self.digest.hour_bank_threshold_days = 60
        self.assertIn('Old Co', self._section())

    def test_disabled_bank_is_flagged(self):
        self._bank('Unwatched Co', mode='disabled', line_value=None)
        html = self._section()
        self.assertIn('Unwatched Co', html)
        # markupsafe escapes the apostrophe: assert on the part that survives escaping.
        self.assertIn('aucun palier ne surveille', html)

    def test_mode_set_but_no_active_line_is_also_flagged(self):
        """A mode with no active line never fires — it is not 'watched'."""
        self._bank('Half Set Co', line_active=False)
        html = self._section()
        self.assertIn('Half Set Co', html)
        # markupsafe escapes the apostrophe: assert on the part that survives escaping.
        self.assertIn('aucun palier ne surveille', html)

    def test_unset_flag_can_be_turned_off_alone(self):
        self._bank('Unwatched Co', mode='disabled', line_value=None)
        self.digest.hour_bank_flag_unset = False
        self.assertEqual(self._section(), "")

    def test_other_company_banks_are_not_reported(self):
        other = self.env['res.company'].create({'name': 'Other Co Inc.'})
        partner = self.env['res.partner'].create({'name': 'Foreign Co'})
        self.env['hour.bank.client'].create({
            'partner_id': partner.id,
            'company_id': other.id,
            'threshold_mode': 'disabled',
        })
        self.assertEqual(self._section(), "")

    def test_truncation_is_announced(self):
        bank = self._bank('Noisy Co')
        for i in range(25):
            self._event(bank, value=float(i + 1))
        html = self._section()
        self.assertIn('et 5 autre(s) sur la période', html)

    def test_section_is_spliced_as_its_own_row_before_the_divider(self):
        bank = self._bank('Alerted Co')
        self._event(bank)
        data = self.digest._gather_digest_data(self.user)
        html = self.digest._generate_html(data, self.user)
        self.assertIn('Alerted Co', html)
        marker = html.index('<!-- Divider -->')
        block_start = html.index('Alerted Co')
        self.assertLess(block_start, marker, "section must sit before the divider")
        # Foster parenting: a bare section between two <tr> is hoisted out of
        # the wrapper table. The block must carry its own row.
        row_start = html.rindex('<tr>', 0, block_start)
        self.assertNotIn('</tr>', html[row_start:block_start])

    # -- résorbé --------------------------------------------------------------

    def _fired(self, bank, key, measured=-12.37):
        """An event tied to its line, the way bf_hour_bank writes a firing."""
        line = bank.threshold_line_ids[:1]
        line.last_fired_period_key = key
        ev = self._event(bank, value=line.value, measured=measured)
        ev.write({'threshold_line_id': line.id, 'period_key': key})
        return line, ev

    def test_rearmed_balance_alert_is_marked_resolved(self):
        """The balance climbed back: the line's key was cleared on re-arm."""
        bank = self._bank('Postpaid Co', line_value=-12)
        line, _ev = self._fired(bank, '2026-09-18 07:14:10')
        line.last_fired_period_key = ''
        html = self._section()
        self.assertIn('Postpaid Co', html)
        self.assertIn('résorbé', html)
        # Fired today, yet no longer a call to action: not in red.
        self.assertNotIn('#dc3545', html)

    def test_balance_alert_still_open_is_not_resolved(self):
        bank = self._bank('Postpaid Co', line_value=-12)
        self._fired(bank, '2026-09-18 07:14:10')
        html = self._section()
        self.assertNotIn('résorbé', html)
        self.assertIn('#dc3545', html)

    def test_older_firing_is_resolved_by_a_later_one(self):
        """Fired, re-armed, fired again: only the latest crossing is open."""
        bank = self._bank('Postpaid Co', line_value=-12)
        _line, old = self._fired(bank, '2026-08-15 03:42:41')
        _line, new = self._fired(bank, '2026-09-18 07:14:10')
        self.assertTrue(self.digest._hour_bank_event_resolved(old))
        self.assertFalse(self.digest._hour_bank_event_resolved(new))

    def test_event_without_line_is_left_as_is(self):
        """Line deleted: nothing proves the balance came back."""
        bank = self._bank('Postpaid Co', line_value=-12)
        _line, ev = self._fired(bank, '2026-09-18 07:14:10')
        ev.threshold_line_id = False
        self.assertFalse(self.digest._hour_bank_event_resolved(ev))

    def test_budget_pct_is_never_marked_resolved(self):
        bank = self._bank('Budget Co', mode='budget_pct', line_value=80)
        line = bank.threshold_line_ids
        ev = self._event(bank, mode='budget_pct', value=80, measured=83.5)
        ev.write({'threshold_line_id': line.id, 'period_key': '80.0000'})
        line.last_fired_period_key = ''
        self.assertFalse(self.digest._hour_bank_event_resolved(ev))
