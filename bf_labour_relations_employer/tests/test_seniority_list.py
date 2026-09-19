from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import EmployerCase


@tagged("post_install", "-at_install")
class TestSeniorityList(EmployerCase):
    """La liste affichée est une PHOTO. C'est tout le modèle."""

    def test_ranks_follow_seniority_not_creation_order(self):
        junior = self._member("Junior", 1)
        senior = self._member("Senior", 10)
        posted = self._posted_list()
        ranks = {line.employee_id: line.rank for line in posted.line_ids}
        self.assertEqual(ranks[senior], 1)
        self.assertEqual(ranks[junior], 2)

    def test_only_covered_people_appear(self):
        """L'affichage porte l'unité de négociation, pas les membres.

        Une personne membre mais non couverte n'a pas de rang dans l'unité.
        """
        covered = self._member("Couverte", 5, is_member=False)
        not_covered = self._member("Non couverte", 5, covered=False)
        posted = self._posted_list()
        people = posted.line_ids.mapped("employee_id")
        self.assertIn(covered, people)
        self.assertNotIn(not_covered, people)

    def test_a_posted_list_does_not_follow_a_later_correction(self):
        """🔴 Le coeur du greffon.

        Corriger une ancienneté APRÈS l'affichage ne doit pas récrire la liste
        affichée : les rangs se contestent contre ce qui a été affiché. Une
        liste qui se recalculerait donnerait raison rétroactivement à
        l'employeur dans chaque grief de rang.
        """
        person = self._member("Corrigée", 3)
        posted = self._posted_list()
        line = posted.line_ids.filtered(lambda l: l.employee_id == person)
        original = line.seniority_date

        membership = person.labour_membership_ids
        membership.write({
            "seniority_date": self.today - relativedelta(years=15),
            "seniority_reason": "Ancienneté reconnue après coup.",
        })
        posted.invalidate_recordset()
        self.assertEqual(line.seniority_date, original)

    def test_a_posted_list_cannot_be_rebuilt(self):
        self._member("Quelqu'un", 2)
        posted = self._posted_list()
        with self.assertRaises(UserError):
            posted.action_build()

    def test_a_posted_line_cannot_be_rewritten(self):
        self._member("Quelqu'un", 2)
        posted = self._posted_list()
        with self.assertRaises(UserError):
            posted.line_ids[0].rank = 99

    def test_contesting_a_rank_is_not_rewriting_it(self):
        """Contester reste possible sur une liste figée : c'est un drapeau,
        pas une correction."""
        self._member("Contestataire", 2)
        posted = self._posted_list()
        posted.line_ids[0].contested = True
        self.assertTrue(posted.line_ids[0].contested)

    def test_an_empty_list_is_not_posted(self):
        record = self.env["bf.labour.seniority.list"].create({
            "unit_id": self.unit.id, "reference_date": self.today,
        })
        record.action_build()
        with self.assertRaises(UserError):
            record.action_post()

    def test_posting_a_new_list_supersedes_the_previous(self):
        self._member("Quelqu'un", 2)
        first = self._posted_list()
        second = self._posted_list()
        self.assertEqual(first.state, "superseded")
        self.assertEqual(first.superseded_by_id, second)
        self.assertEqual(second.state, "posted")

    def test_someone_who_left_before_the_reference_date_is_absent(self):
        stays = self._member("Reste", 6)
        gone = self._member("Partie", 4)
        gone.labour_membership_ids.date_end = self.today - relativedelta(days=1)
        posted = self._posted_list()
        people = posted.line_ids.mapped("employee_id")
        self.assertIn(stays, people)
        self.assertNotIn(gone, people)
