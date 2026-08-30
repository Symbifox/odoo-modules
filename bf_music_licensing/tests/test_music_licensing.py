# Part of bf_music_licensing. Voir LICENSE.
from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMusicTariffSeed(TransactionCase):
    """Le référentiel semé est une donnée livrée : il se contrôle comme du code."""

    def test_seed_loads(self):
        seeded = self.env["bf.music.tariff"].search([])
        self.assertGreaterEqual(len(seeded), 7)
        self.assertEqual(
            sorted(set(seeded.mapped("code"))), ["15.A", "15.B", "3.B"]
        )

    def test_seed_is_entirely_pending(self):
        """Aucun tarif de musique d'ambiance n'est homologué au 2026-08-30."""
        certified = self.env["bf.music.tariff"].search([("state", "=", "certified")])
        self.assertFalse(certified)

    def test_seed_rates_relevees_carry_a_source(self):
        """Un taux marqué relevé porte sa source et un montant. Sinon il ment."""
        for t in self.env["bf.music.tariff"].search([("rate_confirmed", "=", True)]):
            self.assertTrue(t.source_url, f"{t.name} sans source")
            self.assertTrue(
                t.rate_proposed or t.rate_base_proposed,
                f"{t.name} relevé mais sans montant",
            )

    def test_seed_unconfirmed_rates_are_empty(self):
        """Un taux non relevé ne doit surtout pas porter de chiffre inventé."""
        for t in self.env["bf.music.tariff"].search([("rate_confirmed", "=", False)]):
            self.assertFalse(t.rate_proposed, f"{t.name} porte un taux non relevé")
            self.assertFalse(t.rate_base_proposed)

    def test_seed_socan_15a_2025(self):
        t = self.env.ref("bf_music_licensing.tariff_socan_15a_2025")
        self.assertAlmostEqual(t.rate_proposed, 2.32)
        self.assertAlmostEqual(t.minimum_proposed, 177.99)
        self.assertEqual(t.basis, "area_sqm")
        self.assertTrue(t.seasonal_half)

    def test_seed_socan_15b_is_first_line_plus_extras(self):
        t = self.env.ref("bf_music_licensing.tariff_socan_15b_2025")
        self.assertAlmostEqual(t.rate_base_proposed, 177.99)
        self.assertAlmostEqual(t.rate_proposed, 3.94)
        self.assertEqual(t.basis, "line")

    def test_seed_resound_is_per_day(self):
        """Ré:Sonne compte la superficie PAR JOUR : l'assiette n'est pas celle de SOCAN."""
        t = self.env.ref("bf_music_licensing.tariff_resound_3b_2023")
        self.assertEqual(t.basis, "area_sqm_day")
        self.assertAlmostEqual(t.rate_proposed, 0.00965)
        self.assertAlmostEqual(t.minimum_proposed, 140.93)

    def test_seed_has_no_overlapping_period(self):
        """Le contrôle de chevauchement doit passer sur la donnée livrée elle-même."""
        self.env["bf.music.tariff"].search([])._check_no_overlap()


