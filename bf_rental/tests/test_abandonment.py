"""Le départ sans préavis, et ce que le locateur ne peut PAS faire.

🔴 Ces essais gardent le coin du louage où un module généraliste fait le plus de
dégâts : « le locataire est parti, on vide le logement » est illégal presque à
coup sûr.
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAbandonment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.landlord = cls.env["bf.property.organisation"].create(
            {"name": "Départs inc.", "kind": "landlord"})
        cls.building = cls.env["bf.property.building"].create(
            {"name": "5 Ontario", "organisation_id": cls.landlord.id})
        cls.tenant = cls.env["res.partner"].create({"name": "Sacha Lemay"})
        cls.lease = cls.env["bf.rental.lease"].create({
            "organisation_id": cls.landlord.id,
            "building_id": cls.building.id,
            "tenant_ids": [(6, 0, [cls.tenant.id])],
            "duration_kind": "fixed",
            "date_start": "2026-07-01", "date_end": "2027-06-30",
            "rent": 1000.0,
        })
        cls.day = fields.Date.to_date("2026-09-01")

    def _abandon(self, **kw):
        vals = {
            "lease_id": self.lease.id,
            "date_noticed": self.day,
            "kind": "deguerpissement",
        }
        vals.update(kw)
        return self.env["bf.rental.abandonment"].create(vals)

    # ── Les deux cas de l'art. 1975 ──

    def test_deguerpissement_resiliates_by_operation_of_law(self):
        """Al. 1 : « Le bail est résilié DE PLEIN DROIT »."""
        rec = self._abandon()
        self.assertTrue(rec.lease_resiliated_by_operation_of_law)

    def test_an_unfit_dwelling_does_not_resiliate_automatically(self):
        """🔴 Al. 2 : « il PEUT être résilié ». Facultatif, pas automatique.
        Les confondre priverait quelqu'un de son bail sans que la loi le fasse."""
        rec = self._abandon(kind="unfit")
        self.assertFalse(rec.lease_resiliated_by_operation_of_law)

    # ── La condition qui décide de tout ──

    def test_calling_it_deguerpissement_with_effects_left_is_refused(self):
        """🔴 « en emportant ses effets mobiliers » n'est pas décoratif. Des
        effets laissés font sortir de l'al. 1, et le bail n'est alors PAS résilié
        de plein droit."""
        with self.assertRaises(ValidationError) as caught:
            self._abandon(effects_left=True)
        self.assertIn("1978", str(caught.exception))

    def test_an_unfit_departure_may_leave_effects(self):
        """Le pendant : l'al. 2 ne pose pas cette condition. Refuser ici serait
        inventer une règle."""
        rec = self._abandon(kind="unfit", effects_left=True)
        self.assertTrue(rec)
        self.assertFalse(rec.lease_resiliated_by_operation_of_law)

    # ── Les effets laissés : 90 jours, puis 90 jours d'avis ──

    def test_effects_are_forgotten_only_after_ninety_days(self):
        rec = self._abandon(kind="unfit", effects_left=True)
        self.assertEqual(rec.forgotten_from, self.day + relativedelta(days=90))

    def test_no_disposal_date_without_a_notice(self):
        """⚠️ Ce n'est pas « on ne sait pas » : l'avis est une CONDITION, et
        elle n'est pas remplie."""
        rec = self._abandon(kind="unfit", effects_left=True)
        self.assertFalse(rec.may_not_dispose_before)

    def test_the_notice_adds_its_own_ninety_days(self):
        """Art. 944 : « après avoir donné un avis de la même durée »."""
        rec = self._abandon(
            kind="unfit", effects_left=True,
            disposal_notice_date=self.day + relativedelta(days=60))
        # l'avis se termine après les 90 jours de l'oubli
        self.assertEqual(rec.may_not_dispose_before,
                         self.day + relativedelta(days=150))

    def test_the_two_delays_may_run_together(self):
        """⚠️ Un avis donné le jour du constat court en même temps que les 90
        jours de l'oubli : la date retenue est la plus tardive des deux, pas
        leur somme. Inventer 180 jours serait aussi faux qu'en inventer 90."""
        rec = self._abandon(kind="unfit", effects_left=True,
                            disposal_notice_date=self.day)
        self.assertEqual(rec.may_not_dispose_before,
                         self.day + relativedelta(days=90))

    def test_disposing_before_the_date_is_refused(self):
        """🔴 Le refus est ferme parce que l'erreur est irréversible : on rend
        un logement, on ne rend pas des affaires vendues."""
        with self.assertRaises(ValidationError) as caught:
            self._abandon(
                kind="unfit", effects_left=True,
                disposal_notice_date=self.day,
                disposed_on=self.day + relativedelta(days=30))
        self.assertIn("944", str(caught.exception))

    def test_disposing_without_any_notice_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self._abandon(kind="unfit", effects_left=True,
                          disposed_on=self.day + relativedelta(days=200))
        self.assertIn("avis", str(caught.exception))

    def test_disposing_after_the_date_passes(self):
        """Le pendant : sans lui, refuser toute disposition passerait pour de
        la rigueur."""
        rec = self._abandon(
            kind="unfit", effects_left=True,
            disposal_notice_date=self.day,
            disposed_on=self.day + relativedelta(days=120),
            disposal_method="auction")
        self.assertTrue(rec)

    def test_disposal_methods_follow_the_order_of_article_945(self):
        """⚠️ Vendre, à défaut donner, et seulement à défaut, disposer à son
        gré. « Jeter » n'est pas une option nommée par le texte, et le module ne
        l'offre pas comme un choix ordinaire."""
        options = dict(
            self.env["bf.rental.abandonment"]._fields["disposal_method"].selection
        )
        self.assertIn("auction", options)
        self.assertIn("charity", options)
        labels = " ".join(options.values()).lower()
        self.assertNotIn("jeter", labels)
        self.assertNotIn("détruire", labels)
