"""Assemblée par moyens technologiques et scrutin secret.

Art. 1088.1 et 1089.1 C.c.Q., introduits par la Loi 103 (2021, c. 35, a. 2
et 3), en vigueur le 9 décembre 2021 ; art. 351 al. 2 pour le droit d'exiger le
scrutin secret, qui atteint le syndicat par les art. 1039 et 334.

Ce qui est éprouvé ici tient en trois idées :

1. Le mode inscrit au procès-verbal doit être celui qui a été suivi. Deux
   contraintes le gardent, dans les deux sens, parce qu'aucune des deux ne voit
   ce que voit l'autre.
2. Un scrutin secret ne laisse en base aucun chemin de la personne vers son
   choix — et les tests le vérifient au schéma, pas seulement à l'usage.
3. « Vérifiés subséquemment » se prouve : l'urne doit être une permutation
   exacte du registre, et le contrôle doit MORDRE quand elle ne l'est plus.
"""
import re
from datetime import datetime, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged

RECEIPT_RE = re.compile(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}")


@tagged("post_install", "-at_install")
class TestRemoteAndSecretBallot(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.syndicat"].create(
            {"name": "Syndicat 1088.1", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble 1088.1", "syndicat_id": cls.syndicat.id}
        )
        cls.units = cls.env["bf.property.unit"]
        cls.owners = cls.env["res.partner"]
        for index, quote_part in enumerate([400.0, 300.0, 200.0, 100.0], start=1):
            unit = cls.env["bf.property.unit"].create(
                {
                    "name": "20%d" % index,
                    "building_id": cls.building.id,
                    "quote_part": quote_part,
                }
            )
            owner = cls.env["res.partner"].create(
                {
                    "name": "Votant %d" % index,
                    "email": "v%d@example.invalid" % index,
                }
            )
            cls.env["bf.property.ownership"].create(
                {"unit_id": unit.id, "partner_id": owner.id}
            )
            cls.units |= unit
            cls.owners |= owner

    # ── Outillage ──

    def _assembly(self, **kw):
        vals = {
            "name": "AG à distance",
            "syndicat_id": self.syndicat.id,
            "date": datetime.now() + timedelta(days=20),
            "convocation_date": datetime.now().date(),
        }
        vals.update(kw)
        assembly = self.env["bf.property.assembly"].create(vals)
        assembly.action_load_attendance()
        return assembly

    def _set_present(self, assembly, owners):
        for line in assembly.attendance_ids:
            line.status = "present" if line.partner_id in owners else "absent"

    def _line(self, assembly, owner):
        return assembly.attendance_ids.filtered(lambda a: a.partner_id == owner)

    def _secret_resolution(self, assembly, majority_type="art_1096"):
        resolution = self.env["bf.property.resolution"].create(
            {
                "name": "Résolution au scrutin secret",
                "assembly_id": assembly.id,
                "majority_type": majority_type,
                "ballot_mode": "secret",
                "secret_requested_by_id": self.owners[0].id,
            }
        )
        action = resolution.action_load_ballot()
        wizard = self.env["bf.property.ballot.issue"].browse(action["res_id"])
        return resolution, wizard

    def _receipts(self, assembly, wizard):
        """Rend {partner_id: [codes]} en relisant la liste de distribution."""
        codes = {}
        for text in wizard.distribution.splitlines():
            match = RECEIPT_RE.search(text)
            self.assertTrue(match, "Chaque ligne remise porte un récépissé.")
            for partner in assembly.attendance_ids.mapped("partner_id"):
                if partner.name in text:
                    codes.setdefault(partner.id, []).append(match.group(0))
        return codes

    def _deposit(self, resolution, code, choice):
        wizard = self.env["bf.property.ballot.deposit"].create(
            {
                "resolution_id": resolution.id,
                "receipt_code": code,
                "choice": choice,
            }
        )
        return wizard.action_deposit()

    # ── Mode de tenue (art. 1088.1) ──

    def test_remote_attendance_refused_in_an_in_person_assembly(self):
        assembly = self._assembly(participation_mode="in_person")
        line = self._line(assembly, self.owners[0])
        line.status = "present"
        with self.assertRaises(ValidationError):
            line.participation_mode = "remote"

    def test_in_person_attendance_refused_in_a_remote_assembly(self):
        assembly = self._assembly(
            participation_mode="remote", remote_means="https://exemple.invalid/ag"
        )
        line = self._line(assembly, self.owners[0])
        line.status = "present"
        with self.assertRaises(ValidationError):
            line.participation_mode = "in_person"

    def test_hybrid_assembly_holds_both_modes(self):
        assembly = self._assembly(
            participation_mode="hybrid", remote_means="https://exemple.invalid/ag"
        )
        self._set_present(assembly, self.owners[0] | self.owners[1])
        self._line(assembly, self.owners[1]).participation_mode = "remote"
        self.assertEqual(assembly.remote_attendee_count, 1)
        self.assertEqual(assembly.in_person_attendee_count, 1)

    def test_narrowing_the_mode_under_remote_attendees_is_refused(self):
        """Le double de la contrainte : c'est l'assemblée qui change, pas la ligne.

        Une contrainte posée sur la feuille de présence ne se déclenche pas
        quand c'est le mode de l'assemblée qui bouge. Sans le double, une
        assemblée hybride redevient « en personne » avec ses participants à
        distance toujours inscrits, et le procès-verbal ment.
        """
        assembly = self._assembly(
            participation_mode="hybrid", remote_means="https://exemple.invalid/ag"
        )
        self._set_present(assembly, self.owners[0])
        self._line(assembly, self.owners[0]).participation_mode = "remote"
        with self.assertRaises(ValidationError):
            assembly.participation_mode = "in_person"

    def test_an_absent_member_has_no_mode_to_contradict(self):
        """L'exemption des absents, éprouvée là où elle mord vraiment.

        Un absent n'a participé d'aucune manière : sa colonne de mode ne dit
        rien et ne peut donc rien contredire. Sans l'exemption, la feuille de
        présence d'une assemblée à distance refuserait qu'on touche la ligne de
        qui ne s'est jamais connecté — la ligne serait figée par une règle qui
        ne le concerne pas.
        """
        assembly = self._assembly(
            participation_mode="remote", remote_means="https://exemple.invalid/ag"
        )
        line = self._line(assembly, self.owners[0])
        line.status = "absent"
        line.participation_mode = "in_person"
        self.assertEqual(line.participation_mode, "in_person")

    def test_absent_line_does_not_block_a_mode_change(self):
        """Le même égard, côté assemblée : un absent ne retient pas le mode."""
        assembly = self._assembly(
            participation_mode="hybrid", remote_means="https://exemple.invalid/ag"
        )
        line = self._line(assembly, self.owners[0])
        line.status = "present"
        line.participation_mode = "remote"
        line.status = "absent"
        assembly.participation_mode = "in_person"
        self.assertEqual(assembly.participation_mode, "in_person")

    def test_remote_assembly_seeds_its_attendance_as_remote(self):
        assembly = self._assembly(
            participation_mode="remote", remote_means="https://exemple.invalid/ag"
        )
        modes = set(assembly.attendance_ids.mapped("participation_mode"))
        self.assertEqual(modes, {"remote"})

    def test_hybrid_assembly_presumes_nothing(self):
        """C'est là que les deux modes coexistent : chacun se porte à la main."""
        assembly = self._assembly(
            participation_mode="hybrid", remote_means="https://exemple.invalid/ag"
        )
        modes = set(assembly.attendance_ids.mapped("participation_mode"))
        self.assertEqual(modes, {"in_person"})

    def test_convocation_refused_without_the_means(self):
        """Art. 346 : l'avis indique le lieu, et le lien EST le lieu."""
        assembly = self._assembly(participation_mode="remote")
        with self.assertRaises(UserError):
            assembly.action_convene()

    def test_convocation_passes_once_the_means_are_given(self):
        assembly = self._assembly(participation_mode="remote")
        assembly.remote_means = "https://exemple.invalid/ag"
        assembly.action_convene()
        self.assertEqual(assembly.state, "convened")

    def test_in_person_convocation_never_asked_for_means(self):
        """La garde ne doit mordre que le distanciel : rien de neuf en salle."""
        assembly = self._assembly(participation_mode="in_person")
        assembly.action_convene()
        self.assertEqual(assembly.state, "convened")

    def test_warning_names_the_missing_attestation(self):
        assembly = self._assembly(
            participation_mode="hybrid", remote_means="https://exemple.invalid/ag"
        )
        self.assertIn("1088.1", assembly.participation_warning)
        assembly.remote_immediate_communication = True
        self.assertFalse(assembly.participation_warning)

    def test_in_person_assembly_carries_no_warning(self):
        assembly = self._assembly(participation_mode="in_person")
        self.assertFalse(assembly.participation_warning)

    def test_minutes_block_carries_the_mode_and_appends(self):
        assembly = self._assembly(
            participation_mode="hybrid",
            remote_means="https://exemple.invalid/ag",
            remote_immediate_communication=True,
        )
        assembly.minutes = "<p>Texte du secrétaire.</p>"
        assembly.action_append_participation_to_minutes()
        self.assertIn("Texte du secrétaire.", assembly.minutes)
        self.assertIn("exemple.invalid", assembly.minutes)
        self.assertIn("Hybride", assembly.minutes)

    # ── Scrutin secret : ce que la base ne doit pas savoir ──

    def test_secret_ballot_needs_no_remote_assembly(self):
        """Art. 351 al. 2, pas art. 1089.1 : le droit d'exiger le secret ne
        dépend pas du mode de tenue. Coder le secret pour le seul distanciel
        serait se tromper de source.
        """
        assembly = self._assembly(participation_mode="in_person")
        self._set_present(assembly, self.owners)
        resolution, wizard = self._secret_resolution(assembly)
        self.assertEqual(resolution.ballot_issued_count, 4)
        self.assertTrue(wizard.distribution)

    def test_register_holds_no_choice(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        self.assertEqual(len(resolution.vote_ids), 4)
        self.assertFalse(any(resolution.vote_ids.mapped("choice")))
        self.assertTrue(all(resolution.vote_ids.mapped("ballot_issued")))

    def test_writing_a_choice_on_a_secret_register_line_is_refused(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        with self.assertRaises(ValidationError):
            resolution.vote_ids[0].choice = "for"

    def test_receipt_is_never_stored_in_clear(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, wizard = self._secret_resolution(assembly)
        codes = {c for group in self._receipts(assembly, wizard).values() for c in group}
        self.assertEqual(len(codes), 4)
        stored = str(resolution.secret_ballot_ids.read())
        for code in codes:
            self.assertNotIn(code, stored)

    def test_the_urn_keeps_no_journal_of_who_wrote_when(self):
        """L'heure d'écriture rendrait l'ordre des passages, donc les votants.

        Le contrôle porte sur le SCHÉMA et non sur l'usage : `_log_access` se
        désactive d'une ligne et se réactive d'une ligne, sans qu'aucun test
        fonctionnel ne s'en aperçoive.
        """
        self.env.cr.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'bf_property_secret_ballot'
            """
        )
        columns = {row[0] for row in self.env.cr.fetchall()}
        self.assertFalse(
            columns & {"create_date", "create_uid", "write_date", "write_uid"}
        )

    def test_the_urn_order_says_nothing_of_the_attendance_order(self):
        """Le brassage est invisible à l'usage : il se prouve par répétition.

        Si les bulletins étaient créés dans l'ordre de la feuille de présence,
        la suite de leurs poids en base serait CHAQUE FOIS celle du registre,
        et le premier bulletin de l'urne serait celui du premier nom de la
        liste. Six scrutins tirés au hasard concordent tous avec une chance sur
        24 puissance 6 ; sans brassage, ils concordent tous, toujours.
        """
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        expected = [
            line.votes
            for line in assembly.attendance_ids.filtered(
                lambda a: a.status in ("present", "represented")
            )
        ]
        same_order = 0
        for index in range(6):
            resolution = self.env["bf.property.resolution"].create(
                {
                    "name": "Scrutin %d" % index,
                    "assembly_id": assembly.id,
                    "ballot_mode": "secret",
                }
            )
            resolution.action_load_ballot()
            if resolution.secret_ballot_ids.mapped("votes") == expected:
                same_order += 1
        self.assertLess(same_order, 6)

    # ── Scrutin secret : dépôt ──

    def test_deposit_records_the_choice_in_the_urn(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, wizard = self._secret_resolution(assembly)
        codes = self._receipts(assembly, wizard)
        self._deposit(resolution, codes[self.owners[0].id][0], "for")
        self.assertEqual(resolution.ballot_cast_count, 1)
        self.assertEqual(resolution.votes_for, 400.0)
        self.assertFalse(any(resolution.vote_ids.mapped("choice")))

    def test_a_receipt_serves_only_once(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, wizard = self._secret_resolution(assembly)
        code = self._receipts(assembly, wizard)[self.owners[0].id][0]
        self._deposit(resolution, code, "for")
        with self.assertRaises(UserError):
            self._deposit(resolution, code, "against")

    def test_an_unknown_receipt_is_refused(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        with self.assertRaises(UserError):
            self._deposit(resolution, "AAAA-BBBB-CCCC", "for")

    def test_a_receipt_is_read_however_it_is_typed(self):
        """Un code se dicte au téléphone : la casse et les tirets ne comptent pas."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, wizard = self._secret_resolution(assembly)
        code = self._receipts(assembly, wizard)[self.owners[0].id][0]
        self._deposit(resolution, code.lower().replace("-", " "), "for")
        self.assertEqual(resolution.ballot_cast_count, 1)

    def test_result_is_pending_until_a_ballot_is_deposited(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        self.assertEqual(resolution.result, "pending")

    def test_secret_tally_carries_the_majority(self):
        """Art. 1096 sur un scrutin secret : 700 voix pour sur 1000 présentes."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, wizard = self._secret_resolution(assembly)
        codes = self._receipts(assembly, wizard)
        self._deposit(resolution, codes[self.owners[0].id][0], "for")
        self._deposit(resolution, codes[self.owners[1].id][0], "for")
        self._deposit(resolution, codes[self.owners[2].id][0], "against")
        self._deposit(resolution, codes[self.owners[3].id][0], "abstain")
        self.assertEqual(resolution.votes_for, 700.0)
        self.assertEqual(resolution.votes_against, 200.0)
        self.assertEqual(resolution.votes_abstain, 100.0)
        self.assertEqual(resolution.result, "adopted")

    def test_undeposited_ballots_are_not_abstentions(self):
        """Ne pas voter n'est pas s'abstenir : le module ne comble pas le vide."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, wizard = self._secret_resolution(assembly)
        codes = self._receipts(assembly, wizard)
        self._deposit(resolution, codes[self.owners[0].id][0], "for")
        self.assertEqual(resolution.votes_abstain, 0.0)
        self.assertEqual(resolution.votes_not_cast, 600.0)

    def test_headcount_counts_persons_not_ballots(self):
        """Art. 1098 : le détenteur de deux fractions ne compte que pour un.

        C'est la raison d'être de la clé de votant. Une urne qui compterait des
        bulletins gonflerait la majorité en nombre du plus gros porteur.
        """
        second_unit = self.units[3]
        second_unit.ownership_ids.unlink()
        self.env["bf.property.ownership"].create(
            {"unit_id": second_unit.id, "partner_id": self.owners[0].id}
        )
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, wizard = self._secret_resolution(assembly)
        codes = self._receipts(assembly, wizard)
        for code in codes[self.owners[0].id]:
            self._deposit(resolution, code, "for")
        self.assertEqual(len(codes[self.owners[0].id]), 2)
        self.assertEqual(resolution.votes_for, 500.0)
        self.assertEqual(resolution.owners_for, 1)

    def test_a_deprived_member_gets_a_ballot_worth_nothing(self):
        """Art. 1094 : privé de vote, donc un bulletin de zéro voix.

        Il en reçoit tout de même un : le registre du scrutin doit refléter la
        feuille de présence, sans quoi l'urne ne s'y compare plus.
        """
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        self._line(assembly, self.owners[0]).voting_deprived = True
        resolution, wizard = self._secret_resolution(assembly)
        codes = self._receipts(assembly, wizard)
        self._deposit(resolution, codes[self.owners[0].id][0], "for")
        self.assertEqual(resolution.votes_for, 0.0)
        self.assertEqual(resolution.ballot_box_state, "balanced")

    def test_no_bulletin_for_the_fraction_the_syndicat_holds(self):
        """Art. 1076 : aucune voix pour ces parties, donc aucun bulletin.

        ⚠️ Traitement volontairement distinct de celui du copropriétaire privé
        de vote juste au-dessus, qui reçoit un bulletin sans poids. Celui-là
        reste un copropriétaire présent ; le syndicat, lui, « ne dispose
        d'aucune voix pour ces parties ». Un bulletin de poids nul se
        compterait dans l'urne et, son poids étant unique, gonflerait le nombre
        de bulletins que leur poids expose. Le registre et l'urne se comparent
        entre eux, pas à la feuille de présence : les exclure des deux laisse
        le contrôle de permutation intact.
        """
        self.syndicat.partner_id = self.env["res.partner"].create(
            {"name": "Syndicat 1088.1 — personne morale",
             "email": "syndicat1088@example.invalid"}
        )
        self.units[3].ownership_ids.filtered("is_current").unlink()
        self.env["bf.property.ownership"].create(
            {"unit_id": self.units[3].id,
             "partner_id": self.syndicat.partner_id.id}
        )
        assembly = self._assembly()
        self._set_present(assembly, self.owners | self.syndicat.partner_id)
        self.assertEqual(assembly.total_votes, 900.0)
        resolution, wizard = self._secret_resolution(assembly)
        self.assertEqual(resolution.ballot_issued_count, 3)
        self.assertNotIn(
            self.syndicat.partner_id.id, self._receipts(assembly, wizard)
        )
        self.assertEqual(resolution.ballot_box_state, "balanced")

    # ── « Vérifiés subséquemment » (art. 1089.1) ──

    def test_the_urn_balances_against_the_register(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        self.assertEqual(resolution.ballot_box_state, "balanced")

    def test_a_missing_ballot_unbalances_the_urn(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        resolution.secret_ballot_ids[0].unlink()
        self.assertEqual(resolution.ballot_box_state, "unbalanced")

    def test_a_ballot_of_the_wrong_weight_unbalances_the_urn(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        resolution.secret_ballot_ids[0].votes += 1.0
        self.assertEqual(resolution.ballot_box_state, "unbalanced")

    def test_a_stuffed_urn_is_caught(self):
        """Un bulletin de plus, du poids d'un autre : le compte des voix reste
        plausible et seul le profil par personne le démasque."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        model = self.env["bf.property.secret.ballot"]
        model.create(
            {
                "resolution_id": resolution.id,
                "receipt_hash": "f" * 64,
                "voter_key": "intrus",
                "votes": 100.0,
            }
        )
        self.assertEqual(resolution.ballot_box_state, "unbalanced")

    def test_touching_the_attendance_sheet_after_the_fact_unbalances_the_urn(self):
        """L'urne est un instantané ; le registre suit la feuille de présence.

        Priver quelqu'un de son vote APRÈS la remise des bulletins ne retire
        rien de l'urne, où son bulletin pèse toujours ses voix. Le contrôle est
        là pour que ce décalage se voie : c'est exactement l'« erreur dans le
        calcul des voix » que l'art. 1103 sanctionne d'une annulation.
        """
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        self.assertEqual(resolution.ballot_box_state, "balanced")
        self._line(assembly, self.owners[0]).voting_deprived = True
        self.assertEqual(resolution.ballot_box_state, "unbalanced")

    def test_receipt_check_returns_the_ballot_to_its_owner(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, wizard = self._secret_resolution(assembly)
        code = self._receipts(assembly, wizard)[self.owners[0].id][0]
        self._deposit(resolution, code, "against")
        check = self.env["bf.property.ballot.receipt"].create(
            {"resolution_id": resolution.id, "receipt_code": code}
        )
        check.action_check()
        self.assertIn("Contre", check.outcome)

    def test_receipt_check_says_when_nothing_was_deposited(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, wizard = self._secret_resolution(assembly)
        code = self._receipts(assembly, wizard)[self.owners[0].id][0]
        check = self.env["bf.property.ballot.receipt"].create(
            {"resolution_id": resolution.id, "receipt_code": code}
        )
        check.action_check()
        self.assertIn("jamais déposé", check.outcome)

    def test_recount_leaves_a_trace_on_the_resolution(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        before = len(resolution.message_ids)
        resolution.action_verify_ballot_box()
        self.assertGreater(len(resolution.message_ids), before)

    def test_recount_refused_on_a_show_of_hands(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution = self.env["bf.property.resolution"].create(
            {"name": "À main levée", "assembly_id": assembly.id}
        )
        resolution.action_load_ballot()
        with self.assertRaises(UserError):
            resolution.action_verify_ballot_box()

    # ── Le secret que l'arithmétique ne tient pas ──

    def test_unique_weights_expose_their_ballots(self):
        """Quatre quotes-parts distinctes : quatre bulletins identifiables."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        self.assertEqual(resolution.secret_exposure_count, 4)

    def test_equal_weights_hide_their_ballots(self):
        """Deux votants de même poids : leurs deux bulletins cessent de parler."""
        self.units[1].quote_part = 400.0
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        exposed = resolution.secret_ballot_ids.filtered("exposed_by_weight")
        self.assertEqual(resolution.secret_exposure_count, 2)
        self.assertEqual(set(exposed.mapped("votes")), {200.0, 100.0})

    # ── Ce qui se fige, et ce qui se reprend ──

    def test_a_secret_ballot_does_not_reopen(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        with self.assertRaises(UserError):
            resolution.action_load_ballot()

    def test_reset_before_any_deposit_clears_everything(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        resolution.action_reset_ballot()
        self.assertFalse(resolution.secret_ballot_ids)
        self.assertFalse(resolution.vote_ids)
        self.assertEqual(resolution.ballot_box_state, "open")

    def test_reset_after_a_deposit_is_refused(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, wizard = self._secret_resolution(assembly)
        code = self._receipts(assembly, wizard)[self.owners[0].id][0]
        self._deposit(resolution, code, "for")
        with self.assertRaises(UserError):
            resolution.action_reset_ballot()

    def test_the_ballot_mode_freezes_once_the_scrutin_is_open(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution, _wizard = self._secret_resolution(assembly)
        with self.assertRaises(UserError):
            resolution.ballot_mode = "open"

    def test_the_ballot_mode_moves_freely_before_the_scrutin(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution = self.env["bf.property.resolution"].create(
            {"name": "Question", "assembly_id": assembly.id}
        )
        resolution.ballot_mode = "secret"
        self.assertEqual(resolution.ballot_mode, "secret")

    def test_two_secret_ballots_do_not_open_at_once(self):
        """Les récépissés de la seconde seraient perdus sans un mot."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        pair = self.env["bf.property.resolution"].create(
            [
                {
                    "name": "Première",
                    "assembly_id": assembly.id,
                    "ballot_mode": "secret",
                },
                {
                    "name": "Seconde",
                    "assembly_id": assembly.id,
                    "ballot_mode": "secret",
                },
            ]
        )
        with self.assertRaises(UserError):
            pair.action_load_ballot()

    def test_a_show_of_hands_still_works_as_before(self):
        """Le mode par défaut ne change pas : l'ancien chemin reste ouvert."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        resolution = self.env["bf.property.resolution"].create(
            {"name": "À main levée", "assembly_id": assembly.id}
        )
        resolution.action_load_ballot()
        self.assertEqual(len(resolution.vote_ids), 4)
        self.assertEqual(set(resolution.vote_ids.mapped("choice")), {"abstain"})
        self.assertEqual(resolution.ballot_box_state, "na")