@tagged("post_install", "-at_install")
class TestMusicLicensing(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        # Le référentiel semé couvre les mêmes périodes que les tarifs d'essai :
        # le laisser actif ferait entrer ses lignes dans chaque calcul mesuré ici.
        # Sa cohérence est contrôlée par TestMusicTariffSeed.
        cls.env["bf.music.tariff"].search([]).write({"active": False})
        cls.partner = cls.env["res.partner"].create({"name": "Clinique Dupuis"})
        cls.tariff = cls.env["bf.music.tariff"].create({
            "society": "socan",
            "code": "TEST.A",
            "label": "Musique d'ambiance",
            "usage": "background",
            "date_start": date(2025, 1, 1),
            "date_end": date(2027, 12, 31),
            "basis": "area_sqm",
            "state": "proposed",
            "rate_proposed": 2.32,
        })
        cls.establishment = cls.env["bf.music.establishment"].create({
            "name": "Clinique Dupuis - Longueuil",
            "partner_id": cls.partner.id,
            "area_value": 250.0,
            "area_uom": "sqm",
            "usage_background": True,
            "music_source": "radio",
        })

    def _licence(self, year=2025):
        return self.env["bf.music.licence"].create({
            "establishment_id": self.establishment.id,
            "date_start": date(year, 1, 1),
            "date_end": date(year, 12, 31),
            "due_date": date(year, 12, 31),
        })

    # ------------------------------------------------------------------
    # Le référentiel
    # ------------------------------------------------------------------

    def test_periods_cannot_overlap(self):
        """Deux lignes qui se chevauchent feraient compter deux fois la même année."""
        with self.assertRaises(ValidationError):
            self.env["bf.music.tariff"].create({
                "society": "socan",
                "code": "TEST.A",
                "label": "Doublon",
                "usage": "background",
                "date_start": date(2026, 1, 1),
                "date_end": date(2028, 12, 31),
                "basis": "area_sqm",
            })

    def test_certified_requires_a_date(self):
        """Sans date d'homologation, on ne sait pas d'où court le rajustement."""
        with self.assertRaises(ValidationError):
            self.tariff.write({"state": "certified"})

    def test_period_end_after_start(self):
        with self.assertRaises(ValidationError):
            self.tariff.write({"date_end": date(2024, 1, 1)})

    def test_rate_in_force_follows_state(self):
        self.assertEqual(self.tariff.rate_in_force, 2.32)
        self.tariff.write({"rate_certified": 2.50})
        self.tariff.action_mark_certified()
        self.assertEqual(self.tariff.rate_in_force, 2.50)
        self.assertTrue(self.tariff.certification_date)

    # ------------------------------------------------------------------
    # L'assiette
    # ------------------------------------------------------------------

    def test_area_converts_both_ways(self):
        self.assertAlmostEqual(self.establishment.area_sqft, 2690.98, places=1)
        imperial = self.env["bf.music.establishment"].create({
            "name": "Boutique",
            "partner_id": self.partner.id,
            "area_value": 1000.0,
            "area_uom": "sqft",
        })
        self.assertAlmostEqual(imperial.area_sqm, 92.90, places=1)

    def test_consumer_streaming_raises_a_finding(self):
        """La provenance grand public est le constat le moins cher du diagnostic."""
        self.assertFalse(self.establishment.source_warning)
        self.establishment.music_source = "consumer_streaming"
        self.assertIn("personnel", self.establishment.source_warning)

    # ------------------------------------------------------------------
    # Le calcul
    # ------------------------------------------------------------------

    def test_line_takes_its_base_from_the_establishment(self):
        licence = self._licence()
        licence.action_generate_lines()
        line = licence.line_ids
        self.assertEqual(len(line), 1)
        self.assertEqual(line.tariff_id, self.tariff)
        self.assertAlmostEqual(line.base_quantity, 250.0)
        self.assertAlmostEqual(line.amount_proposed, 580.0)

    def test_minimum_floors_the_amount(self):
        self.tariff.minimum_proposed = 900.0
        licence = self._licence()
        licence.action_generate_lines()
        self.assertAlmostEqual(licence.line_ids.amount_proposed, 900.0)

    def test_paid_amount_takes_over_from_the_tariff(self):
        licence = self._licence()
        licence.action_generate_lines()
        licence.line_ids.amount_paid = 610.0
        self.assertAlmostEqual(licence.amount_reference, 610.0)
        self.assertAlmostEqual(licence.amount_at_risk, 610.0)

    def test_at_risk_only_while_uncertified(self):
        licence = self._licence()
        licence.action_generate_lines()
        licence.line_ids.amount_paid = 580.0
        self.assertTrue(licence.has_uncertified)
        self.assertAlmostEqual(licence.amount_at_risk, 580.0)
        self.assertAlmostEqual(licence.adjustment_total, 0.0)

        self.tariff.write({"rate_certified": 2.50})
        self.tariff.action_mark_certified()
        licence.invalidate_recordset()
        self.assertFalse(licence.has_uncertified)
        self.assertAlmostEqual(licence.amount_at_risk, 0.0)
        # 250 m² x 2,50 $ = 625 $, contre 580 $ versés.
        self.assertAlmostEqual(licence.adjustment_total, 45.0)

    def test_line_refuses_a_tariff_outside_the_period(self):
        licence = self._licence(year=2019)
        with self.assertRaises(ValidationError):
            self.env["bf.music.licence.line"].create({
                "licence_id": licence.id,
                "tariff_id": self.tariff.id,
            })

    def test_generate_lines_skips_unused_usages(self):
        """L'attente téléphonique n'est pas cochée : son tarif ne doit pas entrer."""
        self.env["bf.music.tariff"].create({
            "society": "socan",
            "code": "TEST.B",
            "label": "Attente téléphonique",
            "usage": "on_hold",
            "date_start": date(2025, 1, 1),
            "date_end": date(2027, 12, 31),
            "basis": "line",
            "rate_proposed": 100.0,
        })
        licence = self._licence()
        licence.action_generate_lines()
        self.assertEqual(licence.line_ids.tariff_id, self.tariff)

        self.establishment.write({"usage_on_hold": True, "on_hold_lines": 3})
        licence.action_generate_lines()
        self.assertEqual(len(licence.line_ids), 2)
        on_hold = licence.line_ids.filtered(lambda l: l.tariff_id.code == "TEST.B")
        self.assertAlmostEqual(on_hold.base_quantity, 3.0)
        self.assertAlmostEqual(on_hold.amount_proposed, 300.0)

    def test_generate_lines_is_idempotent(self):
        licence = self._licence()
        licence.action_generate_lines()
        licence.action_generate_lines()
        self.assertEqual(len(licence.line_ids), 1)

    def test_first_line_is_not_multiplied(self):
        """« 177,99 $ pour une ligne, plus 3,94 $ par ligne de plus » : la
        première ne se multiplie pas."""
        self.establishment.write({"usage_on_hold": True, "on_hold_lines": 4})
        tariff = self.env["bf.music.tariff"].create({
            "society": "socan", "code": "TEST.HOLD", "label": "Attente",
            "usage": "on_hold",
            "date_start": date(2025, 1, 1), "date_end": date(2027, 12, 31),
            "basis": "line", "rate_base_proposed": 177.99, "rate_proposed": 3.94,
        })
        licence = self._licence()
        licence.action_generate_lines()
        line = licence.line_ids.filtered(lambda l: l.tariff_id == tariff)
        # 177,99 + 3 x 3,94 = 189,81, et non 4 x 177,99.
        self.assertAlmostEqual(line.amount_proposed, 189.81, places=2)

    def test_single_line_pays_only_the_base(self):
        self.establishment.write({"usage_on_hold": True, "on_hold_lines": 1})
        self.env["bf.music.tariff"].create({
            "society": "socan", "code": "TEST.HOLD1", "label": "Attente",
            "usage": "on_hold",
            "date_start": date(2025, 1, 1), "date_end": date(2027, 12, 31),
            "basis": "line", "rate_base_proposed": 177.99, "rate_proposed": 3.94,
        })
        licence = self._licence()
        licence.action_generate_lines()
        line = licence.line_ids.filtered(lambda l: l.tariff_id.code == "TEST.HOLD1")
        self.assertAlmostEqual(line.amount_proposed, 177.99, places=2)

    def test_area_per_day_basis(self):
        """Ré:Sonne multiplie la superficie par les jours d'exploitation."""
        self.establishment.write({"days_of_operation": 300})
        tariff = self.env["bf.music.tariff"].create({
            "society": "resound", "code": "TEST.3B", "label": "Ambiance",
            "usage": "background",
            "date_start": date(2025, 1, 1), "date_end": date(2026, 12, 31),
            "basis": "area_sqm_day", "rate_proposed": 0.00965,
            "minimum_proposed": 140.93,
        })
        licence = self._licence()
        licence.action_generate_lines()
        line = licence.line_ids.filtered(lambda l: l.tariff_id == tariff)
        self.assertAlmostEqual(line.base_quantity, 75000.0)   # 250 m2 x 300 j
        self.assertAlmostEqual(line.amount_proposed, 723.75, places=2)

    def test_seasonal_halves_only_where_the_tariff_says_so(self):
        self.tariff.write({"seasonal_half": True})
        licence = self._licence()
        licence.action_generate_lines()
        line = licence.line_ids
        self.assertAlmostEqual(line.amount_proposed, 580.0)
        self.establishment.seasonal = True
        self.assertAlmostEqual(line.amount_proposed, 290.0)
        # Un tarif sans demi-tarif saisonnier ne bouge pas.
        self.tariff.seasonal_half = False
        self.assertAlmostEqual(line.amount_proposed, 580.0)

    def test_minimum_applies_after_the_seasonal_half(self):
        self.tariff.write({"seasonal_half": True, "minimum_proposed": 400.0})
        self.establishment.seasonal = True
        licence = self._licence()
        licence.action_generate_lines()
        # 580 / 2 = 290, relevé au minimum de 400.
        self.assertAlmostEqual(licence.line_ids.amount_proposed, 400.0)

    def test_build_history_uses_the_january_deadline(self):
        created = self.establishment.action_build_history(
            year_from=2025, year_to=2025,
        )
        self.assertEqual(created.due_date, date(2025, 1, 31))

    # ------------------------------------------------------------------
    # L'échéance
    # ------------------------------------------------------------------

    def test_status_follows_the_due_date(self):
        licence = self._licence()
        licence.due_date = date(2020, 1, 1)
        self.assertEqual(licence.status, "overdue")
        licence.action_complete()
        self.assertEqual(licence.status, "completed")
        licence.action_reset()
        self.assertEqual(licence.status, "overdue")
        self.assertFalse(licence.reminder_sent)

    def test_reminder_posts_an_activity_once(self):
        licence = self._licence()
        licence.due_date = date(2020, 1, 1)
        self.env["bf.music.licence"]._cron_check_licence_deadlines()
        self.assertTrue(licence.reminder_sent)
        first = len(licence.activity_ids)
        self.assertTrue(first)
        self.env["bf.music.licence"]._cron_check_licence_deadlines()
        self.assertEqual(len(licence.activity_ids), first)

    # ------------------------------------------------------------------
    # L'exposition
    # ------------------------------------------------------------------

    def test_build_history_creates_one_period_per_year(self):
        created = self.establishment.action_build_history(
            year_from=2025, year_to=2026,
        )
        self.assertEqual(len(created), 2)
        self.assertEqual(
            sorted(lic.date_start.year for lic in created), [2025, 2026]
        )
        # 250 m² x 2,32 $ sur deux ans, tout sous tarif proposé.
        self.assertAlmostEqual(self.establishment.amount_at_risk, 1160.0)
        self.assertEqual(self.establishment.uncertified_period_count, 2)

    def test_build_history_does_not_duplicate(self):
        self.establishment.action_build_history(year_from=2025, year_to=2025)
        again = self.establishment.action_build_history(year_from=2025, year_to=2026)
        self.assertEqual(len(again), 1)
        self.assertEqual(self.establishment.licence_count, 2)
