"""Ce que le module refuse de dire, et qui se casse si on l'enlève."""
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import RentCase


@tagged("post_install", "-at_install")
class TestRentRefusals(RentCase):

    def test_no_field_suggests_terminating_the_lease(self):
        """🔴 Le refus central du module, gardé par une ABSENCE.

        Aucun champ ne doit suggérer que le locateur peut résilier : l'art. 1971
        dit « peut obtenir », c'est-à-dire demander au tribunal. Il n'y a pas de
        résiliation unilatérale du bail de logement au Québec, et un champ
        nommé `may_terminate` ferait croire le contraire à qui le lirait.
        """
        forbidden = ("may_terminate", "can_terminate", "peut_resilier",
                     "termination_allowed", "eviction_allowed")
        names = " ".join(self.env["bf.rental.lease"]._fields).lower()
        for word in forbidden:
            self.assertNotIn(word, names, f"Un champ « {word} » est apparu.")

    def test_no_field_totals_the_remaining_rent(self):
        """🔴 Art. 1905 : la clause rendant le loyer TOTAL exigible en cas de
        défaut est sans effet. La déchéance du terme est banale ailleurs et
        nulle ici ; un champ « solde du bail » inviterait à la réclamer."""
        forbidden = ("remaining_rent", "total_rent_due", "balance_of_lease",
                     "acceleration")
        names = " ".join(self.env["bf.rental.lease"]._fields).lower()
        for word in forbidden:
            self.assertNotIn(word, names, f"Un champ « {word} » est apparu.")

    def test_the_module_computes_no_interest(self):
        """⚠️ Le taux est celui de l'art. 28 de la Loi sur l'administration
        fiscale, fixé par règlement et révisé trimestriellement. Une donnée
        publiée ailleurs et datée : on l'enregistre, on ne l'invente pas."""
        names = " ".join(self.env["bf.rental.term"]._fields).lower()
        self.assertNotIn("interest", names)

    def test_a_term_cannot_exceed_the_agreed_rent(self):
        """Art. 1905 et 1904 al. 1 : réclamer d'un coup ce qui reste du bail est
        sans effet, et exiger un versement supérieur à un mois est interdit."""
        lease = self._lease(rent=1000.0)
        with self.assertRaises(ValidationError) as caught:
            self._term(lease=lease, amount=12000.0)
        self.assertIn("1905", str(caught.exception))

    def test_a_term_at_the_agreed_rent_passes(self):
        """Le pendant : sans lui, refuser tout terme passerait pour de la
        rigueur."""
        lease = self._lease(rent=1000.0, services_cost=50.0)
        term = self._term(lease=lease, amount=1050.0)
        self.assertTrue(term)

    def test_a_negative_payment_is_refused(self):
        term = self._term()
        with self.assertRaises(ValidationError):
            self.env["bf.rental.payment"].create({
                "term_id": term.id, "amount": -100.0, "date": self.today,
            })

    def test_terms_are_walled_off_between_companies(self):
        other = self.env["res.company"].create({"name": "Ailleurs loyer inc."})
        term = self._term(days_ago=5)
        outsider = self.env["res.users"].create({
            "name": "ailleurs", "login": "qa_outsider_rent",
            "company_id": other.id, "company_ids": [(6, 0, [other.id])],
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_property_core.group_bf_property_manager").id,
            ])],
        })
        visible = self.env["bf.rental.term"].with_user(outsider).search([])
        self.assertNotIn(term, visible)
