"""Le nom d'un terme et d'un versement, tel que l'usager le lit.

Sans nom, la fiche s'affichait « bf.rental.term,12 » au fil d'Ariane.
"""
from odoo.tests.common import tagged
from odoo.tools.misc import format_amount

from .common import RentCase


@tagged("post_install", "-at_install")
class TestRentDisplayName(RentCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_CA")

    def _named(self):
        lease = self._lease()
        term = self._term(lease=lease, date_due="2026-10-01", amount=1000.0)
        payment = self.env["bf.rental.payment"].create({
            "term_id": term.id, "amount": 600.0, "date": "2026-10-03",
        })
        return lease, term, payment

    def test_a_term_is_named_by_its_due_date_and_lease(self):
        lease, term, _payment = self._named()
        self.assertTrue(lease.name.startswith("BAIL/"))
        self.assertEqual(
            term.with_context(lang="fr_CA").display_name,
            "Loyer exigible le 2026-10-01 · %s" % lease.name,
        )

    def test_a_payment_is_named_by_amount_date_and_lease(self):
        lease, _term, payment = self._named()
        fr = payment.with_context(lang="fr_CA")
        self.assertEqual(
            fr.display_name,
            "Versement de %s reçu le 2026-10-03 · %s" % (
                format_amount(fr.env, 600.0, payment.currency_id), lease.name),
        )
        self.assertIn("600", fr.display_name)

    def test_the_names_read_in_english(self):
        """Lus en français puis en anglais dans la même transaction : le nom
        suit la langue du lecteur, il ne reste pas en cache dans la première."""
        lease, term, payment = self._named()
        self.assertTrue(term.with_context(lang="fr_CA").display_name)
        self.assertTrue(payment.with_context(lang="fr_CA").display_name)
        self.assertEqual(
            term.with_context(lang="en_CA").display_name,
            "Rent payable on 2026-10-01 · %s" % lease.name,
        )
        en = payment.with_context(lang="en_CA")
        self.assertEqual(
            en.display_name,
            "Payment of %s received on 2026-10-03 · %s" % (
                format_amount(en.env, 600.0, payment.currency_id), lease.name),
        )

    def test_the_names_are_never_the_technical_ones(self):
        _lease, term, payment = self._named()
        for rec in (term, payment):
            self.assertTrue(rec.display_name)
            self.assertNotIn("bf.rental", rec.display_name)

    def test_the_lessee_reads_the_name_of_her_own_term_at_the_portal(self):
        """🔴 Le locataire lit ses termes et ses versements au portail. Le nom
        ne lui montre que ce qu'il lit déjà : la date, le montant, la référence
        de SON bail. Pas l'immeuble ni le logement, que le portail ne lit pas
        (bf_rental_portal), et rien qui lève une erreur d'accès."""
        if "bf_rental_portal" not in self.env.registry._init_modules:
            self.skipTest("bf_rental_portal non installé : aucun accès portail")
        unit = self.env["bf.property.unit"].create(
            {"name": "4B", "building_id": self.building.id})
        partner = self.env["res.partner"].create(
            {"name": "Camille Portail", "email": "camille@example.invalid"})
        user = self.env["res.users"].create({
            "name": "Camille Portail", "login": "rent_dn_portal",
            "password": "rent_dn_portal_pwd", "partner_id": partner.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
        })
        lease = self._lease(unit_id=unit.id,
                            tenant_ids=[(6, 0, [partner.id])])
        term = self._term(lease=lease, date_due="2026-10-01", amount=1000.0)
        payment = self.env["bf.rental.payment"].create({
            "term_id": term.id, "amount": 1000.0, "date": "2026-10-02",
        })
        self.env.invalidate_all()
        as_tenant = self.env(user=user, context={"lang": "fr_CA"})
        t = as_tenant["bf.rental.term"].search([("id", "=", term.id)])
        p = as_tenant["bf.rental.payment"].search([("id", "=", payment.id)])
        self.assertEqual(t.id, term.id)
        self.assertEqual(p.id, payment.id)
        names = [t.display_name, p.display_name,
                 t.read(["display_name"])[0]["display_name"]]
        self.assertEqual(names[0], "Loyer exigible le 2026-10-01 · %s" % lease.name)
        for name in names:
            self.assertIn(lease.name, name)
            for secret in ("12 Rachel", "4B", "Loyer inc."):
                self.assertNotIn(secret, name)
