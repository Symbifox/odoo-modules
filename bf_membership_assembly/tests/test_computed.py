from datetime import datetime, time

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import AssemblyCase


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestComputedFields(AssemblyCase):
    """🔴 Un champ calculé stocké ne s'écrit pas, ni à la création ni ensuite.

    L'ORM ne le refuse pas de lui-même. Chaque écriture se joue comme un client
    RPC la ferait, dans le rôle de l'agent, et chaque essai lit le message de
    l'erreur pour savoir que c'est bien la garde qui refuse. Un essai par
    modèle.
    """

    def setUp(self):
        super().setUp()
        # Deux voix dans la salle, sur sept votants : pas de quorum.
        self.assembly = self._opened()
        self._attend(self.assembly, self.alice | self.gilles)
        self.env.invalidate_all()

    def _refused(self, method, values):
        with self.assertRaises(UserError) as caught:
            method(values)
        self.assertIn("se calculent", str(caught.exception))

    def test_assembly_counts_are_not_written(self):
        """Un quorum « atteint » à deux voix sur sept, des voix qui n'existent pas."""
        assembly = self.assembly.with_user(self.agent)
        self._refused(assembly.write, {"voice_count": 7, "quorum_met": True})
        self._refused(assembly.write, {"quorum_required": 1})
        self._refused(assembly.write, {"present_count": 7})
        self._refused(self.env["bf.membership.assembly"].with_user(self.agent).create, {
            "name": "AGA (essai)", "date": datetime.combine(self.meeting_day, time(16, 0)),
            "voter_count": 300})
        self.env.invalidate_all()
        self.assertEqual((self.assembly.voice_count, self.assembly.quorum_met), (2, False))

    def test_voter_voice_is_not_written(self):
        """Une voix posée sur une personne absente lui donnerait un bulletin et
        gonflerait le quorum."""
        henri = self._line(self.assembly, self.henri)
        for values in ({"has_voice": True}, {"missing_representative": True},
                       {"company_id": self.company.id}):
            self._refused(henri.with_user(self.agent).write, values)
        convened = self._convened(name="Convoquée (essai)")
        line = self._line(convened, self.henri)
        values = {"assembly_id": convened.id, "member_id": self.henri.id,
                  "membership_id": line.membership_id.id, "has_voice": True}
        line.with_user(self.agent).unlink()
        self._refused(self.env["bf.membership.assembly.voter"].with_user(self.agent).create, values)
        proposal = self._proposal(self.assembly, vote_mode="secret")
        proposal.with_user(self.agent).action_issue_ballots()
        self.env.invalidate_all()
        self.assertNotIn(henri, proposal.ballot_ids.voter_id)
        self.assertEqual(self.assembly.voice_count, 2)

    def test_proposal_result_and_counts_are_not_written(self):
        """Cinquante bulletins « remis » pour deux ; « adoptée » à 0 pour et 1 contre."""
        secret = self._proposal(self.assembly, vote_mode="secret")
        secret.action_issue_ballots()
        self._refused(secret.with_user(self.agent).write, {"ballot_count": 50})
        with self.assertRaises(ValidationError):
            secret.with_user(self.agent).write({"votes_for": 30})
        rejected = self._proposal(self.assembly, name="Rejetée (essai)")
        rejected.write({"votes_for": 0, "votes_against": 1})
        self._refused(rejected.with_user(self.agent).write, {"result": "adopted"})
        self._refused(rejected.with_user(self.agent).write, {"votes_cast": 9})
        self._refused(self.env["bf.membership.assembly.proposal"].with_user(self.agent).create, {
            "assembly_id": self.assembly.id, "name": "Née adoptée (essai)", "result": "adopted"})
        self.env.invalidate_all()
        self.assertEqual((rejected.result, secret.ballot_count), ("rejected", 2))

    def test_ballot_takes_its_assembly_from_its_proposal(self):
        """L'assemblée et la société d'un bulletin viennent de sa proposition,
        jamais de l'appel."""
        other = self._opened(name="Autre assemblée (essai)")
        secret = self._proposal(self.assembly, vote_mode="secret")
        self._refused(self.env["bf.membership.assembly.ballot"].with_user(self.agent).create, {
            "proposal_id": secret.id, "voter_id": self._line(self.assembly, self.alice).id,
            "assembly_id": other.id})
        self.assertFalse(secret.ballot_ids)


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestContextDefaults(AssemblyCase):
    """🔴 Une garde de création qui ne lit que les valeurs reçues laisse passer
    une valeur par défaut du contexte : l'ORM complète les valeurs APRÈS le
    contrôle. Rejoué dans le rôle de l'agent, avec le contexte qu'un client
    RPC fournirait. Un essai par modèle."""

    def setUp(self):
        super().setUp()
        self.assembly = self._opened()
        self._attend(self.assembly, self.alice | self.gilles)
        self.other = self.env["res.company"].create({"name": "Autre société (essai)"})
        self.env.invalidate_all()

    def test_assembly_ignores_protected_defaults(self):
        assembly = self.env["bf.membership.assembly"].with_user(self.agent).with_context(
            default_voice_count=7, default_quorum_met=True, default_voter_count=300,
        ).create({"name": "AGA (essai)", "date": datetime.combine(self.meeting_day, time(16, 0))})
        self.env.invalidate_all()
        self.assertEqual((assembly.voice_count, assembly.quorum_met, assembly.voter_count), (0, False, 0))

    def test_voter_ignores_protected_defaults(self):
        convened = self._convened(name="Convoquée (essai)")
        line = self._line(convened, self.henri)
        values = {"assembly_id": convened.id, "member_id": self.henri.id,
                  "membership_id": line.membership_id.id}
        line.with_user(self.agent).unlink()
        Voter = self.env["bf.membership.assembly.voter"].with_user(self.agent)
        added = Voter.with_context(
            default_has_voice=True, default_missing_representative=True, default_in_grace=True,
            default_notice_channel="email", default_company_id=self.other.id,
        ).create(values)
        self.env.invalidate_all()
        self.assertEqual(
            (added.has_voice, added.missing_representative, added.in_grace,
             added.notice_channel, added.company_id),
            (False, False, False, "post", self.company))
        # L'assemblée elle-même, par défaut : le gel lit l'assemblée visée.
        closed = self._opened(name="Close (essai)")
        closed.action_close()
        with self.assertRaises(UserError) as caught:
            Voter.with_context(default_assembly_id=closed.id).create({
                "member_id": self.jules.id, "membership_id": self.jules.membership_ids.id})
        self.assertIn("gelée", str(caught.exception))

    def test_proposal_ignores_protected_defaults(self):
        Proposal = self.env["bf.membership.assembly.proposal"].with_user(self.agent)
        proposal = Proposal.with_context(
            default_result="adopted", default_ballot_count=50, default_votes_cast=9,
            default_company_id=self.other.id,
        ).create({"assembly_id": self.assembly.id, "name": "Née adoptée (essai)"})
        self.env.invalidate_all()
        self.assertEqual((proposal.result, proposal.ballot_count, proposal.votes_cast, proposal.company_id),
                         ("pending", 0, 0, self.company))
        # Le drapeau du dépouillement : ni par une valeur par défaut, ni par les valeurs.
        flagged = Proposal.with_context(default_tally_started=True).create({
            "assembly_id": self.assembly.id, "name": "Drapeau par défaut (essai)"})
        self.env.invalidate_all()
        self.assertFalse(flagged.tally_started)
        with self.assertRaises(UserError) as caught:
            Proposal.create({"assembly_id": self.assembly.id, "name": "Drapeau saisi (essai)",
                             "tally_started": True})
        self.assertIn("ne se saisit pas", str(caught.exception))
        # Un résultat par défaut dans une assemblée qui n'est pas ouverte.
        convened = self._convened(name="Convoquée (essai)")
        self._attend(convened, self.alice | self.gilles)
        with self.assertRaises(UserError) as caught:
            Proposal.with_context(default_votes_for=2).create({
                "assembly_id": convened.id, "name": "Votée d'avance (essai)"})
        self.assertIn("ouverte", str(caught.exception))

    def test_ballot_ignores_protected_defaults(self):
        other_assembly = self._opened(name="Autre assemblée (essai)")
        secret = self._proposal(self.assembly, vote_mode="secret")
        alice = self._line(self.assembly, self.alice)
        Ballot = self.env["bf.membership.assembly.ballot"].with_user(self.agent)
        ballot = Ballot.with_context(
            default_assembly_id=other_assembly.id, default_company_id=self.other.id,
            default_via_proxy=True,
        ).create({"proposal_id": secret.id, "voter_id": alice.id})
        self.env.invalidate_all()
        self.assertEqual((ballot.assembly_id, ballot.company_id, ballot.via_proxy),
                         (self.assembly, self.company, False))
        with self.assertRaises(UserError):
            Ballot.with_context(default_proposal_id=secret.id).create({
                "voter_id": self._line(self.assembly, self.gilles).id})

    def test_list_built_ignores_list_defaults(self):
        """La liste rebâtie après la convocation, sous un contexte qui fixe la
        grâce et le canal : la ligne nouvelle reçoit ce que la liste calcule.
        La liste écrit en superutilisateur, et `sudo()` retire les valeurs par
        défaut du contexte : cet essai garde ce comportement."""
        convened = self._convened(name="Convoquée (essai)")
        newcomer = self.env["res.partner"].create({"name": "Membre retrouvée (essai)"})
        self._membership(newcomer, payment_state="paid", date_start=self.start, date_end=self.end)
        convened.with_user(self.agent).with_context(
            default_in_grace=True, default_notice_channel="email").action_build_voters()
        line = self._line(convened, newcomer)
        # 🔴 Lu en base : le cache peut dire autre chose que ce qui est écrit.
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT in_grace, notice_channel FROM bf_membership_assembly_voter WHERE id = %s", [line.id])
        self.assertEqual(self.env.cr.fetchone(), (False, "post"))
