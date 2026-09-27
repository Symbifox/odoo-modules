"""Ce qu'un bail enregistre, et ce que le module calcule depuis le formulaire.

Les refus, eux, sont dans `test_refusals.py` : ils tiennent ce que le module ne
fait PAS, et ce sont eux qui se cassent le jour où quelqu'un « simplifie ».
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRentalLease(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.landlord = cls.env["bf.property.organisation"].create({
            "name": "Immeubles Beauport",
            "kind": "landlord",
        })
        cls.building = cls.env["bf.property.building"].create({
            "name": "1200 Cartier",
            "organisation_id": cls.landlord.id,
        })
        cls.tenant = cls.env["res.partner"].create({"name": "Locataire Test"})

    def _lease(self, **kw):
        vals = {
            "name": "BAIL-001",
            "organisation_id": self.landlord.id,
            "building_id": self.building.id,
            "tenant_ids": [(6, 0, [self.tenant.id])],
            "duration_kind": "fixed",
            "date_start": "2026-07-01",
            "date_end": "2027-06-30",
            "rent": 1200.0,
        }
        vals.update(kw)
        return self.env["bf.rental.lease"].create(vals)

    def test_a_lease_holds_what_was_agreed(self):
        lease = self._lease(services_cost=150.0)
        self.assertEqual(lease.rent_total, 1350.0)
        self.assertEqual(lease.organisation_id, self.landlord)
        self.assertIn(self.tenant, lease.tenant_ids)

    def test_the_total_is_rent_plus_services(self):
        """Le formulaire demande les trois montants, pas deux : c'est le total
        qui se compare d'une année à l'autre."""
        lease = self._lease(rent=1000.0, services_cost=0.0)
        self.assertEqual(lease.rent_total, 1000.0)
        lease.services_cost = 75.0
        self.assertEqual(lease.rent_total, 1075.0)

    # ── La lettre de section n'est qu'un rendu ──

    def test_the_common_forms_put_the_restriction_at_f(self):
        for kind in ("student", "mobile", "coop", "general"):
            lease = self._lease(form_kind=kind)
            self.assertEqual(lease.restriction_section, "F", kind)

    def test_the_verbal_lease_shifts_everything_by_one(self):
        """🔴 L'écrit du bail verbal n'a pas de section Durée : le loyer est en
        C, la restriction en D et l'avis en E. Un module qui coderait « F »
        écrirait au locataire de regarder une section qui parle d'autre chose."""
        lease = self._lease(form_kind="verbal")
        self.assertEqual(lease.restriction_section, "D")
        self.assertEqual(lease.notice_section, "E")

    def test_the_common_forms_put_the_notice_at_g(self):
        for kind in ("student", "mobile", "coop", "general"):
            lease = self._lease(form_kind=kind)
            self.assertEqual(lease.notice_section, "G", kind)

    # ── Durée ──

    def test_an_indeterminate_lease_has_no_term(self):
        lease = self._lease(duration_kind="indeterminate", date_end=False)
        self.assertFalse(lease.date_end)

    # ── Le chèque postdaté ──

    def test_postdated_cheques_are_off_by_default(self):
        """⚠️ Le silence n'est pas un consentement. La case du formulaire se
        remplit, elle ne se présume pas."""
        lease = self._lease()
        self.assertFalse(lease.postdated_cheques_accepted)

    def test_postdated_cheques_can_be_consented_to(self):
        """Le locateur ne peut pas les EXIGER (art. 1904 al. 2) ; le locataire
        peut y consentir, et le formulaire officiel porte la case avec ses
        initiales. Refuser de l'enregistrer refuserait ce que le formulaire
        obligatoire prévoit."""
        lease = self._lease(postdated_cheques_accepted=True)
        self.assertTrue(lease.postdated_cheques_accepted)
