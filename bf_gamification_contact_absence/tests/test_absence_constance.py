"""Une absence déclarée qui change après coup refait la constance.

Mêmes dates figées que les essais de Fox Quest : lundi 5 au vendredi 9 octobre
2026, dans le fuseau de Montréal.
"""
from datetime import date

from freezegun import freeze_time

from odoo.tests import TransactionCase, new_test_user, tagged

MON, TUE, WED, THU, FRI = (date(2026, 10, d) for d in range(5, 10))


@tagged("post_install", "-at_install")
class TestAbsenceConstance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.user = new_test_user(cls.env, login="fqa_constance", name="Fox Absence",
                                 tz="America/Montreal")
        cls.boss = new_test_user(
            cls.env, login="fqa_boss", name="Fox Absence Boss", tz="America/Montreal",
            groups="base.group_user,bf_gamification.group_gamification_manager")
        cls.calendar = cls.env["resource.calendar"].create({"name": "Semaine FQA"})
        boss_employee = cls.env["hr.employee"].create({
            "name": "Fox Absence Boss", "user_id": cls.boss.id,
            "resource_calendar_id": cls.calendar.id})
        cls.env["hr.employee"].create({
            "name": "Fox Absence", "user_id": cls.user.id, "parent_id": boss_employee.id,
            "resource_calendar_id": cls.calendar.id})
        cls.Profile = cls.env["bf.gamification.profile"]

    def setUp(self):
        super().setUp()
        # Un TransactionCase n'a pas de requête HTTP : sans ce remplacement, aucun
        # geste ne compterait. Les gestes sont ceux de la personne elle-même.
        self.patch(type(self.Profile), "_bf_is_interactive", lambda model: True)
        self.profile = self.Profile.with_user(self.user)._get_or_create_profile(self.user)

    def _work(self, *stamps):
        for stamp in stamps:
            with freeze_time(stamp):
                self.profile._award_xp(1, "manual", "jour")

    def _absence(self, first, last):
        return self.env["bf.partner.absence"].create({
            "partner_id": self.user.partner_id.id,
            "date_from": first, "date_to": last, "nature": "leave",
        })

    def _enable_leave(self, step):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_gamification.leave_enabled", "True")
        ICP.set_param("bf_gamification.leave_streak_days", str(step))
        ICP.set_param("bf_gamification.leave_days_per_step", "1")
        ICP.set_param("bf_gamification.leave_max_per_year", "5")

    def _claims(self):
        return self.env["bf.gamification.reward.claim"].sudo().search([
            ("user_id", "=", self.user.id),
            ("reward_id", "=", self.env.ref("bf_gamification.reward_leave_constance").id)])

    def test_an_absence_declared_afterwards_repairs_streak_and_march(self):
        # Venu de Fox Quest avec le pont qui le porte.
        self._enable_leave(step=3)
        with freeze_time("2026-10-01 13:00:00"):
            self.env["bf.gamification.dashboard"].with_user(self.user).set_leave_opt_in(True)
        self._work("2026-10-01 15:00:00", "2026-10-02 15:00:00")   # jeudi, vendredi
        with freeze_time("2026-10-08 15:00:00"):   # malade lundi, mardi, mercredi
            self.profile._award_xp(1, "manual", "jeudi")
            self.assertEqual(self.profile.current_streak, 1, "le premier XP du jeudi casse")
            # Déclarée le jeudi, pour mardi et mercredi : lundi seul manque, toléré.
            absence = self._absence(TUE, WED)
            self.assertEqual(self.profile.current_streak, 3, "l'absence répare la constance")
            self.assertEqual(len(self._claims()), 1, "et la marche : le palier de 3 est atteint")
            self.assertEqual(self.profile.sudo().leave_last_step_day, THU)
            absence.write({"date_from": WED})   # corrigée : mercredi seul
            self.assertEqual(self.profile.current_streak, 1, "lundi et mardi manquent")
            absence.write({"date_from": MON})
            self.assertEqual(self.profile.current_streak, 3)
            # Retirée : la constance recasse ; la demande ouverte reste à trancher.
            absence.unlink()
            self.assertEqual(self.profile.current_streak, 1)
        self.assertEqual(len(self._claims()), 1, "le palier du jeudi ne se recompte pas")

    def test_t2_6_archiving_an_absence_redoes_the_streak(self):
        # Archiver est la seule façon, pour un interne, de retirer une absence.
        self._work("2026-10-01 15:00:00", "2026-10-02 15:00:00")
        with freeze_time("2026-10-08 15:00:00"):
            self.profile._award_xp(1, "manual", "jeudi")
            absence = self._absence(MON, WED)
            self.assertEqual(self.profile.current_streak, 3)
            absence.with_user(self.user).action_archive()
            self.assertEqual(self.profile.current_streak, 1, "archivée : les trois jours manquent")
            absence.with_user(self.user).action_unarchive()
            self.assertEqual(self.profile.current_streak, 3, "désarchivée : la pause revient")

    def test_t2_6_an_employer_closure_declared_afterwards_repairs_its_staff(self):
        # Une fermeture de l'employeur (`applies_to_children`) pose une pause pour
        # tout son personnel : déclarée après coup, elle répare leur constance
        # (aucun essai ne la voyait).
        employer = self.user.partner_id.parent_id or self.user.company_id.partner_id
        self._work("2026-10-01 15:00:00", "2026-10-02 15:00:00")
        with freeze_time("2026-10-08 15:00:00"):
            self.profile._award_xp(1, "manual", "jeudi")
            self.assertEqual(self.profile.current_streak, 1)
            self.env["bf.partner.absence"].create({
                "partner_id": employer.id, "applies_to_children": True,
                "date_from": MON, "date_to": WED, "nature": "leave",
            })
            self.assertEqual(self.profile.current_streak, 3, "la fermeture répare le personnel")

    def test_t2_6_base_automation_does_not_unhook_the_absences(self):
        # Modifier une règle automatisée retire, dans ce processus, toute
        # enveloppe posée à l'exécution sur create, write et unlink.
        if "base.automation" not in self.env:
            self.skipTest("base_automation n'est pas installé")
        Automation = self.env["base.automation"]
        Automation._unregister_hook()
        self.addCleanup(Automation._register_hook)
        self._work("2026-10-01 15:00:00", "2026-10-02 15:00:00")
        with freeze_time("2026-10-08 15:00:00"):
            self.profile._award_xp(1, "manual", "jeudi")
            self.assertEqual(self.profile.current_streak, 1)
            self._absence(MON, WED)
            self.assertEqual(self.profile.current_streak, 3)
