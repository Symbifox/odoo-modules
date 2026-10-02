from datetime import datetime, time

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import AssemblyCase


@tagged("post_install", "-at_install", "bf_membership_assembly")
class TestNotice(AssemblyCase):

    def _at(self, days):
        return datetime.combine(self._day(days=days), time(16, 0))

    def _outbox(self):
        return (self.env["mail.mail"].sudo().search_count([]),
                self.env["mail.notification"].sudo().search_count([]))

    def test_notice_too_short_is_refused(self):
        assembly = self._assembly(date=self._at(5))
        with self.assertRaises(UserError) as caught:
            assembly.action_convene()
        self.assertIn("au moins 10", str(caught.exception))
        self.assertEqual(assembly.state, "draft")

    def test_notice_too_long_is_refused(self):
        assembly = self._assembly(date=self._at(60))
        with self.assertRaises(UserError) as caught:
            assembly.action_convene()
        self.assertIn("au plus 45", str(caught.exception))

    def test_bounds_at_the_limit_pass(self):
        self.assertEqual(self._convened(date=self._at(10)).state, "convened")
        self.assertEqual(self._convened(date=self._at(45)).state, "convened")

    def test_federal_bounds_are_a_setting(self):
        """LBNL : 21 à 60 jours. Les mêmes 50 jours passent ici et pas au défaut québécois."""
        federal = self._convened(date=self._at(50), notice_min_days=21, notice_max_days=60)
        self.assertEqual(federal.state, "convened")
        self.assertEqual(federal.notice_days, 50)
        with self.assertRaises(UserError):
            self._convened(date=self._at(15), notice_min_days=21, notice_max_days=60)

    def test_the_notice_date_is_set_by_the_convocation_only(self):
        """🔴 Une date de l'avis antidatée ferait passer le délai minimal à un
        avis parti trop tard : elle ne se saisit ni à la création, ni par une
        valeur par défaut, ni ensuite. La convocation la fixe au jour réel."""
        Assembly = self.env["bf.membership.assembly"].with_user(self.agent)
        values = {"name": "AGA (essai)", "date": self._at(5), "record_date": self.record_day}
        antidated = self._day(days=-20)
        for model, vals in ((Assembly, dict(values, notice_date=antidated)),
                            (Assembly.with_context(default_notice_date=antidated), values)):
            with self.assertRaises(UserError) as caught:
                model.create(vals)
            self.assertIn("se fixe par la convocation", str(caught.exception))
        assembly = Assembly.create(values)
        with self.assertRaises(UserError):
            assembly.write({"notice_date": antidated})
        with self.assertRaises(UserError) as caught:
            assembly.action_convene()
        self.assertIn("au moins 10", str(caught.exception), "Le délai se compte sur le jour réel.")

    def test_annual_notice_needs_the_financial_statements(self):
        with self.assertRaises(UserError):
            self._convened(attachment_ids=False)
        special = self._convened(kind="special", attachment_ids=False)
        self.assertEqual(special.state, "convened")

    def test_convocation_sends_nothing_and_prefills_the_composer(self):
        """🔴 La convocation n'écrit à personne : elle ouvre le compositeur."""
        before = self._outbox()
        assembly = self._assembly()
        action = assembly.action_convene()
        self.assertEqual(self._outbox(), before, "La convocation a créé un courriel.")
        self.assertEqual(action["res_model"], "mail.compose.message")
        self.assertIs(action["context"]["mail_post_autofollow"], False,
                      "Le bouton Envoyer transmet ce contexte : les membres ne doivent pas devenir abonnés.")

        composer = self.env["mail.compose.message"].with_context(action["context"]).create({})
        self.assertEqual(composer.partner_ids, self.alice | self.gilles,
                         "Seuls les membres qui ont consenti ET qui ont une adresse.")
        self.assertEqual(composer.attachment_ids, assembly.attachment_ids)
        self.assertIn(assembly.name, composer.subject)
        self.assertEqual(self._outbox(), before, "Ouvrir le compositeur a créé un courriel.")

        postal = assembly.voter_ids.filtered(lambda v: v.notice_channel == "post").member_id
        self.assertEqual(postal, self.bruno | self.francine | self.henri | self.member_org | self.orphan_org)
        postal_action = assembly.action_view_postal_notices()
        self.assertEqual(
            self.env["bf.membership.assembly.voter"].search(postal_action["domain"]).member_id, postal)

    def test_notice_speaks_the_organisation_language_not_the_senders(self):
        """Envoyé par une personne dont l'interface est en anglais, l'avis ne
        dit pas « le Saturday 31 October » au milieu du français : la langue
        est celle de l'organisme."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env.company.partner_id.lang = "fr_CA"
        sender = self.env.user
        sender.lang = "en_US"
        assembly = self._assembly()
        action = assembly.action_convene()
        composer = self.env["mail.compose.message"].with_user(sender).with_context(
            action["context"], lang="en_US").create({})
        local = assembly._local_datetime()
        english = local.strftime("%A")  # le jour en anglais, nom de la locale C
        self.assertNotIn(english, composer.body, "Le jour est en anglais dans l'avis.")
        self.assertEqual(composer.lang, "fr_CA")

    def test_mail_leaves_only_when_the_person_clicks_send(self):
        assembly = self._assembly()
        action = assembly.action_convene()
        composer = self.env["mail.compose.message"].with_context(action["context"]).create({})
        before = self.env["mail.mail"].sudo().search([])
        # Le widget d'envoi du compositeur rappelle `action_send_mail` avec le
        # contexte de l'action : on le rejoue tel quel.
        composer.with_context(action["context"], mail_notify_force_send=False).action_send_mail()
        mails = self.env["mail.mail"].sudo().search([]) - before
        self.assertTrue(mails, "Le clic sur Envoyer n'a rien produit.")
        self.assertIn(self.alice, mails.recipient_ids)
        self.assertNotIn(self.bruno, mails.recipient_ids, "Bruno n'a pas consenti aux avis par courriel.")
        self.assertNotIn(self.alice, assembly.message_partner_ids,
                         "Un membre convoqué ne devient pas abonné de l'assemblée.")

    def test_no_email_consent_means_no_composer(self):
        (self.alice | self.gilles).write({"notice_email_consent": False})
        action = self._assembly().action_convene()
        self.assertEqual(action["res_model"], "bf.membership.assembly.voter")

    def test_what_the_notice_fixed_does_not_move(self):
        assembly = self._convened()
        self.assertEqual(assembly.notice_date, self.today)
        with self.assertRaises(UserError):
            assembly.date = self._at(20)
        with self.assertRaises(UserError):
            assembly.record_date = self.today
        assembly.action_reset_draft()
        self.assertFalse(assembly.notice_date)
        assembly.date = self._at(20)
        self.assertEqual(assembly.state, "draft")

    def test_the_notice_channel_is_fixed_by_the_convocation(self):
        """Le canal dit comment l'avis a été donné : ni une liste rebâtie, ni une
        écriture ne le changent ensuite. Remise en brouillon, l'assemblée se
        convoquera de nouveau, et le canal se recalcule."""
        assembly = self._convened()
        alice = self._line(assembly, self.alice)
        self.assertEqual(alice.notice_channel, "email")
        self.alice.notice_email_consent = False
        assembly.with_user(self.agent).action_build_voters()
        self.assertEqual(alice.notice_channel, "email", "L'avis est parti par courriel.")
        self.env.invalidate_all()
        with self.assertRaises(UserError) as caught:
            alice.with_user(self.agent).write({"notice_channel": "post"})
        self.assertIn("canal", str(caught.exception))
        assembly.action_reset_draft()
        assembly.action_build_voters()
        self.assertEqual(alice.notice_channel, "post")

    def test_a_portal_member_does_not_see_the_other_recipients(self):
        """🔴 L'avis part à plusieurs membres ; une personne membre qui a accès
        au portail ne lit pas, par un appel RPC, qui d'autre a été convoqué."""
        portal = self.env["res.users"].with_context(no_reset_password=True).create({
            "login": "portail_alice", "partner_id": self.alice.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        assembly = self._assembly()
        action = assembly.action_convene()
        composer = self.env["mail.compose.message"].with_context(action["context"]).create({})
        composer.with_context(action["context"], mail_notify_force_send=False).action_send_mail()
        self.env.invalidate_all()
        Message = self.env["mail.message"].with_user(portal)
        seen = Message.search([("model", "=", assembly._name), ("res_id", "=", assembly.id)])
        exposed = self.env["res.partner"]
        for message in seen:
            for field in ("partner_ids", "notified_partner_ids"):
                try:
                    exposed |= self.env["res.partner"].browse(message.read([field])[0][field])
                except Exception:  # noqa: BLE001 - un champ refusé ne fuit pas
                    pass
        self.assertNotIn(self.gilles, exposed, "La personne au portail lit les autres convoqués.")

    def test_a_consent_withdrawn_before_sending_is_honoured(self):
        """Une personne qui retire son consentement aux avis par courriel entre la
        convocation et l'envoi ne reçoit pas le courriel : son avis passe à la
        poste, et le fil le consigne."""
        assembly = self._assembly()
        action = assembly.action_convene()
        composer = self.env["mail.compose.message"].with_context(action["context"]).create({})
        self.assertEqual(composer.partner_ids, self.alice | self.gilles)
        self.alice.notice_email_consent = False
        before = self.env["mail.mail"].sudo().search([])
        composer.with_context(action["context"], mail_notify_force_send=False).action_send_mail()
        mails = self.env["mail.mail"].sudo().search([]) - before
        self.assertIn(self.gilles, mails.recipient_ids)
        self.assertNotIn(self.alice, mails.recipient_ids)
        self.assertEqual(self._line(assembly, self.alice).notice_channel, "post")
        logs = assembly.message_ids.filtered(lambda m: "Avis par la poste, et non par courriel" in (m.body or ""))
        self.assertIn("Alice Essai", logs.body)

    def test_the_notice_email_is_not_presented_as_internal(self):
        """L'avis se range en note (le portail ne le lit pas), mais le courriel du
        membre ne s'annonce pas comme une « communication interne » : ce
        pré-en-tête caché s'affiche en aperçu dans sa boîte. Une vraie note
        interne de l'assemblée, rendue dans la même mise en page, le garde."""
        markers = ("Internal communication", "Communication interne")
        assembly = self._assembly()
        action = assembly.action_convene()
        composer = self.env["mail.compose.message"].with_context(action["context"]).create({})
        before = self.env["mail.mail"].sudo().search([])
        composer.with_context(action["context"], mail_notify_force_send=False).action_send_mail()
        notices = self.env["mail.mail"].sudo().search([]) - before
        self.assertIn(self.alice, notices.recipient_ids)
        for body in notices.mapped("body_html"):
            self.assertIn("vous convoque", body)
            for marker in markers:
                self.assertNotIn(marker, body)
        before = self.env["mail.mail"].sudo().search([])
        assembly.with_context(mail_notify_force_send=False).message_post(
            body="Note de préparation (essai).", subtype_xmlid="mail.mt_note",
            partner_ids=self.agent.partner_id.ids,
            email_layout_xmlid="bf_onboarding_base.bf_mail_layout")
        notes = self.env["mail.mail"].sudo().search([]) - before
        self.assertTrue(notes, "La note interne n'a produit aucun courriel.")
        self.assertTrue(any(marker in body for body in notes.mapped("body_html") for marker in markers),
                        "La note interne a perdu son pré-en-tête.")

    def test_each_member_gets_their_own_notice(self):
        """🔴 Un courriel par membre, à son seul nom, avec les pièces et la mise en
        page de l'avis ; aucun message du fil ne porte la liste des
        destinataires, et la note de décompte ne nomme personne."""
        assembly = self._assembly()
        action = assembly.action_convene()
        composer = self.env["mail.compose.message"].with_context(action["context"]).create({})
        before = self.env["mail.mail"].sudo().search([])
        composer.with_context(action["context"], mail_notify_force_send=False).action_send_mail()
        mails = self.env["mail.mail"].sudo().search([]) - before
        self.assertEqual(len(mails), 2)
        self.assertEqual(mails.recipient_ids, self.alice | self.gilles)
        for mail in mails:
            self.assertEqual(len(mail.recipient_ids), 1, "Un courriel ne porte qu'un destinataire.")
            self.assertEqual(mail.attachment_ids, assembly.attachment_ids, "Les pièces suivent.")
            self.assertIn("vous convoque", mail.body_html)
        messages = self.env["mail.message"].sudo().search([("model", "=", assembly._name), ("res_id", "=", assembly.id)])
        self.assertFalse(messages.partner_ids, "Un message du fil porte des destinataires.")
        log = messages.filtered(lambda m: "Avis de convocation envoyé" in (m.body or ""))
        self.assertEqual(len(log), 1)
        self.assertIn("2 membre(s)", log.body)
        self.assertNotIn("Alice Essai", log.body)

    def test_a_convened_employee_reads_no_other_recipient(self):
        """🔴 Une personne employée, sans le rôle Membres et elle-même convoquée, ne
        lit aucun message qui nomme une autre personne convoquée."""
        employee = self.env["res.users"].with_context(no_reset_password=True).create({
            "login": "employe_convoque", "partner_id": self.gilles.id,
            "groups_id": [Command.set([self.env.ref("base.group_user").id])]})
        assembly = self._assembly()
        action = assembly.action_convene()
        composer = self.env["mail.compose.message"].with_context(action["context"]).create({})
        composer.with_context(action["context"], mail_notify_force_send=False).action_send_mail()
        self.env.invalidate_all()
        seen = self.env["mail.message"].with_user(employee).search([])
        others = self.env["res.partner"]
        for message in seen:
            values = message.read(["partner_ids", "notified_partner_ids", "body"])[0]
            others |= self.env["res.partner"].browse(values["partner_ids"] + values["notified_partner_ids"])
            self.assertNotIn("Alice Essai", values["body"] or "")
        self.assertFalse(others - self.gilles, "La personne lit d'autres convoqués.")

    def test_the_notice_goes_to_the_email_lines_only(self):
        """🔴 La preuve de l'avis. Une personne hors de la liste « courriel » ne
        le reçoit pas ; un membre retiré du compositeur passe à l'avis par la
        poste, et le fil le nomme ; chaque ligne avisée porte la date de
        l'envoi, qui ne se saisit pas ; un brouillon n'envoie rien, même avec
        un contexte forgé."""
        stranger = self.env["res.partner"].create({"name": "Personne non membre (essai)",
                                                   "email": "personne@essai.example"})
        assembly = self._assembly()
        action = assembly.action_convene()
        composer = self.env["mail.compose.message"].with_context(action["context"]).create({})
        composer.partner_ids = [Command.set((self.alice | self.gilles | stranger).ids)]
        with self.assertRaises(UserError) as caught:
            composer.with_context(action["context"], mail_notify_force_send=False).action_send_mail()
        self.assertIn("ne figure pas à la liste", str(caught.exception))
        composer.partner_ids = [Command.set(self.alice.ids)]
        before = self.env["mail.mail"].sudo().search([])
        composer.with_context(action["context"], mail_notify_force_send=False).action_send_mail()
        mails = self.env["mail.mail"].sudo().search([]) - before
        self.assertEqual(mails.recipient_ids, self.alice)
        alice, gilles = self._line(assembly, self.alice), self._line(assembly, self.gilles)
        self.assertTrue(alice.notice_sent_at, "La ligne avisée porte la date de l'envoi.")
        self.assertFalse(gilles.notice_sent_at)
        self.assertEqual(gilles.notice_channel, "post", "Gilles n'a rien reçu : son avis part par la poste.")
        logs = assembly.message_ids.filtered(lambda m: "retirés des destinataires" in (m.body or ""))
        self.assertIn("Gilles Essai", logs.body)
        with self.assertRaises(UserError) as caught:
            alice.with_user(self.agent).write({"notice_sent_at": False})
        self.assertIn("ne se saisit pas", str(caught.exception))
        draft = self._assembly(name="Brouillon (essai)")
        forged = {"default_model": draft._name, "default_res_ids": draft.ids,
                  "default_composition_mode": "comment", "default_partner_ids": self.alice.ids,
                  "bf_assembly_notice": True}
        composer = self.env["mail.compose.message"].with_context(forged).create({})
        with self.assertRaises(UserError) as caught:
            composer.with_context(forged, mail_notify_force_send=False).action_send_mail()
        self.assertIn("une fois l'assemblée convoquée", str(caught.exception))

    def _send(self, assembly, partners=None):
        action = assembly.action_notice_email() if assembly.state == "convened" else assembly.action_convene()
        composer = self.env["mail.compose.message"].with_context(action["context"]).create({})
        if partners is not None:
            composer.partner_ids = [Command.set(partners.ids)]
        before = self.env["mail.mail"].sudo().search([])
        composer.with_context(action["context"], mail_notify_force_send=False).action_send_mail()
        return self.env["mail.mail"].sudo().search([]) - before

    def test_a_resend_to_one_member_keeps_the_others_notified(self):
        """Renvoyer l'avis à une seule personne ne fait pas passer les autres à
        la poste : elles l'ont déjà reçu, gardent leur canal et leur date, et le
        fil dit le renvoi sans les nommer."""
        assembly = self._assembly()
        self._send(assembly)
        gilles = self._line(assembly, self.gilles)
        first = gilles.notice_sent_at
        self.assertTrue(first)
        mails = self._send(assembly, self.alice)
        self.assertEqual(mails.recipient_ids, self.alice)
        self.env.invalidate_all()
        self.assertEqual((gilles.notice_channel, gilles.notice_sent_at), ("email", first))
        logs = assembly.message_ids.filtered(lambda m: "déjà avisé" in (m.body or ""))
        self.assertEqual(len(logs), 1)
        self.assertNotIn("Gilles Essai", logs.body)
        self.assertFalse(assembly.message_ids.filtered(lambda m: "retirés des destinataires" in (m.body or "")))

    def test_reset_to_draft_clears_the_proof_of_notice(self):
        """Remise en brouillon : la preuve de l'avis précédent s'efface des
        lignes, et le fil le consigne ; la prochaine convocation partira de
        nouveau à chacun."""
        assembly = self._assembly()
        self._send(assembly)
        self.assertTrue(assembly.voter_ids.filtered("notice_sent_at"))
        assembly.with_user(self.agent).action_reset_draft()
        self.env.invalidate_all()
        self.assertFalse(assembly.voter_ids.filtered("notice_sent_at"))
        self.assertTrue(assembly.message_ids.filtered(lambda m: "dates d'envoi de l'avis précédent" in (m.body or "")))

    def test_no_notice_through_a_mass_mailing(self):
        """L'avis ne part pas par un envoi de masse, qui échapperait aux gardes de
        l'assemblée (convoquée, lignes « courriel », preuve par ligne)."""
        draft = self._assembly(name="Brouillon (essai)")
        forged = {"default_model": draft._name, "default_res_ids": draft.ids,
                  "default_composition_mode": "mass_mail", "default_partner_ids": self.alice.ids,
                  "default_subject": "Avis (essai)", "default_body": "<p>Avis (essai)</p>",
                  "bf_assembly_notice": True}
        composer = self.env["mail.compose.message"].with_context(forged).create({})
        before = self.env["mail.mail"].sudo().search([])
        with self.assertRaises(UserError) as caught:
            composer.with_context(forged, mail_notify_force_send=False).action_send_mail()
        self.assertIn("envoi de masse", str(caught.exception))
        self.assertFalse(self.env["mail.mail"].sudo().search([]) - before)

    def test_the_notice_template_leaves_only_by_the_guarded_sending(self):
        """🔴 Le gabarit de l'avis ne part que par l'envoi individuel gardé de
        l'assemblée convoquée : ni par un compositeur sans la clé de l'avis
        (envoi de masse ou message ordinaire), ni par `send_mail`, ni par le fil
        (`message_post_with_source`, `message_mail_with_source`). Rejoué dans le
        rôle de l'agent, sur un brouillon, comme par un appel RPC."""
        draft = self._assembly(name="Brouillon (essai)")
        template = self.env.ref("bf_membership_assembly.mail_template_assembly_notice")
        mails_before = self.env["mail.mail"].sudo().search([])
        messages_before = self.env["mail.message"].sudo().search([("model", "=", draft._name)])
        attempts = []
        for mode in ("mass_mail", "comment"):
            context = {"default_model": draft._name, "default_res_ids": draft.ids,
                       "default_composition_mode": mode, "default_template_id": template.id,
                       "default_partner_ids": (self.alice | self.bruno).ids}
            composer = self.env["mail.compose.message"].with_user(self.agent).with_context(context).create({})
            attempts.append(lambda c=composer, ctx=context: c.with_context(
                ctx, mail_notify_force_send=False).action_send_mail())
        attempts += [
            lambda: template.with_user(self.agent).send_mail(draft.id),
            lambda: draft.with_user(self.agent).message_post_with_source(
                template, partner_ids=(self.alice | self.bruno).ids),
            lambda: draft.with_user(self.agent).message_mail_with_source(template),
        ]
        for number, attempt in enumerate(attempts):
            with self.assertRaises(UserError, msg=str(number)) as caught:
                attempt()
            self.assertIn("pas autrement", str(caught.exception), number)
        self.assertFalse(self.env["mail.mail"].sudo().search([]) - mails_before)
        posted = self.env["mail.message"].sudo().search([("model", "=", draft._name)]) - messages_before
        self.assertFalse(posted.partner_ids)
