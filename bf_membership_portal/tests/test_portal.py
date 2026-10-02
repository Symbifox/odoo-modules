import html

from dateutil.relativedelta import relativedelta

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import MembershipPortalCase


@tagged("post_install", "-at_install", "bf_membership_portal")
class TestPortalRules(MembershipPortalCase):
    """La règle du portail, jouée dans le rôle visé, cache vidé avant chaque
    lecture : le cache de la transaction masque un refus si l'enregistrement
    a déjà été lu en administrateur."""

    def _as(self, user):
        self.env.invalidate_all()
        return self.env["bf.membership"].with_user(user)

    def test_member_sees_only_own_memberships(self):
        found = self._as(self.u_alice).search([])
        self.assertEqual(found, self.m_alice)
        with self.assertRaises(AccessError):
            self._as(self.u_alice).browse(self.m_bruno.id).read(["state"])

    def test_portal_reads_but_never_writes(self):
        with self.assertRaises(AccessError):
            self._as(self.u_alice).browse(self.m_alice.id).write({"payment_state": "exempt"})
        with self.assertRaises(AccessError):
            self._as(self.u_alice).create({"partner_id": self.u_alice.partner_id.id, "type_id": self.type_person.id})

    def test_office_fields_stay_at_the_office(self):
        self.m_alice.note = "<p>Note du bureau</p>"
        with self.assertRaises(AccessError):
            self._as(self.u_alice).browse(self.m_alice.id).read(["note"])
        with self.assertRaises(AccessError):
            self._as(self.u_alice).browse(self.m_alice.id).read(["withdrawal_reason"])

    def test_delegate_in_office_reads_the_organization(self):
        found = self._as(self.u_carole).search([])
        self.assertEqual(found, self.m_org)
        with self.assertRaises(AccessError):
            self._as(self.u_carole).browse(self.m_org.id).write({"note": "x"})

    def test_former_delegate_sees_nothing(self):
        self.assertFalse(self._as(self.u_denis).search([]))
        with self.assertRaises(AccessError):
            self._as(self.u_denis).browse(self.m_org.id).read(["state"])

    def test_link_follows_the_login_not_the_email(self):
        """Un contact qui porte le même courriel que l'usager n'ouvre rien."""
        twin = self.env["res.partner"].create({"name": "Jumeau", "email": self.u_alice.email,
                                               "street": "3, rue Jumeau", "city": "Bourg-Exemple"})
        twin_membership = self._membership(twin, payment_state="paid", payment_source="cheque")
        self.assertNotIn(twin_membership, self._as(self.u_alice).search([]))
        self.u_alice.partner_id.email = "nouveau@essai.example"
        self.assertEqual(self._as(self.u_alice).search([]), self.m_alice)


