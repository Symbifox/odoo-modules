"""Tests du socle copropriété.

Rejoue les vérifications qui ont servi à valider le module, y compris les
défauts trouvés en revue adversariale : archivage ignoré du total, unicité
aveugle à l'archivage, cloisonnement multi-société, et fraction étrangère
sur une partie commune à usage restreint.
"""
from datetime import date, timedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged
from psycopg2 import IntegrityError
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestPropertyCore(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.syndicat"].create(
            {"name": "Syndicat d'essai", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble A", "syndicat_id": cls.syndicat.id}
        )
        cls.u1 = cls.env["bf.property.unit"].create(
            {"name": "101", "building_id": cls.building.id, "quote_part": 400.0}
        )
        cls.u2 = cls.env["bf.property.unit"].create(
            {"name": "102", "building_id": cls.building.id, "quote_part": 350.0}
        )
        cls.u3 = cls.env["bf.property.unit"].create(
            {
                "name": "P-1",
                "building_id": cls.building.id,
                "unit_type": "parking",
                "quote_part": 250.0,
            }
        )
        cls.p1 = cls.env["res.partner"].create(
            {"name": "Copropriétaire Un", "email": "un@example.invalid"}
        )
        cls.p2 = cls.env["res.partner"].create(
            {"name": "Copropriétaire Deux", "email": "deux@example.invalid"}
        )

    # ── Quotes-parts ──

    def test_quote_part_totals_and_state(self):
        self.assertEqual(self.syndicat.quote_part_total, 1000.0)
        self.assertEqual(self.syndicat.quote_part_state, "balanced")
        self.assertEqual(self.syndicat.quote_part_gap, 0.0)
        self.assertEqual(self.u1.quote_part_pct, 40.0)

    def test_quote_part_incomplete(self):
        self.u3.quote_part = 50.0
        self.assertEqual(self.syndicat.quote_part_total, 800.0)
        self.assertEqual(self.syndicat.quote_part_state, "under")
        self.assertEqual(self.syndicat.quote_part_gap, -200.0)

    def test_archived_unit_leaves_the_total(self):
        """Défaut trouvé en revue : `active` manquait aux dépendances."""
        self.u3.active = False
        self.assertEqual(self.syndicat.quote_part_total, 750.0)
        self.assertEqual(self.syndicat.quote_part_state, "under")
        self.assertEqual(self.syndicat.unit_count, 2)

    def test_totals_are_partitioned_between_syndicats(self):
        other = self.env["bf.property.syndicat"].create(
            {"name": "Autre syndicat", "fraction_base": 1000}
        )
        other_building = self.env["bf.property.building"].create(
            {"name": "Immeuble B", "syndicat_id": other.id}
        )
        self.env["bf.property.unit"].create(
            {"name": "301", "building_id": other_building.id, "quote_part": 999.0}
        )
        self.assertEqual(self.syndicat.quote_part_total, 1000.0)
        self.assertEqual(other.quote_part_total, 999.0)

    def test_moving_a_unit_updates_both_syndicats(self):
        other = self.env["bf.property.syndicat"].create(
            {"name": "Autre syndicat", "fraction_base": 1000}
        )
        other_building = self.env["bf.property.building"].create(
            {"name": "Immeuble B", "syndicat_id": other.id}
        )
        self.u1.building_id = other_building
        self.assertEqual(self.u1.syndicat_id, other)
        self.assertEqual(self.syndicat.quote_part_total, 600.0)
        self.assertEqual(other.quote_part_total, 400.0)

    # ── Propriété ──

    def test_indivision_and_history(self):
        Ownership = self.env["bf.property.ownership"]
        former = self.env["res.partner"].create(
            {"name": "Ancien", "email": "ancien@example.invalid"}
        )
        past = Ownership.create(
            {
                "unit_id": self.u1.id,
                "partner_id": former.id,
                "date_start": "2015-01-01",
                "date_end": "2020-06-30",
            }
        )
        Ownership.create(
            {
                "unit_id": self.u1.id,
                "partner_id": self.p1.id,
                "share": 60.0,
                "date_start": "2020-07-01",
            }
        )
        Ownership.create(
            {
                "unit_id": self.u1.id,
                "partner_id": self.p2.id,
                "share": 40.0,
                "date_start": "2020-07-01",
            }
        )
        self.assertEqual(len(self.u1.owner_ids), 2)
        self.assertNotIn(former, self.u1.owner_ids)
        self.assertFalse(past.is_current)

    def test_simultaneous_shares_cannot_exceed_100(self):
        Ownership = self.env["bf.property.ownership"]
        Ownership.create(
            {"unit_id": self.u1.id, "partner_id": self.p1.id, "share": 60.0}
        )
        with self.assertRaises(ValidationError):
            Ownership.create(
                {"unit_id": self.u1.id, "partner_id": self.p2.id, "share": 60.0}
            )

    def test_consecutive_owners_at_100_are_allowed(self):
        Ownership = self.env["bf.property.ownership"]
        Ownership.create(
            {
                "unit_id": self.u1.id,
                "partner_id": self.p1.id,
                "date_start": "2015-01-01",
                "date_end": "2019-12-31",
            }
        )
        Ownership.create(
            {
                "unit_id": self.u1.id,
                "partner_id": self.p2.id,
                "date_start": "2020-01-01",
            }
        )
        self.assertEqual(len(self.u1.owner_ids), 1)

    def test_reversed_dates_rejected(self):
        with self.assertRaises(ValidationError):
            self.env["bf.property.ownership"].create(
                {
                    "unit_id": self.u1.id,
                    "partner_id": self.p1.id,
                    "date_start": "2024-01-01",
                    "date_end": "2023-01-01",
                }
            )

    def test_cron_refreshes_lapsed_ownership(self):
        """Les champs dérivés de la date du jour se périment sans écriture."""
        Ownership = self.env["bf.property.ownership"]
        record = Ownership.create(
            {
                "unit_id": self.u1.id,
                "partner_id": self.p1.id,
                "date_start": "2020-01-01",
                "date_end": date.today() + timedelta(days=5),
            }
        )
        self.assertTrue(record.is_current)
        # On recule l'échéance en SQL : le temps passe sans écriture ORM.
        self.env.cr.execute(
            "UPDATE bf_property_ownership SET date_end = %s WHERE id = %s",
            (date.today() - timedelta(days=1), record.id),
        )
        record.invalidate_recordset()
        self.assertTrue(record.is_current, "l'état périmé doit survivre à l'invalidation")
        Ownership._cron_refresh_current()
        record.invalidate_recordset()
        self.u1.invalidate_recordset()
        self.assertFalse(record.is_current)
        self.assertNotIn(self.p1, self.u1.owner_ids)

    # ── Garde-fous de structure ──

    @mute_logger("odoo.sql_db")
    def test_unit_number_unique_per_building(self):
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.env["bf.property.unit"].create(
                    {"name": "101", "building_id": self.building.id}
                )

    def test_archived_unit_number_can_be_reused(self):
        """Défaut trouvé en revue : la contrainte comptait les archivées.

        L'index partiel vit dans PostgreSQL, pas dans l'ORM : il ne voit que ce
        qui est écrit. L'archivage doit donc être poussé avant la ressaisie.
        Dans l'usage courant c'est acquis, les deux gestes étant deux requêtes.
        Un script d'import qui ferait les deux dans la même transaction sans
        vider le cache se heurterait à l'index.
        """
        self.u2.active = False
        self.u2.flush_recordset()
        reused = self.env["bf.property.unit"].create(
            {"name": "102", "building_id": self.building.id, "quote_part": 10.0}
        )
        self.assertTrue(reused.id)

    @mute_logger("odoo.sql_db")
    def test_negative_quote_part_rejected(self):
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.env["bf.property.unit"].create(
                    {"name": "999", "building_id": self.building.id, "quote_part": -1.0}
                )

    # ── Occupation ──

    def test_occupant_requires_rented_flag(self):
        with self.assertRaises(ValidationError):
            self.u2.write({"occupant_id": self.p1.id})

    def test_unchecking_rented_clears_the_occupant(self):
        """Défaut trouvé en revue : l'opération inverse était refusée."""
        self.u2.write({"is_rented": True, "occupant_id": self.p1.id})
        self.u2.write({"is_rented": False})
        self.assertFalse(self.u2.occupant_id)

    # ── Parties communes ──

    def test_general_common_area_rejects_beneficiaries(self):
        area = self.env["bf.property.common.area"].create(
            {"name": "Hall", "building_id": self.building.id}
        )
        with self.assertRaises(ValidationError):
            area.write({"restricted_unit_ids": [(6, 0, [self.u1.id])]})

    def test_restricted_area_rejects_foreign_units(self):
        """Défaut trouvé en revue : seul le domaine de la vue protégeait."""
        other_building = self.env["bf.property.building"].create(
            {"name": "Immeuble B", "syndicat_id": self.syndicat.id}
        )
        foreign = self.env["bf.property.unit"].create(
            {"name": "201", "building_id": other_building.id}
        )
        area = self.env["bf.property.common.area"].create(
            {
                "name": "Terrasse",
                "building_id": self.building.id,
                "area_type": "restricted",
            }
        )
        with self.assertRaises(ValidationError):
            area.write({"restricted_unit_ids": [(6, 0, [foreign.id])]})

    # ── Cloisonnement ──

    def test_company_propagates_to_personal_data_models(self):
        """Défaut trouvé en revue : ownership n'avait ni société ni règle."""
        ownership = self.env["bf.property.ownership"].create(
            {"unit_id": self.u1.id, "partner_id": self.p1.id}
        )
        area = self.env["bf.property.common.area"].create(
            {"name": "Gym", "building_id": self.building.id}
        )
        self.assertEqual(ownership.company_id, self.syndicat.company_id)
        self.assertEqual(area.company_id, self.syndicat.company_id)
        for model in (
            "bf.property.syndicat",
            "bf.property.building",
            "bf.property.unit",
            "bf.property.ownership",
            "bf.property.common.area",
        ):
            rule = self.env["ir.rule"].search([("model_id.model", "=", model)])
            self.assertTrue(rule, "aucune règle multi-société sur %s" % model)
