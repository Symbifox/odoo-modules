from dateutil.relativedelta import relativedelta

from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import Form, tagged
from odoo.tools import mute_logger

from .common import EmployerCase


@tagged("post_install", "-at_install")
class TestPosting(EmployerCase):

    def setUp(self):
        super().setUp()
        self.senior = self._member("Plus ancienne", 12)
        self.junior = self._member("Moins ancienne", 2)
        self.posted_list = self._posted_list()
        self.posting = self.env["bf.labour.posting"].create({
            "name": "Préposée aux bénéficiaires, quart de soir",
            "unit_id": self.unit.id,
            "kind": "posting",
            "seniority_list_id": self.posted_list.id,
            "date_posted": self.today,
        })
        self.posting.action_open()

    def _bid(self, employee, **kw):
        line = self.posted_list.line_ids.filtered(lambda l: l.employee_id == employee)
        vals = {
            "posting_id": self.posting.id,
            "employee_id": employee.id,
            "seniority_rank": line.rank,
            "seniority_date": line.seniority_date,
        }
        vals.update(kw)
        return self.env["bf.labour.posting.bid"].create(vals)

    def test_awarding_to_the_most_senior_needs_no_reason(self):
        senior_bid = self._bid(self.senior)
        self._bid(self.junior)
        self.posting.awarded_bid_id = senior_bid
        self.posting.action_award()
        self.assertEqual(self.posting.state, "awarded")

    def test_skipping_the_most_senior_demands_a_written_reason(self):
        """🔴 La garde n'interdit pas l'octroi hors rang, elle l'oblige à s'écrire.

        Un employeur a le droit d'écarter la personne la plus ancienne. Il n'a
        pas le droit de le faire sans raison, et c'est cette raison qu'on
        relira en grief.
        """
        self._bid(self.senior)
        junior_bid = self._bid(self.junior)
        self.posting.awarded_bid_id = junior_bid
        with self.assertRaises(UserError):
            self.posting.action_award()

    def test_skipping_with_a_reason_is_allowed(self):
        self._bid(self.senior)
        junior_bid = self._bid(self.junior)
        self.posting.write({
            "awarded_bid_id": junior_bid.id,
            "award_reason": "Ne détient pas le certificat exigé à l'article 14.03.",
        })
        self.posting.action_award()
        self.assertEqual(self.posting.state, "awarded")

    def test_a_withdrawn_bid_is_not_someone_who_was_skipped(self):
        senior_bid = self._bid(self.senior)
        junior_bid = self._bid(self.junior)
        senior_bid.state = "withdrawn"
        self.posting.awarded_bid_id = junior_bid
        self.posting.action_award()
        self.assertEqual(self.posting.state, "awarded")

    def test_an_ineligible_bid_needs_a_reason(self):
        bid = self._bid(self.senior)
        with self.assertRaises(ValidationError):
            bid.state = "ineligible"

    def test_the_rank_is_copied_not_read_live(self):
        """Une candidature de mars se juge avec les rangs de mars."""
        bid = self._bid(self.senior)
        original = bid.seniority_rank
        self.senior.labour_membership_ids.write({
            "seniority_date": self.today - relativedelta(days=1),
            "seniority_reason": "Correction postérieure.",
        })
        bid.invalidate_recordset()
        self.assertEqual(bid.seniority_rank, original)

    def test_onchange_proposes_the_rank_from_the_posted_list(self):
        with Form(self.env["bf.labour.posting.bid"].with_context(
            default_posting_id=self.posting.id,
        )) as form:
            form.posting_id = self.posting
            form.employee_id = self.senior
            self.assertEqual(form.seniority_rank, 1)

    @mute_logger("odoo.sql_db")
    def test_one_bid_per_person(self):
        self._bid(self.senior)
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self._bid(self.senior)

    def test_closing_date_cannot_precede_the_posting(self):
        with self.assertRaises(ValidationError):
            self.env["bf.labour.posting"].create({
                "name": "Dates inversées",
                "unit_id": self.unit.id,
                "date_posted": self.today,
                "date_close": self.today - relativedelta(days=1),
            })

    def test_awarding_without_a_chosen_bid_is_refused(self):
        self._bid(self.senior)
        with self.assertRaises(UserError):
            self.posting.action_award()
