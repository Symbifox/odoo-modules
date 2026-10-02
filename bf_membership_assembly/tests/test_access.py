from datetime import datetime, time

from dateutil.relativedelta import relativedelta

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import AssemblyCase


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestAccess(AssemblyCase):
    """La portée, jouée dans le rôle visé et non en administrateur."""

    def test_plain_employee_sees_nothing(self):
        assembly = self._convened()
        self._proposal(assembly)
        for model in ("bf.membership.assembly", "bf.membership.assembly.voter",
                      "bf.membership.assembly.proposal", "bf.membership.assembly.ballot"):
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(self.employee).search([])

    def test_other_company_is_invisible(self):
        other = self.env["res.company"].create({"name": "Autre société (essai)"})
        hidden = self.env["bf.membership.assembly"].create({
            "name": "AGA de l'autre société",
            "date": datetime.combine(self.meeting_day, time(16, 0)),
            "company_id": other.id,
        })
        hidden_proposal = self._proposal(hidden)
        visible = self._assembly()
        Assembly = self.env["bf.membership.assembly"].with_user(self.manager)
        found = Assembly.search([])
        self.assertIn(visible, found)
        self.assertNotIn(hidden, found)
        proposals = self.env["bf.membership.assembly.proposal"].with_user(self.manager).search([])
        self.assertNotIn(hidden_proposal, proposals)

    def test_agent_reads_documents_joined_by_someone_else(self):
        """La fiche s'ouvre à l'agente quand les états financiers ont été
        joints par une autre personne."""
        assembly = self.env["bf.membership.assembly"].with_user(self.manager).create({
            "name": "AGA préparée par la responsable (essai)",
            "date": datetime.combine(self.meeting_day, time(16, 0)),
            "attachment_ids": [Command.create({"name": "États financiers.pdf", "raw": b"%PDF-1.4 essai"})],
        })
        # 🔴 Vider le cache : la création par la responsable l'a réchauffé, et
        # une lecture servie par le cache ne passe par aucun contrôle d'accès.
        self.env.invalidate_all()
        values = assembly.with_user(self.agent).web_read({"attachment_ids": {"fields": {"name": {}}}})
        self.assertEqual([a["name"] for a in values[0]["attachment_ids"]], ["États financiers.pdf"])

    def test_foreign_attachment_is_not_captured(self):
        """Ajouter la pièce d'un autre à la liste ne la rattache pas."""
        foreign = self.env["ir.attachment"].with_user(self.manager).create({
            "name": "Pièce privée.pdf", "raw": b"%PDF-1.4 essai"})
        assembly = self.env["bf.membership.assembly"].with_user(self.agent).create({
            "name": "AGA (essai)", "date": datetime.combine(self.meeting_day, time(16, 0))})
        assembly.sudo().attachment_ids = [Command.link(foreign.id)]
        assembly.with_user(self.agent)._attach_documents()
        self.assertFalse(foreign.res_id)

    def test_agent_holds_the_assembly_but_does_not_delete_it(self):
        Assembly = self.env["bf.membership.assembly"].with_user(self.agent)
        assembly = Assembly.browse(self._assembly().id)
        assembly.action_convene()
        self._attend(assembly, self.persons | self.member_org)
        assembly.action_open()
        proposal = self.env["bf.membership.assembly.proposal"].with_user(self.agent).create({
            "assembly_id": assembly.id, "name": "Adoption des états financiers"})
        proposal.write({"votes_for": 5, "votes_against": 1})
        assembly.action_close()
        self.assertEqual(proposal.result, "adopted")
        draft = Assembly.browse(self._assembly(name="Brouillon (essai)").id)
        with self.assertRaises(AccessError):
            draft.unlink()
        self.env["bf.membership.assembly"].with_user(self.manager).browse(draft.id).unlink()

    def test_agent_and_manager_read_the_voter_list(self):
        """Les champs de membre du contact sont réservés au rôle Membres (socle).
        La liste des votants, qui en reprend le numéro, se lit et se cherche
        dans les deux rôles qui tiennent l'assemblée."""
        assembly = self._convened()
        number = self.alice.member_number
        self.assertTrue(number)
        line = self._line(assembly, self.alice)
        spec = {name: {} for name in (
            "member_number", "member_id", "representative_id", "notice_channel",
            "attendance", "has_voice", "street", "city", "zip")}
        for user in (self.agent, self.manager):
            # 🔴 Vider le cache : une lecture servie par le cache ne passe par
            # aucun contrôle d'accès.
            self.env.invalidate_all()
            Voter = self.env["bf.membership.assembly.voter"].with_user(user)
            rows = Voter.web_search_read([("assembly_id", "=", assembly.id)], spec)["records"]
            self.assertIn(number, [row["member_number"] for row in rows], user.login)
            self.assertIn(line.id, [found[0] for found in Voter.name_search(number)], user.login)


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestStateLock(AssemblyCase):
    """🔴 L'état ne s'écrit pas, et la clé des transitions ne vient pas du client.

    Chaque écriture se joue comme un client RPC la ferait : dans le rôle de
    l'agent, avec le contexte qu'il voudrait bien fournir. AccessError hérite
    de UserError : les essais lisent le message pour savoir qui a refusé.
    """

    def _held(self):
        """Une assemblée tenue et close, avec une proposition adoptée."""
        assembly = self._opened()
        self._attend(assembly, self.persons | self.member_org)
        proposal = self._proposal(assembly, name="Adoption des états financiers (essai)")
        proposal.write({"votes_for": 5, "votes_against": 1})
        assembly.action_close()
        self.env.invalidate_all()
        return assembly, proposal

    def _draft_values(self, **vals):
        values = {
            "name": "AGA (essai)",
            "date": datetime.combine(self.meeting_day, time(16, 0)),
            "record_date": self.record_day,
        }
        values.update(vals)
        return values

    def test_state_is_never_written_directly(self):
        """Close à ouverte rouvrirait les propositions ; close à brouillon
        permettrait de supprimer une assemblée tenue."""
        assembly, proposal = self._held()
        for user, state in ((self.agent, "open"), (self.agent, "draft"),
                            (self.agent, "cancelled"), (self.manager, "draft")):
            with self.assertRaises(UserError, msg=state) as caught:
                assembly.with_user(user).write({"state": state})
            self.assertIn("ne s'écrit pas", str(caught.exception))
        self.env.invalidate_all()
        self.assertEqual(assembly.state, "closed")
        with self.assertRaises(UserError):
            assembly.with_user(self.manager).unlink()
        with self.assertRaises(UserError):
            proposal.with_user(self.agent).write({"votes_for": 6})
        self.assertTrue(assembly.exists())

    def test_transition_key_from_a_client_unfreezes_nothing(self):
        """Avec la clé de transition dans le contexte, comme un client RPC peut
        l'y mettre, la date et la date de référence d'une assemblée close
        restent gelées, et son état aussi."""
        assembly, _proposal = self._held()
        client = assembly.with_user(self.agent).with_context(bf_assembly_transition=True)
        later = datetime.combine(self.meeting_day + relativedelta(days=7), time(16, 0))
        with self.assertRaises(UserError) as caught:
            client.write({"date": later, "record_date": self.today})
        self.assertIn("ne changent plus", str(caught.exception))
        with self.assertRaises(UserError) as caught:
            client.write({"state": "open"})
        self.assertIn("ne s'écrit pas", str(caught.exception))
        self.env.invalidate_all()
        self.assertEqual((assembly.state, assembly.record_date), ("closed", self.record_day))

    def test_an_assembly_is_born_in_draft(self):
        """Créée « convoquée », elle échapperait au contrôle du délai d'avis et
        des documents joints. L'état peut aussi venir d'une valeur par défaut
        du contexte, et la clé de transition n'y change rien."""
        Assembly = self.env["bf.membership.assembly"].with_user(self.agent)
        attempts = (
            (Assembly, self._draft_values(state="convened")),
            (Assembly.with_context(default_state="convened"), self._draft_values()),
            (Assembly.with_context(bf_assembly_transition=True), self._draft_values(state="closed")),
        )
        for model, values in attempts:
            with self.assertRaises(UserError) as caught:
                model.create(values)
            self.assertIn("naît en brouillon", str(caught.exception))
        self.assertEqual(Assembly.create(self._draft_values()).state, "draft")

    def test_assembly_officers_are_fixed_at_closing(self):
        """La présidence et le secrétariat d'assemblée signent les résolutions
        inscrites au registre corporatif : les changer après la clôture
        changerait qui a signé. Ouverte, l'assemblée les nomme encore ; close,
        elle garde modifiables son nom, son lieu et son lien."""
        Partner = self.env["res.partner"]
        chair = Partner.create({"name": "Présidence (essai)"})
        secretary = Partner.create({"name": "Secrétariat (essai)"})
        stranger = Partner.create({"name": "Personne nommée après coup (essai)"})
        assembly = self._opened()
        assembly.with_user(self.agent).write({"chair_id": chair.id, "secretary_id": secretary.id})
        assembly.action_close()
        self.env.invalidate_all()
        for field in ("chair_id", "secretary_id"):
            with self.assertRaises(UserError, msg=field) as caught:
                assembly.with_user(self.agent).write({field: stranger.id})
            self.assertIn("ne changent plus", str(caught.exception))
        assembly.with_user(self.agent).write({
            "name": "AGA (essai, titre corrigé)", "location": "Salle B",
            "remote_url": "https://assemblee.example/aga"})
        self.env.invalidate_all()
        self.assertEqual((assembly.chair_id, assembly.secretary_id), (chair, secretary))
        self.assertEqual(assembly.location, "Salle B")

    def test_no_trace_keys_from_a_client_are_ignored(self):
        """Une assemblée créée puis modifiée par un client RPC qui demande de ne
        rien suivre garde sa trace : le message de création et le suivi du lieu."""
        Assembly = self.env["bf.membership.assembly"].with_user(self.agent).with_context(
            tracking_disable=True, mail_notrack=True, mail_create_nolog=True)
        assembly = Assembly.create(self._draft_values())
        self.assertTrue(assembly.message_ids, "La création n'a laissé aucun message.")
        # Odoo ne suit pas un enregistrement créé dans la même transaction :
        # la file de pré-validation se vide d'abord, comme à la fin d'une requête.
        self.env.flush_all()
        self.env.cr.precommit.run()
        # 🔴 La création rend l'assemblée dans un contexte déjà nettoyé : les
        # clés se repassent, sinon l'essai ne prouverait rien sur l'écriture.
        silent = {"tracking_disable": True, "mail_notrack": True}
        assembly.with_context(**silent).write({"location": "Salle B (essai)"})
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.env.invalidate_all()
        tracked = assembly.sudo().message_ids.tracking_value_ids.filtered(
            lambda t: t.field_id.name == "location")
        self.assertTrue(tracked, "Le changement de lieu n'a pas été suivi.")
        # Les transitions écrivent en superutilisateur : la clé du client ne
        # doit pas y survivre non plus.
        assembly.with_context(**silent).action_cancel()
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.env.invalidate_all()
        tracked = assembly.sudo().message_ids.tracking_value_ids.filtered(
            lambda t: t.field_id.name == "state")
        self.assertTrue(tracked, "L'annulation n'a pas été suivie.")

    def test_transitions_check_the_right_to_write(self):
        """Les transitions écrivent en superutilisateur : le droit d'écrire se
        vérifie avant, dans le rôle de la personne qui clique. Un rôle qui
        lit les assemblées sans les écrire ne les ouvre ni ne les annule."""
        readers = self.env["res.groups"].create({"name": "Lecture des assemblées (essai)"})
        for model in ("bf.membership.assembly", "bf.membership.assembly.voter",
                      "bf.membership.assembly.proposal"):
            self.env["ir.model.access"].create({
                "name": "%s lecture (essai)" % model,
                "model_id": self.env["ir.model"]._get_id(model),
                "group_id": readers.id,
                "perm_read": True,
            })
        reader = new_test_user(self.env, login="lecture_assemblees", groups="base.group_user",
                               context={"no_reset_password": True})
        reader.groups_id = [Command.link(readers.id)]
        assembly = self._convened()
        self.env.invalidate_all()
        for action in ("action_open", "action_cancel", "action_reset_draft"):
            with self.assertRaises(AccessError, msg=action):
                getattr(assembly.with_user(reader), action)()
        self.env.invalidate_all()
        self.assertEqual(assembly.state, "convened")


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestMinutesReport(AssemblyCase):

    def test_minutes_show_attendance_quorum_and_results(self):
        assembly = self._opened(chair_id=self.gilles.id)
        self._attend(assembly, self.persons | self.member_org)
        proposal = self._proposal(assembly, name="Élection du conseil (essai)")
        proposal.write({"votes_for": 5, "votes_against": 1})
        assembly.action_close()
        html, _format = self.env["ir.actions.report"]._render_qweb_html(
            "bf_membership_assembly.report_assembly_minutes", assembly.ids)
        html = html.decode()
        self.assertIn(assembly.name, html)
        self.assertIn("Le quorum est atteint", html)
        self.assertIn("Élection du conseil (essai)", html)
        self.assertIn("Adoptée", html)
        self.assertIn("Carole Essai", html, "L'organisme vote par sa déléguée.")
        self.assertIn("Certifié conforme", html)


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestDocumentsTrace(AssemblyCase):

    def test_minutes_and_documents_changes_are_traced(self):
        """Après la clôture, le procès-verbal et les documents joints restent
        modifiables ; chaque changement se consigne au fil, au nom de la
        personne, avec le nom de la pièce. En brouillon, rien ne se consigne."""
        assembly = self._opened()
        assembly.action_close()
        statements = assembly.attachment_ids
        as_agent = assembly.with_user(self.agent)
        as_agent.write({"minutes": "<p>Procès-verbal rédigé après la séance (essai).</p>"})
        as_agent.write({"attachment_ids": [Command.create({
            "name": "Rapport annuel (essai).pdf", "raw": b"%PDF-1.4 essai"})]})
        as_agent.write({"attachment_ids": [Command.unlink(statements.id)]})
        self.env.invalidate_all()
        bodies = assembly.message_ids.filtered(lambda m: m.author_id == self.agent.partner_id).mapped("body")
        self.assertTrue(any("Procès-verbal modifié" in b for b in bodies), bodies)
        self.assertTrue(any("Pièce ajoutée : Rapport annuel (essai).pdf" in b for b in bodies), bodies)
        self.assertTrue(any("Pièce retirée : États financiers (essai).pdf" in b for b in bodies), bodies)
        draft = self._assembly(name="Brouillon (essai)")
        draft.with_user(self.agent).write({"minutes": "<p>Brouillon de procès-verbal (essai).</p>"})
        self.assertFalse(draft.message_ids.filtered(lambda m: "Procès-verbal modifié" in (m.body or "")))

    def test_documents_of_a_convened_assembly_are_not_replaced_or_deleted(self):
        """🔴 Dès la convocation, une pièce de l'assemblée accompagne l'avis : sa
        boîte du fil ne la remplace ni ne la détache ni ne la supprime. En
        brouillon, elle se remplace encore. Rejoué dans le rôle de l'agent."""
        assembly = self._convened()
        statements = assembly.attachment_ids.with_user(self.agent)
        other = self._assembly(name="Autre brouillon (essai)")
        for values in ({"raw": b"%PDF-1.4 autre contenu"}, {"res_id": other.id}):
            with self.assertRaises(UserError, msg=str(values)) as caught:
                statements.write(values)
            self.assertIn("ne se remplace ni ne se détache", str(caught.exception))
        with self.assertRaises(UserError) as caught:
            statements.unlink()
        self.assertIn("ne se supprime pas", str(caught.exception))
        statements.write({"name": "États financiers, version de l'avis (essai).pdf"})
        self.env.invalidate_all()
        self.assertEqual(statements.sudo().raw, b"%PDF-1.4 essai")
        draft = other.attachment_ids.with_user(self.agent)
        draft.write({"raw": b"%PDF-1.4 version corrigee"})
        draft.unlink()

    def test_someone_elses_private_document_is_not_joined(self):
        """La pièce encore privée d'une autre personne ne se joint pas : l'avis
        par courriel l'enverrait aux membres. Le refus ne la nomme pas."""
        foreign = self.env["ir.attachment"].with_user(self.manager).create({
            "name": "Pièce privée (essai).pdf", "raw": b"%PDF-1.4 essai"})
        assembly = self._assembly().with_user(self.agent)
        with self.assertRaises(UserError) as caught:
            assembly.write({"attachment_ids": [Command.link(foreign.id)]})
        self.assertIn("ne se joint pas", str(caught.exception))
        self.assertNotIn("Pièce privée", str(caught.exception))