@tagged("post_install", "-at_install", "bf_membership_portal")
class TestPortalPages(MembershipPortalCase):

    def test_page_shows_own_membership_only(self):
        self.authenticate("portail_alice", "portail_alice")
        page = self.url_open("/my/membership").text
        self.assertIn(self.u_alice.partner_id.member_number, page)
        self.assertNotIn(self.u_bruno.partner_id.member_number, page)
        self.assertNotIn("Bruno Portail", page)
        # Aucun identifiant dans l'adresse n'ouvre l'adhésion d'un autre.
        for query in ("?partner_id=%s" % self.u_bruno.partner_id.id,
                      "?membership_id=%s" % self.m_bruno.id, "?member=%s" % self.m_bruno.id):
            other = self.url_open("/my/membership" + query).text
            self.assertNotIn(self.u_bruno.partner_id.member_number, other)
        self.assertEqual(self.url_open("/my/membership/%s" % self.m_bruno.id).status_code, 404)

    def test_delegate_page_shows_organization(self):
        self.authenticate("portail_carole", "portail_carole")
        page = self.url_open("/my/membership").text
        self.assertIn("Adhésion de Organisation des Portails (essai)", page)
        self.assertIn(self.org.member_number, page)
        self.authenticate("portail_denis", "portail_denis")
        self.assertNotIn("Organisation des Portails", self.url_open("/my/membership").text)

    def test_home_counter(self):
        self.authenticate("portail_alice", "portail_alice")
        counters = self.make_jsonrpc_request("/my/counters", {"counters": ["membership_count"]})
        self.assertEqual(counters["membership_count"], 1)
        self.authenticate("portail_personne", "portail_personne")
        counters = self.make_jsonrpc_request("/my/counters", {"counters": ["membership_count"]})
        self.assertEqual(counters["membership_count"], 0)

    def test_card_for_members_only(self):
        self.authenticate("portail_alice", "portail_alice")
        response = self.url_open("/my/membership/card")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Alice Portail", response.text)
        self.assertIn(self.u_alice.partner_id.member_number, response.text)
        self.assertIn("En règle jusqu'au", response.text)
        self.authenticate("portail_personne", "portail_personne")
        self.assertEqual(self.url_open("/my/membership/card").status_code, 404)

    def test_member_changes_own_consents(self):
        partner = self.u_alice.partner_id
        self.assertFalse(partner.directory_consent)
        messages_before = partner.message_ids
        self.authenticate("portail_alice", "portail_alice")
        token = self._csrf("/my/membership")
        self.url_open("/my/membership/consent", data={"csrf_token": token, "directory_consent": "1"})
        self.env.invalidate_all()
        self.assertTrue(partner.directory_consent)
        self.assertEqual(partner.message_ids, messages_before,
                         "Rien au fil du contact, pas même un message de suivi vide.")
        self.assertFalse(partner.notice_email_consent)
        # 🔴 La preuve va sur l'adhésion, lue des seuls agents, jamais au fil du
        # contact, que tout employé lit (elle dirait qu'il est membre). Le socle
        # la consigne : une seule ligne, qui dit « au portail ».
        def notes(record):
            return record.message_ids.filtered(lambda m: "Paraît au répertoire des membres" in (m.body or ""))
        self.assertFalse(notes(partner))
        self.assertEqual(len(notes(self.m_alice)), 1, "Une seule ligne par changement.")
        self.assertIn("au portail", notes(self.m_alice).body)

    def test_card_reads_the_status_of_this_company(self):
        """🔴 La carte se lit dans la société du site : une personne en règle dans
        une autre société seulement ne l'obtient pas d'ici."""
        other = self.env["res.company"].create({"name": "Seconde société (essai)"})
        other_type = self.type_person.copy({"company_id": other.id, "code": "REGC"})
        person = self.u_nobody.partner_id
        self.env["bf.membership"].create({
            "partner_id": person.id, "type_id": other_type.id, "company_id": other.id,
            "payment_state": "paid", "payment_source": "cheque"})
        self._recompute_status(person)
        self.assertEqual(person.member_status, "member", "En règle, toutes sociétés confondues.")
        self.authenticate("portail_personne", "portail_personne")
        self.assertEqual(self.url_open("/my/membership/card").status_code, 404)
        self.u_nobody.write({"company_ids": [(4, other.id)], "company_id": other.id})
        self.authenticate("portail_personne", "portail_personne")
        response = self.url_open("/my/membership/card")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Seconde société (essai)", response.text)

    def test_card_button_follows_this_company(self):
        """Le bouton de la carte se fonde sur le statut de CETTE société, comme la
        route de la carte : une ancienne membre d'ici, en règle ailleurs, ne
        voit pas un bouton qui répondrait 404."""
        other = self.env["res.company"].create({"name": "Seconde société (essai)"})
        other_type = self.type_person.copy({"company_id": other.id, "code": "REGD"})
        person = self.u_nobody.partner_id
        self._membership(person, payment_state="paid", payment_source="cheque",
                         date_start=self._day(years=-3), date_end=self._day(years=-2)).state = "expired"
        self.env["bf.membership"].create({
            "partner_id": person.id, "type_id": other_type.id, "company_id": other.id,
            "payment_state": "paid", "payment_source": "cheque"})
        self._recompute_status(person)
        self.assertEqual(person.member_status, "member", "En règle, toutes sociétés confondues.")
        self.authenticate("portail_personne", "portail_personne")
        page = self.url_open("/my/membership").text
        self.assertIn("Historique", page, "La page montre bien les adhésions d'ici.")
        self.assertNotIn('href="/my/membership/card"', page)
        self.assertEqual(self.url_open("/my/membership/card").status_code, 404)

    def test_consents_need_a_membership(self):
        self.authenticate("portail_personne", "portail_personne")
        token = self._csrf("/my/account")
        response = self.url_open("/my/membership/consent", data={"csrf_token": token, "directory_consent": "1"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.u_nobody.partner_id.directory_consent)


@tagged("post_install", "-at_install", "bf_membership_portal")
class TestPortalRenewal(MembershipPortalCase):

    def setUp(self):
        super().setUp()
        # Une adhésion qui finit dans dix jours : le renouvellement est offert.
        self.m_alice.write({"date_start": self._day(years=-1, days=11), "date_end": self._day(days=10)})

    def test_renewal_forged_for_another_person_is_refused(self):
        """🔴 Le renouvellement d'une autre personne, rattaché (ici en
        superutilisateur, comme par un chemin oublié) à l'adhésion d'Alice :
        Alice ne reçoit ni facture ni redirection au nom de l'autre."""
        other = self.env["bf.membership"].create({
            "partner_id": self.bruno.id, "type_id": self.type_person.id,
            "date_start": self.m_alice.date_end + relativedelta(days=1),
            "renewal_of_id": self.m_alice.id,
        })
        self.assertEqual(other.state, "waiting")
        self.authenticate("portail_alice", "portail_alice")
        page = self.url_open("/my/membership").text
        self.assertNotIn('action="/my/membership/renew"', page, "Rien à renouveler n'est offert.")
        self.assertNotIn("Payer le renouvellement", page)
        response = self._renew()
        self.assertNotIn("/my/invoices/", response.url)
        self.env.invalidate_all()
        self.assertFalse(other.invoice_id)
        self.assertFalse(self.env["account.move"].search([("partner_id", "=", self.bruno.id)]))

    def test_withdrawal_in_another_company_does_not_block_renewal(self):
        """Un retrait dans une autre société ne bloque pas le renouvellement d'ici."""
        other = self.env["res.company"].create({"name": "Seconde société (essai)"})
        other_type = self.type_person.copy({"company_id": other.id, "code": "REGE"})
        elsewhere = self.env["bf.membership"].create({
            "partner_id": self.u_alice.partner_id.id, "type_id": other_type.id, "company_id": other.id})
        elsewhere._withdraw(self.today, "Départ de l'autre société")
        self.assertEqual(elsewhere.state, "withdrawn")
        self.authenticate("portail_alice", "portail_alice")
        self.assertIn('action="/my/membership/renew"', self.url_open("/my/membership").text)

    def test_changed_fee_is_not_invoiced_by_the_renewal(self):
        """La garde du bouton « Facturer » vaut au portail : une cotisation mise à
        1,00 $ par l'agent n'est pas validée au clic « Renouveler »."""
        self.m_alice.with_user(self.agent).action_renew()
        renewal = self.m_alice.renewal_ids
        renewal.with_user(self.agent).write({"amount": 1.0})
        with self.assertRaises(AccessError):
            renewal.with_user(self.agent).action_create_invoice()
        self.authenticate("portail_alice", "portail_alice")
        response = self._renew()
        self.assertIn("message=renewal_office", response.url)
        self.assertIn("Communiquez avec l'organisme", html.unescape(response.text))
        self.env.invalidate_all()
        self.assertFalse(renewal.invoice_id)
        self.assertFalse(self.env["account.move"].search([("partner_id", "=", self.u_alice.partner_id.id)]))

    def test_person_of_an_organization_renews_without_invoice(self):
        """Rattachée à une entreprise, la personne ne se facture pas dans Odoo :
        le renouvellement est préparé, et l'organisme règle le paiement."""
        self.u_alice.partner_id.parent_id = self.org
        self.authenticate("portail_alice", "portail_alice")
        response = self._renew()
        self.assertEqual(response.status_code, 200)
        self.assertIn("message=renewal_office", response.url)
        self.env.invalidate_all()
        renewal = self.m_alice.renewal_ids
        self.assertEqual(len(renewal), 1)
        self.assertFalse(renewal.invoice_id)
        self.assertFalse(self.env["account.move"].search([("commercial_partner_id", "=", self.org.id)]))

    def _renew(self, token_page="/my/membership"):
        token = self._csrf(token_page)
        return self.url_open("/my/membership/renew", data={"csrf_token": token})

    def test_renew_twice_makes_one_renewal_and_one_invoice(self):
        self.authenticate("portail_alice", "portail_alice")
        self.assertIn("Renouveler", self.url_open("/my/membership").text)
        first = self._renew()
        second = self._renew()
        self.env.invalidate_all()
        renewal = self.m_alice.renewal_ids
        self.assertEqual(len(renewal), 1)
        invoice = renewal.invoice_id
        self.assertEqual(invoice.state, "posted", "Validée : le paiement en ligne l'exige.")
        self.assertEqual(self.env["account.move"].search_count([
            ("partner_id", "=", self.u_alice.partner_id.id), ("move_type", "=", "out_invoice")]), 1)
        self.assertFalse(invoice.invoice_user_id, "Le membre n'est pas vendeur de sa facture.")
        for response in (first, second):
            self.assertIn("/my/invoices/%s" % invoice.id, response.url)
        self.assertIn("Payer le renouvellement", self.url_open("/my/membership").text)
        self._assert_nothing_sent(self.u_alice.partner_id)

    def test_payment_of_the_renewal_makes_it_current(self):
        self.authenticate("portail_alice", "portail_alice")
        self._renew()
        renewal = self.m_alice.renewal_ids
        self._pay(renewal.invoice_id)
        self.assertEqual(renewal.state, "active")

    def test_not_offered_too_early(self):
        self.m_alice.write({"date_start": self.today, "date_end": self._day(days=200)})
        self.authenticate("portail_alice", "portail_alice")
        self.assertNotIn("Renouveler", self.url_open("/my/membership").text)
        self._renew()
        self.assertFalse(self.m_alice.renewal_ids)

    def test_delegate_cannot_renew_the_organization(self):
        self.m_org.write({"date_start": self._day(years=-1, days=11), "date_end": self._day(days=10)})
        self.authenticate("portail_carole", "portail_carole")
        self._renew(token_page="/my/account")
        self.assertFalse(self.m_org.renewal_ids)
