"""Fox Quest 18.0.3.0.0 : constance en jours ouvrables, envois à temps, renard « à jour », congés.

Les dates sont figées sur une semaine connue d'octobre 2026 :
lundi 5, mardi 6, ..., vendredi 9, samedi 10, dimanche 11, lundi 12.
Les usagers d'essai sont dans le fuseau de Montréal.
"""
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz
from freezegun import freeze_time
from psycopg2 import errors

from odoo import SUPERUSER_ID
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.base.models.res_config import ResConfigSettings
from odoo.addons.bf_gamification.models import fq_days, fq_savepoint

from .common import in_a_request

MON, TUE, WED, THU, FRI, SAT, SUN = (date(2026, 10, d) for d in range(5, 12))
NEXT_MON = date(2026, 10, 12)
NEXT_TUE = date(2026, 10, 13)


@tagged("post_install", "-at_install")
class TestConstance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.user = new_test_user(cls.env, login="fq_constance", name="Fox Constance",
                                 tz="America/Montreal")
        cls.boss = new_test_user(
            cls.env, login="fq_boss", name="Fox Boss", tz="America/Montreal",
            groups="base.group_user,bf_gamification.group_gamification_manager")
        cls.robot = new_test_user(cls.env, login="fq_robot", name="Fox Robot")
        cls.robot.bf_gamification_excluded = True
        cls.Profile = cls.env["bf.gamification.profile"]
        cls.Txn = cls.env["bf.gamification.xp.transaction"].sudo()
        cls.profile = cls.Profile._get_or_create_profile(cls.user)
        # Un calendrier lundi-vendredi, sans dépendre de celui de la société, et
        # le gestionnaire sur la fiche employé : c'est lui qui doit approuver.
        cls.calendar = cls.env["resource.calendar"].create({"name": "Semaine FQ"})
        boss_employee = cls.env["hr.employee"].create({
            "name": "Fox Boss", "user_id": cls.boss.id, "resource_calendar_id": cls.calendar.id})
        cls.employee = cls.env["hr.employee"].create({
            "name": "Fox Constance", "user_id": cls.user.id, "parent_id": boss_employee.id,
            "resource_calendar_id": cls.calendar.id})

    def setUp(self):
        super().setUp()
        # Un TransactionCase n'a pas de requête HTTP : sans ce remplacement, chaque
        # geste des essais passerait pour un cron. Les essais du cron
        # remettent `self.interactive` à faux eux-mêmes.
        self.interactive = True
        self.real_is_interactive = type(self.Profile)._bf_is_interactive
        self.patch(type(self.Profile), "_bf_is_interactive", lambda model: self.interactive)
        # Seul le geste de la personne ELLE-MÊME fait
        # un jour de constance. `self.profile` agit donc sous son compte ; les
        # gestes des autres passent par `with_user`.
        self.profile = self.Profile.with_user(self.user)._get_or_create_profile(self.user)

    def _txns(self, user=None, source=None):
        domain = [("user_id", "=", (user or self.user).id)]
        if source:
            domain.append(("source", "=", source))
        return self.Txn.search(domain)

    # ------------------------------------------------------------------
    # Jours ouvrables
    # ------------------------------------------------------------------

    def _streak_on(self, last_day, stamp):
        """La constance, le jour `stamp` (UTC), d'une personne dont le dernier jour
        travaillé est `last_day` : celle que lit l'écran ou que le cron refait."""
        self._history(last_day)
        with freeze_time(stamp):
            self.profile._bf_recompute_constance()
        return self.profile.current_streak

    def test_weekend_does_not_break_the_streak(self):
        self.assertEqual(fq_days.non_working_days(self.env, self.user, SAT, SUN), {SAT, SUN})
        # Vendredi travaillé, lundi encore rien : la fin de semaine ne casse pas.
        self.assertEqual(self._streak_on(FRI, "2026-10-12 15:00:00"), 1)

    def test_one_missed_working_day_is_tolerated_two_are_not(self):
        self.assertEqual(self._streak_on(THU, "2026-10-12 15:00:00"), 1, "vendredi manqué : toléré")
        self.profile.invalidate_recordset()
        self.Txn.search([("user_id", "=", self.user.id)]).unlink()
        self.assertEqual(self._streak_on(WED, "2026-10-12 15:00:00"), 0, "jeudi et vendredi manqués")

    def test_public_holiday_pauses_the_streak(self):
        self.env["resource.calendar.leaves"].create({
            "name": "Action de grâce",
            "date_from": datetime(2026, 10, 12, 4, 0),   # minuit à Montréal
            "date_to": datetime(2026, 10, 13, 4, 0),
            "calendar_id": False,
            "resource_id": False,
        })
        self.assertIn(NEXT_MON, fq_days.non_working_days(self.env, self.user, NEXT_MON, NEXT_MON))
        # Jeudi travaillé, mardi sans rien : vendredi manqué, lundi férié (en pause).
        self.assertEqual(self._streak_on(THU, "2026-10-13 15:00:00"), 1)

    def test_declared_absence_pauses_the_streak(self):
        if "bf.partner.absence" not in self.env:
            self.skipTest("bf_contact_absence n'est pas installé")
        self.env["bf.partner.absence"].create({
            "partner_id": self.user.partner_id.id,
            "date_from": TUE, "date_to": THU, "nature": "leave",
        })
        # Lundi travaillé, lundi suivant sans rien : mardi à jeudi en pause, vendredi manqué.
        self.assertEqual(self._streak_on(MON, "2026-10-12 15:00:00"), 1)

    def test_saturday_work_neither_counts_nor_breaks(self):
        with freeze_time("2026-10-09 15:00:00"):   # vendredi, 11 h à Montréal
            self.profile._award_xp(1, "manual", "vendredi")
        self.assertEqual((self.profile.current_streak, self.profile.last_activity_date), (1, FRI))
        with freeze_time("2026-10-10 15:00:00"):   # samedi
            self.profile._award_xp(1, "manual", "samedi")
        self.assertEqual((self.profile.current_streak, self.profile.last_activity_date), (1, FRI))
        with freeze_time("2026-10-12 15:00:00"):   # lundi
            self.profile._award_xp(1, "manual", "lundi")
        self.assertEqual((self.profile.current_streak, self.profile.last_activity_date), (2, NEXT_MON))

    def test_the_streak_rule_finally_pays(self):
        with freeze_time("2026-10-08 15:00:00"):
            self.profile._award_xp(1, "manual", "jeudi")
        with freeze_time("2026-10-09 15:00:00"):
            self.profile._award_xp(1, "manual", "vendredi")
            self.profile._award_xp(1, "manual", "vendredi encore")
        streak = self._txns(source="streak")
        self.assertEqual(len(streak), 1, "une fois par jour où la constance continue")
        self.assertEqual(streak.xp_amount, self.env.ref("bf_gamification.xp_rule_streak").xp_amount)

    def test_the_day_is_the_person_s_day(self):
        # 2 h UTC le samedi = 22 h le vendredi à Montréal : encore vendredi.
        with freeze_time("2026-10-10 02:00:00"):
            self.profile._award_xp(1, "manual", "vendredi soir")
        self.assertEqual(self.profile.last_activity_date, FRI)

    def _history(self, *days, user=None):
        """De l'XP de travail, né d'un geste, à 15 h UTC (11 h à Montréal) ces jours-là."""
        for day in days:
            self.Txn.create({"user_id": (user or self.user).id, "xp_amount": 1, "source": "message",
                             "from_request": True,
                             "date": datetime.combine(day, datetime.min.time()) + timedelta(hours=15)})

    def test_cron_resets_only_after_missed_working_days(self):
        # La constance se dérive de l'historique : le cron la refait.
        self._history(MON, TUE, WED, THU, FRI)
        with freeze_time("2026-10-12 15:00:00"):   # lundi : rien de manqué
            self.Profile._cron_update_streaks()
        self.assertEqual(self.profile.current_streak, 5)
        with freeze_time("2026-10-14 15:00:00"):   # mercredi : lundi et mardi manqués
            self.Profile._cron_update_streaks()
        self.assertEqual(self.profile.current_streak, 0)

    def test_recompute_from_history(self):
        for day in (MON, TUE, WED, THU, FRI, SAT):
            self.Txn.create({"user_id": self.user.id, "xp_amount": 1, "source": "message",
                             "from_request": True,
                             "date": datetime.combine(day, datetime.min.time()) + timedelta(hours=15)})
        with freeze_time("2026-10-12 15:00:00"):
            self.profile._bf_recompute_constance()
        self.assertEqual((self.profile.current_streak, self.profile.longest_streak,
                          self.profile.last_activity_date), (5, 5, FRI))

    # ------------------------------------------------------------------
    # Comptes de service
    # ------------------------------------------------------------------

    def test_service_account_earns_nothing_and_leaves_the_board(self):
        robot_profile = self.Profile._get_or_create_profile(self.robot)
        robot_profile._award_xp(50, "manual", "robot")
        self.assertFalse(self._txns(user=self.robot))
        board = self.env["bf.gamification.dashboard"]._get_leaderboard(limit=500)
        self.assertNotIn(self.robot.name, [row["user_name"] for row in board])
        robot_profile.invalidate_recordset(["rank"])
        self.assertEqual(robot_profile.rank, 0)

    def test_portal_user_earns_nothing(self):
        portal = new_test_user(self.env, login="fq_portal", groups="base.group_portal")
        self.Profile._get_or_create_profile(portal)._award_xp(5, "manual", "portail")
        self.assertFalse(self._txns(user=portal))

    # ------------------------------------------------------------------
    # Tâches à l'échéance
    # ------------------------------------------------------------------

    def _task(self, deadline):
        project = self.env["project.project"].create({"name": "FQ projet"})
        return self.env["project.task"].create({
            "name": "FQ tâche", "project_id": project.id,
            "user_ids": [(6, 0, self.user.ids)], "date_deadline": deadline,
        })

    def _created_on(self, record, stamp):
        # create_date vient de Postgres : freeze_time ne le fige pas.
        self.env.cr.execute("UPDATE %s SET create_date = %%s WHERE id = %%s" % record._table,
                            (stamp, record.id))
        record.invalidate_recordset(["create_date"])

    def test_task_closed_before_its_deadline_pays_the_bonus(self):
        with freeze_time("2026-10-08 15:00:00"):
            task = self._task(datetime(2026, 10, 9, 20, 0))
            self._created_on(task, "2026-10-07 15:00:00")   # planifiée la veille
            task.with_user(self.user).sudo().write({"state": "1_done"})
        amounts = sorted(self._txns(source="task").mapped("xp_amount"))
        self.assertEqual(amounts, sorted([
            self.env.ref("bf_gamification.xp_rule_task_complete").xp_amount,
            self.env.ref("bf_gamification.xp_rule_task_early").xp_amount]))

    def test_task_closed_late_pays_no_bonus_and_pays_once(self):
        with freeze_time("2026-10-12 15:00:00"):
            task = self._task(datetime(2026, 10, 9, 20, 0))
            task.write({"state": "1_done"})
            task.write({"state": "01_in_progress"})
            task.write({"state": "1_done"})
        self.assertEqual(len(self._txns(source="task")), 1)

    # ------------------------------------------------------------------
    # Le renard « à jour »
    # ------------------------------------------------------------------

    def _activity(self, user, deadline):
        partner = self.env["res.partner"].create({"name": "FQ cible"})
        return self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("res.partner"),
            "res_id": partner.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "user_id": user.id,
            "date_deadline": deadline,
        })

    def _fox_messages(self):
        # Odoo 18 garde les messages du bus en attente jusqu'à la validation
        # (précommit) : l'essai lit la file, pas la table.
        pending = self.env.cr.precommit.data.get("bus.bus.values", [])
        return [v for v in pending if "bf_gamification/caught_up" in v["message"]]

    def test_emptying_one_s_own_activities_brings_the_fox_once_a_day(self):
        with freeze_time("2026-10-08 15:00:00"):
            first = self._activity(self.user, THU)
            second = self._activity(self.user, THU)
            before = len(self._fox_messages())
            first.with_user(self.user).action_done()
            self.assertEqual(len(self._fox_messages()), before, "il reste une activité due")
            second.with_user(self.user).action_done()
            self.assertEqual(len(self._fox_messages()), before + 1)
            third = self._activity(self.user, THU)
            third.with_user(self.user).action_done()
            self.assertEqual(len(self._fox_messages()), before + 1, "une fois par jour et par liste")
        kinds = self.env["bf.gamification.caught.up"].sudo().search([
            ("user_id", "=", self.user.id)]).mapped("kind")
        self.assertIn("activity", kinds)
        self.assertTrue(self._txns(source="caught_up"))

    def test_someone_else_emptying_the_list_brings_no_fox(self):
        with freeze_time("2026-10-08 15:00:00"):
            activity = self._activity(self.user, THU)
            before = len(self._fox_messages())
            activity.with_user(self.boss).sudo().action_done()
        self.assertEqual(len(self._fox_messages()), before)
        self.assertFalse(self.env["bf.gamification.caught.up"].sudo().search([
            ("user_id", "=", self.user.id)]))

    def test_activity_done_on_time_pays_only_if_planned_before(self):
        planned = self._activity(self.user, THU)
        # create_date vient de Postgres : freeze_time ne le fige pas.
        self.env.cr.execute("UPDATE mail_activity SET create_date = %s WHERE id = %s",
                            ("2026-10-07 15:00:00", planned.id))
        planned.invalidate_recordset(["create_date"])
        with freeze_time("2026-10-08 15:00:00"):
            planned.with_user(self.user).action_done()
            same_day = self._activity(self.user, THU)
            same_day.with_user(self.user).action_done()
        self.assertEqual(len(self._txns(source="on_time")), 1)

    # ------------------------------------------------------------------
    # Congés de constance
    # ------------------------------------------------------------------

    def _enable_leave(self, step=3):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_gamification.leave_enabled", "True")
        ICP.set_param("bf_gamification.leave_streak_days", str(step))
        ICP.set_param("bf_gamification.leave_days_per_step", "1")
        ICP.set_param("bf_gamification.leave_max_per_year", "5")

    def _claims(self):
        return self.env["bf.gamification.reward.claim"].sudo().search([
            ("user_id", "=", self.user.id),
            ("reward_id", "=", self.env.ref("bf_gamification.reward_leave_constance").id)])

    def test_opt_in_is_off_when_the_company_has_not_enabled_it(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_gamification.leave_enabled", "False")
        profile = self.Profile._get_or_create_profile(self.user).with_user(self.user)
        with self.assertRaises(UserError):
            self.env["bf.gamification.dashboard"].with_user(self.user).set_leave_opt_in(True)
        self.assertFalse(profile.sudo().leave_opt_in)

    def _opt_in(self, stamp="2026-10-01 13:00:00"):
        # La marche part du jour de l'activation : l'activer avant les
        # jours figés de l'essai.
        with freeze_time(stamp):
            self.env["bf.gamification.dashboard"].with_user(self.user).set_leave_opt_in(True)

    def test_reaching_the_step_opens_a_claim_for_a_manager(self):
        self._enable_leave(step=3)
        self._opt_in()
        for stamp in ("2026-10-07 15:00:00", "2026-10-08 15:00:00", "2026-10-09 15:00:00"):
            with freeze_time(stamp):
                self.profile._award_xp(1, "manual", "jour")
        claim = self._claims()
        self.assertEqual(len(claim), 1)
        self.assertEqual((claim.state, claim.leave_days, claim.xp_spent), ("pending", 1.0, 0))
        self.assertEqual(claim.activity_ids.user_id, self.boss)
        self.assertEqual(self.profile.sudo().leave_progress, 0)
        # La personne ne s'approuve pas elle-même, même gestionnaire.
        self.user.groups_id = [(4, self.env.ref("bf_gamification.group_gamification_manager").id)]
        with self.assertRaises(UserError):
            claim.with_user(self.user).action_approve()
        if "allocation_id" in claim._fields:
            # Le pont vers Congés est installé : il exige un type de congé.
            leave_type = self.env["hr.leave.type"].create({
                "name": "FQ constance", "requires_allocation": "yes",
                "allocation_validation_type": "hr"})
            self.env["ir.config_parameter"].sudo().set_param(
                "bf_gamification.leave_type_id", str(leave_type.id))
        claim.with_user(self.boss).action_approve()
        self.assertEqual(claim.state, "approved")
        self.assertFalse(claim.activity_ids)

    def test_the_claim_and_the_approvers_activity_carry_a_readable_name(self):
        self._enable_leave(step=3)
        self._opt_in()
        for stamp in ("2026-10-07 15:00:00", "2026-10-08 15:00:00", "2026-10-09 15:00:00"):
            with freeze_time(stamp):
                self.profile._award_xp(1, "manual", "jour")
        claim = self._claims()
        self.assertEqual(claim.display_name, "Journ\u00e9e de cong\u00e9 (constance) \u2013 Fox Constance")
        self.assertEqual(claim.activity_ids.res_name, claim.display_name)
        self.env.flush_all()
        mails = self.env["mail.mail"].sudo().search([
            ("model", "=", "bf.gamification.reward.claim"), ("res_id", "=", claim.id)])
        self.assertEqual(mails.recipient_ids, self.boss.partner_id,
                         "le gestionnaire est le seul prévenu")
        for mail in mails:
            self.assertNotIn("bf.gamification", mail.subject or "")
            self.assertNotIn("bf.gamification", mail.reply_to or "")
            self.assertIn(claim.display_name, mail.subject)

    def test_no_claim_once_the_company_turns_it_off(self):
        self._enable_leave(step=2)
        self._opt_in()
        self.env["ir.config_parameter"].sudo().set_param("bf_gamification.leave_enabled", "False")
        for stamp in ("2026-10-07 15:00:00", "2026-10-08 15:00:00"):
            with freeze_time(stamp):
                self.profile._award_xp(1, "manual", "jour")
        self.assertFalse(self._claims())

    def test_no_claim_without_opt_in(self):
        self._enable_leave(step=1)
        with freeze_time("2026-10-07 15:00:00"):
            self.profile._award_xp(1, "manual", "jour")
        self.assertFalse(self._claims())

    def test_a_broken_streak_restarts_the_march(self):
        self._enable_leave(step=5)
        self._opt_in()
        with freeze_time("2026-10-05 15:00:00"):
            self.profile._award_xp(1, "manual", "lundi")
        with freeze_time("2026-10-06 15:00:00"):
            self.profile._award_xp(1, "manual", "mardi")
        self.assertEqual(self.profile.sudo().leave_progress, 2)
        with freeze_time("2026-10-09 15:00:00"):   # mercredi et jeudi manqués
            self.profile._award_xp(1, "manual", "vendredi")
        self.assertEqual(self.profile.sudo().leave_progress, 1)

    def test_the_constance_reward_cannot_be_bought_or_forged(self):
        reward = self.env.ref("bf_gamification.reward_leave_constance")
        Claim = self.env["bf.gamification.reward.claim"]
        with self.assertRaises(UserError):
            Claim.with_user(self.user).create({"reward_id": reward.id})
        with self.assertRaises(UserError):
            Claim.with_user(self.user).with_context(bf_gamification_constance=True).create(
                {"reward_id": reward.id})
        with self.assertRaises(UserError):
            Claim.with_user(self.boss).create({"reward_id": reward.id, "user_id": self.user.id})

    def test_colleagues_cannot_read_participation(self):
        self._enable_leave()
        self.env["bf.gamification.dashboard"].with_user(self.user).set_leave_opt_in(True)
        colleague = new_test_user(self.env, login="fq_collegue")
        with self.assertRaises(AccessError):
            self.profile.with_user(colleague).read(["leave_opt_in"])

    def test_opt_out_stops_the_counter(self):
        self._enable_leave(step=2)
        dashboard = self.env["bf.gamification.dashboard"].with_user(self.user)
        self._opt_in()
        with freeze_time("2026-10-07 15:00:00"):
            self.profile._award_xp(1, "manual", "jour")
            dashboard.set_leave_opt_in(False)
        with freeze_time("2026-10-08 15:00:00"):
            self.profile._award_xp(1, "manual", "jour")
        self.assertFalse(self._claims())
        self.assertTrue(self.profile.sudo().leave_opt_out_date)

    def test_portal_cannot_touch_participation(self):
        self._enable_leave()
        portal = new_test_user(self.env, login="fq_portail_conges", groups="base.group_portal")
        with self.assertRaises(AccessError):
            self.env["bf.gamification.dashboard"].with_user(portal).set_leave_opt_in(True)
        self.assertFalse(self.Profile.search([("user_id", "=", portal.id)]))

    # ------------------------------------------------------------------
    # Envois : l'XP va à l'humain qui envoie
    # ------------------------------------------------------------------

    def test_report_sent_credits_the_human_sender_on_time(self):
        record = self.env["meeting.record"].with_user(self.robot).sudo().create({
            "name": "FQ rencontre",
            "date": datetime.now() - timedelta(hours=3),
            "date_delay": 1.0,
            "organizer_id": self.user.id,
        })
        self.assertFalse(self._txns(user=self.robot), "un CR né d'un robot ne rapporte rien")
        record.with_user(self.user).sudo().write({"report_state": "sent",
                                                  "report_sent_date": datetime.now()})
        self.assertEqual(sorted(self._txns(source="meeting").mapped("xp_amount")),
                         [self.env.ref("bf_gamification.xp_rule_meeting_sent").xp_amount])
        self.assertEqual(self._txns(source="on_time").mapped("xp_amount"),
                         [self.env.ref("bf_gamification.xp_rule_on_time_report").xp_amount])
        # Renvoyé après correction : la ponctualité ne paie qu'une fois.
        record.sudo().write({"report_state": "draft"})
        record.with_user(self.user).sudo().write({"report_state": "sent"})
        self.assertEqual(len(self._txns(source="on_time")), 1)

    def test_a_colleague_who_sends_the_report_is_credited_not_the_organizer(self):
        # L'XP d'un envoi va à l'humain qui envoie, l'organisateur seulement
        # quand c'est un robot qui envoie. Les essais n'avaient qu'un envoyeur
        # organisateur : la règle « humain qui agit » ne se voyait pas.
        record = self.env["meeting.record"].sudo().create({
            "name": "FQ rencontre envoyée par un collègue",
            "date": datetime.now() - timedelta(days=3),
            "date_delay": 1.0,
            "organizer_id": self.user.id,
        })
        record.with_user(self.boss).sudo().write({"report_state": "sent",
                                                  "report_sent_date": datetime.now()})
        self.assertTrue(self._txns(user=self.boss, source="meeting"), "le collègue qui envoie")
        self.assertFalse(self._txns(source="meeting"), "pas l'organisatrice")

    def test_a_backfilled_entry_keeps_the_date_of_the_work(self):
        # Le rattrapage de l'installation (`_backfill_recent_xp`) date l'XP du
        # travail rattrapé ; le journal ne remplace que la date qu'on ne lui
        # donne pas.
        when = datetime(2026, 10, 6, 15, 0)
        with freeze_time("2026-10-08 15:00:00"):
            entry = self.Txn._bf_create_entry(self.user, 1, "manual", "rattrapé",
                                              date=when, from_request=False)
        self.assertEqual((entry.date, entry.from_request), (when, False))

    def test_late_report_pays_no_bonus_and_robot_sender_credits_organizer(self):
        record = self.env["meeting.record"].sudo().create({
            "name": "FQ rencontre ancienne",
            "date": datetime.now() - timedelta(days=3),
            "date_delay": 1.0,
            "organizer_id": self.user.id,
        })
        record.with_user(self.robot).sudo().write({"report_state": "sent",
                                                   "report_sent_date": datetime.now()})
        self.assertTrue(self._txns(source="meeting"), "le robot envoie : l'organisateur est crédité")
        self.assertFalse(self._txns(user=self.robot))
        self.assertFalse(self._txns(source="on_time"))

    def test_agenda_sent_a_day_ahead_pays_once(self):
        project = self.env["project.project"].create({"name": "FQ projet OdJ"})
        agenda = self.env["meeting.agenda"].with_user(self.user).sudo().create({
            "name": "FQ OdJ", "project_id": project.id, "organizer_id": self.user.id,
            "date": datetime.now() + timedelta(days=3),
        })
        agenda.with_user(self.user).sudo().write({"email_sent_date": datetime.now()})
        agenda.sudo().write({"email_sent_date": False})
        agenda.with_user(self.user).sudo().write({"email_sent_date": datetime.now()})
        self.assertEqual(self._txns(source="on_time").mapped("xp_amount"),
                         [self.env.ref("bf_gamification.xp_rule_on_time_agenda").xp_amount])

    def test_agenda_sent_the_same_day_pays_no_bonus(self):
        project = self.env["project.project"].create({"name": "FQ projet OdJ 2"})
        agenda = self.env["meeting.agenda"].sudo().create({
            "name": "FQ OdJ tardif", "project_id": project.id, "organizer_id": self.user.id,
            "date": datetime.now() + timedelta(hours=5),
        })
        agenda.with_user(self.user).sudo().write({"email_sent_date": datetime.now()})
        self.assertFalse(self._txns(source="on_time"))

    def test_a_new_profile_is_born_complete(self):
        newcomer = new_test_user(self.env, login="fq_nouveau", name="Fox Nouveau")
        profile = self.Profile._get_or_create_profile(newcomer)
        self.assertEqual(profile.level_id, self.env["bf.gamification.level"].search(
            [], order="min_xp asc", limit=1))
        self.assertEqual(self.Profile._get_or_create_profile(newcomer), profile)

    # ------------------------------------------------------------------
    # Durcissements (1)
    # ------------------------------------------------------------------

    def _leave_on_resource(self, first, last):
        """Un congé posé sur la ressource de l'employée, comme `hr_holidays` le
        pose à la validation : de minuit à minuit, à Montréal."""
        return self.env["resource.calendar.leaves"].create({
            "name": "FQ congé validé",
            "date_from": fq_days.local_midnight_utc(self.user, first),
            "date_to": fq_days.local_midnight_utc(self.user, last + timedelta(days=1)),
            "resource_id": self.employee.resource_id.id,
            "calendar_id": self.calendar.id,
            "time_type": "leave",
        })

    # « Une absence déclarée après coup répare constance et marche » : dans le
    # pont bf_gamification_contact_absence, qui porte ce crochet.

    def test_c1_time_off_validated_afterwards_repairs_the_streak(self):
        for stamp in ("2026-10-01 15:00:00", "2026-10-02 15:00:00"):
            with freeze_time(stamp):
                self.profile._award_xp(1, "manual", "jour")
        with freeze_time("2026-10-07 15:00:00"):
            self.profile._award_xp(1, "manual", "mercredi")
            self.assertEqual(self.profile.current_streak, 1)
            leave = self._leave_on_resource(MON, TUE)
            self.assertEqual(self.profile.current_streak, 3)
            # Le congé annulé : lundi et mardi redeviennent des jours manqués.
            leave.unlink()
            self.assertEqual(self.profile.current_streak, 1)

    def test_c1_a_repaired_march_restarts_from_the_last_step_never_twice(self):
        self._enable_leave(step=3)
        self._opt_in()
        # Jeudi, vendredi, lundi : palier atteint le lundi 5 ; mardi : 1 vers le suivant.
        for stamp in ("2026-10-01 15:00:00", "2026-10-02 15:00:00",
                      "2026-10-05 15:00:00", "2026-10-06 15:00:00"):
            with freeze_time(stamp):
                self.profile._award_xp(1, "manual", "jour")
        self.assertEqual(len(self._claims()), 1)
        self.assertEqual(self.profile.sudo().leave_progress, 1)
        with freeze_time("2026-10-09 15:00:00"):   # mercredi et jeudi manqués
            self.profile._award_xp(1, "manual", "vendredi")
            self.assertEqual(self.profile.sudo().leave_progress, 1)
            # Mercredi et jeudi en congé, validé après coup : la constance revient
            # à 5 jours, la marche repart du palier du lundi (mardi, vendredi).
            self._leave_on_resource(WED, THU)
            self.assertEqual(self.profile.current_streak, 5)
            self.assertEqual(self.profile.sudo().leave_progress, 2)
            self.Profile._cron_update_streaks()
        self.assertEqual(len(self._claims()), 1, "le palier du lundi ne se recompte jamais")

    def test_c2_outside_a_request_nothing_is_a_gesture(self):
        # Un essai, comme un cron ou un import, n'a pas de requête HTTP.
        self.assertFalse(self.real_is_interactive(self.Profile))

    def test_c2_a_cron_neither_brings_the_fox_nor_counts_a_day(self):
        self.interactive = False   # un cron sous le compte de la personne, le mercredi
        with freeze_time("2026-10-07 15:00:00"):
            activity = self._activity(self.user, WED)
            before = len(self._fox_messages())
            activity.with_user(self.user).action_done()
            self.assertEqual(len(self._fox_messages()), before, "pas de renard sans requête")
            self.profile._award_xp(1, "manual", "cron")
        self.assertFalse(self.env["bf.gamification.caught.up"].sudo().search([
            ("user_id", "=", self.user.id)]))
        self.assertFalse(any(self._txns().mapped("from_request")))
        self.assertEqual((self.profile.current_streak, self.profile.last_activity_date), (0, False))
        self.interactive = True    # la personne, dans une requête, le jeudi
        with freeze_time("2026-10-08 15:00:00"):
            self.profile._award_xp(1, "manual", "geste")
        self.assertEqual((self.profile.current_streak, self.profile.last_activity_date), (1, THU),
                         "le mercredi du cron ne compte pas")

    def test_c3_nobody_sets_someone_s_participation_by_hand(self):
        self._enable_leave()
        colleague = new_test_user(self.env, login="fq_collegue_c3")
        for vals in ({"leave_opt_in": True}, {"leave_progress": 59},
                     {"leave_last_step_day": FRI}, {"leave_opt_in_date": datetime(2026, 1, 1)}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                self.profile.with_user(self.boss).write(vals)
        with self.assertRaises(AccessError):
            self.Profile.with_user(self.boss).create({"user_id": colleague.id, "leave_opt_in": True})
        self.assertFalse(self.profile.sudo().leave_opt_in)

    def _free_reward(self, **vals):
        return self.env["bf.gamification.reward"].create(dict(
            {"name": "FQ récompense", "xp_cost": 0, "available": True}, **vals))

    def _constance_claim(self, user=None):
        return self.env["bf.gamification.reward.claim"].sudo().with_context(
            bf_gamification_constance=True).create({
                "user_id": (user or self.user).id, "leave_days": 1.0,
                "reward_id": self.env.ref("bf_gamification.reward_leave_constance").id})

    def test_c4_a_claim_cannot_change_hands_reward_or_quantity(self):
        own = self.env["bf.gamification.reward.claim"].with_user(self.boss).create(
            {"reward_id": self._free_reward().id})
        constance = self.env.ref("bf_gamification.reward_leave_constance")
        with self.assertRaises(UserError):   # sa réclamation devient un congé de constance
            own.with_user(self.boss).write({"reward_id": constance.id})
        claim = self._constance_claim()
        for vals in ({"user_id": self.boss.id}, {"leave_days": 5.0}, {"xp_spent": 7},
                     {"user_id": self.boss.id, "state": "approved"}):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                claim.with_user(self.boss).write(vals)
        self.assertEqual((claim.user_id, claim.state, claim.leave_days), (self.user, "pending", 1.0))
        # Réécrire la même valeur n'est pas un changement.
        claim.with_user(self.boss).write({"user_id": self.user.id})

    def test_c4_the_guard_reads_the_claim_before_and_after_the_write(self):
        # Si la liste des champs figés oubliait un jour `user_id`, la garde lue
        # avant ET après l'écriture refuserait encore de s'approuver soi-même.
        claim = self._constance_claim()
        self.patch(type(claim), "_bf_frozen_changes", lambda records, vals: set())
        # (Le point de sauvegarde défait l'écriture refusée, comme la requête RPC.)
        with self.assertRaises(UserError), self.env.cr.savepoint():   # après : devenue la sienne
            claim.with_user(self.boss).write({"user_id": self.boss.id, "state": "approved"})
        self.user.groups_id = [(4, self.env.ref("bf_gamification.group_gamification_manager").id)]
        with self.assertRaises(UserError), self.env.cr.savepoint():   # avant : la sienne, cédée
            claim.with_user(self.user).write({"user_id": self.boss.id, "state": "approved"})
        self.assertEqual((claim.user_id, claim.state), (self.user, "pending"))

    def test_c4_a_manager_cannot_turn_a_reward_into_constance_time_off(self):
        reward = self._free_reward()
        with self.assertRaises(AccessError):
            reward.with_user(self.boss).write({"is_constance_leave": True})
        with self.assertRaises(AccessError):
            self.env.ref("bf_gamification.reward_leave_constance").with_user(self.boss).write(
                {"is_constance_leave": False})
        with self.assertRaises(AccessError):
            self.env["bf.gamification.reward"].with_user(self.boss).create(
                {"name": "FQ faux congé", "xp_cost": 0, "is_constance_leave": True})

    def _allow_bridge_approval(self):
        if "allocation_id" in self.env["bf.gamification.reward.claim"]._fields:
            # Le pont vers Congés est installé : il exige un type de congé.
            leave_type = self.env["hr.leave.type"].create({
                "name": "FQ constance", "requires_allocation": "yes",
                "allocation_validation_type": "hr"})
            self.env["ir.config_parameter"].sudo().set_param(
                "bf_gamification.leave_type_id", str(leave_type.id))

    def test_c4_approve_and_refuse_read_the_current_state(self):
        self.profile._award_xp(10, "manual", "solde")
        reward = self._free_reward(xp_cost=5)
        claim = self.env["bf.gamification.reward.claim"].with_user(self.user).create(
            {"reward_id": reward.id})
        claim.with_user(self.boss).action_refuse()
        with self.assertRaises(UserError):   # pas de second remboursement
            claim.with_user(self.boss).action_refuse()
        with self.assertRaises(UserError):   # une demande refusée ne s'approuve plus
            claim.with_user(self.boss).action_approve()
        refunds = self._txns(source="reward").filtered(lambda t: t.xp_amount > 0)
        self.assertEqual(len(refunds), 1)
        self._allow_bridge_approval()
        leave = self._constance_claim()
        leave.with_user(self.boss).action_approve()
        with self.assertRaises(UserError):
            leave.with_user(self.boss).action_approve()
        leave.with_user(self.boss).action_consume()
        with self.assertRaises(UserError):   # un congé pris ne se refuse plus
            leave.with_user(self.boss).action_refuse()
        self.assertEqual(leave.state, "consumed")

    def _failing_count(self, error):
        def count(model, user, kind):
            if isinstance(error, str):
                model.env.cr.execute(error)
            raise error
        self.patch(type(self.Profile), "_bf_pending_count", count)

    @mute_logger("odoo.sql_db", "odoo.addons.bf_gamification.models.fq_savepoint")
    def test_c5_a_failing_celebration_does_not_abort_the_gesture(self):
        self._failing_count("SELECT 1 / 0")
        with freeze_time("2026-10-08 15:00:00"):
            activity = self._activity(self.user, THU)
            activity.with_user(self.user).action_done()
        # La transaction du geste est intacte : elle répond encore.
        self.env.cr.execute("SELECT 1")
        self.assertFalse(activity.exists())
        self.assertTrue(self._txns(source="activity"), "l'XP du geste est gardé")

    @mute_logger("odoo.sql_db", "odoo.addons.bf_gamification.models.fq_savepoint")
    def test_c5_a_failing_streak_does_not_abort_the_gesture(self):
        # Même principe pour la constance refaite au premier geste du jour.
        def recompute(profiles):
            profiles.env.cr.execute("SELECT 1 / 0")
        self.patch(type(self.Profile), "_bf_recompute_constance", recompute)
        with freeze_time("2026-10-08 15:00:00"):
            self.profile._award_xp(1, "manual", "geste")
        self.env.cr.execute("SELECT 1")
        self.assertTrue(self._txns(source="manual"), "l'XP du geste est gardé")

    def test_c1_a_time_zone_postgres_does_not_know_falls_back_to_python(self):
        # La base de fuseaux de Postgres n'est pas celle de pytz : un fuseau
        # inconnu de Postgres ne doit faire échouer ni le geste ni la migration.
        # Une instance neuve (pas le singleton de pytz) : Montréal, sous un nom inconnu.
        zone = type(pytz.timezone("America/Montreal"))()
        zone.zone = "Nulle/Part"
        self.patch(fq_days, "user_tz", lambda user: zone)
        self._history(MON, TUE, WED, THU, FRI)
        with freeze_time("2026-10-12 15:00:00"):
            self.profile._bf_recompute_constance()
        self.assertEqual((self.profile.current_streak, self.profile.last_activity_date), (5, FRI))

    def test_c5_concurrency_errors_go_up_to_be_replayed(self):
        for error in (errors.SerializationFailure, errors.LockNotAvailable,
                      errors.DeadlockDetected):
            with self.subTest(error=error.__name__):
                self._failing_count(error("concurrence simulée"))
                with self.assertRaises(error):
                    self.Profile.with_user(self.user)._bf_caught_up_check(self.user, "activity")
                self.env.cr.execute("SELECT 1")

    def test_c6_the_claim_is_followed_by_the_beneficiary_and_the_approver_only(self):
        self._enable_leave(step=1)
        self._opt_in()
        colleague = new_test_user(self.env, login="fq_collegue_c6", name="Fox Collègue")
        # Le recalcul qu'un collègue déclenche (il pose une absence, il valide un
        # congé) atteint le palier : la demande naît dans SA requête. (Le geste
        # d'un collègue ne fait pas de jour de constance : c'est l'historique de
        # la personne qui atteint le palier.)
        self._history(WED)
        with freeze_time("2026-10-07 15:00:00"):
            self.Profile.with_user(colleague).with_context(
                tracking_disable=False)._bf_constance_changed(self.user)
        claim = self._claims()
        self.assertEqual(len(claim), 1)
        self.assertEqual(claim.message_partner_ids,
                         self.user.partner_id | self.boss.partner_id)
        # Création, note et activité signées par le système, pas par le collègue.
        self.assertEqual(claim.message_ids.author_id, self.env.ref("base.partner_root"))
        self.assertEqual(claim.activity_ids.create_uid.id, SUPERUSER_ID)

    def test_c7_the_reset_keeps_constance_time_off(self):
        self._allow_bridge_approval()
        leave = self._constance_claim()
        leave.with_user(self.boss).action_approve()
        other = self.env["bf.gamification.reward.claim"].with_user(self.user).create(
            {"reward_id": self._free_reward().id})
        other.sudo().activity_schedule("mail.mail_activity_data_todo", user_id=self.boss.id)
        self.env["res.config.settings"].create({}).action_reset_all_progress()
        self.env.cr.execute("SELECT id FROM bf_gamification_reward_claim WHERE id IN %s",
                            ((leave.id, other.id),))
        self.assertEqual([row[0] for row in self.env.cr.fetchall()], [leave.id])
        self.env.cr.execute("SELECT count(*) FROM mail_activity WHERE res_model = %s AND res_id = %s",
                            ("bf.gamification.reward.claim", other.id))
        self.assertEqual(self.env.cr.fetchone()[0], 0, "pas d'activité orpheline")

    # ------------------------------------------------------------------
    # À part (durcissements, 1)
    # ------------------------------------------------------------------

    def test_without_fox_quest_managers_an_administrator_decides(self):
        # Une société où personne n'est gestionnaire Fox Quest.
        self.env.ref("bf_gamification.group_gamification_manager").users = [(5, 0, 0)]
        self._allow_bridge_approval()
        claim = self._constance_claim()
        claim._bf_ask_approval()
        approver = claim.activity_ids.user_id
        self.assertEqual(len(approver), 1)
        self.assertNotEqual(approver, self.user)
        self.assertIn(self.env.ref("base.group_system"), approver.groups_id)
        claim.with_user(approver).action_approve()
        self.assertEqual(claim.state, "approved")

    def test_with_nobody_to_decide_the_request_waits_and_says_so(self):
        claim = self._constance_claim()
        self.patch(type(claim), "_bf_leave_approver", lambda record: self.env["res.users"])
        claim._bf_ask_approval()
        self.assertFalse(claim.activity_ids)
        self.assertIn("attend", claim.message_ids[:1].body)

    def test_only_an_administrator_ticks_out_of_fox_quest(self):
        # Garde de non-régression (corrigé à la note, pas au code) : un
        # gestionnaire Fox Quest ne sort personne du jeu, ni soi-même.
        for actor in (self.boss, self.user):
            with self.subTest(actor=actor.login), self.assertRaises(AccessError):
                self.user.with_user(actor).write({"bf_gamification_excluded": True})

    def _folded_stage_task(self, deadline):
        task = self._task(deadline)
        folded = self.env["project.task.type"].create({
            "name": "FQ fermée", "fold": True, "project_ids": [(4, task.project_id.id)]})
        task.write({"stage_id": folded.id, "state": "01_in_progress"})
        return task

    def test_a_task_in_a_folded_stage_is_not_overdue(self):
        with freeze_time("2026-10-12 15:00:00"):
            task = self._folded_stage_task(datetime(2026, 10, 9, 20, 0))
            self.assertEqual(task.state, "01_in_progress")
            self.assertEqual(self.Profile._bf_pending_count(self.user, "task"), 0)
            self.assertFalse(task._bf_fq_overdue_for(self.user))

    def test_the_task_fox_needs_a_closure(self):
        with freeze_time("2026-10-12 15:00:00"):
            before = len(self._fox_messages())
            postponed = self._task(datetime(2026, 10, 9, 20, 0))
            postponed.with_user(self.user).sudo().write({"date_deadline": datetime(2026, 10, 20, 20, 0)})
            left = self._task(datetime(2026, 10, 9, 20, 0))
            left.with_user(self.user).sudo().write({"user_ids": [(3, self.user.id)]})
            self.assertEqual(len(self._fox_messages()), before, "repousser ou se retirer n'est pas fermer")
            closed = self._task(datetime(2026, 10, 9, 20, 0))
            closed.with_user(self.user).sudo().write({"state": "1_done"})
            self.assertEqual(len(self._fox_messages()), before + 1)

    def test_a_task_created_the_same_day_pays_no_deadline_bonus(self):
        with freeze_time("2026-10-08 15:00:00"):
            task = self._task(datetime(2026, 10, 9, 20, 0))
            self._created_on(task, "2026-10-08 13:00:00")
            task.with_user(self.user).sudo().write({"state": "1_done"})
        self.assertEqual(self._txns(source="task").mapped("xp_amount"),
                         [self.env.ref("bf_gamification.xp_rule_task_complete").xp_amount])

    def test_a_manager_cannot_read_someone_s_caught_up_days(self):
        self.env["bf.gamification.caught.up"].sudo().create(
            {"user_id": self.user.id, "kind": "activity", "day": THU})
        CaughtUp = self.env["bf.gamification.caught.up"]
        self.assertFalse(CaughtUp.with_user(self.boss).search([("user_id", "=", self.user.id)]))
        self.assertTrue(CaughtUp.with_user(self.user).search([("user_id", "=", self.user.id)]))

    # ------------------------------------------------------------------
    # Durcissements (2)
    # ------------------------------------------------------------------

    def test_t2_1_a_context_default_never_reaches_the_ledger(self):
        # Un `default_date` passé par RPC à un geste antidatait l'XP et fabriquait
        # des jours de constance, jusqu'au palier. Toutes les portes du journal :
        # le geste, la constance, le badge, la récompense et son remboursement.
        badge = self.env["bf.gamification.badge"].create({"name": "FQ badge T2-1", "xp_reward": 2})
        forged = {"default_date": "2026-10-05 15:00:00", "default_from_request": True,
                  "default_reference": "bf.gamification.badge,%d" % badge.id}
        now = datetime(2026, 10, 8, 15, 0)
        with freeze_time(now):
            for stamp in ("2026-10-05 15:00:00", "2026-10-06 15:00:00", "2026-10-07 15:00:00"):
                activity = self._activity(self.user, THU)
                activity.with_user(self.user).with_context(default_date=stamp).action_done()
            self.assertEqual((self.profile.current_streak, self.profile.last_activity_date),
                             (1, THU), "aucun jour fabriqué")
            profile = self.profile.with_context(**forged)
            profile._award_xp(1, "manual", "geste")
            profile._grant_badge(badge)
            claim = self.env["bf.gamification.reward.claim"].with_user(self.user).with_context(
                **forged).create({"reward_id": self._free_reward(xp_cost=1).id})
            claim.with_user(self.boss).with_context(**forged).action_refuse()
        txns = self._txns()
        self.assertTrue({"activity", "manual", "badge", "reward"} <= set(txns.mapped("source")))
        self.assertEqual(set(txns.mapped("date")), {now})
        self.assertFalse([t.reference for t in txns if t.source in ("activity", "manual") and t.reference])
        self.assertFalse(txns.filtered(lambda t: t.source == "reward" and t.xp_amount > 0).from_request,
                         "le remboursement par la gestionnaire n'est pas un geste de la personne")

    def test_t2_2_a_manager_neither_backdates_nor_rewrites_nor_erases_the_ledger(self):
        Txn = self.env["bf.gamification.xp.transaction"].with_user(self.boss)
        with freeze_time("2026-10-08 15:00:00"):
            made = Txn.create({"user_id": self.user.id, "xp_amount": 5, "source": "message",
                               "date": "2026-10-05 15:00:00", "from_request": True})
        self.assertEqual((made.date, made.from_request), (datetime(2026, 10, 8, 15, 0), False))
        for vals in ({"user_id": self.boss.id}, {"date": "2026-10-05 15:00:00"},
                     {"from_request": True}, {"source": "task"}, {"xp_amount": 99},
                     {"reference": "bf.gamification.badge,1"}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                made.with_user(self.boss).write(vals)
        made.with_user(self.boss).write({"description": "libellé corrigé"})
        with self.assertRaises(AccessError):
            made.with_user(self.boss).unlink()
        self.assertTrue(made.exists())
        # Sans le dire, une transaction n'est pas née d'un geste.
        self.assertFalse(self.Txn.create({"user_id": self.user.id, "xp_amount": 1,
                                          "source": "manual"}).from_request)

    def test_t2_3_only_the_person_s_own_gesture_counts(self):
        record = self.env["meeting.record"].with_user(self.robot).sudo().create({
            "name": "FQ rencontre T2-3", "date": datetime(2026, 10, 7, 14, 0),
            "date_delay": 1.0, "organizer_id": self.user.id})
        project = self.env["project.project"].create({"name": "FQ projet T2-3"})
        task = self.env["project.task"].create({
            "name": "FQ tâche T2-3", "project_id": project.id, "user_ids": [(6, 0, self.user.ids)]})
        with freeze_time("2026-10-08 15:00:00"):
            # Dans LEUR requête : le robot des rencontres (XML-RPC) fait passer le
            # CR en « Révisé », un collègue ferme la tâche de la personne.
            record.with_user(self.robot).sudo().write({"report_state": "reviewed"})
            task.with_user(self.boss).sudo().write({"state": "1_done"})
        credited = self._txns()
        self.assertTrue({"meeting", "task"} <= set(credited.mapped("source")), "la personne est créditée")
        self.assertFalse(any(credited.mapped("from_request")))
        self.assertEqual((self.profile.current_streak, self.profile.last_activity_date), (0, False))
        with freeze_time("2026-10-08 16:00:00"):
            self.profile._award_xp(1, "manual", "son propre geste")
        self.assertEqual((self.profile.current_streak, self.profile.last_activity_date), (1, THU))

    def _inbox_row(self, subject):
        return self.env["bf.email"].sudo().create({
            "date": "2026-10-08 14:00:00", "email_from": "Acme <expediteur@ailleurs.test>",
            "email_to": "fq@exemple.test", "subject": subject, "direction": "in",
            "source": "chatter", "user_id": self.user.id, "body_preview": "Corps",
            "message_id_header": "<fq-t2-4-%s@test.invalid>" % subject.replace(" ", "-"),
        })

    def test_t2_4_the_mailbox_engines_bring_no_fox(self):
        # « Synchroniser maintenant », « Réappliquer les règles » : dans la requête
        # de la personne, mais ce que la règle ou l'ingestion traite n'est pas son geste.
        from odoo.addons.bf_email_management.models import bf_email as engine
        account = self.env["bf.email.account"].create({
            "name": "FQ boîte", "user_id": self.user.id, "host": "imap.exemple.test",
            "port": 993, "login": "fq@exemple.test", "password": "x"})
        Rule = self.env["bf.email.rule"].sudo()
        Rule.with_context(active_test=False).search([("user_id", "=", self.user.id)]).unlink()
        BfEmail = self.env["bf.email"].with_user(self.user)
        before = len(self._fox_messages())
        # Les règles : une vraie règle, réappliquée à la main par la personne.
        row = self._inbox_row("FQ tri")
        Rule.create({"name": "FQ tri", "scope": "user", "user_id": self.user.id, "match_type": "all",
                     "condition_ids": [(0, 0, {"kind": "condition", "field_name": "subject",
                                               "operator": "contains", "value": "FQ tri"})],
                     "set_handled": True})
        row.with_user(self.user).action_apply_rules_now()
        self.assertTrue(row.is_handled)
        self.assertEqual(self.Profile._bf_pending_count(self.user, "inbox"), 0)
        self.assertEqual(len(self._fox_messages()), before, "règles")

        # Ingestion et synchronisation : le cœur du moteur remplacé par un traitement.
        def handles(row):
            def run(model, *args, **kwargs):
                row.with_env(model.env).write({"is_handled": True})
            return run

        for method, call in (
                ("_sync_account", lambda: BfEmail._sync_account(account)),
                ("_cron_sync_emails", lambda: BfEmail._cron_sync_emails()),
                ("_ingest_rfc822", lambda: BfEmail._ingest_rfc822(b"", 1, "INBOX", account))):
            row = self._inbox_row("FQ %s" % method)
            self.patch(engine.BfEmail, method, handles(row))
            call()
            self.assertTrue(row.is_handled, method)
            self.assertEqual(self.Profile._bf_pending_count(self.user, "inbox"), 0, method)
            self.assertEqual(len(self._fox_messages()), before, method)
        # Le même traitement fait à la main par la personne, lui, fait sortir le renard.
        self._inbox_row("FQ à la main").with_user(self.user).write({"is_handled": True})
        self.assertEqual(len(self._fox_messages()), before + 1)

    def test_t2_5_tolerated_swallows_errors_but_not_concurrency(self):
        with mute_logger("odoo.addons.bf_gamification.models.fq_savepoint"):
            with fq_savepoint.tolerated("essai"):
                raise ValueError("avalée")
        for error in fq_savepoint.PG_CONCURRENCY_ERRORS:
            with self.subTest(error=error.__name__), self.assertRaises(error):
                with fq_savepoint.tolerated("essai"):
                    raise error("concurrence simulée")

    def test_t2_5_a_concurrency_error_goes_up_from_the_gestures(self):
        # Avalée par l'appelant, l'erreur de sérialisation laissait la transaction
        # avortée : l'usager voyait une erreur au lieu d'un rejeu.
        partner = self.env["res.partner"].create({"name": "FQ fil T2-5"})
        activity = self._activity(self.user, THU)
        task = self._task(datetime(2026, 10, 9, 20, 0))
        record = self.env["meeting.record"].sudo().create({
            "name": "FQ CR T2-5", "date": datetime(2026, 10, 8, 12, 0), "date_delay": 1.0,
            "organizer_id": self.user.id})
        project = self.env["project.project"].create({"name": "FQ projet T2-5"})
        agenda = self.env["meeting.agenda"].sudo().create({
            "name": "FQ OdJ T2-5", "project_id": project.id, "organizer_id": self.user.id,
            "date": datetime(2026, 10, 12, 15, 0)})
        row = self._inbox_row("FQ T2-5")

        def conflict(profile, *args, **kwargs):
            raise errors.SerializationFailure("concurrence simulée")
        self.patch(type(self.Profile), "_award_xp", conflict)
        gestures = (
            ("activité", lambda: activity.with_user(self.user).action_done()),
            ("tâche", lambda: task.with_user(self.user).sudo().write({"state": "1_done"})),
            ("message", lambda: partner.with_user(self.user).sudo().message_post(
                body="FQ", message_type="comment")),
            ("compte rendu", lambda: record.with_user(self.user).sudo().write(
                {"report_state": "sent", "report_sent_date": datetime(2026, 10, 8, 15, 0)})),
            ("ordre du jour", lambda: agenda.with_user(self.user).sudo().write(
                {"email_sent_date": datetime(2026, 10, 8, 15, 0)})),
            ("courriel", lambda: row.with_user(self.user).sudo().write({"status": "replied"})),
        )
        with freeze_time("2026-10-08 15:00:00"):
            for name, gesture in gestures:
                with self.subTest(gesture=name), self.assertRaises(errors.SerializationFailure), \
                        self.env.cr.savepoint():
                    gesture()

    def test_t2_5_no_caller_swallows_errors_on_its_own(self):
        # L'aide commune, et elle seule, décide de ce qui s'avale autour du code
        # de Fox Quest : un `except Exception` d'appelant avale aussi la concurrence.
        folder = Path(fq_savepoint.__file__).parent
        offenders = sorted(path.name for path in folder.glob("*.py")
                           if path.name != "fq_savepoint.py" and "except Exception" in path.read_text())
        self.assertEqual(offenders, [])

    def test_t2_7_a_manager_cannot_read_someone_s_caught_up_xp(self):
        Txn = self.env["bf.gamification.xp.transaction"]
        caught = Txn._bf_create_entry(self.user, 3, "caught_up", "À jour : Activités")
        work = Txn._bf_create_entry(self.user, 1, "message", "Message posté")
        seen = Txn.with_user(self.boss).search([("user_id", "=", self.user.id)])
        self.assertIn(work, seen)
        self.assertNotIn(caught, seen)
        self.assertIn(caught, Txn.with_user(self.user).search([("user_id", "=", self.user.id)]))

    # --- À part (durcissements, 2)

    def test_t2_the_list_is_not_counted_once_celebrated_today(self):
        counted = []
        real = type(self.Profile)._bf_pending_count

        def count(model, user, kind):
            counted.append(kind)
            return real(model, user, kind)
        self.patch(type(self.Profile), "_bf_pending_count", count)
        with freeze_time("2026-10-08 15:00:00"):
            self.env["bf.gamification.caught.up"].sudo().create(
                {"user_id": self.user.id, "kind": "inbox", "day": THU})
            self.Profile.with_user(self.user)._bf_caught_up_check(self.user, "inbox")
        self.assertEqual(counted, [], "déjà fêtée aujourd'hui : la boîte n'est pas recomptée")

    def test_t2_a_constance_request_is_never_deleted_by_hand(self):
        leave = self._constance_claim()
        with self.assertRaises(UserError):
            leave.with_user(self.boss).unlink()
        self.assertTrue(leave.exists())
        other = self.env["bf.gamification.reward.claim"].with_user(self.user).create(
            {"reward_id": self._free_reward().id})
        other.with_user(self.boss).unlink()   # une réclamation ordinaire, oui
        self.assertFalse(other.exists())

    def test_t2_approved_by_is_set_by_approving_only(self):
        self._allow_bridge_approval()
        leave = self._constance_claim()
        with self.assertRaises(UserError):
            leave.with_user(self.boss).write({"approved_by": self.boss.id})
        leave.with_user(self.boss).action_approve()
        self.assertEqual(leave.approved_by, self.boss)
        colleague = new_test_user(self.env, login="fq_collegue_t2_approb")
        with self.assertRaises(UserError):
            leave.with_user(self.boss).write({"approved_by": colleague.id})
        # « Approuver » écrit en sudo : il vérifie d'abord le droit d'écrire. Un
        # usager n'approuve pas sa propre réclamation ordinaire par RPC.
        own = self.env["bf.gamification.reward.claim"].with_user(self.user).create(
            {"reward_id": self._free_reward().id})
        with self.assertRaises(AccessError):
            own.with_user(self.user).action_approve()
        self.assertEqual((own.state, own.approved_by), ("pending", self.env["res.users"]))

    def test_t2_lowering_the_step_opens_one_request_at_a_time(self):
        self._enable_leave(step=60)
        self._opt_in()
        self._history(MON, TUE, WED, THU, FRI)
        with freeze_time("2026-10-09 20:00:00"):
            self.profile._bf_recompute_constance()
            self.assertFalse(self._claims())
            self.env["ir.config_parameter"].sudo().set_param("bf_gamification.leave_streak_days", "1")
            self.profile._bf_recompute_constance()
            self.assertEqual(len(self._claims()), 1, "une demande par passage, pas une rafale de cinq")
            self.profile._bf_recompute_constance()
        self.assertEqual(len(self._claims()), 2, "la suivante au passage suivant")

    def test_t2_the_streak_cron_isolates_each_profile(self):
        other = new_test_user(self.env, login="fq_cron_autre", name="Fox Autre", tz="America/Montreal")
        other_profile = self.Profile._get_or_create_profile(other)
        self._history(MON, TUE)
        self._history(MON, TUE, user=other)
        real = type(self.Profile)._bf_recompute_constance

        def recompute(profiles):
            if self.user in profiles.user_id:
                raise errors.SerializationFailure("concurrence simulée")
            return real(profiles)
        self.patch(type(self.Profile), "_bf_recompute_constance", recompute)
        with freeze_time("2026-10-06 20:00:00"), \
                mute_logger("odoo.addons.bf_gamification.models.fq_savepoint"):
            self.Profile._cron_update_streaks()
        self.assertEqual(other_profile.current_streak, 2, "le conflit d'un profil n'arrête pas les autres")

    # ------------------------------------------------------------------
    # Durcissements (3)
    # ------------------------------------------------------------------

    # La classe de défaut : ce que le code ne pose pas, le contexte de l'appelant
    # le remplit (`default_*`), même sous sudo et même quand la garde lit `vals`.

    def test_t3_1_a_context_default_never_decides_a_claim(self):
        # Un usager réclamait une récompense du catalogue déjà « approuvée », XP
        # débité, sans gestionnaire.
        self.profile._award_xp(10, "manual", "solde")
        reward = self._free_reward(xp_cost=5, category="time_off")
        forged = {"default_state": "approved", "default_approved_by": self.boss.id,
                  "default_date_processed": "2026-10-05 15:00:00", "default_leave_days": 3.0,
                  "default_refusal_reason": "forgé", "default_date_claimed": "2020-01-01 00:00:00"}
        with freeze_time("2026-10-08 15:00:00"):
            claim = self.env["bf.gamification.reward.claim"].with_user(self.user).with_context(
                **forged).create({"reward_id": reward.id})
        self.assertEqual((claim.state, claim.approved_by, claim.date_processed, claim.leave_days),
                         ("pending", self.env["res.users"], False, 0.0))
        self.assertFalse(claim.refusal_reason)
        self.assertEqual(claim.date_claimed, datetime(2026, 10, 8, 15, 0))
        self.assertEqual(claim.xp_spent, 5, "le prix payé est celui du catalogue")

    def test_t3_1_a_context_default_never_opts_a_profile_in(self):
        # Même classe : la garde des champs `leave_*` ne lit que `vals`.
        colleague = new_test_user(self.env, login="fq_collegue_t3_1")
        self._enable_leave()
        forged = {"default_leave_opt_in": True, "default_leave_progress": 59,
                  "default_leave_opt_in_date": "2026-01-01 00:00:00",
                  "default_leave_last_step_day": "2026-10-01"}
        profile = self.Profile.with_user(self.boss).with_context(**forged).create(
            {"user_id": colleague.id})
        sprofile = profile.sudo()
        self.assertEqual((sprofile.leave_opt_in, sprofile.leave_progress, sprofile.leave_opt_in_date,
                          sprofile.leave_last_step_day), (False, 0, False, False))

    def test_t3_1_a_context_default_never_flags_a_reward_as_constance(self):
        # Même classe : la garde du drapeau ne lit que `vals`.
        reward = self.env["bf.gamification.reward"].with_user(self.boss).with_context(
            default_is_constance_leave=True).create({"name": "FQ faux congé (contexte)", "xp_cost": 0})
        self.assertFalse(reward.is_constance_leave)

    def test_t3_2_the_constance_request_takes_nothing_from_the_callers_context(self):
        # Sortie du plafond annuel par un `default_date_claimed` ancien, ou déjà décidée.
        self._enable_leave(step=1)
        self.env["ir.config_parameter"].sudo().set_param("bf_gamification.leave_max_per_year", "1")
        self._opt_in()
        self._history(WED, THU)
        forged = {"default_date_claimed": "2020-01-01 00:00:00", "default_approved_by": self.boss.id,
                  "default_date_processed": "2020-01-02 00:00:00", "default_date": "2020-01-01 00:00:00"}
        with freeze_time("2026-10-09 20:00:00"):
            profile = self.profile.with_context(**forged)
            profile._bf_recompute_constance()
            profile._bf_recompute_constance()
        claim = self._claims()
        self.assertEqual(len(claim), 1, "la première demande compte au plafond de 1 par année")
        self.assertEqual((claim.state, claim.approved_by, claim.date_processed, claim.date_claimed),
                         ("pending", self.env["res.users"], False, datetime(2026, 10, 9, 20, 0)))
        self.assertEqual(set(claim.message_ids.mapped("date")), {datetime(2026, 10, 9, 20, 0)},
                         "la note de la demande n'est pas antidatée non plus")
        # L'activité de l'approbateur se nettoie aussi d'elle-même : le seul appelant
        # du module lui passe déjà un contexte propre, un appelant futur déjà en sudo
        # ne le ferait peut-être pas. La note, elle, `mail` la nettoie toujours
        # (`_message_create`) ; l'activité non : `default_active=False` la créait
        # archivée, et l'approbateur ne voyait jamais la demande.
        other = self._constance_claim()
        other.with_context(default_active=False, default_date="2020-01-01 00:00:00",
                           default_request_partner_id=self.user.partner_id.id)._bf_ask_approval()
        activity = self.env["mail.activity"].with_context(active_test=False).search([
            ("res_model", "=", other._name), ("res_id", "=", other.id)])
        self.assertEqual(len(activity), 1)
        self.assertEqual((activity.active, activity.request_partner_id), (True, self.env["res.partner"]),
                         "l'activité demandée par un appelant au contexte forgé reste visible et propre")

    def test_t3_2_a_badge_is_dated_now_whatever_the_context(self):
        badge = self.env["bf.gamification.badge"].create({"name": "FQ badge T3-2", "xp_reward": 0})
        with freeze_time("2026-10-08 15:00:00"):
            user_badge = self.profile.with_context(
                default_date_earned="2020-01-01 00:00:00")._grant_badge(badge)
        self.assertEqual(user_badge.date_earned, datetime(2026, 10, 8, 15, 0))

    def test_t3_3_a_support_session_makes_no_gesture_no_day_no_fox(self):
        self.patch(type(self.Profile), "_bf_is_interactive", self.real_is_interactive)
        in_a_request(self)
        self.assertFalse(self.Profile._bf_is_impersonated())
        self.assertTrue(self.Profile._bf_is_interactive())
        self.assertTrue(self.profile._bf_is_own_gesture(self.user))
        in_a_request(self, impersonated=True)
        self.assertTrue(self.Profile._bf_is_impersonated())
        self.assertFalse(self.Profile._bf_is_interactive())
        self.assertFalse(self.profile._bf_is_own_gesture(self.user))
        with freeze_time("2026-10-08 15:00:00"):
            activity = self._activity(self.user, THU)
            before = len(self._fox_messages())
            activity.with_user(self.user).action_done()
            self.profile._award_xp(1, "manual", "geste du support")
            self.assertEqual(len(self._fox_messages()), before, "pas de renard sous usurpation")
        self.assertFalse(self.env["bf.gamification.caught.up"].sudo().search([
            ("user_id", "=", self.user.id)]))
        self.assertTrue(self._txns(), "l'XP est crédité à la personne, comme avant")
        self.assertFalse(any(self._txns().mapped("from_request")))
        self.assertEqual((self.profile.current_streak, self.profile.last_activity_date), (0, False))
        # Revenue à elle-même, dans sa requête : le geste compte.
        in_a_request(self)
        with freeze_time("2026-10-08 16:00:00"):
            self.profile._award_xp(1, "manual", "son geste")
        self.assertEqual((self.profile.current_streak, self.profile.last_activity_date), (1, THU))

    def test_t3_3_nobody_takes_part_for_someone_under_impersonation(self):
        self.patch(type(self.Profile), "_bf_is_interactive", self.real_is_interactive)
        self._enable_leave()
        dashboard = self.env["bf.gamification.dashboard"].with_user(self.user)
        in_a_request(self, impersonated=True)
        with self.assertRaises(AccessError) as caught:
            dashboard.set_leave_opt_in(True)
        self.assertIn("usurpation", str(caught.exception))
        self.assertFalse(self.profile.sudo().leave_opt_in)
        # La lecture du tableau de bord, elle, reste permise.
        data = dashboard.get_dashboard_data()
        self.assertTrue(data["constance"]["leave"]["enabled"])
        self.assertFalse(data["constance"]["leave"]["opt_in"])
        # Retirer la participation d'une personne n'est pas permis non plus.
        in_a_request(self)
        dashboard.set_leave_opt_in(True)
        self.assertTrue(self.profile.sudo().leave_opt_in)
        in_a_request(self, impersonated=True)
        with self.assertRaises(AccessError):
            dashboard.set_leave_opt_in(False)
        self.assertTrue(self.profile.sudo().leave_opt_in)

    def test_t3_3_a_constance_request_is_not_decided_under_impersonation(self):
        self._allow_bridge_approval()
        Claim = self.env["bf.gamification.reward.claim"]
        leave = self._constance_claim()
        in_a_request(self, impersonated=True)
        with self.assertRaises(AccessError) as caught:
            leave.with_user(self.boss).action_approve()
        self.assertIn("usurpation", str(caught.exception))
        # Sudo ne contourne pas la garde : elle lit la requête, pas les droits.
        with self.assertRaises(AccessError):
            leave.action_refuse()
        # Ni l'écriture de l'état, qui est une décision aussi.
        for state in ("approved", "refused", "consumed"):
            with self.subTest(state=state), self.assertRaises(AccessError), self.env.cr.savepoint():
                leave.with_user(self.boss).write({"state": state})
        self.assertEqual(leave.state, "pending")
        # Une réclamation ordinaire ne se décide pas sous une autre règle.
        ordinary = Claim.with_user(self.user).create({"reward_id": self._free_reward().id})
        ordinary.with_user(self.boss).action_approve()
        self.assertEqual(ordinary.state, "approved")
        # Revenue à son propre compte, la gestionnaire décide.
        in_a_request(self)
        leave.with_user(self.boss).action_approve()
        self.assertEqual(leave.state, "approved")

    def test_t3_4_a_manager_adds_no_avatar_parts_to_the_constance_reward(self):
        # `bf_avatar_gamification` approuve en sudo toute réclamation dont la
        # récompense porte des pièces d'avatar : sur le congé de constance, chaque
        # demande ouverte par le palier serait approuvée sans personne.
        Reward = self.env["bf.gamification.reward"]
        if "avatar_style" not in Reward._fields:
            self.skipTest("bf_avatar_gamification n'est pas installé")
        style = Reward._fields["avatar_style"].selection[0][0]
        constance = self.env.ref("bf_gamification.reward_leave_constance")
        for vals in ({"avatar_style": style}, {"avatar_parts": False}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                constance.with_user(self.boss).write(vals)
        self.assertFalse(constance.avatar_style)
        # Une récompense ordinaire garde ses pièces d'avatar.
        self._free_reward().with_user(self.boss).write({"avatar_style": style})

    # --- À part

    def test_t3_the_mailbox_engine_wrappers_pass_any_signature_through(self):
        # `bf_email_management` change la signature de ces moteurs sans cesse.
        from odoo.addons.bf_gamification.models import bf_email as ours
        BfEmail = self.env["bf.email"].with_user(self.user)
        # L'enveloppe de Fox Quest appelée seule (d'autres modules, plus haut dans la
        # chaîne, en posent une à signature figée), et sous elle la classe suivante
        # qui définit le moteur.
        chain = type(BfEmail).__mro__
        below = chain[chain.index(ours.BfEmail) + 1:]
        calls = []

        def record(name):
            def run(model, *args, **kwargs):
                calls.append((name, args, kwargs, model.env.context.get("bf_gamification_no_celebration")))
            return run

        for name, args, kwargs in (
                ("_apply_rules", (False, None, "neuf"), {"extra": 1}),
                ("_sync_account", ("compte", "neuf"), {"extra": 1}),
                ("_cron_sync_emails", ("neuf",), {"extra": 1}),
                ("_ingest_rfc822", (b"", 1, "INBOX", "compte", "neuf"), {"extra": 1})):
            self.patch(next(klass for klass in below if name in vars(klass)), name, record(name))
            getattr(ours.BfEmail, name)(BfEmail, *args, **kwargs)
            self.assertEqual(calls[-1], (name, args, kwargs, True), name)

    def test_t3_zero_is_not_unlimited_in_the_settings(self):
        # Odoo ne garde pas un 0 dans un paramètre : le défaut revient. Les aides le disent.
        settings = self.env["res.config.settings"].create({
            "gamification_leave_max_per_year": 0, "gamification_leave_streak_days": 0,
            "gamification_leave_days_per_step": 0})
        # Le `set_values` du cœur, sans ceux des autres modules (OnlyOffice appelle
        # son serveur à chaque enregistrement).
        ResConfigSettings.set_values(settings)
        settings_now = self.Profile._bf_leave_settings()
        settings_now.pop("enabled")   # l'interrupteur est celui de la base, pas de l'essai
        self.assertEqual(settings_now, {"step": 60, "days_per_step": 1.0, "max_per_year": 5})
        for name in ("gamification_leave_max_per_year", "gamification_leave_streak_days",
                     "gamification_leave_days_per_step"):
            help_text = settings._fields[name].help
            self.assertNotIn("0 = sans plafond", help_text, name)
            self.assertIn("remet", help_text, name)
