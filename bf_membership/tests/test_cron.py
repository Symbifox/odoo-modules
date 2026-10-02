from freezegun import freeze_time

from odoo.tests import tagged

from .common import MembershipCase


@tagged("post_install", "-at_install", "bf_membership")
class TestDailyPass(MembershipCase):
    """La passe quotidienne : échoir, renouveler, rappeler.

    Les rappels se comptent en `mail.mail` créés pour le membre, sans jamais
    les envoyer (aucun serveur de courriel dans un essai).
    """

    def _mails_to(self, partner):
        return self.env["mail.mail"].search([("recipient_ids", "in", partner.ids)])

    def setUp(self):
        super().setUp()
        self.company.write({
            "membership_auto_renewal": False,
            "membership_reminders": False,
            "membership_reminder_first_days": 30,
            "membership_reminder_second_days": 7,
        })

    def test_expires_after_end(self):
        membership = self._membership(self.alice, payment_state="paid",
                                      date_start=self._day(years=-1), date_end=self._day(days=-1))
        self.env["bf.membership"]._cron_daily()
        self.assertEqual(membership.state, "expired")
        self._recompute_status(self.alice)
        self.assertEqual(self.alice.member_status, "grace")

    def test_everything_off_by_default(self):
        """Installé, le module ne renouvelle rien et n'écrit à personne."""
        fresh = self.env["res.company"].create({"name": "Société neuve (essai)"})
        self.assertFalse(fresh.membership_auto_renewal)
        self.assertFalse(fresh.membership_reminders)
        membership = self._membership(self.alice, payment_state="paid",
                                      date_start=self._day(years=-1), date_end=self._day(days=5))
        self.env["bf.membership"]._cron_daily()
        self.assertFalse(membership.renewal_ids)
        self.assertFalse(self._mails_to(self.alice))

    def test_renewal_created_when_enabled(self):
        self.company.membership_auto_renewal = True
        membership = self._membership(self.alice, payment_state="paid",
                                      date_start=self._day(years=-1), date_end=self._day(days=20))
        far = self._membership(self.bruno, payment_state="paid",
                               date_start=self._day(days=-10), date_end=self._day(days=200))
        self.env["bf.membership"]._cron_daily()
        self.assertEqual(len(membership.renewal_ids), 1)
        self.assertEqual(membership.renewal_ids.state, "waiting")
        self.assertFalse(far.renewal_ids)
        self.env["bf.membership"]._cron_daily()
        self.assertEqual(len(membership.renewal_ids), 1, "Pas de second renouvellement.")

    def test_reminders_follow_stages_once(self):
        self.company.membership_reminders = True
        membership = self._membership(self.alice, payment_state="paid",
                                      date_start=self._day(years=-1), date_end=self._day(days=30))
        self.env["bf.membership"]._cron_daily()
        self.assertEqual(membership.reminder_stage, "first")
        self.assertEqual(len(self._mails_to(self.alice)), 1)
        self.env["bf.membership"]._cron_daily()
        self.assertEqual(len(self._mails_to(self.alice)), 1, "Le même rappel ne part pas deux fois.")
        with freeze_time(self._day(days=23)):
            self.env["bf.membership"]._cron_daily()
        self.assertEqual(membership.reminder_stage, "second")
        self.assertEqual(len(self._mails_to(self.alice)), 2)

    def test_late_reminder_is_skipped_not_sent(self):
        """Allumer les rappels ne réveille pas les échéances passées."""
        membership = self._membership(self.alice, payment_state="paid",
                                      date_start=self._day(years=-1), date_end=self._day(days=-15))
        membership.state = "expired"
        self.company.membership_reminders = True
        self.env["bf.membership"]._cron_daily()
        self.assertFalse(self._mails_to(self.alice))
        self.assertEqual(membership.reminder_stage, "due", "Marqué comme passé, sans envoi.")

    def test_paid_renewal_stops_reminders(self):
        self.company.membership_reminders = True
        membership = self._membership(self.alice, payment_state="paid",
                                      date_start=self._day(years=-1), date_end=self._day(days=10))
        membership.action_renew()
        membership.renewal_ids.action_mark_paid()
        self.env["bf.membership"]._cron_daily()
        self.assertEqual(membership.reminder_stage, "done")
        self.assertFalse(self._mails_to(self.alice))

    def test_withdrawn_member_gets_no_reminder(self):
        self.company.membership_reminders = True
        membership = self._membership(self.alice, payment_state="paid",
                                      date_start=self._day(years=-1), date_end=self._day(days=30))
        membership._withdraw(self.today, "Déménagement")
        self.env["bf.membership"]._cron_daily()
        self.assertFalse(self._mails_to(self.alice))

    def test_member_without_email_is_skipped_quietly(self):
        self.company.membership_reminders = True
        nomail = self.env["res.partner"].create({"name": "Sans courriel"})
        membership = self._membership(nomail, payment_state="paid",
                                      date_start=self._day(years=-1), date_end=self._day(days=30))
        self.env["bf.membership"]._cron_daily()
        self.assertEqual(membership.reminder_stage, "first")

    def test_daily_pass_reaches_every_company(self):
        """La tâche tourne avec un usager limité à sa société : elle doit quand
        même faire échoir les adhésions des autres."""
        other = self.env["res.company"].create({"name": "Autre association (essai)"})
        other_type = self.type_person.copy({"company_id": other.id, "code": "REG-AUTRE"})
        membership = self.env["bf.membership"].create({
            "partner_id": self.bruno.id, "type_id": other_type.id, "company_id": other.id,
            "payment_state": "paid", "date_start": self._day(years=-1), "date_end": self._day(days=-2)})
        # 🔴 Pas l'usager système : Odoo le traite d'office en superusager, et
        # l'essai passerait sans prouver quoi que ce soit. La tâche peut tourner
        # sous un usager ordinaire, limité à sa société.
        self.env["bf.membership"].with_user(self.manager).with_context(
            allowed_company_ids=[self.company.id])._cron_daily()
        self.assertEqual(membership.state, "expired")

    def test_a_withdrawal_elsewhere_does_not_silence_reminders(self):
        """Un départ d'une autre association de la même base ne coupe pas les
        rappels de celle-ci."""
        self.company.membership_reminders = True
        other = self.env["res.company"].create({"name": "Autre association (rappels)"})
        other_type = self.type_person.copy({"company_id": other.id, "code": "AUTR"})
        elsewhere = self.env["bf.membership"].create({
            "partner_id": self.alice.id, "type_id": other_type.id, "company_id": other.id,
            "payment_state": "paid", "date_start": self._day(months=-2)})
        elsewhere._withdraw(self._day(days=-1), "Départ de l'autre association")
        membership = self._membership(self.alice, payment_state="paid",
                                      date_start=self._day(years=-1), date_end=self._day(days=30))
        self.env["bf.membership"]._cron_daily()
        self.assertEqual(membership.reminder_stage, "first")
