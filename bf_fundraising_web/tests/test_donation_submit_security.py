# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import re

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestDonationSubmitSecurity(HttpCase):
    """/don/submit ne doit ni énumérer le carnet d'adresses, ni
    accepter un montant non fini, ni se laisser marteler depuis une IP."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["product.product"].create(
            {
                "name": "Don web (test)",
                "type": "service",
                "donation_type": "donation",
                "tax_receipt_ok": True,
                "taxes_id": [(5,)],
                "supplier_taxes_id": [(5,)],
            }
        )
        cls.victim = cls.env["res.partner"].create(
            {"name": "Victime", "email": "secret.victime@exemple-prive.test"}
        )

    def setUp(self):
        super().setUp()
        from odoo.addons.bf_fundraising_web.controllers import main

        self.main = main
        data = getattr(main, "_submit_data", None)
        if data is not None:
            data.clear()
            self.addCleanup(data.clear)

    def _post(self, **data):
        resp = self.url_open("/don")
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', resp.text)
        self.assertIsNotNone(m)
        payload = {"csrf_token": m.group(1), "name": "Visiteur", "amount": "10"}
        payload.update(data)
        return self.url_open("/don/submit", data=payload)

    def _donation_count(self):
        return self.env["donation.donation"].sudo().search_count([])

    def test_wildcard_email_does_not_leak_partner(self):
        before = self._donation_count()
        resp = self._post(email="secret%@%")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("secret.victime@exemple-prive.test", resp.text)
        self.assertEqual(self._donation_count(), before)
        # Aucun don rattaché à la fiche de la victime.
        self.assertFalse(
            self.env["donation.donation"].sudo().search(
                [("partner_id", "=", self.victim.id)]
            )
        )

    def test_thanks_page_shows_only_typed_email(self):
        # Même courriel à la casse près : le don est apparié, mais la page
        # n'affiche que ce que le visiteur a saisi.
        before = self._donation_count()
        resp = self._post(email="SECRET.Victime@exemple-prive.test")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._donation_count(), before + 1)
        self.assertIn("Merci de votre", resp.text)
        self.assertIn("SECRET.Victime@exemple-prive.test", resp.text)
        self.assertNotIn("secret.victime@exemple-prive.test", resp.text)

    def test_nan_and_infinite_amount_rejected(self):
        before = self._donation_count()
        for bad in ("nan", "inf", "-inf", "0", "-5"):
            resp = self._post(email="donateur@exemple.test", amount=bad)
            # Refus propre (formulaire réaffiché), pas une erreur serveur.
            self.assertEqual(resp.status_code, 200, bad)
            self.assertIn('name="amount"', resp.text)
        self.assertEqual(self._donation_count(), before)

    def test_submit_is_rate_limited_per_ip(self):
        before = self._donation_count()
        limit = getattr(self.main, "_SUBMIT_MAX", 5)
        for i in range(limit + 3):
            self._post(email="d%d@exemple.test" % i)
        self.assertEqual(self._donation_count() - before, limit)
