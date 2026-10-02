from dateutil.relativedelta import relativedelta

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import new_test_user, tagged

from .common import AssemblyCase


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestVoters(AssemblyCase):

    def test_voters_are_read_at_the_record_date(self):
        """Ni le non-payé, ni la catégorie sans vote, ni l'entrée tardive, ni la grâce d'office."""
        assembly = self._assembly()
        assembly.action_build_voters()
        self.assertEqual(assembly.voter_ids.member_id, self.expected_voters)
        self.assertNotIn(self.jules, assembly.voter_ids.member_id, "Entré après la date de référence.")
        self.assertNotIn(self.ines, assembly.voter_ids.member_id, "Catégorie sans droit de vote.")
        self.assertNotIn(self.louis, assembly.voter_ids.member_id, "Adhésion jamais payée.")
        self.assertNotIn(self.karine, assembly.voter_ids.member_id, "En grâce, exclue d'office.")

    def test_record_date_decides_not_today(self):
        """La même assemblée, date de référence aujourd'hui : Jules y entre."""
        assembly = self._assembly(record_date=self.today)
        assembly.action_build_voters()
        self.assertIn(self.jules, assembly.voter_ids.member_id)

    def test_membership_expired_since_record_date_still_votes(self):
        """🔴 Une adhésion qui couvrait la date de référence compte, même échue depuis.

        Lire `_covers()` (état « en règle » aujourd'hui) l'aurait exclue.
        """
        marc = self.env["res.partner"].create({"name": "Marc Échu"})
        membership = self._membership(
            marc, payment_state="paid",
            date_start=self._day(days=-100), date_end=self._day(days=-10))
        membership.state = "expired"
        assembly = self._assembly(record_date=self._day(days=-20))
        assembly.action_build_voters()
        self.assertIn(marc, assembly.voter_ids.member_id)

    def test_withdrawn_before_the_meeting_does_not_vote(self):
        membership = self.bruno.membership_ids
        membership._withdraw(self.today, "Démission par lettre")
        assembly = self._assembly()
        assembly.action_build_voters()
        self.assertNotIn(self.bruno, assembly.voter_ids.member_id)

    def test_grace_follows_the_setting(self):
        assembly = self._assembly(include_grace=True)
        assembly.action_build_voters()
        line = self._line(assembly, self.karine)
        self.assertTrue(line, "Échue cinq jours avant la date de référence, délai de grâce de 30 jours.")
        self.assertTrue(line.in_grace)

    def test_organization_is_one_line_voting_through_its_delegate(self):
        # Une organisation a une voix (socle) : Denis est délégué sans vote.
        self.env["bf.membership.delegate"].create({
            "organization_id": self.member_org.id, "partner_id": self.denis.id,
            "date_from": self.start + relativedelta(days=1), "voting": False})
        assembly = self._assembly()
        assembly.action_build_voters()
        lines = assembly.voter_ids.filtered(lambda v: v.member_id == self.member_org)
        self.assertEqual(len(lines), 1, "Deux délégués, une seule voix.")
        self.assertEqual(lines.representative_id, self.carole)
        self.assertFalse(lines.missing_representative)
        self.assertEqual(self._line(assembly, self.alice).representative_id, self.alice)

    def test_organization_without_delegate_is_flagged_and_cannot_attend(self):
        assembly = self._assembly()
        assembly.action_build_voters()
        orphan = self._line(assembly, self.orphan_org)
        self.assertTrue(orphan.missing_representative)
        self.assertEqual(assembly.missing_representative_count, 1)
        with self.assertRaises(ValidationError):
            orphan.attendance = "onsite"

    def test_person_member_votes_herself(self):
        assembly = self._assembly()
        assembly.action_build_voters()
        with self.assertRaises(ValidationError):
            self._line(assembly, self.alice).representative_id = self.bruno

    def test_rebuilding_keeps_what_was_recorded(self):
        assembly = self._convened()
        self._line(assembly, self.alice).attendance = "onsite"
        assembly.action_build_voters()
        self.assertEqual(self._line(assembly, self.alice).attendance, "onsite")
        self.assertEqual(len(assembly.voter_ids), len(self.expected_voters))

    def test_list_is_frozen_once_open(self):
        # Denis est un délégué votant valide : seul le gel peut refuser de le
        # nommer. Sans lui, la contrainte sur le délégué refuserait à la place
        # du gel (ValidationError hérite de UserError) et l'essai ne
        # prouverait rien.
        # Une organisation n'a qu'un délégué votant à la fois (socle) : Carole
        # porte la voix à la date de référence, Denis le jour de l'assemblée.
        # Les deux sont des représentants permis.
        carole = self.member_org.delegate_ids.filtered(lambda d: d.partner_id == self.carole)
        carole.date_to = self.record_day
        self.env["bf.membership.delegate"].create({
            "organization_id": self.member_org.id, "partner_id": self.denis.id,
            "date_from": self.record_day + relativedelta(days=1)})
        assembly = self._opened()
        line = self._line(assembly, self.alice)
        with self.assertRaises(UserError):
            assembly.action_build_voters()
        with self.assertRaises(UserError):
            line.unlink()
        with self.assertRaises(UserError) as caught:
            self.env["bf.membership.assembly.voter"].create({
                "assembly_id": assembly.id, "member_id": self.jules.id})
        self.assertIn("gelée", str(caught.exception))
        member_org = self._line(assembly, self.member_org)
        self.assertIn(self.denis, member_org.allowed_representative_ids)
        other = (self.carole | self.denis) - member_org.representative_id
        with self.assertRaises(UserError) as caught:
            member_org.representative_id = other
        self.assertIn("gelée", str(caught.exception))
        line.attendance = "remote"
        self.assertEqual(line.attendance, "remote", "Les présences se notent pendant l'assemblée.")

    def test_a_line_does_not_move_to_another_assembly(self):
        """🔴 Une ligne ne passe pas d'une assemblée en brouillon à une assemblée close.

        Le gel se lisait sur l'assemblée d'origine : le brouillon laissait
        passer, et la liste close gagnait un votant. Rejoué comme un client
        RPC, dans le rôle de l'agent, directement et par la liste de
        l'assemblée close.
        """
        closed = self._opened()
        closed.action_close()
        draft = self._assembly(name="Brouillon (essai)", record_date=self.today)
        draft.action_build_voters()
        jules = self._line(draft, self.jules)
        self.assertTrue(jules, "Jules vote à la date de référence du brouillon.")
        before = closed.voter_count
        self.env.invalidate_all()
        with self.assertRaises(UserError) as caught:
            jules.with_user(self.agent).write({"assembly_id": closed.id})
        self.assertIn("ne change pas d'assemblée", str(caught.exception))
        with self.assertRaises(UserError) as caught:
            closed.with_user(self.agent).write({"voter_ids": [Command.link(jules.id)]})
        self.assertIn("ne change pas d'assemblée", str(caught.exception))
        self.env.invalidate_all()
        self.assertEqual(jules.assembly_id, draft)
        self.assertEqual(closed.voter_count, before)

    def test_a_line_added_by_hand_tells_the_truth(self):
        """🔴 Une personne non membre n'entre pas à la liste en citant l'adhésion
        d'une autre.

        Rejoué dans le rôle de l'agent, sur une assemblée convoquée. Une ligne
        ajoutée à la main doit citer l'adhésion du membre lui-même, et ce
        membre doit voter à la date de référence. L'ajout se trace au fil.
        """
        assembly = self._convened()
        Voter = self.env["bf.membership.assembly.voter"].with_user(self.agent)
        alice_membership = self._line(assembly, self.alice).membership_id
        henri_line = self._line(assembly, self.henri)
        henri_membership = henri_line.membership_id
        henri_line.with_user(self.agent).unlink()
        stranger = self.env["res.partner"].create({"name": "Personne non membre (essai)"})
        cases = (
            # Une personne non membre, qui cite l'adhésion d'une autre.
            (stranger, alice_membership, "n'est pas celle de"),
            # Henri vote, mais l'adhésion citée n'est pas la sienne.
            (self.henri, alice_membership, "n'est pas celle de"),
            # Sa propre adhésion, mais entrée après la date de référence.
            (self.jules, self.jules.membership_ids, "ne vote pas"),
            (self.henri, self.env["bf.membership"], "doit citer"),
        )
        for member, membership, message in cases:
            with self.assertRaises(ValidationError, msg=member.name) as caught:
                Voter.create({"assembly_id": assembly.id, "member_id": member.id,
                              "membership_id": membership.id or False})
            self.assertIn(message, str(caught.exception), member.name)
        line = Voter.create({"assembly_id": assembly.id, "member_id": self.henri.id,
                             "membership_id": henri_membership.id})
        self.assertEqual(line.member_id, self.henri)
        logs = assembly.message_ids.filtered(lambda m: "Ajout à la main" in (m.body or ""))
        self.assertEqual(len(logs), 1, "Un ajout à la main, une trace au fil.")
        self.assertIn("Henri Essai", logs.body)
        self.assertEqual(logs.author_id, self.agent.partner_id)
        # Une ligne existante ne change ni de membre, ni d'adhésion pour celle d'une autre.
        alice = self._line(assembly, self.alice).with_user(self.agent)
        with self.assertRaises(UserError) as caught:
            alice.write({"member_id": stranger.id})
        self.assertIn("ne change pas de membre", str(caught.exception))
        with self.assertRaises(ValidationError) as caught:
            alice.write({"membership_id": henri_membership.id})
        self.assertIn("n'est pas celle de", str(caught.exception))


    def test_grace_and_channel_of_a_line_added_by_hand_are_computed(self):
        """La grâce et le canal de l'avis d'une ligne ajoutée à la main sont ceux
        que la liste bâtie lui aurait donnés : ils ne se saisissent ni à la
        création ni ensuite. Rejoué dans le rôle de l'agent."""
        assembly = self._convened(include_grace=True)
        Voter = self.env["bf.membership.assembly.voter"].with_user(self.agent)
        karine, alice = self._line(assembly, self.karine), self._line(assembly, self.alice)
        self.assertEqual((karine.in_grace, alice.notice_channel), (True, "email"))
        added = {}
        for partner, line in ((self.karine, karine), (self.alice, alice)):
            added[partner] = {"assembly_id": assembly.id, "member_id": partner.id,
                              "membership_id": line.membership_id.id}
            line.with_user(self.agent).unlink()
        for values in (dict(added[self.karine], in_grace=False),
                       dict(added[self.alice], notice_channel="post")):
            with self.assertRaises(UserError) as caught:
                Voter.create(values)
            self.assertIn("se calculent", str(caught.exception))
        karine, alice = Voter.create(added[self.karine]), Voter.create(added[self.alice])
        self.assertEqual((karine.in_grace, karine.notice_channel), (True, "post"))
        self.assertEqual((alice.in_grace, alice.notice_channel), (False, "email"))
        with self.assertRaises(UserError) as caught:
            karine.write({"in_grace": False})
        self.assertIn("se calculent", str(caught.exception))

    def test_a_line_removed_by_hand_is_traced(self):
        """Après la convocation, une ligne retirée à la main se trace au fil de
        l'assemblée, au nom de la personne, comme un ajout. Celle que la liste
        bâtie retire est dite par son propre message."""
        assembly = self._convened()
        self._line(assembly, self.henri).with_user(self.agent).unlink()
        traces = assembly.message_ids.filtered(lambda m: "Retrait à la main" in (m.body or ""))
        self.assertEqual(len(traces), 1, "Un retrait à la main, une trace au fil.")
        self.assertIn("Henri Essai", traces.body)
        self.assertEqual(traces.author_id, self.agent.partner_id)
        # Bruno se retire : la liste rebâtie l'enlève, sans trace « à la main ».
        self.bruno.membership_ids._withdraw(self.today, "Démission par lettre")
        assembly.with_user(self.agent).action_build_voters()
        self.assertFalse(self._line(assembly, self.bruno))
        traces = assembly.message_ids.filtered(lambda m: "Retrait à la main" in (m.body or ""))
        self.assertEqual(len(traces), 1)


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestPartnerMerge(AssemblyCase):
    """🔴 La fusion de contacts réécrit les lignes de votant en SQL.

    Quand le contact gardé figure déjà à la liste, l'assistant d'Odoo
    supprime la ligne du contact fusionné, avec ses bulletins en cascade.
    """

    def _merge(self, source, kept):
        Merge = self.env["base.partner.merge.automatic.wizard"].with_user(self.env.ref("base.user_admin"))
        Merge._merge((source | kept).ids, kept)

    def _ballots(self, proposal):
        return self.env["bf.membership.assembly.ballot"].search_count([("proposal_id", "=", proposal.id)])

    def test_merge_never_rewrites_a_held_assembly(self):
        assembly = self._opened()
        self._attend(assembly, self.persons | self.member_org)
        proposal = self._proposal(assembly, vote_mode="secret")
        proposal.action_issue_ballots()
        proposal.write({"votes_for": 4, "votes_against": 2})
        assembly.action_close()
        ballots = self._ballots(proposal)
        self.assertEqual(ballots, 6)
        # Bruno fusionné dans Alice, qui figure déjà à la même liste.
        with self.assertRaises(UserError) as caught:
            self._merge(self.bruno, self.alice)
        self.assertIn("liste des votants", str(caught.exception))
        # Carole, qui vote pour l'organisme, fusionnée dans Denis.
        with self.assertRaises(UserError):
            self._merge(self.carole, self.denis)
        self.env.invalidate_all()
        Voter = self.env["bf.membership.assembly.voter"]
        self.assertTrue(Voter.search([("assembly_id", "=", assembly.id), ("member_id", "=", self.bruno.id)]))
        self.assertEqual(self._ballots(proposal), ballots)
        self.assertEqual(self._line(assembly, self.member_org).representative_id, self.carole)

        # Le contact gardé peut figurer à la liste : ses lignes ne sont pas réécrites.
        twin = self.env["res.partner"].create({"name": "Alice Essai (doublon)", "email": "alice@essai.example"})
        self._merge(twin, self.alice)
        self.assertFalse(twin.exists())
        # Une assemblée en brouillon ne bloque pas : sa liste se rebâtit.
        draft = self._assembly(name="Brouillon (essai)", record_date=self.today)
        draft.action_build_voters()
        self.assertTrue(self._line(draft, self.jules))
        jules_twin = self.env["res.partner"].create({"name": "Jules Tardif (doublon)"})
        self._merge(self.jules, jules_twin)
        self.assertFalse(self.jules.exists())

    def test_merge_without_the_role_learns_nothing(self):
        """🔴 Sans le rôle Membres, le refus est neutre : ni le contact, ni
        l'assemblée, ni la raison.

        Rejoué par une personne qui gère les contacts sans le rôle. Un membre
        porté à une liste close, puis une personne qui votait pour une
        organisation et dont la délégation a pris fin depuis : elle n'a plus ni
        adhésion ni délégation, et seule la liste de votants la porte encore.
        Avec le rôle, le message nomme l'assemblée.
        """
        assembly = self._opened()
        self._attend(assembly, self.persons | self.member_org)
        assembly.action_close()
        groups = ["base.group_user", "base.group_partner_manager"]
        if self.env.ref("account.group_account_manager", raise_if_not_found=False):
            groups.append("account.group_account_manager")
        contacts = new_test_user(self.env, login="fusion_contacts_assemblee", groups=",".join(groups),
                                 context={"no_reset_password": True})
        Merge = self.env["base.partner.merge.automatic.wizard"]
        neutral = "Ces contacts ne peuvent pas être fusionnés avec vos droits"
        self.member_org.delegate_ids.filtered(lambda d: d.partner_id == self.carole).unlink()
        for source, kept in ((self.bruno, self.alice), (self.carole, self.denis)):
            with self.assertRaises(UserError, msg=source.name) as caught:
                Merge.with_user(contacts)._merge((source | kept).ids, kept, extra_checks=False)
            message = str(caught.exception)
            self.assertIn(neutral, message, source.name)
            self.assertNotIn(assembly.name, message, source.name)
            self.assertNotIn(source.name, message, source.name)
        with self.assertRaises(UserError) as caught:
            Merge.with_user(self.manager)._merge((self.carole | self.denis).ids, self.denis, extra_checks=False)
        self.assertIn(assembly.name, str(caught.exception))
        self.assertTrue(self.carole.exists())


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestHeldContacts(AssemblyCase):

    def test_a_contact_held_by_an_assembly_is_archived_not_deleted(self):
        """🔴 Sans le rôle Membres, supprimer un contact porté par une assemblée
        l'archive en silence : la personne qui votait pour une organisation (sa
        délégation retirée depuis), la présidence d'une assemblée close, une
        scrutatrice. Sinon le refus natif nommerait le modèle qui le retient,
        la présidence se viderait, et les scrutateurs partiraient en cascade.
        L'archivage se note au fil de l'assemblée."""
        chair = self.env["res.partner"].create({"name": "Présidente de l'AGA (essai)"})
        scrutineer = self.env["res.partner"].create({"name": "Scrutatrice de l'AGA (essai)"})
        assembly = self._opened(chair_id=chair.id)
        self._attend(assembly, self.persons | self.member_org)
        proposal = self._proposal(assembly, scrutineer_ids=[Command.set(scrutineer.ids)])
        proposal.write({"votes_for": 5, "votes_against": 1})
        assembly.action_close()
        self.member_org.delegate_ids.filtered(lambda d: d.partner_id == self.carole).unlink()
        groups = ["base.group_user", "base.group_partner_manager"]
        if self.env.ref("account.group_account_manager", raise_if_not_found=False):
            groups.append("account.group_account_manager")
        contacts = new_test_user(self.env, login="suppression_contacts", groups=",".join(groups),
                                 context={"no_reset_password": True})
        for partner in (self.carole, chair, scrutineer):
            partner.with_user(contacts).unlink()
        self.env.invalidate_all()
        for partner in (self.carole, chair, scrutineer):
            self.assertTrue(partner.exists(), partner.name)
            self.assertFalse(partner.active, partner.name)
        self.assertEqual(self._line(assembly, self.member_org).representative_id, self.carole)
        self.assertEqual(assembly.chair_id, chair)
        # Un many2many masque les contacts archivés : la ligne est bien restée.
        self.assertEqual(proposal.with_context(active_test=False).scrutineer_ids, scrutineer)
        notes = " ".join(assembly.message_ids.mapped("body"))
        for partner in (self.carole, chair, scrutineer):
            self.assertIn("%s : contact archivé" % partner.name, notes)

    def test_the_role_reads_why_an_officer_is_not_deleted(self):
        """Avec le rôle Membres, supprimer la présidente d'une assemblée close
        est refusé, et le message dit pourquoi : la présidence se viderait sans
        trace."""
        chair = self.env["res.partner"].create({"name": "Présidente de l'AGA (essai)"})
        assembly = self._opened(chair_id=chair.id)
        assembly.action_close()
        agent = new_test_user(
            self.env, login="agent_et_contacts_asm", context={"no_reset_password": True},
            groups="bf_membership.group_membership_user,base.group_partner_manager")
        with self.assertRaises(UserError) as caught:
            chair.with_user(agent).unlink()
        self.assertIn("Archivez plutôt ce contact", str(caught.exception))
        self.env.invalidate_all()
        self.assertTrue(chair.active)
        self.assertEqual(assembly.chair_id, chair)

    def test_a_rebuilt_list_names_who_it_removes(self):
        """Une liste rebâtie après la convocation nomme le membre convoqué
        qu'elle retire."""
        assembly = self._convened()
        self.bruno.membership_ids._withdraw(self.today, "Démission par lettre")
        assembly.with_user(self.agent).action_build_voters()
        logs = assembly.message_ids.filtered(lambda m: "Retirés de la liste" in (m.body or ""))
        self.assertEqual(len(logs), 1)
        self.assertIn("Bruno Essai", logs.body)
