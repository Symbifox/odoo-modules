from odoo.tests.common import tagged

from .common import EmployerCase

MODELS = [
    "bf.labour.obligation",
    "bf.labour.seniority.list",
    "bf.labour.seniority.list.line",
    "bf.labour.posting",
    "bf.labour.posting.bid",
    "bf.labour.committee",
    "bf.labour.committee.meeting",
]


@tagged("post_install", "-at_install")
class TestViews(EmployerCase):

    def test_every_model_renders_its_views(self):
        for model in MODELS:
            with self.subTest(model=model):
                self.env[model].get_views([(False, "list"), (False, "form")])

    def test_the_inherited_remittance_form_still_builds(self):
        """Le greffon greffe des colonnes dans la vue du socle par xpath.

        Un xpath qui ne résout plus casse le formulaire du socle sans qu'aucun
        essai Python ne bronche.
        """
        self.env["bf.labour.dues.remittance"].get_views([(False, "form")])

    def test_search_views_build(self):
        for model in ["bf.labour.obligation", "bf.labour.posting"]:
            with self.subTest(model=model):
                self.env[model].get_views([(False, "search")])

    def test_the_cron_points_at_a_real_method(self):
        cron = self.env.ref("bf_labour_relations_employer.cron_obligation_reminders")
        self.assertTrue(cron.active)
        self.assertTrue(hasattr(self.env[cron.model_id.model], "_cron_post_reminders"))


@tagged("post_install", "-at_install")
class TestCommittee(EmployerCase):

    def test_cadence_is_silent_until_a_first_meeting(self):
        """Une cadence sans dernière rencontre ne réclame rien.

        Au premier jour, un comité neuf serait sinon déclaré en retard, et
        l'alerte perdrait son sens avant d'avoir servi.
        """
        committee = self.env["bf.labour.committee"].create({
            "unit_id": self.unit.id, "cadence": "quarterly",
        })
        self.assertFalse(committee.next_expected_date)
        self.assertFalse(committee.is_overdue)

    def test_overdue_is_measured_from_the_last_held_meeting(self):
        from dateutil.relativedelta import relativedelta

        committee = self.env["bf.labour.committee"].create({
            "unit_id": self.unit.id, "cadence": "quarterly",
        })
        meeting = self.env["bf.labour.committee.meeting"].create({
            "committee_id": committee.id,
            "date": self.today - relativedelta(months=5),
        })
        committee.invalidate_recordset()
        self.assertFalse(committee.is_overdue, "une rencontre PRÉVUE ne compte pas")
        meeting.action_hold()
        committee.invalidate_recordset()
        self.assertTrue(committee.is_overdue)
        self.assertEqual(committee.last_meeting_date, meeting.date)
