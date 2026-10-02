from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MembershipPortalCase, quiet_new_test_user

FORM = "/membres/adhesion"
THANKS = "/membres/adhesion/merci"
PAY = "/membres/adhesion/payer"


@tagged("post_install", "-at_install", "bf_membership_portal")
class TestReconcile(MembershipPortalCase):
    """🔴 Une demande au courriel d'un membre existant ne doit jamais faire
    lire, à la session qui a rempli le formulaire, une facture au nom et à
    l'adresse de la vraie personne (Loi 25)."""

    def setUp(self):
        super().setUp()
        self.type_person.public_signup = True

    def _post(self, email, name="Personne Tapée"):
        return self.url_open(FORM, data={
            "csrf_token": self._csrf(FORM),
            "type_id": str(self.type_person.id),
            "name": name,
            "email": email,
            "street": "4, rue Tapée",
            "city": "Lac-Exemple",
        })

    def _last_request(self):
        return self.env["bf.membership"].search([("public_request", "=", True)], order="id desc", limit=1)

    def _click_pay(self):
        # Le jeton vient du formulaire : la page de remerciement n'en porte que
        # si le bouton « Payer en ligne » y paraît.
        return self.url_open(PAY, data={"csrf_token": self._csrf(FORM)})

    def _new_session(self):
        self.opener.cookies.clear()
        self.authenticate(None, None)

    def _merge(self, partners, dst):
        wizard = self.env["base.partner.merge.automatic.wizard"].create({
            "partner_ids": [Command.set(partners.ids)], "dst_partner_id": dst.id})
        wizard.action_merge()
        self.env.flush_all()
        self.env.invalidate_all()

    def test_pay_button_is_the_same_whether_the_email_is_known_or_not(self):
        """Refuser le paiement d'une demande « à rapprocher » dirait qui est au registre."""
        known = self._post("ALICE@essai.example")
        self.assertTrue(self._last_request().to_reconcile)
        self._new_session()
        unknown = self._post("inconnue@essai.example")
        self.assertFalse(self._last_request().to_reconcile)
        known_block = self._block(known.text, "bf_membership_signup_payment")
        self.assertTrue(known_block, "Le bouton « Payer en ligne » paraît pour un courriel connu.")
        self.assertEqual(known_block, self._block(unknown.text, "bf_membership_signup_payment"))

    def test_merged_request_is_not_paid_in_the_real_person_name(self):
        """La fusion passe sans facture, mais la session ne paie plus."""
        self._post("ALICE@essai.example")
        request = self._last_request()
        form = request.partner_id
        self._merge(form | self.alice, self.alice)
        self.assertFalse(form.exists())
        self.assertEqual(request.partner_id, self.alice)
        self.assertNotIn('action="/membres/adhesion/payer"', self.url_open(THANKS).text)
        response = self._click_pay()
        self.assertNotIn("/my/invoices/", response.url)
        self.assertFalse(request.invoice_id)
        self.assertFalse(self.env["account.move"].search([("partner_id", "=", self.alice.id)]),
                         "Aucune facture au nom de la vraie personne.")

    def test_form_contact_with_an_invoice_is_not_merged(self):
        """L'assistant de fusion réécrirait la facture au nom de l'autre contact."""
        self._post("ALICE@essai.example")
        request = self._last_request()
        form = request.partner_id
        self._click_pay()
        invoice = request.invoice_id
        self.assertEqual(invoice.partner_id, form)
        for dst in (self.alice, form):
            with self.subTest(dst=dst.name), self.assertRaises(UserError):
                self._merge(form | self.alice, dst)
        self.env.invalidate_all()
        self.assertEqual(invoice.partner_id, form)
        self.assertEqual(request.partner_id, form)

    def test_attach_moves_the_request_and_cancels_the_unpaid_invoice(self):
        self._post("ALICE@essai.example")
        request = self._last_request()
        form = request.partner_id
        self._click_pay()
        invoice = request.invoice_id
        self.assertEqual(invoice.state, "posted")
        wizard = self.env["bf.membership.attach"].with_user(self.agent).with_context(
            default_membership_id=request.id).create({})
        self.assertEqual(wizard.partner_id, self.alice, "Le contact au même courriel est proposé.")
        wizard.action_confirm()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(request.partner_id, self.alice)
        self.assertFalse(request.to_reconcile)
        self.assertFalse(request.invoice_id, "La facture du formulaire est séparée de l'adhésion.")
        self.assertEqual(invoice.state, "cancel")
        self.assertEqual(invoice.partner_id, form, "La facture reste au nom tapé au formulaire.")
        self.assertFalse(form.membership_ids)
        self.assertTrue(form.exists())
        self.assertFalse(form.active, "La facture le pointe : archivé, pas actif au courriel de la vraie personne.")
        response = self._click_pay()
        self.assertNotIn("/my/invoices/", response.url)
        self.assertFalse(self.env["account.move"].search([("partner_id", "=", self.alice.id)]))
        self._assert_nothing_sent(self.alice)

    def test_attach_deletes_the_form_contact_without_invoice(self):
        self._post("ALICE@essai.example")
        request = self._last_request()
        form = request.partner_id
        request.with_user(self.agent)._attach_to_partner(self.alice)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertFalse(form.exists(), "Plus pointé : supprimé.")
        self.assertEqual(request.partner_id, self.alice)

    def test_attach_spares_an_invoice_being_paid_online(self):
        """Comme le ménage quotidien : on n'annule pas une facture sous les
        pieds de la personne qui la paie, même quand sa transaction est encore
        en brouillon (elle est sur la page du prestataire)."""
        provider = self.env["payment.provider"].create({"name": "Fournisseur d'essai"})
        for state, target in (("draft", self.alice), ("pending", self.bruno)):
            with self.subTest(state=state):
                self._new_session()
                self._post("ALICE@essai.example")
                request = self._last_request()
                form = request.partner_id
                self._click_pay()
                invoice = request.invoice_id
                self.env["payment.transaction"].create({
                    "provider_id": provider.id,
                    "payment_method_id": self.env.ref("payment.payment_method_unknown").id,
                    "amount": invoice.amount_total,
                    "currency_id": invoice.currency_id.id,
                    "partner_id": form.id,
                    "reference": "ESSAI-%s" % state,
                    "invoice_ids": [Command.set(invoice.ids)],
                    "state": state,
                })
                request.with_user(self.agent)._attach_to_partner(target)
                self.env.flush_all()
                self.env.invalidate_all()
                self.assertEqual(invoice.state, "posted")
                self.assertEqual(request.invoice_id, invoice)
                self.assertEqual(request.partner_id, target)
                self.assertFalse(form.active)

    def test_agent_refuses_a_request_whose_session_clicked_pay(self):
        """Sans droits de facturation, l'agent refuse la demande en double :
        la facture impayée du formulaire est d'abord annulée."""
        self._post("ALICE@essai.example")
        request = self._last_request()
        self._click_pay()
        invoice = request.invoice_id
        self.assertEqual(invoice.state, "posted")
        request.with_user(self.agent).action_refuse()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(request.state, "refused")
        self.assertEqual(invoice.state, "cancel")
        self.assertFalse(request.invoice_id)

    def test_form_contact_with_an_invoice_is_archived_not_deleted(self):
        """Une facture, même annulée, garde son contact : un message clair dit
        d'archiver, plutôt qu'une erreur de la base."""
        self._post("ALICE@essai.example")
        request = self._last_request()
        form = request.partner_id
        self._click_pay()
        request.with_user(self.agent).action_refuse()
        self.env.flush_all()
        with self.assertRaises(UserError) as caught:
            form.unlink()
        self.assertIn("Archivez", str(caught.exception))
        self.env.invalidate_all()
        self.assertTrue(form.exists())

    def test_member_portal_hides_the_invoice_in_the_typed_name(self):
        """🔴 Rattachée payée, la demande garde la facture au nom tapé : son lien
        à jeton ne paraît pas au portail du membre réel."""
        self._post("personne@essai.example")
        request = self._last_request()
        self._click_pay()
        invoice = request.invoice_id
        self._pay(invoice)
        request.with_user(self.agent)._attach_to_partner(self.u_nobody.partner_id)
        self.env.flush_all()
        self.authenticate("portail_personne", "portail_personne")
        page = self.url_open("/my/membership").text
        self.assertIn(request.period_label, page, "L'adhésion rattachée paraît au portail du membre.")
        self.assertNotIn(invoice.name, page)
        self.assertNotIn("/my/invoices/%s" % invoice.id, page)

    def test_attach_keeps_the_existing_member_number(self):
        """Deux numéros : le contact existant garde le sien, sans erreur."""
        past = self._membership(self.alice, payment_state="paid", payment_source="cheque",
                                date_start=self._day(years=-3), date_end=self._day(years=-2))
        past.state = "expired"
        number = self.alice.member_number
        self.assertTrue(number)
        self._post("ALICE@essai.example")
        request = self._last_request()
        form = request.partner_id
        self._click_pay()
        self._pay(request.invoice_id)
        self.assertEqual(request.state, "active")
        self.assertTrue(form.member_number)
        request.with_user(self.agent)._attach_to_partner(self.alice)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(self.alice.member_number, number)
        self.assertEqual(request.partner_id, self.alice)
        self.assertTrue(request.invoice_id, "Une facture payée reste liée.")
        self.assertEqual(request.state, "active")

    def test_attach_hands_over_the_number_when_the_contact_has_none(self):
        self._post("ALICE@essai.example")
        request = self._last_request()
        form = request.partner_id
        self._click_pay()
        self._pay(request.invoice_id)
        number = form.member_number
        self.assertTrue(number)
        self.assertFalse(self.alice.member_number)
        request.with_user(self.agent)._attach_to_partner(self.alice)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(self.alice.member_number, number)
        self.assertFalse(form.member_number)


    def test_cancelled_form_invoice_gets_a_new_access_token(self):
        """Le lien reçu par le visiteur meurt avec la facture annulée : sinon une
        fusion faite plus tard le rendrait de nouveau parlant."""
        self._post("inconnue@essai.example")
        request = self._last_request()
        response = self._click_pay()
        invoice = request.invoice_id
        self.assertIn("access_token=", response.url)
        old = invoice.access_token
        self.assertTrue(old)
        request._cancel_unpaid_invoice(detach=False)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(invoice.state, "cancel")
        self.assertNotEqual(invoice.access_token, old)
        self.assertEqual(self.url_open("/my/invoices/%s?access_token=%s" % (invoice.id, old)).status_code in (403, 404)
                         or "/web/login" in self.url_open("/my/invoices/%s?access_token=%s" % (invoice.id, old)).url,
                         True, "L'ancien lien ne montre plus la facture.")

    def test_form_contact_is_never_the_kept_contact(self):
        """Gardé, le contact du formulaire imposerait les données et les
        consentements qu'un inconnu a tapés, même sans facture."""
        self._post("ALICE@essai.example", name="Personne Tapée")
        form = self._last_request().partner_id
        with self.assertRaises(UserError):
            self._merge(form | self.alice, form)
        self.env.invalidate_all()
        self.assertTrue(form.exists() and self.alice.exists())
        self.assertEqual(self.alice.name, "Alice Essai")

    def test_member_reads_nothing_typed_after_attach(self):
        """🔴 Rattachée, la demande devient celle du membre, qu'il lit au portail :
        ce que l'inconnu a tapé et « à rapprocher » restent des notes internes."""
        self._post("personne@essai.example", name="Votre compte est suspendu")
        request = self._last_request()
        request.with_user(self.agent)._attach_to_partner(self.u_nobody.partner_id)
        self.env.flush_all()
        self.env.invalidate_all()
        readable = self.env["mail.message"].with_user(self.u_nobody).search([
            ("model", "=", "bf.membership"), ("res_id", "=", request.id)])
        self.assertTrue(request.with_user(self.u_nobody).read(["state"]), "Le membre lit son adhésion.")
        for message in readable.sudo():
            self.assertNotIn("suspendu", (message.body or "") + (message.subject or ""))
            self.assertNotIn("rapprocher", message.body or "")

    def test_agent_cannot_forge_a_public_request(self):
        """🔴 Par un appel direct, un agent forgeait une « demande publique » sur
        le contact de son choix, liée à la facture de son choix, que le refus,
        le ménage quotidien ou le rattachement traitaient en superutilisateur."""
        foreign = self._invoice(self._membership(self.bruno))
        self.env.invalidate_all()
        Membership = self.env["bf.membership"].with_user(self.agent)
        forged = Membership.with_context(
            default_public_partner_id=self.alice.id, default_public_request=True,
            default_to_reconcile=True, default_public_ip="203.0.113.10",
            default_invoice_id=foreign.id,
        ).create({"partner_id": self.alice.id, "type_id": self.type_person.id})
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(
            (forged.public_request, forged.public_partner_id, forged.to_reconcile, forged.invoice_id),
            (False, self.env["res.partner"], False, self.env["account.move"]))
        for field, value in (("public_request", True), ("public_partner_id", self.alice.id)):
            with self.subTest(field=field), self.assertRaises(AccessError):
                Membership.create({"partner_id": self.denis.id, "type_id": self.type_person.id, field: value})
        with self.assertRaises(UserError):
            forged.with_user(self.agent)._attach_to_partner(self.denis)
        forged.with_user(self.agent).action_refuse()
        self.env.cr.execute("UPDATE account_move SET create_date = now() - interval '30 days' WHERE id = %s",
                            [foreign.id])
        self.env["bf.membership"]._cron_public_requests_daily()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(foreign.state, "posted", "La facture d'un autre client n'est jamais annulée.")
        self.assertTrue(self.alice.active)

    def test_refuse_never_cancels_another_invoice(self):
        """En défense : même liée par un chemin oublié (ici en base), une facture
        qui n'est pas celle de la demande n'est pas annulée."""
        foreign = self._invoice(self._membership(self.bruno))
        self._post("inconnue@essai.example")
        request = self._last_request()
        self.env.cr.execute("UPDATE bf_membership SET invoice_id = %s, payment_source = 'invoice' WHERE id = %s",
                            [foreign.id, request.id])
        self.env.invalidate_all()
        with self.assertRaises(UserError):
            request.with_user(self.agent).action_refuse()
        self.env.invalidate_all()
        self.assertEqual(foreign.state, "posted")

    def test_attach_never_removes_a_contact_with_a_login(self):
        """En défense : le rattachement ne supprime ni n'archive un contact qui a
        un accès, même désigné (ici en superutilisateur) comme contact du
        formulaire."""
        person = self.u_nobody.partner_id
        request = self.env["bf.membership"].create({
            "partner_id": person.id, "public_partner_id": person.id, "public_request": True,
            "type_id": self.type_person.id})
        request.with_user(self.agent)._attach_to_partner(self.alice)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertTrue(person.exists())
        self.assertTrue(person.active)
        self.assertEqual(request.partner_id, self.alice)

    def test_portal_reads_no_office_trace_of_the_request(self):
        """Rattachée, la demande est lue par le membre au portail, par RPC : ni
        qu'elle vient du formulaire public, ni la facture au nom tapé."""
        self._post("personne@essai.example")
        request = self._last_request()
        self._click_pay()
        self._pay(request.invoice_id)
        request.with_user(self.agent)._attach_to_partner(self.u_nobody.partner_id)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertTrue(request.invoice_id and request.payment_reference)
        mine = request.with_user(self.u_nobody)
        self.assertTrue(mine.read(["state"]), "Le membre lit son adhésion.")
        for field in ("public_request", "invoice_id", "invoice_payment_state", "payment_reference"):
            with self.subTest(field=field), self.assertRaises(AccessError):
                self.env.invalidate_all()
                mine.read([field])

    def test_merge_refusal_says_nothing_without_the_role(self):
        """Sans le rôle Membres, le refus de fusion est celui du socle, neutre :
        le nôtre nommerait le formulaire et la facture, et dirait qu'un contact
        a demandé à adhérer."""
        contacts = quiet_new_test_user(self.env, login="gestion_contacts_fusion",
                                       groups="base.group_user,base.group_partner_manager")
        self._post("ALICE@essai.example")
        form = self._last_request().partner_id
        self._click_pay()
        wizard = self.env["base.partner.merge.automatic.wizard"].with_user(contacts).create({
            "partner_ids": [Command.set((form | self.alice).ids)], "dst_partner_id": self.alice.id})
        with self.assertRaises(UserError) as caught:
            wizard._merge((form | self.alice).ids, self.alice)
        message = str(caught.exception)
        self.assertIn("avec vos droits", message)
        for word in ("formulaire", "facture", "adhésion"):
            self.assertNotIn(word, message)

    def test_form_contact_archived_in_silence_without_the_role(self):
        """Sans le rôle Membres, supprimer un contact du formulaire qui porte une
        facture l'archive en silence : aucun message ne dit pourquoi."""
        contacts = quiet_new_test_user(self.env, login="gestion_contacts_suppression",
                                       groups="base.group_user,base.group_partner_manager")
        self._post("ALICE@essai.example")
        request = self._last_request()
        form = request.partner_id
        self._click_pay()
        request.with_user(self.agent)._attach_to_partner(self.alice)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertFalse(form.active, "Le rattachement l'a déjà archivé.")
        form.with_user(contacts).unlink()
        self.env.invalidate_all()
        self.assertTrue(form.exists(), "Archivé, pas supprimé : la facture le garde.")

    def test_daily_task_spares_an_invoice_issued_by_the_team(self):
        """Le ménage des 14 jours ne vise que la facture du clic « Payer en
        ligne » : une facture émise par l'équipe reste à l'équipe."""
        self._post("inconnue@essai.example")
        request = self._last_request()
        invoice = self._invoice(request)
        self.assertFalse(request.public_invoice_id)
        self.env.cr.execute("UPDATE account_move SET create_date = now() - interval '15 days' WHERE id = %s",
                            [invoice.id])
        self.env.invalidate_all()
        self.env["bf.membership"]._cron_public_requests_daily()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(request.state, "waiting")

    def test_attached_form_contact_merge_refused_neutrally_without_the_role(self):
        """🔴 Rattachée, la demande a quitté le contact du formulaire, qui n'a plus
        d'adhésion. Sans le rôle Membres, fusionner ce contact avec un doublon
        reçoit le refus neutre du socle, jamais notre message nominatif."""
        contacts = quiet_new_test_user(self.env, login="gestion_contacts_doublon",
                                       groups="base.group_user,base.group_partner_manager")
        self._post("ALICE@essai.example")
        request = self._last_request()
        form = request.partner_id
        self._click_pay()
        request.with_user(self.agent)._attach_to_partner(self.alice)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertTrue(form.exists())
        self.assertFalse(form.membership_ids)
        duplicate = self.env["res.partner"].create({"name": "Personne Tapée (doublon)", "email": form.email})
        wizard = self.env["base.partner.merge.automatic.wizard"].with_user(contacts).create({
            "partner_ids": [Command.set((form | duplicate).ids)], "dst_partner_id": duplicate.id})
        with self.assertRaises(UserError) as caught:
            wizard._merge((form | duplicate).ids, duplicate)
        message = str(caught.exception)
        self.assertIn("avec vos droits", message)
        for word in ("formulaire", "facture", "adhésion"):
            self.assertNotIn(word, message)
