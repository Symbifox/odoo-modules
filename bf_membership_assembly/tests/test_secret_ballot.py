from odoo.exceptions import UserError, ValidationError
from odoo.models import MAGIC_COLUMNS
from odoo.tests import tagged

from .common import AssemblyCase

# Les seuls champs que le registre des bulletins a le droit de porter. Un
# champ de plus, c'est une porte vers le choix d'une personne : l'essai tombe
# et oblige à se demander pourquoi.
BALLOT_FIELDS = {"proposal_id", "assembly_id", "company_id", "voter_id", "received_by_id", "via_proxy"}
CHOICE_VALUES = {"for", "against", "abstain", "yes", "no", "pour", "contre"}
MODELS = (
    "bf.membership.assembly",
    "bf.membership.assembly.voter",
    "bf.membership.assembly.proposal",
    "bf.membership.assembly.ballot",
)


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestSecretBallot(AssemblyCase):

    def setUp(self):
        super().setUp()
        self.assembly = self._convened(proxy_allowed=True)
        self._attend(self.assembly, self.alice | self.gilles | self.member_org)
        self._line(self.assembly, self.bruno).write({
            "attendance": "proxy", "proxy_holder_id": self._line(self.assembly, self.alice).id})
        self.assembly.action_open()
        self.proposal = self._proposal(self.assembly, vote_mode="secret")

    def test_no_field_links_a_voter_to_a_choice(self):
        """🔴 Le registre dit qui a reçu un bulletin, et rien d'autre."""
        Ballot = self.env["bf.membership.assembly.ballot"]
        own = {name for name, field in Ballot._fields.items()
               if not field.automatic and name not in set(MAGIC_COLUMNS) | {"display_name"}}
        self.assertEqual(own, BALLOT_FIELDS)
        for model in MODELS:
            for name, field in self.env[model]._fields.items():
                if field.type != "selection":
                    continue
                values = {v for v, _label in field._description_selection(self.env)}
                self.assertFalse(values & CHOICE_VALUES, "%s.%s porte un choix de vote." % (model, name))
        # Le dépouillement est un total sur la proposition, jamais une ligne par bulletin.
        Proposal = self.env["bf.membership.assembly.proposal"]
        for name in ("votes_for", "votes_against", "votes_abstain", "votes_spoiled"):
            self.assertEqual(Proposal._fields[name].type, "integer")

    def test_one_ballot_per_voter_proxies_included(self):
        self.proposal.action_issue_ballots()
        ballots = self.proposal.ballot_ids
        self.assertEqual(len(ballots), 4, "Alice, Gilles, l'organisme, et Bruno par procuration.")
        bruno = ballots.filtered(lambda b: b.voter_id.member_id == self.bruno)
        self.assertTrue(bruno.via_proxy)
        self.assertEqual(bruno.received_by_id, self.alice, "Le bulletin de Bruno va à sa mandataire.")
        self.assertEqual(
            ballots.filtered(lambda b: b.voter_id.member_id == self.member_org).received_by_id, self.carole)
        self.proposal.action_issue_ballots()
        self.assertEqual(len(self.proposal.ballot_ids), 4, "Un second clic ne double rien.")
        with self.assertRaises(UserError):
            self.env["bf.membership.assembly.ballot"].create({
                "proposal_id": self.proposal.id, "voter_id": bruno.voter_id.id})

    def test_absent_voter_gets_no_ballot(self):
        with self.assertRaises(UserError):
            self.env["bf.membership.assembly.ballot"].create({
                "proposal_id": self.proposal.id,
                "voter_id": self._line(self.assembly, self.henri).id})

    def test_cannot_count_more_ballots_than_issued(self):
        self.proposal.action_issue_ballots()
        with self.assertRaises(ValidationError):
            self.proposal.write({"votes_for": 3, "votes_against": 1, "votes_spoiled": 1})
        self.proposal.write({"votes_for": 2, "votes_against": 1, "votes_spoiled": 1})
        self.assertEqual(self.proposal.result, "adopted")

    def test_tally_before_any_ballot_is_refused(self):
        with self.assertRaises(ValidationError):
            self.proposal.votes_for = 1

    def test_issued_ballots_are_fixed_once_counting_starts(self):
        self.proposal.action_issue_ballots()
        ballot = self.proposal.ballot_ids[:1]
        with self.assertRaises(UserError):
            ballot.write({"via_proxy": True})
        self.proposal.votes_for = 1
        with self.assertRaises(UserError):
            ballot.unlink()

    def test_vote_mode_is_frozen_once_ballots_are_issued(self):
        self.proposal.action_issue_ballots()
        with self.assertRaises(UserError):
            self.proposal.vote_mode = "show_of_hands"

    def test_show_of_hands_has_no_ballots(self):
        show = self._proposal(self.assembly)
        with self.assertRaises(UserError):
            show.action_issue_ballots()

    def test_no_ballot_once_counting_has_started(self):
        """🔴 Trois bulletins, 2 pour et 1 contre : la personne arrivée en retard
        n'en reçoit pas un quatrième.

        Sinon le décompte corrigé (2 et 2) dirait comment elle a voté. Rejoué
        dans le rôle de l'agent, par le bouton et par une création directe.
        """
        latecomer = self._line(self.assembly, self.gilles)
        latecomer.attendance = "absent"
        self.proposal.action_issue_ballots()
        self.assertEqual(self.proposal.ballot_count, 3)
        self.proposal.write({"votes_for": 2, "votes_against": 1})
        latecomer.attendance = "onsite"
        self.env.invalidate_all()
        with self.assertRaises(UserError) as caught:
            self.proposal.with_user(self.agent).action_issue_ballots()
        self.assertIn("dépouillement", str(caught.exception))
        with self.assertRaises(UserError) as caught:
            self.env["bf.membership.assembly.ballot"].with_user(self.agent).create({
                "proposal_id": self.proposal.id, "voter_id": latecomer.id})
        self.assertIn("dépouillement", str(caught.exception))
        self.env.invalidate_all()
        self.assertEqual(self.proposal.ballot_count, 3)
        with self.assertRaises(ValidationError):
            self.proposal.write({"votes_for": 2, "votes_against": 2})

    def test_a_started_count_is_corrected_not_erased(self):
        """🔴 Remis à zéro, le dépouillement rouvrirait la remise des bulletins.

        Le décompte se corrige, il ne s'efface pas. À main levée, il n'y a pas
        de bulletin, et effacer une saisie reste permis.
        """
        self._line(self.assembly, self.gilles).attendance = "absent"
        self.proposal.action_issue_ballots()
        proposal = self.proposal.with_user(self.agent)
        proposal.write({"votes_for": 2, "votes_against": 1})
        with self.assertRaises(UserError) as caught:
            proposal.write({"votes_for": 0, "votes_against": 0})
        self.assertIn("ne s'efface pas", str(caught.exception))
        proposal.write({"votes_for": 1, "votes_against": 2})
        self.assertEqual(proposal.result, "rejected", "Un décompte se corrige.")
        show = self._proposal(self.assembly, name="À main levée (essai)")
        show.write({"votes_for": 1})
        show.write({"votes_for": 0})
        self.assertEqual(show.result, "pending")

    def test_a_secret_proposal_and_its_ballots_stay_in_their_assembly(self):
        """Le registre des bulletins suit sa proposition, qui ne change pas d'assemblée."""
        self.proposal.action_issue_ballots()
        other = self._opened(name="Autre assemblée (essai)")
        other_proposal = self._proposal(other, vote_mode="secret")
        self.env.invalidate_all()
        with self.assertRaises(UserError) as caught:
            self.proposal.with_user(self.agent).write({"assembly_id": other.id})
        self.assertIn("ne change pas d'assemblée", str(caught.exception))
        with self.assertRaises(UserError):
            self.proposal.ballot_ids[:1].with_user(self.manager).write({"proposal_id": other_proposal.id})
        self.env.invalidate_all()
        self.assertEqual(self.proposal.ballot_ids.assembly_id, self.assembly)
