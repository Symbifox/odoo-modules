import re

from odoo.tests import HttpCase, tagged

from .common import BillingFixture

PAGE = "/my/school/childcare"


@tagged("post_install", "-at_install")
class TestChildcareBillingPortal(BillingFixture, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_billing()
        cls.env["bf.school.childcare.rate"].create({"school_id": cls.school.id, "year_id": cls.year.id,
                                                    "kind": "ticket_book", "amount": 30.0, "tickets": 10})

    def _post(self, url, **data):
        page = self.url_open("/my/account").text
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        return self.url_open(url, data=dict(data, csrf_token=token), allow_redirects=False)

    def test_buy_a_book_and_pay_it_online(self):
        self.authenticate("cc_mom", "cc_mom")
        page = self.url_open(PAGE).text
        self.assertIn("/my/school/childcare/tickets/buy", page)
        response = self._post(PAGE + "/tickets/buy", student_id=self.alice.id)
        self.assertEqual(response.status_code, 303)
        book = self.env["bf.school.childcare.ticket_book"].search([("student_id", "=", self.alice.id)])
        self.assertEqual((book.payer_id, book.state, book.tickets), (self.mom.partner_id, "to_pay", 10))
        self.assertIn("/my/invoices/%s" % book.invoice_id.id, response.headers["Location"])
        self.assertIn(PAGE + "/tickets/%s/pay" % book.id, self.url_open(PAGE).text)
        # The other parent does not see a book they did not buy, nor reach its invoice.
        self.authenticate("cc_dad", "cc_dad")
        self.assertNotIn(PAGE + "/tickets/%s/pay" % book.id, self.url_open(PAGE).text)
        # Followed: the website first sends a French reader to its French address.
        self.assertEqual(self.url_open(PAGE + "/tickets/%s/pay" % book.id).status_code, 404)
        # Paid: the tickets left are the buyer's to read, not the other parent's.
        self.env["account.payment.register"].with_context(active_model="account.move",
                                                          active_ids=book.invoice_id.ids).create({})._create_payments()
        self.assertNotIn("<strong>10</strong>", self.url_open(PAGE).text)
        self.authenticate("cc_mom", "cc_mom")
        self.assertIn("<strong>10</strong>", self.url_open(PAGE).text)

    def test_only_an_adult_with_parental_authority_buys(self):
        for login in ("cc_grandma", "cc_other"):
            self.authenticate(login, login)
            self.assertEqual(self._post(PAGE + "/tickets/buy", student_id=self.alice.id).status_code, 404, login)
        self.assertFalse(self.env["bf.school.childcare.ticket_book"].search([("student_id", "=", self.alice.id)]))
