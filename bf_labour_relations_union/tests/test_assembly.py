from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import UnionCase


@tagged("post_install", "-at_install")
class TestAssembly(UnionCase):
    """🔴 Le droit de vote suit l'ADHÉSION. Miroir exact de la cotisation."""

    def _assembly(self, **kw):
        vals = {
            "name": "Assemblée d'essai",
            "unit_id": self.unit.id,
            "date": self.today,
        }
        vals.update(kw)
        return self.env["bf.labour.assembly"].create(vals)

    def test_eligible_count_follows_membership_not_coverage(self):
        """Le contre-essai du socle, pris par l'autre bout.

        Les cotisations comptent les couverts ; les votes comptent les membres.
        Si ces deux chiffres étaient les mêmes, les deux états ne serviraient
        à rien.
        """
        self._membership("Couverte et membre")
        self._membership("Couverte non membre A", is_member=False)
        self._membership("Couverte non membre B", is_member=False)
        self._membership("Membre non couverte", covered=False)
        assembly = self._assembly()
        assembly.invalidate_recordset()
        # ⚠️ Les deux chiffres DOIVENT différer, sinon un vote qui suivrait la
        # couverture rendrait le même total et l'essai passerait en mentant.
        self.assertEqual(assembly.eligible_count, 2)
        self.assertEqual(self.unit.covered_count, 3)
        members = self.unit.membership_ids.filtered("is_member")
        covered = self.unit.membership_ids.filtered("covered")
        self.assertNotEqual(members, covered)

    def test_a_non_member_attendee_does_not_count_for_quorum(self):
        member = self._membership("Membre")
        payer = self._membership("Cotisante non membre", is_member=False)
        assembly = self._assembly(quorum_required=2)
        assembly.attendee_ids = member | payer
        assembly.invalidate_recordset()
        self.assertEqual(assembly.attendee_count, 2)
        self.assertEqual(assembly.eligible_attendee_count, 1)
        self.assertFalse(assembly.quorum_met)

    def test_no_quorum_required_is_met(self):
        """Zéro veut dire « aucun quorum exigé », pas « quorum à zéro »."""
        assembly = self._assembly(quorum_required=0)
        self.assertTrue(assembly.quorum_met)

    def test_abstentions_are_out_of_the_majority_base(self):
        """⚠️ L'erreur de calcul classique sur un mandat de grève.

        Inclure les abstentions dans l'assiette fait échouer des votes que
        l'assemblée a adoptés.
        """
        for index in range(10):
            self._membership("Membre %s" % index)
        assembly = self._assembly()
        assembly.attendee_ids = self.unit.membership_ids
        assembly.action_hold()
        vote = self.env["bf.labour.assembly.vote"].create({
            "assembly_id": assembly.id,
            "question": "Mandat de grève",
            "votes_for": 6,
            "votes_against": 3,
            "votes_abstain": 1,
            "majority_required": 50.0,
        })
        self.assertEqual(vote.total_cast, 9)
        self.assertAlmostEqual(vote.share_for, 66.67, places=1)
        self.assertTrue(vote.passed)

    def test_a_tie_does_not_pass_a_simple_majority(self):
        for index in range(4):
            self._membership("Membre %s" % index)
        assembly = self._assembly()
        assembly.attendee_ids = self.unit.membership_ids
        vote = self.env["bf.labour.assembly.vote"].create({
            "assembly_id": assembly.id,
            "question": "Égalité",
            "votes_for": 2,
            "votes_against": 2,
        })
        self.assertAlmostEqual(vote.share_for, 50.0, places=1)
        self.assertFalse(vote.passed)

    def test_a_reinforced_majority_is_honoured(self):
        for index in range(10):
            self._membership("Membre %s" % index)
        assembly = self._assembly()
        assembly.attendee_ids = self.unit.membership_ids
        vote = self.env["bf.labour.assembly.vote"].create({
            "assembly_id": assembly.id,
            "question": "Mandat renforcé",
            "votes_for": 6,
            "votes_against": 4,
            "majority_required": 66.0,
        })
        self.assertFalse(vote.passed)

    def test_more_votes_than_eligible_attendees_is_refused(self):
        member = self._membership("Seule présente")
        assembly = self._assembly()
        assembly.attendee_ids = member
        with self.assertRaises(ValidationError):
            self.env["bf.labour.assembly.vote"].create({
                "assembly_id": assembly.id,
                "question": "Décompte impossible",
                "votes_for": 5,
            })

    def test_negative_counts_are_refused(self):
        assembly = self._assembly()
        with self.assertRaises(ValidationError):
            self.env["bf.labour.assembly.vote"].create({
                "assembly_id": assembly.id,
                "question": "Négatif",
                "votes_for": -1,
            })

    def test_a_vote_is_not_recorded_before_the_assembly_is_held(self):
        assembly = self._assembly()
        vote = self.env["bf.labour.assembly.vote"].create({
            "assembly_id": assembly.id, "question": "Prématuré", "votes_for": 1,
        })
        with self.assertRaises(UserError):
            vote.action_record()
        assembly.action_hold()
        self.assertTrue(vote.action_record())

    def test_someone_who_left_before_the_assembly_is_not_eligible(self):
        gone = self._membership("Partie")
        gone.date_end = self.today - relativedelta(days=1)
        assembly = self._assembly()
        assembly.invalidate_recordset()
        self.assertEqual(assembly.eligible_count, 0)

