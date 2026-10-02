import re

from odoo.tests import tagged

from .common import MembershipPortalCase

FORM = "/membres/adhesion"


@tagged("post_install", "-at_install", "bf_membership_portal")
class TestPublicForm(MembershipPortalCase):

    def setUp(self):
        super().setUp()
        self.type_person.public_signup = True
        self.alice.write({"phone": "555 555-0101", "directory_consent": False})

    def _post(self, **extra):
        data = {
            "csrf_token": self._csrf(FORM),
            "type_id": str(self.type_person.id),
            "name": "Gilberte Nouvelle",
            "email": "gilberte@essai.example",
            "phone": "555 555-0199",
            "street": "4, rue Nouvelle",
            "city": "Lac-Exemple",
            "zip": "A1A 1A1",
        }
        data.update(extra)
        return self.url_open(FORM, data=data)

    def _requests(self):
        return self.env["bf.membership"].search([("public_request", "=", True)])

    def test_form_offers_public_categories_and_unchecked_consents(self):
        page = self.url_open(FORM).text
        self.assertIn(self.type_person.name, page)
        self.assertNotIn(self.type_review.name, page, "Seules les catégories ouvertes au public.")
        for name in ("directory_consent", "notice_email_consent"):
            tag = re.search(r'<input[^>]*name="%s"[^>]*>' % name, page).group(0)
            self.assertNotIn("checked", tag, "Décochée d'office (Loi 25, art. 9.1).")
        self.assertIn('name="website_url"', page, "Le champ piège est là.")

    def test_closed_without_public_category(self):
        self.type_person.public_signup = False
        self.assertEqual(self.url_open(FORM).status_code, 404)

    def test_new_request_has_no_invoice_until_pay_click(self):
        """La facture naît au clic « Payer en ligne », jamais à l'envoi du
        formulaire (une facture validée ne se supprime pas)."""
        response = self._post()
        membership = self._requests()
        self.assertEqual(len(membership), 1)
        partner = membership.partner_id
        self.assertEqual(partner.name, "Gilberte Nouvelle")
        self.assertEqual(partner.email, "gilberte@essai.example")
        self.assertFalse(partner.directory_consent)
        self.assertFalse(membership.to_reconcile)
        self.assertEqual(membership.state, "waiting")
        self.assertFalse(membership.invoice_id, "Aucune facture à l'envoi du formulaire.")
        self.assertFalse(self.env["account.move"].search_count([("partner_id", "=", partner.id)]))
        self.assertIn("Votre demande d'adhésion", response.text)
        self.assertIn('action="/membres/adhesion/payer"', response.text)
        acks = self._mails_to(partner)
        self.assertEqual(len(acks), 1, "Un seul courriel : l'accusé de réception.")
        self.assertEqual(acks.subject, "Votre demande d'adhésion est reçue")

    def test_pay_click_creates_one_invoice_and_leads_to_payment(self):
        self._post()
        membership = self._requests()
        token = self._csrf("/membres/adhesion/merci")
        first = self.url_open("/membres/adhesion/payer", data={"csrf_token": token})
        invoice = membership.invoice_id
        self.assertEqual(invoice.state, "posted", "Validée : le paiement en ligne l'exige.")
        self.assertFalse(invoice.invoice_user_id, "Le visiteur n'est pas vendeur de sa facture.")
        self.assertRegex(first.url, r"/my/invoices/%s\?access_token=[\w-]+" % invoice.id)
        second = self.url_open("/membres/adhesion/payer", data={"csrf_token": token})
        self.assertIn("/my/invoices/%s" % invoice.id, second.url)
        self.assertEqual(self.env["account.move"].search_count([
            ("partner_id", "=", membership.partner_id.id), ("move_type", "=", "out_invoice")]), 1,
            "Deux clics, une facture.")
        self.assertEqual(len(self._mails_to(membership.partner_id)), 1,
                         "Le clic ne fait partir aucun courriel de plus que l'accusé.")

    def test_another_session_cannot_pay_or_invoice(self):
        """Le bouton et la facture appartiennent à la session qui a envoyé la demande."""
        self._post()
        membership = self._requests()
        self.opener.cookies.clear()
        self.authenticate(None, None)  # une autre session anonyme, sur la même base
        self.assertNotIn('action="/membres/adhesion/payer"', self.url_open("/membres/adhesion/merci").text)
        token = self._csrf(FORM)  # un jeton valide pour CETTE nouvelle session
        response = self.url_open("/membres/adhesion/payer", data={"csrf_token": token})
        self.assertNotIn("/my/invoices/", response.url)
        self.assertFalse(membership.invoice_id)

    def _mails_to(self, partner):
        """Les courriels à ce contact, par son adresse (l'accusé part à
        l'adresse seule) ou comme destinataire."""
        return self.env["mail.mail"].search([
            "|", ("recipient_ids", "in", partner.ids), ("email_to", "=", partner.email)])

    def test_team_is_told_of_a_new_request(self):
        """Les responsables sont prévenus, pas l'agent ni l'employé."""
        self._post()
        membership = self._requests()
        notice = membership.message_ids.filtered(lambda m: "Nouvelle demande d'adhésion" in (m.body or ""))
        self.assertEqual(len(notice), 1)
        self.assertEqual(notice.subject, "Nouvelle demande d'adhésion",
                         "Un objet générique : le nom tapé par un inconnu reste dans le corps.")
        self.assertTrue(notice.subtype_id.internal, "Une note interne, que le portail ne lit pas.")
        self.assertIn(self.manager.partner_id, notice.partner_ids)
        self.assertTrue(self.env["mail.notification"].search([
            ("mail_message_id", "=", notice.id), ("res_partner_id", "=", self.manager.partner_id.id)]),
            "La personne responsable est avisée.")
        self.assertNotIn(self.agent.partner_id, notice.partner_ids)
        self.assertNotIn(self.employee.partner_id, notice.partner_ids)
        self.assertNotIn(membership.partner_id, notice.partner_ids, "La personne n'est pas dans le fil de l'équipe.")

    def test_ack_is_the_same_whether_the_email_is_known_or_not(self):
        """🔴 L'accusé ne révèle rien (Loi 25) : « à rapprocher » ne va qu'à l'équipe."""
        self._post(name="Même Nom", email="ALICE@essai.example")
        self._post(name="Même Nom", email="inconnue@essai.example")
        known, unknown = (self._requests().filtered(lambda m: m.partner_id.email == e)
                          for e in ("alice@essai.example", "inconnue@essai.example"))
        ack_known, ack_unknown = self._mails_to(known.partner_id), self._mails_to(unknown.partner_id)
        self.assertEqual((ack_known.subject, ack_known.body_html), (ack_unknown.subject, ack_unknown.body_html))
        self.assertNotIn("rapprocher", ack_known.body_html)
        self.assertFalse(self.env["mail.mail"].search([("recipient_ids", "in", self.alice.ids)]),
                         "Rien au contact existant lui-même.")
        team = known.message_ids.filtered(lambda m: "Nouvelle demande" in (m.body or ""))
        self.assertIn("rapprocher", team.body)

    def test_known_email_never_touches_the_existing_contact(self):
        """🔴 Le contact existant reste intact, et la réponse est la même."""
        watched = ["name", "phone", "street", "city", "directory_consent", "notice_email_consent", "member_number"]
        before = self.alice.read(watched)[0]
        known = self._post(name="Quelqu'un d'autre", email="ALICE@essai.example", phone="000",
                           directory_consent="1", notice_email_consent="1")
        unknown = self._post(email="inconnue@essai.example")
        self.env.invalidate_all()
        after = self.alice.read(watched)[0]
        for key, value in before.items():
            self.assertEqual(after[key], value, "Le formulaire a changé %s du contact existant." % key)
        requests = self._requests()
        self.assertEqual(len(requests), 2)
        twin = requests.filtered(lambda m: m.partner_id.email == "alice@essai.example")
        self.assertNotEqual(twin.partner_id, self.alice, "Un contact neuf, toujours.")
        self.assertTrue(twin.to_reconcile)
        self.assertTrue(twin.partner_id.directory_consent, "Les choix vont au contact neuf.")
        self.assertFalse((requests - twin).to_reconcile)
        message_known = self._block(known.text, "bf_membership_signup_message")
        self.assertTrue(message_known)
        self.assertEqual(message_known, self._block(unknown.text, "bf_membership_signup_message"),
                         "La réponse ne dit pas si le courriel est connu.")

    def test_honeypot_creates_nothing_and_answers_the_same(self):
        normal = self._post(email="vraie@essai.example")
        robot = self._post(email="robot@essai.example", website_url="http://spam.example")
        self.assertEqual(len(self._requests()), 1)
        self.assertFalse(self.env["res.partner"].search([("email", "=", "robot@essai.example")]))
        self.assertEqual(self._block(normal.text, "bf_membership_signup_message"),
                         self._block(robot.text, "bf_membership_signup_message"))

    def test_csrf_is_required(self):
        response = self.url_open(FORM, data={
            "type_id": str(self.type_person.id), "name": "Sans jeton", "email": "sansjeton@essai.example"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self._requests())

    def test_decision_category_waits_without_invoice(self):
        self.type_org.public_signup = True
        response = self._post(type_id=str(self.type_org.id), name="Association des Essais",
                              email="association@essai.example")
        membership = self._requests()
        self.assertTrue(membership.partner_id.is_company)
        self.assertEqual(membership.state, "draft")
        self.assertFalse(membership.invoice_id)
        self.assertNotIn('action="/membres/adhesion/payer"', response.text, "Rien à payer avant la décision.")

    def test_non_public_category_refused(self):
        response = self._post(type_id=str(self.type_review.id))
        self.assertIn("Choisissez une catégorie", response.text)
        self.assertFalse(self._requests())

    def test_burst_from_one_address_is_capped(self):
        Membership = self.env["bf.membership"]
        # L'adresse du client HTTP de l'essai, la boucle locale : la rafale vient
        # de la même adresse que l'envoi qui suit.
        for i in range(5):
            partner = self.env["res.partner"].create({"name": "Rafale %s" % i})
            Membership.create({"partner_id": partner.id, "type_id": self.type_person.id,
                               "public_request": True, "public_ip": "127.0.0.1"})
        before = len(self._requests())
        response = self._post()
        self.assertIn("Trop de demandes", response.text)
        self.assertEqual(len(self._requests()), before)

    def test_public_ip_is_forgotten(self):
        self._post()
        membership = self._requests()
        self.assertTrue(membership.public_ip)
        self.env.cr.execute("UPDATE bf_membership SET create_date = now() - interval '8 days' WHERE id = %s",
                            [membership.id])
        self.env.invalidate_all()
        self.env["bf.membership"]._cron_forget_public_ip()
        self.assertFalse(membership.public_ip)

    def test_ack_repeats_nothing_that_was_typed(self):
        """🔴 L'accusé ne reprend pas le nom tapé : sinon, n'importe qui ferait
        partir de l'adresse de l'organisme un texte de son choix."""
        self._post(name="Gilberte Visitez-www-exemple-test")
        ack = self._mails_to(self._requests().partner_id)
        self.assertEqual(len(ack), 1)
        self.assertNotIn("Gilberte", ack.body_html)
        self.assertNotIn("exemple-test", ack.body_html)
        self.assertIn("Bonjour,", ack.body_html)
        # L'en-tête « À » : l'adresse seule, sans le nom tapé.
        self.assertEqual(ack.email_to, "gilberte@essai.example")
        self.assertFalse(ack.recipient_ids)
        headers = [address for email in ack._prepare_outgoing_list() for address in email["email_to"]]
        self.assertEqual(headers, ["gilberte@essai.example"])

    def test_unpaid_public_invoice_cancelled_after_fourteen_days(self):
        """Un robot ne laisse pas de factures orphelines."""
        pay = "/membres/adhesion/payer"
        requests = {}
        for key in ("old", "recent", "paid"):
            self.opener.cookies.clear()
            self.authenticate(None, None)
            before = self._requests()
            self._post(email="%s@essai.example" % key)
            requests[key] = self._requests() - before
            self.url_open(pay, data={"csrf_token": self._csrf("/membres/adhesion/merci")})
        self._pay(requests["paid"].invoice_id)
        invoices = {key: rec.invoice_id for key, rec in requests.items()}
        self.env.cr.execute("UPDATE account_move SET create_date = now() - interval '15 days' WHERE id IN %s",
                            [(invoices["old"].id, invoices["paid"].id)])
        self.env.invalidate_all()
        self.env["bf.membership"]._cron_public_requests_daily()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(invoices["old"].state, "cancel")
        self.assertEqual(requests["old"].state, "refused")
        self.assertEqual(invoices["recent"].state, "posted")
        self.assertEqual(requests["recent"].state, "waiting")
        self.assertEqual(invoices["paid"].state, "posted")
        self.assertEqual(requests["paid"].state, "active")
        self.assertEqual(len(self._mails_to(requests["old"].partner_id)), 1, "Rien de plus que l'accusé.")

    def test_wildcard_in_the_email_matches_no_other_contact(self):
        """`_` est un joker de `=ilike` : sans échappement, jean_paul@ rapprocherait jeanxpaul@."""
        self.env["res.partner"].create({"name": "Jean X Paul", "email": "jeanxpaul@essai.example"})
        self._post(email="jean_paul@essai.example")
        self.assertFalse(self._requests().to_reconcile)

    def test_consents_given_on_the_form_are_logged_on_the_request(self):
        """Loi 25 : qui, quand, par où. Le contact naît avant la demande, et le
        socle ne consigne pas un consentement sans adhésion : la demande le
        consigne, les deux consentements, cochés ou non."""
        self._post(directory_consent="1")
        request = self._requests()
        log = request.message_ids.filtered(lambda m: "au formulaire public" in (m.body or ""))
        self.assertEqual(len(log), 1)
        self.assertIn("Gilberte Nouvelle", log.body)
        self.assertIn("Paraît au répertoire des membres : oui", log.body)
        self.assertIn("Accepte les avis par courriel : non", log.body)
        self.assertTrue(log.subtype_id.internal, "Lue des seuls agents.")

    def test_changed_fee_is_not_invoiced_by_the_pay_click(self):
        """La garde du bouton « Facturer » vaut pour « Payer en ligne » : une
        cotisation changée par l'équipe n'est pas validée au clic anonyme."""
        self._post()
        request = self._requests()
        request.with_user(self.agent).write({"amount": 1.0})
        response = self.url_open("/membres/adhesion/payer",
                                 data={"csrf_token": self._csrf("/membres/adhesion/merci")})
        self.assertIn("message=office", response.url)
        self.assertIn("Communiquez avec l'organisme", response.text)
        self.env.invalidate_all()
        self.assertFalse(request.invoice_id)
