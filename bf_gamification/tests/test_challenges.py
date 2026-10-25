"""Défis du jour, carte à tampons, titres portés et prochains objectifs.

Les dates sont figées sur une semaine connue d'octobre 2026 :
lundi 5, mardi 6, ..., vendredi 9, samedi 10, dimanche 11.
Les usagers d'essai sont dans le fuseau de Montréal (UTC-4 en octobre).
"""
from collections import Counter
from datetime import date, datetime

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_gamification.models import gamification_challenge as challenges
from odoo.addons.base.models.res_config import ResConfigSettings
from odoo.addons.bf_gamification.models.gamification_constance import NO_CELEBRATION
from odoo.addons.bf_gamification.models.gamification_profile import CONSTANCE_EXCLUDED_SOURCES

from .common import in_a_request

THU, FRI, SAT = date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 10)
THU_NOON = "2026-10-08 15:00:00"
FRI_NOON = "2026-10-09 15:00:00"
SAT_NOON = "2026-10-10 15:00:00"


@tagged("post_install", "-at_install")
class TestChallenges(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.user = new_test_user(cls.env, login="fq_defi", name="Fox Defi", tz="America/Montreal")
        cls.colleague = new_test_user(cls.env, login="fq_defi_coll", name="Fox Collegue",
                                      tz="America/Montreal")
        cls.boss = new_test_user(
            cls.env, login="fq_defi_boss", name="Fox Boss", tz="America/Montreal",
            groups="base.group_user,bf_gamification.group_gamification_manager")
        cls.admin = new_test_user(cls.env, login="fq_defi_admin", name="Fox Admin",
                                  tz="America/Montreal", groups="base.group_user,base.group_system")
        cls.robot = new_test_user(cls.env, login="fq_defi_robot", name="Fox Robot")
        cls.robot.bf_gamification_excluded = True
        cls.Profile = cls.env["bf.gamification.profile"]
        cls.Txn = cls.env["bf.gamification.xp.transaction"].sudo()
        cls.Line = cls.env["bf.gamification.challenge.line"].sudo()
        cls.Challenge = cls.env["bf.gamification.challenge"]
        # Un calendrier lundi-vendredi pour tout le monde.
        calendar = cls.env["resource.calendar"].create({"name": "Semaine FQ défis"})
        for user in (cls.user, cls.colleague, cls.boss, cls.admin):
            cls.env["hr.employee"].create({
                "name": user.name, "user_id": user.id, "resource_calendar_id": calendar.id})
        # Un jeu de défis connu : le catalogue livré n'entre pas dans les essais.
        cls.Challenge.search([]).write({"active": False})
        cls.c_task = cls.Challenge.create({
            "name": "FQ deux tâches", "source": "task", "target": 2, "xp_reward": 10, "sequence": 1})
        cls.c_time = cls.Challenge.create({
            "name": "FQ une heure", "source": "timesheet", "target": 1, "xp_reward": 8, "sequence": 2})
        cls.c_ticket = cls.Challenge.create({
            "name": "FQ un ticket", "source": "helpdesk", "target": 1, "xp_reward": 6, "sequence": 3})
        cls.pool = cls.c_task | cls.c_time | cls.c_ticket

    def setUp(self):
        super().setUp()
        # Un TransactionCase n'a pas de requête HTTP : sans ce remplacement, chaque
        # geste des essais passerait pour un cron.
        self.interactive = True
        self.real_is_interactive = type(self.Profile)._bf_is_interactive
        self.patch(type(self.Profile), "_bf_is_interactive", lambda model: self.interactive)
        self.profile = self.Profile.with_user(self.user)._get_or_create_profile(self.user)

    # ------------------------------------------------------------------
    # Aides
    # ------------------------------------------------------------------

    def _lines(self, user=None, day=THU):
        return self.Line.search([("user_id", "=", (user or self.user).id), ("day", "=", day)],
                                order="slot")

    def _issue(self, user=None, stamp=THU_NOON):
        with freeze_time(stamp):
            return self.Profile._bf_ensure_challenges(user or self.user)

    def _line(self, challenge, user=None, day=THU):
        return self._lines(user, day).filtered(lambda line: line.challenge_id == challenge)

    def _task(self):
        return self.env["project.task"].sudo().create({"name": "FQ tâche"})

    def _gesture(self, source, amount=3, ref=None, stamp=THU_NOON, profile=None):
        with freeze_time(stamp):
            (profile or self.profile)._award_xp(amount, source, "geste %s" % source, reference=ref)

    def _challenge_txns(self, user=None):
        return self.Txn.search([("user_id", "=", (user or self.user).id),
                                ("source", "=", "challenge")], order="id")

    def _events(self, kind):
        # Odoo 18 garde les messages du bus en attente jusqu'à la validation : on lit la
        # file et ce qu'un vidage a déjà écrit. La table peut aussi porter des lignes
        # validées avant l'essai (un banc où l'on a joué un parcours) : les essais comparent
        # donc avant et après (`_new_events`), jamais « le dernier ».
        pending = self.env.cr.precommit.data.get("bus.bus.values", [])
        written = self.env["bus.bus"].sudo().search([("message", "ilike", kind)]).mapped("message")
        return [v["message"] for v in pending if kind in v["message"]] + list(written)

    def _new_events(self, kind, before):
        return list((Counter(self._events(kind)) - Counter(before)).elements())

    # ------------------------------------------------------------------
    # Émission
    # ------------------------------------------------------------------

    def test_three_challenges_are_issued_on_a_working_day(self):
        lines = self._issue()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines.challenge_id, self.pool)
        self.assertEqual(lines.mapped("slot"), [1, 2, 3])
        for line in lines:
            factor = challenges.FIRST_SLOT_FACTOR if line.slot == 1 else 1
            self.assertEqual(line.xp_reward, line.challenge_id.xp_reward * factor, line.slot)
            self.assertEqual((line.source, line.target),
                             (line.challenge_id.source, line.challenge_id.target))
            self.assertFalse(line.done)

    def test_issuing_twice_changes_nothing(self):
        first = self._issue()
        second = self._issue()
        self.assertEqual(first, second)
        self.assertEqual(len(self._lines()), 3)

    def test_no_challenge_on_a_weekend(self):
        self.assertFalse(self._issue(stamp=SAT_NOON))
        with freeze_time(SAT_NOON):
            data = self.Profile._bf_challenges_data(self.user)
        self.assertTrue(data["rest_day"])
        self.assertFalse(data["lines"])

    def test_no_challenge_on_a_public_holiday(self):
        self.env["resource.calendar.leaves"].create({
            "name": "Férié d'essai",
            "date_from": datetime(2026, 10, 8, 4, 0),   # minuit à Montréal
            "date_to": datetime(2026, 10, 9, 4, 0),
            "calendar_id": False,
            "resource_id": False,
        })
        self.assertFalse(self._issue())

    def test_nothing_when_the_company_turns_challenges_off(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_gamification.challenges_enabled", "False")
        self.assertFalse(self._issue())
        with freeze_time(THU_NOON):
            data = self.Profile._bf_challenges_data(self.user)
        self.assertFalse(data["enabled"])
        self.assertFalse(data["lines"])

    def test_a_service_account_gets_no_challenge(self):
        self.assertFalse(self._issue(self.robot))

    def test_the_draw_is_deterministic_for_a_person_and_a_day(self):
        pool = self.pool
        for index in range(5):
            pool |= self.Challenge.create({
                "name": "FQ de plus %d" % index, "source": "document", "target": 1,
                "xp_reward": 5, "sequence": 10 + index})
        first = self._issue().challenge_id
        self.Line.search([("user_id", "=", self.user.id)]).unlink()
        self.assertEqual(self._issue().challenge_id, first)

    def test_yesterdays_challenges_are_not_repeated_while_others_remain(self):
        for index in range(5):
            self.Challenge.create({
                "name": "FQ de plus %d" % index, "source": "document", "target": 1,
                "xp_reward": 5, "sequence": 10 + index})
        today = self._issue(stamp=THU_NOON).challenge_id
        tomorrow = self._issue(stamp=FRI_NOON).challenge_id
        self.assertEqual(len(tomorrow), 3)
        self.assertFalse(today & tomorrow)

    def test_a_small_pool_gives_fewer_than_three(self):
        (self.c_ticket | self.c_time).write({"active": False})
        self.assertEqual(len(self._issue()), 1)

    def test_an_empty_pool_gives_nothing(self):
        self.pool.write({"active": False})
        self.assertFalse(self._issue())

    def test_only_challenges_the_person_can_do_are_offered(self):
        # Une personne ordinaire n'écrit pas une société ; un administrateur, si.
        locked = self.Challenge.create({
            "name": "FQ réservé", "source": "document", "model_name": "res.company",
            "target": 1, "xp_reward": 5, "sequence": 4})
        with freeze_time(THU_NOON):
            plain = self.Profile._bf_challenge_pool(self.user)
            admin = self.Profile._bf_challenge_pool(self.admin)
        self.assertNotIn(locked, plain)
        self.assertIn(locked, admin)
        self.assertEqual(plain, self.pool)

    def test_a_challenge_needs_a_real_positive_target_and_a_model_that_exists(self):
        with self.assertRaises(ValidationError):
            self.Challenge.create({"name": "FQ zéro", "source": "task", "target": 0})
        with self.assertRaises(ValidationError):
            self.Challenge.create({"name": "FQ trop", "source": "task", "xp_reward": 100000})
        with self.assertRaises(ValidationError):
            self.Challenge.create({"name": "FQ fantôme", "source": "task", "model_name": "x.y"})

    def test_messages_are_not_a_challenge_source(self):
        self.assertNotIn("message", dict(challenges.CHALLENGE_SOURCES))
        self.assertIn("message", challenges.CHALLENGE_EXCLUDED_SOURCES)
        self.assertNotIn("message", dict(self.Challenge._fields["source"].selection))

    def test_editing_the_catalogue_does_not_move_a_started_day(self):
        self._issue()
        line = self._line(self.c_task)
        self.c_task.write({"target": 9, "xp_reward": 99})
        self.assertEqual((line.target, line.xp_reward // (2 if line.slot == 1 else 1)), (2, 10))

    def test_an_archived_challenge_keeps_todays_lines(self):
        before = self._issue()
        self.c_ticket.write({"active": False})
        self.assertEqual(self._issue(), before)

    def test_two_simultaneous_issues_do_not_fight(self):
        # La barre du haut et le tableau de bord appellent ensemble : le perdant ne voit
        # pas les lignes du gagnant au moment de chercher, puis butte sur l'unicité.
        self._issue()
        Line = type(self.Line)
        real_search = Line.search
        calls = []

        def blind_search(model, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return model.browse()
            return real_search(model, *args, **kwargs)

        self.patch(Line, "search", blind_search)
        lines = self._issue()
        self.assertEqual(len(lines), 3)
        self.assertEqual(len(self._lines()), 3)

    # ------------------------------------------------------------------
    # Progression
    # ------------------------------------------------------------------

    def test_enough_gestures_complete_a_challenge_and_pay_once(self):
        self._issue()
        line = self._line(self.c_task)
        self._gesture("task", ref=self._task())
        self.assertFalse(line.done, "un seul geste sur deux")
        self.assertFalse(self._challenge_txns())
        self._gesture("task", ref=self._task())
        line.invalidate_recordset()
        self.assertTrue(line.done)
        paid = self._challenge_txns()
        self.assertEqual(len(paid), 1)
        self.assertEqual(paid.xp_amount, line.xp_reward)
        self.assertEqual(paid.reference, line)
        self.assertTrue(paid.from_request)
        self._gesture("task", ref=self._task())
        self.assertEqual(len(self._challenge_txns()), 1, "déjà accompli : rien de plus")
        self.assertEqual(self.profile.sudo().stamp_count, 1)

    def test_a_task_that_pays_twice_counts_as_one_gesture(self):
        # Fermer une tâche à temps verse « complétée » ET « avant l'échéance ».
        self._issue()
        line = self._line(self.c_task)
        task = self._task()
        self._gesture("task", ref=task)
        self._gesture("task", amount=12, ref=task)
        line.invalidate_recordset()
        self.assertFalse(line.done)

    def test_the_first_gesture_of_the_day_issues_the_day_and_counts(self):
        self.assertFalse(self._lines())
        self._gesture("timesheet", ref=None)
        line = self._line(self.c_time)
        self.assertTrue(line.done, "le geste qui ouvre la journée compte aussi")

    def test_yesterdays_gestures_do_not_count_today(self):
        self._issue(stamp=THU_NOON)
        self._gesture("timesheet", stamp="2026-10-07 15:00:00")
        self.assertFalse(self._line(self.c_time).done)

    def test_the_day_is_the_persons_day(self):
        # 03 h 30 UTC vendredi = 23 h 30 jeudi à Montréal : encore le jeudi de la personne.
        self._issue()
        self._gesture("timesheet", stamp="2026-10-09 03:30:00")
        self.assertTrue(self._line(self.c_time).done)
        self._issue(stamp=FRI_NOON)
        self.assertFalse(self._line(self.c_time, day=FRI).done)

    def test_a_cron_a_colleague_or_the_support_do_not_move_a_challenge(self):
        self._issue()
        line = self._line(self.c_time)
        # Un cron ou un import : pas de requête.
        self.interactive = False
        self._gesture("timesheet")
        line.invalidate_recordset()
        self.assertFalse(line.done, "cron")
        self.interactive = True
        # Un collègue qui crédite la personne : sa requête n'est pas celle de la personne.
        self._gesture("timesheet", profile=self.Profile.with_user(self.colleague)
                      ._get_or_create_profile(self.user))
        line.invalidate_recordset()
        self.assertFalse(line.done, "collègue")
        # Le support qui voit l'écran de la personne : la vraie garde, pas le remplacement.
        self.patch(type(self.Profile), "_bf_is_interactive", self.real_is_interactive)
        in_a_request(self, impersonated=True)
        self._gesture("timesheet")
        line.invalidate_recordset()
        self.assertFalse(line.done, "usurpation")

    def test_messages_and_fox_quests_own_xp_never_count(self):
        self._issue()
        for _ in range(12):
            self._gesture("message", amount=1)
        for source in ("badge", "streak", "reward", "manual", "challenge"):
            self._gesture(source, amount=5)
        self.assertFalse(self._lines().filtered("done"))
        self.assertEqual(len(self._challenge_txns()), 1, "seulement celle que le test a versée")

    def test_a_challenge_pays_only_once_even_when_completed_twice(self):
        self._issue()
        line = self._line(self.c_time)
        with freeze_time(THU_NOON):
            self.assertIsNotNone(self.profile._bf_complete_challenge(line))
            self.assertIsNone(self.profile._bf_complete_challenge(line), "déjà accompli ailleurs")
        self.assertEqual(len(self._challenge_txns()), 1)
        self.assertEqual(self.profile.sudo().stamp_count, 1)

    def test_a_negative_line_is_not_a_gesture(self):
        # Ce qu'on reprend (montant négatif) n'est pas un geste de travail. La ligne négative
        # d'abord, puis un vrai geste : c'est ce geste qui recompte la journée.
        self._issue()
        line = self._line(self.c_task)
        with freeze_time(THU_NOON):
            self.env["bf.gamification.xp.transaction"]._bf_create_entry(
                self.user, -5, "task", "reprise", reference=self._task(), from_request=True)
        self._gesture("task", ref=self._task())
        line.invalidate_recordset()
        self.assertFalse(line.done, "une ligne négative et un geste : un seul geste")

    def test_a_gesture_credited_by_a_cron_does_not_add_to_the_count(self):
        # Le crédit d'un cron précède un geste propre : les deux ne font pas deux gestes.
        self._issue()
        line = self._line(self.c_task)
        self.interactive = False
        self._gesture("task", ref=self._task())
        self.interactive = True
        self._gesture("task", ref=self._task())
        line.invalidate_recordset()
        self.assertFalse(line.done, "un seul geste de la personne sur deux")

    def test_yesterdays_gesture_does_not_add_to_todays_count(self):
        self._issue()
        line = self._line(self.c_task)
        self.Txn.create({
            "user_id": self.user.id, "xp_amount": 8, "source": "task", "description": "hier",
            "date": "2026-10-07 15:00:00", "from_request": True,
            "reference": "project.task,%d" % self._task().id})
        self._gesture("task", ref=self._task())
        line.invalidate_recordset()
        self.assertFalse(line.done, "un geste d'hier et un d'aujourd'hui : un seul aujourd'hui")

    def test_a_caught_up_challenge_does_not_tell_managers_which_list_was_emptied(self):
        # Les gestionnaires ne lisent pas les journées « à jour » ; le journal de défis, lui, leur
        # est ouvert : il ne nomme pas ce que ce défi-là récompense.
        self.pool.write({"active": False})
        clear = self.Challenge.create({
            "name": "FQ vider une liste", "source": "caught_up", "target": 1, "xp_reward": 12})
        self._issue()
        self.assertTrue(self._line(clear))
        self._gesture("caught_up")
        paid = self._challenge_txns()
        self.assertEqual(len(paid), 1)
        self.assertNotIn("vider", paid.description.lower())
        self.assertNotIn(clear.name, paid.description)

    def test_a_challenge_worth_nothing_writes_no_journal_line(self):
        self.c_time.write({"xp_reward": 0})
        self._issue()
        self._gesture("timesheet")
        self.assertTrue(self._line(self.c_time).done)
        self.assertFalse(self._challenge_txns(), "0 XP : rien au journal")
        self.assertEqual(self.profile.sudo().stamp_count, 1, "le tampon, lui, est donné")

    def test_completion_notifies_the_person_once(self):
        self._issue()
        before = self._events("bf_gamification/challenge_done")
        self._gesture("timesheet")
        new = self._new_events("bf_gamification/challenge_done", before)
        self.assertEqual(len(new), 1)
        self.assertIn("FQ une heure", new[0])

    def test_no_notification_when_popups_are_off(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_gamification.popup_enabled", "False")
        self._issue()
        before = self._events("bf_gamification/challenge_done")
        self._gesture("timesheet")
        self.assertFalse(self._new_events("bf_gamification/challenge_done", before))
        self.assertTrue(self._line(self.c_time).done)

    def test_the_challenge_xp_does_not_make_a_constance_day(self):
        self.assertIn("challenge", CONSTANCE_EXCLUDED_SOURCES)
        marks = []
        self.patch(type(self.Profile), "_bf_mark_active_day", lambda model: marks.append(1))
        with freeze_time(THU_NOON):
            self.profile._award_xp(5, "challenge", "à la main")
        self.assertFalse(marks)

    # ------------------------------------------------------------------
    # Tampons et carte
    # ------------------------------------------------------------------

    def test_five_stamps_complete_a_card_that_pays_and_starts_over(self):
        self._issue()
        for source in ("timesheet", "helpdesk"):
            self._gesture(source)
        self._gesture("task", ref=self._task())
        self._gesture("task", ref=self._task())
        profile = self.profile.sudo()
        self.assertEqual((profile.stamp_count, profile.cards_completed), (3, 0))
        self._issue(stamp=FRI_NOON)
        self._gesture("timesheet", stamp=FRI_NOON)
        self.assertEqual(profile.stamp_count, 4)
        before = self._events("bf_gamification/challenge_done")
        self._gesture("helpdesk", stamp=FRI_NOON)
        profile.invalidate_recordset()
        self.assertEqual((profile.stamp_count, profile.cards_completed), (0, 1))
        card = self._challenge_txns().filtered(lambda t: "Carte" in t.description)
        self.assertEqual(card.xp_amount, 25)
        new = self._new_events("bf_gamification/challenge_done", before)
        self.assertEqual(len(new), 1)
        self.assertIn('"card_complete":true', new[0])

    # ------------------------------------------------------------------
    # Ce que voit, et ne peut pas faire, la personne
    # ------------------------------------------------------------------

    def test_the_dashboard_lists_todays_challenges_with_their_progress(self):
        self._issue()
        self._gesture("task", ref=self._task())
        with freeze_time(THU_NOON):
            data = self.Profile._bf_challenges_data(self.user)
        self.assertTrue(data["enabled"])
        self.assertEqual(len(data["lines"]), 3)
        self.assertEqual(data["per_card"], challenges.STAMPS_PER_CARD)
        task = next(item for item in data["lines"] if item["name"] == "FQ deux tâches")
        self.assertEqual((task["progress"], task["target"], task["done"]), (1, 2, False))
        self.assertEqual([item["slot"] for item in data["lines"]], [1, 2, 3])
        self.assertEqual([item["bonus"] for item in data["lines"]], [True, False, False])
        self.assertFalse(data["all_done"])

    def test_all_done_when_every_challenge_is_done(self):
        self._issue()
        self._gesture("timesheet")
        with freeze_time(THU_NOON):
            partial = self.Profile._bf_challenges_data(self.user)
        self.assertFalse(partial["all_done"], "un défi sur trois : pas tout accompli")
        self._gesture("helpdesk")
        self._gesture("task", ref=self._task())
        self._gesture("task", ref=self._task())
        with freeze_time(THU_NOON):
            data = self.Profile._bf_challenges_data(self.user)
        self.assertTrue(data["all_done"])
        self.assertTrue(all(item["done"] and item["progress"] == item["target"]
                            for item in data["lines"]))

    def test_the_dashboard_survives_a_failing_challenge_block(self):
        def boom(model, user):
            raise ValueError("boum")

        self.patch(type(self.Profile), "_bf_challenges_data", boom)
        with freeze_time(THU_NOON):
            data = self.env["bf.gamification.dashboard"].with_user(self.user).get_dashboard_data()
        self.assertFalse(data["challenges"]["enabled"])
        self.assertIn("next_goals", data)
        self.assertIn("profile", data)

    def test_nobody_writes_a_line_by_hand(self):
        self._issue()
        line = self._lines()[:1]
        for who in (self.user, self.boss, self.admin):
            with self.assertRaises(AccessError, msg=who.login):
                line.with_user(who).write({"done": True})
            with self.assertRaises(AccessError, msg=who.login):
                line.with_user(who).unlink()
            with self.assertRaises(AccessError, msg=who.login):
                self.env["bf.gamification.challenge.line"].with_user(who).create({
                    "user_id": who.id, "day": THU, "slot": 1, "challenge_id": self.c_task.id,
                    "source": "task", "target": 1, "xp_reward": 999})

    def test_only_the_person_reads_her_lines(self):
        self._issue()
        self.assertEqual(len(self.Line.with_user(self.user).search([("user_id", "=", self.user.id)])), 3)
        for other in (self.colleague, self.boss):
            self.assertFalse(self.Line.with_user(other).search([("user_id", "=", self.user.id)]),
                             other.login)

    def test_the_card_and_the_title_are_not_written_by_hand(self):
        for who in (self.user, self.boss):
            with self.assertRaises(AccessError, msg=who.login):
                self.profile.with_user(who).write({"stamp_count": 4})
            with self.assertRaises(AccessError, msg=who.login):
                self.profile.with_user(who).write({"cards_completed": 9})
            with self.assertRaises(AccessError, msg=who.login):
                self.profile.with_user(who).write({"worn_badge_id": False})
        with self.assertRaises(AccessError):
            self.Profile.with_user(self.boss).create({"user_id": self.colleague.id, "stamp_count": 4})
        # Un `default_*` du contexte de l'appelant est ignoré : le profil naît à zéro.
        created = self.Profile.with_user(self.boss).with_context(
            default_stamp_count=4, default_cards_completed=9).create({"user_id": self.colleague.id})
        self.assertEqual((created.sudo().stamp_count, created.sudo().cards_completed), (0, 0))
        with self.assertRaises(AccessError):
            self.profile.with_user(self.colleague).stamp_count   # noqa: B018 (groupe gestionnaire)

    def test_a_challenge_already_proposed_is_archived_not_deleted(self):
        self._issue()
        with self.assertRaises(UserError):
            self.c_task.unlink()
        unused = self.Challenge.create({"name": "FQ jamais proposé", "source": "document"})
        unused.unlink()
        self.assertFalse(unused.exists())

    def test_only_managers_edit_the_catalogue(self):
        with self.assertRaises(AccessError):
            self.c_task.with_user(self.user).write({"xp_reward": 400})
        self.c_task.with_user(self.boss).write({"xp_reward": 11})
        self.assertEqual(self.c_task.xp_reward, 11)

    def test_resetting_progress_clears_lines_and_the_card(self):
        self._issue()
        self._gesture("timesheet")
        self.assertTrue(self._lines())
        self.env["res.config.settings"].with_user(self.admin).create({}).action_reset_all_progress()
        self.assertFalse(self._lines())
        profile = self.profile.sudo()
        profile.invalidate_recordset()
        self.assertEqual((profile.stamp_count, profile.cards_completed), (0, 0))

    def test_what_the_mailbox_engines_process_is_not_the_persons_gesture(self):
        # « Synchroniser maintenant » tourne dans la requête de la personne : l'XP d'un courriel que
        # le moteur passe en « répondu » lui est crédité, mais n'est pas son geste.
        self._issue()
        with freeze_time(THU_NOON):
            self.profile.with_context(**{NO_CELEBRATION: True})._award_xp(3, "timesheet", "tri du moteur")
        txn = self.Txn.search([("user_id", "=", self.user.id), ("description", "=", "tri du moteur")])
        self.assertEqual((txn.xp_amount, txn.from_request), (3, False))
        self.assertFalse(self._line(self.c_time).done)
        self._gesture("timesheet")
        self.assertTrue(self._line(self.c_time).done, "le vrai geste, lui, compte")

    # ------------------------------------------------------------------
    # Réglages et jeu de défis
    # ------------------------------------------------------------------

    def _save_settings(self, **values):
        settings = self.env["res.config.settings"].with_user(self.admin).create(values)
        # Le `set_values` du cœur, puis celui de Fox Quest, sans ceux des autres modules (OnlyOffice
        # appelle son serveur à chaque enregistrement).
        ResConfigSettings.set_values(settings)
        settings._bf_store_toggles()

    def test_the_switches_can_be_turned_off_from_the_settings_screen(self):
        Settings = self.env["res.config.settings"].with_user(self.admin)
        names = ["gamification_challenges_enabled", "gamification_popup_enabled", "gamification_enabled"]
        self._save_settings(gamification_challenges_enabled=False, gamification_popup_enabled=False,
                            gamification_enabled=True)
        self.assertFalse(self.Profile._bf_challenges_enabled())
        shown = Settings.default_get(names)
        self.assertEqual((shown["gamification_challenges_enabled"], shown["gamification_popup_enabled"],
                          shown["gamification_enabled"]), (False, False, True),
                         "l'écran rouvert montre ce qui a été éteint")
        self._save_settings(gamification_challenges_enabled=True, gamification_popup_enabled=True,
                            gamification_enabled=True)
        self.assertTrue(self.Profile._bf_challenges_enabled())
        self.assertTrue(all(Settings.default_get(names)[name] for name in names))

    def test_a_switch_nobody_touched_reads_on(self):
        ICP = self.env["ir.config_parameter"].sudo()
        for key in ("challenges_enabled", "popup_enabled", "gamification_enabled"):
            ICP.set_param("bf_gamification." + key, False)
        Settings = self.env["res.config.settings"].with_user(self.admin)
        names = ["gamification_challenges_enabled", "gamification_popup_enabled"]
        shown = Settings.default_get(names)
        self.assertTrue(shown["gamification_challenges_enabled"] and shown["gamification_popup_enabled"])
        # Une valeur écrite à la main (« false », « 0 ») éteint le module : l'écran le dit aussi.
        for stored in ("false", "0", "non"):
            ICP.set_param("bf_gamification.challenges_enabled", stored)
            self.assertFalse(self.Profile._bf_challenges_enabled(), stored)
            self.assertFalse(Settings.default_get(names)["gamification_challenges_enabled"], stored)

    def test_no_challenge_when_fox_quest_itself_is_off(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_gamification.gamification_enabled", "False")
        self.assertFalse(self.Profile._bf_challenges_enabled())
        self.assertFalse(self._issue())

    def test_a_challenge_whose_xp_rule_is_off_is_not_offered(self):
        # Sans règle d'XP active pour sa source, le défi ne pourrait jamais avancer.
        rules = self.env["bf.gamification.xp.rule"].search([("source", "=", "helpdesk")])
        self.assertTrue(rules, "le catalogue livré paie les tickets")
        rules.write({"active": False})
        with freeze_time(THU_NOON):
            self.assertNotIn(self.c_ticket, self.Profile._bf_challenge_pool(self.user))
        rules.write({"active": True, "xp_amount": 0})
        with freeze_time(THU_NOON):
            self.assertNotIn(self.c_ticket, self.Profile._bf_challenge_pool(self.user), "0 XP ne paie rien")
        rules.write({"xp_amount": 5})
        with freeze_time(THU_NOON):
            self.assertIn(self.c_ticket, self.Profile._bf_challenge_pool(self.user))

    def test_no_notification_for_a_challenge_finished_before_a_later_one_failed(self):
        # Deux défis du même geste : le premier réussit, le second lève. Le point de sauvegarde
        # annule les deux ; le message du premier, lui, serait parti sans l'indicateur de réussite.
        self.pool.write({"active": False})
        first = self.Challenge.create({"name": "FQ tâche A", "source": "task", "target": 1,
                                       "xp_reward": 4, "sequence": 1})
        second = self.Challenge.create({"name": "FQ tâche B", "source": "task", "target": 1,
                                        "xp_reward": 4, "sequence": 2})
        self._issue()
        self.assertEqual(len(self._lines()), 2)
        before = self._events("bf_gamification/challenge_done")
        real = type(self.Profile)._bf_complete_challenge
        calls = []

        def first_then_boom(model, line):
            calls.append(line.id)
            if len(calls) > 1:
                raise ValueError("boum")
            return real(model, line)

        self.patch(type(self.Profile), "_bf_complete_challenge", first_then_boom)
        self._gesture("task", ref=self._task())
        self.assertEqual(len(calls), 2, "le second défi a bien été tenté")
        self.assertFalse(self._new_events("bf_gamification/challenge_done", before))
        self.assertFalse(self.Line.search([("user_id", "=", self.user.id), ("done", "=", True)]))
        self.assertTrue(first and second)

    def test_no_notification_when_the_block_failed_and_was_rolled_back(self):
        # Le message de bus ne se retire pas avec un point de sauvegarde : s'il part et que
        # la base revient en arrière, la personne verrait « Défi accompli » pour un défi non fait.
        self._issue()
        before = self._events("bf_gamification/challenge_done")

        def boom(model):
            raise ValueError("boum")

        self.patch(type(self.Profile), "_bf_add_stamp", boom)
        self._gesture("timesheet")
        self.assertFalse(self._new_events("bf_gamification/challenge_done", before))
        self.assertFalse(self.Line.search([("user_id", "=", self.user.id), ("done", "=", True)]),
                         "la marque est revenue en arrière avec le reste")

    # ------------------------------------------------------------------
    # Titres
    # ------------------------------------------------------------------

    def _badge(self, name, threshold=100, **vals):
        values = {"name": name, "xp_reward": 0, "condition_type": "threshold",
                  "condition_threshold": threshold, "threshold_field": "total_xp"}
        values.update(vals)
        return self.env["bf.gamification.badge"].create(values)

    def test_a_person_wears_the_name_of_a_badge_she_earned(self):
        badge = self._badge("FQ Archiviste")
        self.profile._grant_badge(badge)
        dashboard = self.env["bf.gamification.dashboard"].with_user(self.user)
        worn = dashboard.set_worn_title(badge.id)
        self.assertEqual(worn["name"], "FQ Archiviste")
        self.assertEqual(self.profile.sudo().worn_badge_id, badge)
        rows = dashboard._get_leaderboard(limit=50)
        mine = next(row for row in rows if row["user_name"] == "Fox Defi")
        self.assertEqual(mine["worn_title"], "FQ Archiviste")
        self.assertFalse(dashboard.set_worn_title(False))
        self.assertFalse(self.profile.sudo().worn_badge_id)

    def test_nobody_wears_a_badge_she_has_not_earned(self):
        badge = self._badge("FQ Pas encore")
        other = self._badge("FQ Celui d'une collègue")
        self.Profile._get_or_create_profile(self.colleague)._grant_badge(other)
        dashboard = self.env["bf.gamification.dashboard"].with_user(self.user)
        for candidate in (badge, other):
            with self.assertRaises(UserError, msg=candidate.name):
                dashboard.set_worn_title(candidate.id)
        with self.assertRaises(AccessError):
            dashboard.set_worn_title("pas un nombre")
        self.assertFalse(self.profile.sudo().worn_badge_id)

    def test_a_hidden_badge_once_earned_is_worn_like_any_other(self):
        # Les plus prestigieux sont cachés tant qu'on ne les a pas : une fois gagnés, c'est
        # justement ceux qu'on veut porter (leur nom se lit déjà dans les vues classiques).
        prestigious = self._badge("FQ Prestige", 100, hidden=True)
        self.profile._grant_badge(prestigious)
        worn = self.env["bf.gamification.dashboard"].with_user(self.user).set_worn_title(prestigious.id)
        self.assertEqual(worn["name"], "FQ Prestige")

    def test_a_badge_id_that_is_not_a_plain_positive_int_is_refused(self):
        dashboard = self.env["bf.gamification.dashboard"].with_user(self.user)
        for bad in (True, -4, 2 ** 40, "12abc", "12", [1], {"a": 1}, 1.9, float("inf")):
            with self.assertRaises(AccessError, msg=repr(bad)):
                dashboard.set_worn_title(bad)

    def test_the_support_does_not_pin_a_badge_for_a_person_either(self):
        badge = self._badge("FQ Vitrine")
        user_badge = self.profile._grant_badge(badge)
        self.patch(type(self.Profile), "_bf_is_interactive", self.real_is_interactive)
        in_a_request(self, impersonated=True)
        with self.assertRaises(AccessError):
            self.env["bf.gamification.dashboard"].with_user(self.user).toggle_showcase_badge(user_badge.id)

    def test_the_support_does_not_change_a_persons_title(self):
        badge = self._badge("FQ Archiviste")
        self.profile._grant_badge(badge)
        self.patch(type(self.Profile), "_bf_is_interactive", self.real_is_interactive)
        in_a_request(self, impersonated=True)
        with self.assertRaises(AccessError):
            self.env["bf.gamification.dashboard"].with_user(self.user).set_worn_title(badge.id)

    # ------------------------------------------------------------------
    # Ce que la barre du haut demande à chaque page
    # ------------------------------------------------------------------

    def test_badge_progress_is_the_same_with_what_the_caller_already_knows(self):
        self.env["bf.gamification.badge"].search([]).write({"active": False})
        self.Txn.create({"user_id": self.user.id, "xp_amount": 320, "source": "manual",
                         "description": "Montage"})
        self.profile._compute_xp()
        threshold = self._badge("FQ seuil", 500)
        earned = self._badge("FQ obtenu", 100)
        manual = self._badge("FQ manuel", 1, condition_type="manual")
        counter = self._badge("FQ compteur", 3, condition_type="automatic",
                              condition_model="mail.message", condition_domain="[]",
                              condition_user_field="author_id")
        self.profile._grant_badge(earned)
        earned_ids = set(self.env["bf.gamification.user.badge"].sudo().search(
            [("user_id", "=", self.user.id)]).badge_id.ids)
        for badge in threshold | earned | manual | counter:
            self.assertEqual(
                badge._get_user_progress(self.user),
                badge._get_user_progress(self.user, earned_ids=earned_ids, profile=self.profile),
                badge.name)
        # Un badge obtenu est à 100 %, sans recompter ses fiches, même si le compte a baissé depuis.
        done = self._badge("FQ compteur gagné", 5, condition_type="automatic", condition_model="mail.message",
                           condition_domain="[]", condition_user_field="author_id")
        self.profile._grant_badge(done)
        self.assertEqual(done._get_user_progress(self.user),
                         {"current": 5, "target": 5, "percent": 100.0, "earned": True})
        self.assertTrue(earned._get_user_progress(self.user, earned_ids=earned_ids)["earned"])
        self.assertFalse(threshold._get_user_progress(self.user, earned_ids=earned_ids)["earned"])

    def test_xp_of_the_week_and_the_month_are_the_sums_of_the_persons_own_lines(self):
        def txn(stamp, amount, user=None):
            self.Txn.create({"user_id": (user or self.user).id, "xp_amount": amount,
                             "source": "manual", "description": "essai", "date": stamp})

        txn("2026-10-05 03:59:59", 1000)   # dimanche 23 h 59 à Montréal : la semaine d'avant
        txn("2026-10-05 04:00:00", 7)      # lundi minuit à Montréal : cette semaine
        txn("2026-10-08 12:00:00", 5)
        txn("2026-10-02 12:00:00", 40)     # ce mois-ci, pas cette semaine
        txn("2026-10-01 03:59:00", 2000)   # 30 septembre à Montréal : le mois d'avant
        txn("2026-10-07 12:00:00", 900, user=self.colleague)   # celui d'une autre personne
        with freeze_time(THU_NOON):
            profile = self.Profile._get_or_create_profile(self.user).with_user(self.user)
            profile.invalidate_recordset()
            self.assertEqual((profile.xp_this_week, profile.xp_this_month), (12, 1052))
            # Un gestionnaire lit le journal de tout le monde : la somme reste celle de la personne.
            as_manager = self.Profile._get_or_create_profile(self.user).with_user(self.boss)
            as_manager.invalidate_recordset()
            self.assertEqual((as_manager.xp_this_week, as_manager.xp_this_month), (12, 1052))

    # ------------------------------------------------------------------
    # Prochains objectifs
    # ------------------------------------------------------------------

    def test_a_manual_badge_is_never_a_next_goal(self):
        self.env["bf.gamification.badge"].search([]).write({"active": False})
        self._badge("FQ Manuel seul", 1, condition_type="manual")
        counter = self._badge("FQ Compteur seul", 500)
        with freeze_time(THU_NOON):
            goals = self.env["bf.gamification.dashboard"].with_user(self.user).get_dashboard_data()["next_goals"]
        self.assertEqual([goal["name"] for goal in goals], [counter.name])

    def test_next_goals_are_the_closest_visible_unearned_counters(self):
        self.env["bf.gamification.badge"].search([]).write({"active": False})
        self.Txn.create({"user_id": self.user.id, "xp_amount": 320, "source": "manual",
                         "description": "Montage"})
        self.profile._compute_xp()
        self._badge("FQ Atteint", 100)
        self._badge("FQ Proche", 500)
        self._badge("FQ Moyen", 1000)
        self._badge("FQ Loin", 2000)
        self._badge("FQ Très loin", 5000)
        self._badge("FQ Caché", 400, hidden=True)
        self._badge("FQ Manuel", 1, condition_type="manual")
        earned = self._badge("FQ Déjà à moi", 150)
        self.profile._grant_badge(earned)
        with freeze_time(THU_NOON):
            data = self.env["bf.gamification.dashboard"].with_user(self.user).get_dashboard_data()
        goals = data["next_goals"]
        self.assertEqual([goal["name"] for goal in goals], ["FQ Proche", "FQ Moyen", "FQ Loin"])
        self.assertEqual(goals[0]["target"], 500)
        self.assertEqual(goals[0]["current"], 320)
        self.assertEqual(goals[0]["percent"], 64.0)
