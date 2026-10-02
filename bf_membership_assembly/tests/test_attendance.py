from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import AssemblyCase


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestProxies(AssemblyCase):

    def setUp(self):
        super().setUp()
        self.assembly = self._convened(proxy_allowed=True, proxy_max_per_holder=1)
        self._attend(self.assembly, self.alice | self.gilles)
        self.line = lambda partner: self._line(self.assembly, partner)

    def _give(self, giver, holder):
        self.line(giver).write({"attendance": "proxy", "proxy_holder_id": self.line(holder).id})

    def test_proxies_are_refused_by_default(self):
        assembly = self._convened()
        self._attend(assembly, self.alice)
        with self.assertRaises(ValidationError):
            self._line(assembly, self.bruno).write({
                "attendance": "proxy", "proxy_holder_id": self._line(assembly, self.alice).id})

    def test_a_proxy_to_a_present_voter_carries_the_voice(self):
        self._give(self.bruno, self.alice)
        self.assertTrue(self.line(self.bruno).has_voice)
        self.assertEqual(self.assembly.represented_count, 1)
        self.assertEqual(self.assembly.voice_count, 3)

    def test_a_proxy_to_oneself_is_refused(self):
        with self.assertRaises(ValidationError):
            self._give(self.bruno, self.bruno)

    def test_a_proxy_goes_to_a_present_voter(self):
        with self.assertRaises(ValidationError):
            self._give(self.bruno, self.henri)

    def test_no_proxy_chain_either_way(self):
        """B porte la procuration de A : B ne donne pas la sienne, et nul ne la lui confie."""
        self._give(self.bruno, self.alice)
        with self.assertRaises(ValidationError):
            self._give(self.francine, self.bruno)
        with self.assertRaises(ValidationError):
            self._give(self.alice, self.gilles)

    def test_holder_cannot_leave_while_holding(self):
        self._give(self.bruno, self.alice)
        with self.assertRaises(ValidationError):
            self.line(self.alice).attendance = "absent"

    def test_cap_per_holder(self):
        self._give(self.bruno, self.alice)
        with self.assertRaises(ValidationError):
            self._give(self.henri, self.alice)
        self.assembly.proxy_max_per_holder = 2
        self._give(self.henri, self.alice)
        self.assertEqual(self.line(self.alice).proxy_received_count, 2)

    def test_forbidding_proxies_under_existing_ones_is_refused(self):
        self._give(self.bruno, self.alice)
        with self.assertRaises(ValidationError):
            self.assembly.proxy_allowed = False

    def test_an_organization_without_delegate_gives_no_proxy(self):
        """Personne n'a qualité pour signer la procuration d'une organisation
        sans délégué votant."""
        with self.assertRaises(ValidationError) as caught:
            self._give(self.orphan_org, self.alice)
        self.assertIn("délégué votant", str(caught.exception))

    def test_the_cap_counts_per_person(self):
        """Carole est membre et déléguée de l'organisme : deux lignes, une
        personne. Le plafond d'une procuration par mandataire se compte sur
        elle, pas sur chacune de ses lignes."""
        self._membership(self.carole, payment_state="paid", date_start=self.start, date_end=self.end)
        assembly = self._convened(name="Assemblée de Carole (essai)", proxy_allowed=True,
                                  proxy_max_per_holder=1)
        own, org = self._line(assembly, self.carole), self._line(assembly, self.member_org)
        self.assertEqual(org.representative_id, self.carole)
        (own | org).write({"attendance": "onsite"})
        self._line(assembly, self.bruno).write({"attendance": "proxy", "proxy_holder_id": own.id})
        with self.assertRaises(ValidationError) as caught:
            self._line(assembly, self.henri).write({"attendance": "proxy", "proxy_holder_id": org.id})
        self.assertIn("plafond", str(caught.exception))

    def test_the_cap_follows_the_person_who_votes_for_an_organization(self):
        """🔴 Plafond d'une procuration. Denis, présent, porte celle de Bruno ;
        la ligne de l'organisme, où vote Carole, porte celle de Henri. Passer la
        personne qui vote de l'organisme à Denis lui ferait porter deux
        procurations : refusé. Et une liste qui dépasse le plafond, écrite hors
        des contrôles, ne s'ouvre pas."""
        self._membership(self.denis, payment_state="paid", date_start=self.start, date_end=self.end)
        carole = self.member_org.delegate_ids.filtered(lambda d: d.partner_id == self.carole)
        carole.date_to = self.record_day
        self.env["bf.membership.delegate"].create({
            "organization_id": self.member_org.id, "partner_id": self.denis.id,
            "date_from": self.record_day + relativedelta(days=1)})
        assembly = self._convened(name="Assemblée de Denis (essai)", proxy_allowed=True,
                                  proxy_max_per_holder=1)
        denis, org = self._line(assembly, self.denis), self._line(assembly, self.member_org)
        self.assertEqual(org.representative_id, self.carole)
        (denis | org).write({"attendance": "onsite"})
        self._line(assembly, self.bruno).write({"attendance": "proxy", "proxy_holder_id": denis.id})
        self._line(assembly, self.henri).write({"attendance": "proxy", "proxy_holder_id": org.id})
        with self.assertRaises(ValidationError) as caught:
            org.with_user(self.agent).write({"representative_id": self.denis.id})
        self.assertIn("plafond", str(caught.exception))
        # Écrite hors des contrôles (un import en SQL, une ancienne version) :
        # l'ouverture recompte.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_membership_assembly_voter SET representative_id = %s WHERE id = %s",
            [self.denis.id, org.id])
        self.env.invalidate_all()
        with self.assertRaises(ValidationError) as caught:
            assembly.action_open()
        self.assertIn("plafond", str(caught.exception))
        self.assertEqual(assembly.state, "convened")

    def test_leaving_proxy_clears_the_holder(self):
        self._give(self.bruno, self.alice)
        self.line(self.bruno).attendance = "onsite"
        self.assertFalse(self.line(self.bruno).proxy_holder_id)


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestQuorum(AssemblyCase):

    def test_majority_by_default(self):
        """Sept votants : la majorité, c'est quatre, pas trois et demi."""
        assembly = self._convened()
        self.assertEqual(assembly.quorum_mode, "majority")
        self.assertEqual(assembly.voter_count, 7)
        self.assertEqual(assembly.quorum_required, 4)
        self._attend(assembly, self.alice | self.bruno | self.gilles)
        self.assertFalse(assembly.quorum_met)
        self._attend(assembly, self.henri, mode="remote")
        self.assertTrue(assembly.quorum_met)
        self.assertEqual(assembly.remote_count, 1)

    def test_majority_of_an_even_list_is_more_than_half(self):
        """Huit votants (Karine, en grâce, admise) : quatre ne font pas la majorité, cinq oui."""
        assembly = self._convened(include_grace=True)
        self.assertEqual(assembly.voter_count, 8)
        self.assertEqual(assembly.quorum_required, 5)
        self._attend(assembly, self.alice | self.bruno | self.gilles | self.henri)
        self.assertFalse(assembly.quorum_met)
        self._attend(assembly, self.francine)
        self.assertTrue(assembly.quorum_met)

    def test_quorum_as_a_number(self):
        assembly = self._convened(quorum_mode="count", quorum_count=2)
        self._attend(assembly, self.alice)
        self.assertFalse(assembly.quorum_met)
        self._attend(assembly, self.member_org)
        self.assertTrue(assembly.quorum_met, "L'organisation présente par son délégué compte.")

    def test_quorum_as_a_percentage_rounds_up(self):
        """30 % de 7 votants, c'est 2,1 : il faut trois personnes."""
        assembly = self._convened(quorum_mode="percent", quorum_percent=30.0)
        self.assertEqual(assembly.quorum_required, 3)
        self._attend(assembly, self.alice | self.bruno)
        self.assertFalse(assembly.quorum_met)
        self._attend(assembly, self.gilles)
        self.assertTrue(assembly.quorum_met)

    def test_proxies_count_toward_quorum(self):
        assembly = self._convened(quorum_mode="count", quorum_count=2, proxy_allowed=True)
        self._attend(assembly, self.alice)
        self._line(assembly, self.bruno).write({
            "attendance": "proxy", "proxy_holder_id": self._line(assembly, self.alice).id})
        self.assertTrue(assembly.quorum_met)

    def test_quorum_rule_is_checked(self):
        with self.assertRaises(ValidationError):
            self._assembly(quorum_mode="percent", quorum_percent=0.0)
        with self.assertRaises(ValidationError):
            self._assembly(quorum_mode="count", quorum_count=0)


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestAttendanceTrace(AssemblyCase):

    def test_attendance_proxy_and_representative_changes_are_traced(self):
        """Une présence, une procuration, un délégué remplacé : chaque
        changement se consigne au fil de l'assemblée, au nom de la personne.
        Rejoué dans le rôle de l'agent."""
        # Carole porte la voix de l'organisme jusqu'à la date de référence,
        # Denis à partir du lendemain : les deux sont des délégués permis.
        carole = self.member_org.delegate_ids.filtered(lambda d: d.partner_id == self.carole)
        carole.date_to = self.record_day
        self.env["bf.membership.delegate"].create({
            "organization_id": self.member_org.id, "partner_id": self.denis.id,
            "date_from": self.record_day + relativedelta(days=1)})
        assembly = self._convened(proxy_allowed=True)
        line = lambda partner: self._line(assembly, partner).with_user(self.agent)
        line(self.alice).write({"attendance": "onsite"})
        line(self.bruno).write({"attendance": "proxy", "proxy_holder_id": line(self.alice).id})
        org = line(self.member_org)
        other = (self.carole | self.denis) - org.representative_id
        org.write({"representative_id": other.id})
        self.env.invalidate_all()
        logs = assembly.message_ids.filtered(lambda m: "Présences et procurations" in (m.body or ""))
        self.assertEqual(len(logs), 3)
        self.assertTrue(all(m.author_id == self.agent.partner_id for m in logs))
        bodies = " ".join(logs.mapped("body"))
        self.assertIn("Alice Essai (Présence : Absence → Sur place)", bodies)
        self.assertIn("Bruno Essai (Présence : Absence → Par procuration, Mandataire : (personne) → Alice Essai)",
                      bodies)
        self.assertIn("Personne qui vote", bodies)
        self.assertIn(other.name, bodies)
