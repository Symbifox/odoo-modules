from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import AssemblyCase


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestProposals(AssemblyCase):

    def setUp(self):
        super().setUp()
        # Six voix dans la salle : les cinq personnes et l'organisme par Carole.
        self.assembly = self._opened()
        self._attend(self.assembly, self.persons | self.member_org)
        self.assertEqual(self.assembly.voice_count, 6)

    def _vote(self, proposal, votes_for, against, abstain=0):
        proposal.write({"votes_for": votes_for, "votes_against": against, "votes_abstain": abstain})
        return proposal.result

    def test_nothing_entered_is_pending(self):
        self.assertEqual(self._proposal(self.assembly).result, "pending")

    def test_a_tie_is_rejected(self):
        self.assertEqual(self._vote(self._proposal(self.assembly), 3, 3), "rejected")

    def test_abstentions_are_not_counted(self):
        """2 pour, 1 contre, 3 abstentions : adoptée. Sur les présents, ce serait 2 sur 6."""
        proposal = self._proposal(self.assembly)
        self.assertEqual(self._vote(proposal, 2, 1, 3), "adopted")
        self.assertEqual(proposal.votes_cast, 3)

    def test_everyone_abstains_is_rejected(self):
        self.assertEqual(self._vote(self._proposal(self.assembly), 0, 0, 4), "rejected")

    def test_two_thirds_passes_at_the_bound(self):
        proposal = self._proposal(self.assembly, majority="two_thirds")
        self.assertEqual(self._vote(proposal, 4, 2), "adopted", "Quatre sur six : les deux tiers pile.")
        self.assertEqual(self._vote(proposal, 3, 2), "rejected", "Trois sur cinq : 60 %.")

    def test_free_percentage(self):
        proposal = self._proposal(self.assembly, majority="percent", majority_percent=75.0)
        with self.assertRaises(ValidationError):
            proposal.majority_percent = 50.0
        self.assertEqual(self._vote(proposal, 3, 1), "adopted")
        self.assertEqual(self._vote(proposal, 2, 1), "rejected")

    def test_cannot_count_more_voices_than_in_the_room(self):
        with self.assertRaises(ValidationError):
            self._vote(self._proposal(self.assembly), 5, 2)

    def test_results_are_entered_once_the_assembly_is_open(self):
        # Des présences dans la salle : seul l'état de l'assemblée peut refuser.
        convened = self._convened(name="Convoquée, pas encore ouverte (essai)")
        self._attend(convened, self.alice | self.bruno | self.gilles)
        with self.assertRaises(UserError) as caught:
            self._proposal(convened, votes_for=1)
        self.assertIn("ouverte", str(caught.exception))
        proposal = self._proposal(convened)
        with self.assertRaises(UserError) as caught:
            proposal.votes_for = 1
        self.assertIn("ouverte", str(caught.exception))

    def test_mover_and_seconder_are_two_present_voters(self):
        alice, bruno = self._line(self.assembly, self.alice), self._line(self.assembly, self.bruno)
        proposal = self._proposal(self.assembly, mover_id=alice.id, seconder_id=bruno.id)
        self.assertEqual(proposal.mover_id, alice)
        with self.assertRaises(ValidationError):
            proposal.seconder_id = alice
        with self.assertRaises(ValidationError):
            proposal.seconder_id = self._line(self.assembly, self.orphan_org)

    def test_everything_is_locked_once_closed(self):
        proposal = self._proposal(self.assembly)
        self._vote(proposal, 4, 1)
        self.assembly.action_close()
        self.assertEqual(self.assembly.state, "closed")
        with self.assertRaises(UserError):
            proposal.write({"name": "Réécrite après coup"})
        with self.assertRaises(UserError):
            proposal.votes_for = 6
        with self.assertRaises(UserError):
            self._proposal(self.assembly)
        with self.assertRaises(UserError):
            proposal.unlink()
        with self.assertRaises(UserError):
            self._line(self.assembly, self.henri).attendance = "absent"
        with self.assertRaises(UserError):
            self.assembly.quorum_count = 3
        self.assembly.minutes = "<p>Le procès-verbal se rédige après la clôture.</p>"
        self.assertIn("après la clôture", self.assembly.minutes)

    def test_a_voted_proposal_is_not_deleted(self):
        proposal = self._proposal(self.assembly)
        self._vote(proposal, 4, 1)
        with self.assertRaises(UserError):
            proposal.unlink()

    def test_a_proposal_does_not_move_to_another_assembly(self):
        """🔴 Une proposition votée ne passe pas dans une assemblée close.

        Le verrou se lisait sur l'assemblée d'origine, ouverte : la
        proposition arrivait « adoptée » dans une assemblée close qui ne
        l'avait jamais vue, prête pour le registre corporatif.
        """
        closed = self._opened(name="Assemblée close (essai)")
        closed.action_close()
        proposal = self._proposal(self.assembly, name="Votée ailleurs (essai)")
        self._vote(proposal, 4, 0)
        self.env.invalidate_all()
        with self.assertRaises(UserError) as caught:
            proposal.with_user(self.agent).write({"assembly_id": closed.id})
        self.assertIn("ne change pas d'assemblée", str(caught.exception))
        with self.assertRaises(UserError) as caught:
            closed.with_user(self.agent).write({"proposal_ids": [Command.link(proposal.id)]})
        self.assertIn("ne change pas d'assemblée", str(caught.exception))
        self.env.invalidate_all()
        self.assertEqual(proposal.assembly_id, self.assembly)
        self.assertFalse(closed.proposal_ids)

    def test_no_proposal_slips_into_a_closed_assembly_by_default(self):
        """L'assemblée peut venir d'une valeur par défaut du contexte, qu'un
        client RPC fournit à sa guise : le verrou lit l'assemblée de la
        proposition créée, pas seulement les valeurs reçues."""
        closed = self._opened(name="Assemblée close (essai)")
        closed.action_close()
        Proposal = self.env["bf.membership.assembly.proposal"].with_user(self.agent)
        with self.assertRaises(UserError) as caught:
            Proposal.with_context(default_assembly_id=closed.id).create({"name": "Glissée après coup (essai)"})
        self.assertIn("close", str(caught.exception))
        self.assertFalse(closed.proposal_ids)

    def test_the_majority_is_fixed_once_a_result_is_entered(self):
        """3 pour et 2 contre, à la majorité simple : adoptée. Passer aux deux
        tiers ou à un pourcentage après coup la ferait tomber sur les mêmes
        totaux. Rejoué dans le rôle de l'agent."""
        proposal = self._proposal(self.assembly, majority="percent", majority_percent=55.0)
        proposal.with_user(self.agent).write({"majority": "simple"})
        self._vote(proposal, 3, 2)
        self.assertEqual(proposal.result, "adopted")
        as_agent = proposal.with_user(self.agent)
        for values in ({"majority": "two_thirds"}, {"majority": "percent"}, {"majority_percent": 75.0}):
            with self.assertRaises(UserError, msg=str(values)) as caught:
                as_agent.write(values)
            self.assertIn("majorité requise ne change plus", str(caught.exception))
        self.env.invalidate_all()
        self.assertEqual((proposal.majority, proposal.result), ("simple", "adopted"))
        as_agent.write({"votes_for": 2, "votes_against": 3})
        self.assertEqual(proposal.result, "rejected", "Les totaux, eux, se corrigent.")

    def test_the_majority_stays_fixed_after_the_totals_are_cleared(self):
        """🔴 Le contournement par la remise à zéro, de bout en bout, dans le rôle de l'agent.

        2 pour, 1 contre : adoptée. Totaux remis à zéro (permis à main levée).
        Majorité portée à 90 % : refusé, parce que la majorité se fige au
        premier dépouillement, pas sur les totaux du moment. 2 pour, 1 contre
        de nouveau : adoptée, comme la première fois. Chaque changement est
        consigné au fil de l'assemblée, au nom de la personne.
        """
        proposal = self._proposal(self.assembly, name="Adoption du budget (essai)").with_user(self.agent)
        proposal.write({"votes_for": 2, "votes_against": 1})
        self.assertEqual(proposal.result, "adopted")
        proposal.write({"votes_for": 0, "votes_against": 0})
        self.assertEqual(proposal.result, "pending")
        self.assertTrue(proposal.tally_started, "Le drapeau ne retombe pas avec les totaux.")
        with self.assertRaises(UserError) as caught:
            proposal.write({"majority": "percent", "majority_percent": 90.0})
        self.assertIn("majorité requise ne change plus", str(caught.exception))
        proposal.write({"votes_for": 2, "votes_against": 1})
        self.assertEqual(proposal.result, "adopted")
        with self.assertRaises(UserError) as caught:
            proposal.write({"tally_started": False})
        self.assertIn("ne se saisit pas", str(caught.exception))
        self.env.invalidate_all()
        logs = self.assembly.message_ids.filtered(lambda m: "Adoption du budget (essai)" in (m.body or ""))
        self.assertEqual(len(logs), 3, "Trois saisies des totaux, trois traces.")
        self.assertTrue(all(m.author_id == self.agent.partner_id for m in logs))
        bodies = " ".join(logs.mapped("body"))
        self.assertIn("2 → 0", bodies)
        self.assertIn("Adoptée → Non votée", bodies)
        self.assertIn("Non votée → Adoptée", bodies)

    def test_a_rule_change_before_the_vote_is_traced(self):
        """Avant le premier dépouillement, la majorité se change encore, et le
        changement se consigne au fil de l'assemblée."""
        proposal = self._proposal(self.assembly, name="Règlement (essai)").with_user(self.agent)
        proposal.write({"majority": "two_thirds"})
        logs = self.assembly.message_ids.filtered(lambda m: "Règlement (essai)" in (m.body or ""))
        self.assertEqual(len(logs), 1)
        self.assertIn("Majorité simple → Deux tiers", logs.body)

    def test_what_was_put_to_the_vote_is_fixed_once_counted(self):
        """🔴 Titre, texte, proposeur et appuyeur ne changent plus après le
        premier dépouillement : le pont recopierait au registre un texte que
        l'assemblée n'a pas voté. Avant, un amendement se consigne au fil.
        Rejoué dans le rôle de l'agent."""
        alice, bruno, gilles = (self._line(self.assembly, p) for p in (self.alice, self.bruno, self.gilles))
        proposal = self._proposal(
            self.assembly, name="Cotisation 2027 (essai)", mover_id=alice.id, seconder_id=bruno.id,
            text="<p>Il est proposé de fixer la cotisation à 70 $.</p>").with_user(self.agent)
        proposal.write({"text": "<p>Il est proposé de fixer la cotisation à 75 $.</p>",
                        "seconder_id": gilles.id})
        logs = self.assembly.message_ids.filtered(lambda m: "Cotisation 2027 (essai)" in (m.body or ""))
        self.assertEqual(len(logs), 1, "L'amendement se consigne au fil.")
        self.assertIn("75 $", logs.body)
        self.assertIn("Bruno Essai → Gilles Essai", logs.body)
        self.assertEqual(logs.author_id, self.agent.partner_id)
        proposal.write({"votes_for": 4, "votes_against": 1})
        for values in ({"text": "<p>Il est proposé de fixer la cotisation à 90 $.</p>"},
                       {"name": "Cotisation 2028 (essai)"}, {"mover_id": gilles.id},
                       {"seconder_id": alice.id}):
            with self.assertRaises(UserError, msg=str(values)) as caught:
                proposal.write(values)
            self.assertIn("ne change plus", str(caught.exception))
        self.env.invalidate_all()
        self.assertIn("75 $", proposal.text)
        self.assertEqual((proposal.name, proposal.mover_id, proposal.seconder_id),
                         ("Cotisation 2027 (essai)", alice, gilles))

    def test_closing_rechecks_the_voices_in_the_room(self):
        """5 pour et 1 contre avec six voix ; deux présences retirées ensuite :
        la clôture refuse un décompte qui dépasse les voix de la salle."""
        proposal = self._proposal(self.assembly)
        self._vote(proposal, 5, 1)
        self._attend(self.assembly, self.alice | self.bruno, mode="absent")
        with self.assertRaises(UserError) as caught:
            self.assembly.with_user(self.agent).action_close()
        self.assertIn("avant de clore", str(caught.exception))
        self.assertEqual(self.assembly.state, "open")
