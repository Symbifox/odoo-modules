from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged
from odoo.tools import format_date, mute_logger

from odoo.addons.bf_membership_assembly.tests.common import AssemblyCase


@tagged("post_install", "-at_install", "bf_membership_assembly_governance")
class TestCorporateBridge(AssemblyCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.secretary = new_test_user(
            cls.env, login="secretariat_aga",
            groups="bf_membership.group_membership_user,bf_corporate_governance.group_corporate_manager",
            name="Secrétariat de l'AGA")

    def setUp(self):
        super().setUp()
        self.assembly = self._opened()
        self._attend(self.assembly, self.persons | self.member_org)
        alice, carole = self._line(self.assembly, self.alice), self._line(self.assembly, self.member_org)
        self.adopted = self._proposal(
            self.assembly, name="Adoption des états financiers (essai)",
            text="<p>Il est proposé d'adopter les états financiers.</p>",
            mover_id=alice.id, seconder_id=carole.id)
        self.adopted.write({"votes_for": 4, "votes_against": 1, "votes_abstain": 1})
        self.rejected = self._proposal(self.assembly, name="Proposition rejetée (essai)")
        self.rejected.write({"votes_for": 2, "votes_against": 2})

    def _resolutions(self, proposal):
        return self.env["corporate.resolution"].search([("bf_assembly_proposal_id", "=", proposal.id)])

    def test_adopted_proposal_becomes_one_resolution_linked_both_ways(self):
        self.assembly.action_close()
        proposal = self.adopted.with_user(self.secretary)
        action = proposal.action_create_corporate_resolution()
        resolution = self._resolutions(self.adopted)
        self.assertEqual(len(resolution), 1)
        self.assertEqual(action["res_id"], resolution.id)
        self.assertEqual(resolution.bf_assembly_proposal_id, self.adopted)
        self.assertEqual(resolution.bf_assembly_id, self.assembly)
        self.assertEqual(self.adopted.corporate_resolution_id, resolution)
        self.assertEqual(resolution.resolution_type, "members")
        self.assertEqual(resolution._get_resolution_type_label(), "Résolution de l'assemblée des membres")
        self.assertEqual(resolution.meeting_type, "agm", "Assemblée annuelle : séance d'AGA.")
        self.assertEqual(resolution.status, "adopted")
        self.assertEqual(resolution.meeting_date, self.meeting_day)
        self.assertEqual(resolution.effective_date, self.meeting_day)
        self.assertEqual((resolution.vote_for, resolution.vote_against, resolution.vote_abstain), (4, 1, 1))
        self.assertFalse(resolution.unanimously_adopted)
        # Qui a proposé et qui a appuyé reste à l'assemblée, lisible par le rôle
        # Membres : le registre corporatif se lit sans ce rôle.
        self.assertFalse(resolution.mover_id | resolution.seconder_id)
        self.assertIn("états financiers", resolution.resolved_text)
        self.assertEqual(self.assembly.corporate_resolution_count, 1)

    def test_assembly_officers_sign_the_resolution(self):
        """La présidence et le secrétariat d'assemblée signent, dans cet ordre."""
        chair = self.env["res.partner"].create({"name": "Présidente d'assemblée (essai)"})
        secretary = self.env["res.partner"].create({"name": "Secrétaire d'assemblée (essai)"})
        self.assembly.with_user(self.agent).write({"chair_id": chair.id, "secretary_id": secretary.id})
        self.assembly.action_close()
        self.adopted.with_user(self.secretary).action_create_corporate_resolution()
        signatories = self._resolutions(self.adopted).signatory_ids.sorted("sequence")
        self.assertEqual(signatories.partner_id, chair | secretary)
        self.assertEqual(signatories.mapped("capacity"), ["assembly_chair", "assembly_secretary"])

    def test_second_click_creates_nothing(self):
        self.assembly.action_close()
        proposal = self.adopted.with_user(self.secretary)
        first = proposal.action_create_corporate_resolution()
        second = proposal.action_create_corporate_resolution()
        self.assertEqual(first["res_id"], second["res_id"])
        self.assertEqual(len(self._resolutions(self.adopted)), 1)
        self.assembly.with_user(self.secretary).action_create_corporate_resolutions()
        self.assertEqual(len(self._resolutions(self.adopted)), 1)
        self.assertFalse(self._resolutions(self.rejected), "Une proposition rejetée ne s'inscrit pas.")

    def test_the_database_refuses_a_duplicate(self):
        """Deux clics simultanés passent le contrôle en Python : la base, non."""
        self.assembly.action_close()
        self.adopted.with_user(self.secretary).action_create_corporate_resolution()
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.cr.savepoint():
            self.env["corporate.resolution"].create({
                "name": "Doublon", "meeting_date": self.meeting_day,
                "bf_assembly_proposal_id": self.adopted.id})
            self.env.flush_all()

    def test_special_assembly_is_a_special_meeting(self):
        special = self._opened(kind="special", name="Assemblée extraordinaire (essai)")
        self._attend(special, self.persons)
        proposal = self._proposal(special, name="Modification des règlements (essai)",
                                  majority="two_thirds")
        proposal.write({"votes_for": 4, "votes_against": 0})
        special.action_close()
        proposal.with_user(self.secretary).action_create_corporate_resolution()
        resolution = self._resolutions(proposal)
        self.assertEqual(resolution.meeting_type, "special")
        self.assertTrue(resolution.unanimously_adopted)

    def test_not_before_closing_and_not_when_rejected(self):
        with self.assertRaises(UserError):
            self.adopted.with_user(self.secretary).action_create_corporate_resolution()
        self.assembly.action_close()
        with self.assertRaises(UserError):
            self.rejected.with_user(self.secretary).action_create_corporate_resolution()
        self.assertFalse(self._resolutions(self.adopted) | self._resolutions(self.rejected))

    def test_register_rights_are_not_bypassed(self):
        """L'agent des membres tient l'assemblée ; il n'écrit pas au registre corporatif."""
        self.assembly.action_close()
        with self.assertRaises(UserError):
            self.adopted.with_user(self.agent).action_create_corporate_resolution()
        self.assertFalse(self._resolutions(self.adopted))
        self.adopted.with_user(self.secretary).action_create_corporate_resolution()
        self.assertEqual(
            self.adopted.with_user(self.agent).corporate_resolution_id, self._resolutions(self.adopted),
            "L'agent voit le lien, même sans droit sur le registre.")

    def test_register_reads_without_the_membership_role(self):
        """Une personne gestionnaire du registre, sans rôle Membres, ouvre la
        résolution inscrite : son lien vers l'assemblée se lit, sans rien
        ouvrir des membres."""
        registrar = new_test_user(
            self.env, login="registre_seul", groups="bf_corporate_governance.group_corporate_manager",
            name="Registre seul", context={"no_reset_password": True})
        self.assembly.action_close()
        self.adopted.with_user(self.secretary).action_create_corporate_resolution()
        resolution = self._resolutions(self.adopted)
        self.env.invalidate_all()
        values = resolution.with_user(registrar).web_read({
            "name": {}, "resolution_type": {},
            "bf_assembly_id": {"fields": {"display_name": {}}},
            "bf_assembly_proposal_id": {"fields": {"display_name": {}}},
            "mover_id": {"fields": {"display_name": {}}},
        })[0]
        self.assertEqual(values["resolution_type"], "members")
        self.assertEqual(values["bf_assembly_id"]["display_name"], self.assembly.name)
        self.assertFalse(values["mover_id"], "Le registre ne nomme pas qui a proposé.")

    def test_register_reads_the_totals_not_the_stored_result(self):
        """Un résultat stocké « adoptée » sur des totaux rejetés n'entre pas au
        registre corporatif : le pont réévalue les totaux.

        Le résultat est forcé en superutilisateur, seul à passer la garde des
        champs calculés : une valeur écrite à côté des totaux, quelle qu'en
        soit l'origine.
        """
        self.rejected.write({"result": "adopted"})
        self.env.invalidate_all()
        self.assertEqual(self.rejected.result, "adopted")
        self.assembly.action_close()
        with self.assertRaises(UserError) as caught:
            self.rejected.with_user(self.secretary).action_create_corporate_resolution()
        self.assertIn("n'a pas été adoptée", str(caught.exception))
        self.assembly.with_user(self.secretary).action_create_corporate_resolutions()
        self.assertTrue(self._resolutions(self.adopted))
        self.assertFalse(self._resolutions(self.rejected))

    def test_only_the_bridge_links_a_resolution(self):
        """🔴 Une résolution « Fausse », liée à la main à une proposition, ferait
        croire au pont que la proposition est déjà inscrite et bloquerait
        l'inscription légitime. Rejoué dans le rôle d'une personne
        gestionnaire du registre sans rôle Membres : par les valeurs, par une
        valeur par défaut du contexte, puis par une écriture."""
        registrar = new_test_user(
            self.env, login="registre_lien", groups="bf_corporate_governance.group_corporate_manager",
            name="Registre seul", context={"no_reset_password": True})
        Resolution = self.env["corporate.resolution"].with_user(registrar)
        with self.assertRaises(UserError) as caught:
            Resolution.create({"name": "Fausse", "meeting_date": self.meeting_day,
                               "bf_assembly_proposal_id": self.adopted.id})
        self.assertIn("se pose par l'inscription", str(caught.exception))
        fake = Resolution.with_context(default_bf_assembly_proposal_id=self.rejected.id).create({
            "name": "Fausse", "meeting_date": self.meeting_day})
        self.env.invalidate_all()
        self.assertFalse(fake.bf_assembly_proposal_id)
        with self.assertRaises(UserError) as caught:
            fake.write({"bf_assembly_proposal_id": self.adopted.id})
        self.assertIn("se pose par l'inscription", str(caught.exception))

        self.assembly.action_close()
        self.adopted.with_user(self.secretary).action_create_corporate_resolution()
        resolution = self._resolutions(self.adopted)
        self.assertEqual(resolution.name, "Adoption des états financiers (essai)")
        with self.assertRaises(UserError):
            resolution.with_user(registrar).write({"bf_assembly_proposal_id": False})
        self.assertEqual(self._resolutions(self.adopted), resolution)

    def test_the_note_dates_in_the_organisation_language(self):
        """La note de la résolution se lit au registre de l'organisme : sa date
        s'écrit dans la langue de la société, pas au format de l'interface de
        la personne qui inscrit (« 10/31/2026 » au milieu d'une phrase en
        français)."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env.company.partner_id.lang = "fr_CA"
        self.assembly.action_close()
        self.adopted.with_user(self.secretary).with_context(lang="en_US").action_create_corporate_resolution()
        notes = self._resolutions(self.adopted).notes
        self.assertIn(format_date(self.env, self.meeting_day, lang_code="fr_CA", date_format="d MMMM y"), notes)
        self.assertNotIn(self.meeting_day.strftime("%m/%d/%Y"), notes)

    def test_no_resolution_without_quorum(self):
        """Une assemblée sans quorum ne décide rien : sa proposition « adoptée »
        n'entre pas au registre corporatif."""
        thin = self._opened(name="Assemblée sans quorum (essai)")
        self._attend(thin, self.alice | self.gilles)
        proposal = self._proposal(thin, name="Adoptée sans quorum (essai)")
        proposal.write({"votes_for": 2, "votes_against": 0})
        thin.action_close()
        self.assertFalse(thin.quorum_met)
        with self.assertRaises(UserError) as caught:
            proposal.with_user(self.secretary).action_create_corporate_resolution()
        self.assertIn("quorum", str(caught.exception))
        self.assertFalse(self._resolutions(proposal))

    def test_a_bridged_resolution_keeps_what_the_assembly_adopted(self):
        """Inscrite par le pont, la résolution garde le titre, le texte, les
        totaux et la séance de l'assemblée : le registre ne les réécrit pas. Une
        résolution saisie au registre, elle, se modifie."""
        self.assembly.action_close()
        self.adopted.with_user(self.secretary).action_create_corporate_resolution()
        resolution = self._resolutions(self.adopted).with_user(self.secretary)
        for values in ({"resolved_text": "<p>Un autre texte.</p>"}, {"vote_for": 6},
                       {"name": "Autre titre"}, {"meeting_date": self.today}):
            with self.assertRaises(UserError, msg=str(values)) as caught:
                resolution.write(values)
            self.assertIn("ne se réécrivent pas au registre", str(caught.exception))
        resolution.write({"notes": "Note du registre (essai)."})
        own = self.env["corporate.resolution"].with_user(self.secretary).create({
            "name": "Résolution du registre (essai)", "meeting_date": self.meeting_day})
        own.write({"vote_for": 3, "resolved_text": "<p>Texte corrigé.</p>"})
        self.assertEqual(own.vote_for, 3)

    def test_a_bridged_resolution_is_not_deleted(self):
        """Supprimée, la résolution libérerait sa proposition, qui se
        réinscrirait : le registre la garde, et elle se marque remplacée."""
        self.assembly.action_close()
        self.adopted.with_user(self.secretary).action_create_corporate_resolution()
        resolution = self._resolutions(self.adopted)
        with self.assertRaises(UserError) as caught:
            resolution.with_user(self.secretary).unlink()
        self.assertIn("ne se supprime pas", str(caught.exception))
        self.assertTrue(resolution.exists())

    def test_a_bridged_resolution_keeps_its_signatories_and_status(self):
        """🔴 Les signataires d'une résolution pontée sont la présidence et le
        secrétariat de l'assemblée : une personne gestionnaire du registre, sans
        rôle Membres, ne les retire pas, n'en ajoute pas (en « Actionnaire »)
        et ne les change pas. Son statut ne change plus, sauf pour la marquer
        remplacée."""
        chair = self.env["res.partner"].create({"name": "Présidente d'assemblée (essai)"})
        self.assembly.write({"chair_id": chair.id})
        self.assembly.action_close()
        self.adopted.with_user(self.secretary).action_create_corporate_resolution()
        registrar = new_test_user(
            self.env, login="registre_signataires", groups="bf_corporate_governance.group_corporate_manager",
            name="Registre seul", context={"no_reset_password": True})
        resolution = self._resolutions(self.adopted).with_user(registrar)
        line = resolution.signatory_ids
        self.assertEqual(line.partner_id, chair)
        stranger = self.env["res.partner"].create({"name": "Actionnaire de passage (essai)"})
        attempts = (
            lambda: line.with_user(registrar).unlink(),
            lambda: line.with_user(registrar).write({"capacity": "shareholder"}),
            lambda: resolution.write({"signatory_ids": [Command.create({
                "partner_id": stranger.id, "capacity": "shareholder"})]}),
            lambda: self.env["corporate.resolution.signatory"].with_user(registrar).with_context(
                default_resolution_id=resolution.id).create({
                    "partner_id": stranger.id, "capacity": "assembly_secretary"}),
        )
        for attempt in attempts:
            with self.assertRaises(UserError) as caught:
                attempt()
            self.assertIn("ne changent pas au registre", str(caught.exception))
        for action in ("action_reject", "action_reset_draft"):
            with self.assertRaises(UserError, msg=action) as caught:
                getattr(resolution, action)()
            self.assertIn("sauf pour la marquer remplacée", str(caught.exception))
        resolution.write({"status": "superseded"})
        self.env.invalidate_all()
        self.assertEqual(resolution.status, "superseded")
        self.assertEqual(resolution.signatory_ids.partner_id, chair)
