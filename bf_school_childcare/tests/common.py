from datetime import date, timedelta

from freezegun import freeze_time

from odoo.tests import new_test_user


class ChildcareFixture:
    """A school with its calendar, three periods and two families.

    Accounts, never the superuser for what is tested (🔴 and never by adding to an account the
    group it lacks):
    * office: the school Administration alone (it implies the child care manager);
    * manager: the child care manager alone; educator: the educator alone;
    * teacher: school staff, not child care;
    * mom, dad: portal, parental authority over Alice and Bruno; grandma: portal, may pick up
      Alice, no parental authority; other: portal, parent of Dina only.
    """

    @classmethod
    def _setup_childcare(cls):
        env = cls.env
        if not env["res.lang"]._lang_get("fr_CA"):
            env["res.lang"]._activate_lang("fr_CA")
        env.company.partner_id.tz = "America/Toronto"
        cls.school = env["bf.school"].create({"name": "École de la Garde"})
        cls.today = cls.school._calendar_today()
        cls.year = env["bf.school.year"].create({
            "name": "Garde %s" % cls.today.year, "school_id": cls.school.id,
            "date_start": cls.today - timedelta(days=60), "date_end": cls.today + timedelta(days=200)})
        cls.year.action_set_current()
        cls.year.action_school_generate_calendar()
        # A Monday at least a week ahead: every notice is met unless a test moves the clock.
        cls.monday = cls.today + timedelta(days=7 + (7 - cls.today.weekday()) % 7)
        cls.tuesday = cls.monday + timedelta(days=1)
        cls.pedagogical = cls.monday + timedelta(days=2)
        cls.holiday = cls.monday + timedelta(days=3)
        Day = env["bf.school.calendar.day"]
        Day.search([("school_id", "=", cls.school.id), ("date", "=", cls.pedagogical)]).kind = "pedagogical"
        Day.search([("school_id", "=", cls.school.id), ("date", "=", cls.holiday)]).kind = "holiday"

        level = env.ref
        cls.k5, cls.e3, cls.s1 = level("bf_school_core.level_k5"), level("bf_school_core.level_e3"), \
            level("bf_school_core.level_s1")
        Group = env["bf.school.group"]
        groups = {code: Group.create({"name": "Groupe %s" % code, "school_id": cls.school.id, "year_id": cls.year.id,
                                      "level_ids": [(6, 0, lvl.ids)]})
                  for code, lvl in (("k5", cls.k5), ("e3", cls.e3), ("s1", cls.s1))}
        Partner = env["res.partner"]

        def student(name, group, birthdate=None):
            kid = Partner.create({"name": name, "is_student": True, "student_birthdate": birthdate})
            env["bf.school.enrollment"].create({"student_id": kid.id, "group_id": groups[group].id})
            return kid

        cls.alice = student("Alice Garde", "e3", date(cls.today.year - 9, 1, 15))
        cls.bruno = student("Bruno Garde", "k5")
        cls.chloe = student("Chloé Garde", "s1")
        cls.dina = student("Dina Autre", "e3")
        cls.eli = student("Éli Garde", "e3")

        def portal(login, name):
            return new_test_user(env, login=login, name=name, groups="base.group_portal",
                                 email="%s@example.invalid" % login, lang="fr_CA")

        cls.mom = portal("cc_mom", "Maman Garde")
        cls.dad = portal("cc_dad", "Papa Garde")
        cls.grandma = portal("cc_grandma", "Mamie Garde")
        cls.other = portal("cc_other", "Parent Autre")
        Link = env["bf.school.guardian.link"]
        for kid in (cls.alice, cls.bruno, cls.eli):
            Link.create({"student_id": kid.id, "guardian_id": cls.mom.partner_id.id, "relationship": "mother",
                         "is_payer": True})
            Link.create({"student_id": kid.id, "guardian_id": cls.dad.partner_id.id, "relationship": "father"})
        # Receives the school's notices, holds no parental authority: told of nothing at child care.
        Link.create({"student_id": cls.alice.id, "guardian_id": cls.grandma.partner_id.id,
                     "relationship": "grandparent", "receives_notices": True})
        Link.create({"student_id": cls.dina.id, "guardian_id": cls.other.partner_id.id, "relationship": "parent"})

        Slot = env["bf.school.childcare.slot"]
        cls.before = Slot.create({"name": "Matin", "school_id": cls.school.id, "kind": "before",
                                  "start_time": 7.0, "end_time": 8.0})
        cls.after = Slot.create({"name": "Après l'école", "school_id": cls.school.id, "kind": "after",
                                 "start_time": 15.5, "end_time": 18.0, "capacity": 2, "grace_minutes": 5})
        cls.full = Slot.create({"name": "Journée pédagogique", "school_id": cls.school.id, "kind": "full_day",
                                "start_time": 7.5, "end_time": 17.5, "capacity": 3})
        cls.after_k = Slot.create({"name": "Après l'école, maternelle", "school_id": cls.school.id, "kind": "after",
                                   "start_time": 15.5, "end_time": 17.0, "level_ids": [(6, 0, cls.k5.ids)]})

        cls.office = new_test_user(env, login="cc_office", groups="bf_school_core.group_school_manager")
        cls.manager = new_test_user(env, login="cc_manager", groups="bf_school_childcare.group_school_childcare_manager")
        cls.educator = new_test_user(env, login="cc_educator", groups="bf_school_childcare.group_school_childcare")
        cls.teacher = new_test_user(env, login="cc_teacher", groups="bf_school_core.group_school_user")
        cls.school.childcare_manager_id = cls.manager

    # --- Helpers ---------------------------------------------------------------------------

    def _book(self, student=None, slot=None, day=None, parent=None):
        return self.env["bf.school.childcare.booking"]._school_book_by(
            (parent or self.mom).partner_id, student or self.alice, slot or self.after, day or self.monday)

    def _office_book(self, student=None, slot=None, day=None, user=None):
        return self.env["bf.school.childcare.booking"].with_user(user or self.manager).create({
            "student_id": (student or self.alice).id, "slot_id": (slot or self.after).id,
            "date": day or self.monday})

    def _registration(self, student=None, lines=None, user=None, activate=True, start=None):
        Registration = self.env["bf.school.childcare.registration"].with_user(user or self.manager)
        registration = Registration.create({
            "student_id": (student or self.alice).id, "school_id": self.school.id, "year_id": self.year.id,
            "date_start": start or self.today, "date_end": self.year.date_end,
            "line_ids": [(0, 0, {"slot_id": slot.id, **{d: True for d in days}})
                         for slot, days in (lines or {self.after: ["mon"]}).items()]})
        if activate:
            registration.action_activate()
        return registration

    def _at(self, day, hour):
        """The UTC moment of a time of day at school, for freeze_time."""
        return self.school._calendar_at(day, hour)

    def _sheet(self, slot=None, day=None, user=None):
        """Opened on its own day: the educator opens today's sheets only."""
        day = day or self.monday
        with freeze_time(self._at(day, 12.0)):
            return self.env["bf.school.childcare.sheet"].with_user(user or self.educator).create(
                {"slot_id": (slot or self.after).id, "date": day})

    def _line(self, sheet, student):
        return sheet.line_ids.filtered(lambda l: l.student_id == student)

    def _mails(self, partner, model):
        return self.env["mail.mail"].sudo().search([("recipient_ids", "in", partner.ids), ("model", "=", model)])
