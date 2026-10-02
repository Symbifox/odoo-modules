"""Tests du socle copropriété.

Rejoue les vérifications qui ont servi à valider le module, y compris les
défauts trouvés en revue adversariale : archivage ignoré du total, unicité
aveugle à l'archivage, cloisonnement multi-société, et fraction étrangère
sur une partie commune à usage restreint.
"""
from datetime import date, timedelta
from odoo import fields

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged
from psycopg2 import IntegrityError
from odoo.tools import mute_logger

from odoo.addons.bf_property_core.tools import CRON_TIME_UTC, anchor_crons_at_dawn


@tagged("post_install", "-at_install")
class TestPropertyCore(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat d'essai", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble A", "organisation_id": cls.syndicat.id}
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
        other = self.env["bf.property.organisation"].create(
            {"name": "Autre syndicat", "fraction_base": 1000}
        )
        other_building = self.env["bf.property.building"].create(
            {"name": "Immeuble B", "organisation_id": other.id}
        )
        self.env["bf.property.unit"].create(
            {"name": "301", "building_id": other_building.id, "quote_part": 999.0}
        )
        self.assertEqual(self.syndicat.quote_part_total, 1000.0)
        self.assertEqual(other.quote_part_total, 999.0)

    def test_moving_a_unit_updates_both_syndicats(self):
        other = self.env["bf.property.organisation"].create(
            {"name": "Autre syndicat", "fraction_base": 1000}
        )
        other_building = self.env["bf.property.building"].create(
            {"name": "Immeuble B", "organisation_id": other.id}
        )
        self.u1.building_id = other_building
        self.assertEqual(self.u1.organisation_id, other)
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
            {"name": "Immeuble B", "organisation_id": self.syndicat.id}
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
            "bf.property.organisation",
            "bf.property.building",
            "bf.property.unit",
            "bf.property.ownership",
            "bf.property.common.area",
        ):
            rule = self.env["ir.rule"].search([("model_id.model", "=", model)])
            self.assertTrue(rule, "aucune règle multi-société sur %s" % model)

    def test_the_smart_buttons_return_a_coherent_action(self):
        """Un bouton qui rend une action cassée casse à l'écran, pas au test.

        Le QA statique a compté quatorze méthodes `action_*` qu'aucun test
        n'appelait. Une sonde a montré qu'elles tiennent ; ceci les garde.
        """
        for record, method in (
            (self.syndicat, "action_view_units"),
            (self.syndicat, "action_view_buildings"),
            (self.building, "action_view_units"),
        ):
            with self.subTest(method=method):
                action = getattr(record, method)()
                self.assertEqual(action["type"], "ir.actions.act_window")
                model = action["res_model"]
                self.assertIn(model, self.env)
                for leaf in action.get("domain") or []:
                    if isinstance(leaf, (list, tuple)):
                        self.assertIn(leaf[0], self.env[model]._fields)


@tagged("post_install", "-at_install")
class TestOrganisationRegime(TransactionCase):
    """Ce que la neutralisation du socle a rendu possible, et ce qu'elle borne.

    Le modèle porte désormais des immeubles pour un syndicat de copropriété
    comme pour un bailleur. Ce qui n'existe que sous le régime de la
    copropriété divise doit refuser de naître ailleurs, et ce refus n'est pas
    une question de permission mais de sens.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat du régime", "kind": "syndicat", "fraction_base": 1000}
        )
        cls.landlord = cls.env["bf.property.organisation"].create(
            {"name": "Immeubles Locatifs inc.", "kind": "landlord"}
        )

    def test_the_socle_carries_both_natures(self):
        self.assertTrue(self.syndicat.is_syndicat)
        self.assertFalse(self.landlord.is_syndicat)

    def test_a_landlord_holds_buildings_and_units_like_anyone(self):
        """Le socle est neutre : c'est tout l'objet de la manœuvre."""
        building = self.env["bf.property.building"].create(
            {"name": "Le 4000 Saint-Denis", "organisation_id": self.landlord.id}
        )
        unit = self.env["bf.property.unit"].create(
            {"name": "3", "building_id": building.id}
        )
        self.assertEqual(unit.organisation_id, self.landlord)
        self.assertEqual(self.landlord.building_count, 1)
        self.assertEqual(self.landlord.unit_count, 1)

    def test_the_nature_is_stored_so_it_stays_searchable(self):
        """⚠️ `is_syndicat` ne dépend que de la nature, donc d'une écriture.
        Aucun cron ne lui est nécessaire, à la différence des états qui suivent
        le calendrier."""
        found = self.env["bf.property.organisation"].search(
            [("is_syndicat", "=", False)]
        )
        self.assertIn(self.landlord, found)
        self.assertNotIn(self.syndicat, found)
        self.landlord.kind = "syndicat"
        self.assertTrue(self.landlord.is_syndicat)

    def test_an_assembly_refuses_to_exist_at_a_landlord(self):
        """🔴 Une assemblée générale de copropriétaires chez un bailleur est un
        non-sens juridique, pas une question de droits d'accès.

        D'où `ValidationError` : le gestionnaire a bien la permission, c'est
        l'objet qui n'existe pas dans ce régime.
        """
        with self.assertRaises(ValidationError) as caught:
            self.env["bf.property.assembly"].create(
                {
                    "name": "Assemblée impossible",
                    "organisation_id": self.landlord.id,
                    "date": fields.Datetime.now() + timedelta(days=30),
                }
            )
        self.assertIn("copropriété divise", str(caught.exception))
        self.assertIn("Bailleur", str(caught.exception))

    def test_the_same_assembly_is_fine_at_a_syndicat(self):
        assembly = self.env["bf.property.assembly"].create(
            {
                "name": "Assemblée annuelle",
                "organisation_id": self.syndicat.id,
                "date": fields.Datetime.now() + timedelta(days=30),
            }
        )
        self.assertTrue(assembly.exists())

    def test_every_copropriete_object_carries_the_regime_guard(self):
        """⚠️ La garde est héritée d'un socle, jamais recopiée.

        Ce test nomme les modèles attendus plutôt que de compter : un compteur
        resterait vert le jour où l'un d'eux perd son socle et qu'un autre est
        ajouté.

        ⚠️ Il lit l'ordre de résolution, pas `_inherit`. Premier jet : il
        interrogeait `model._inherit`, qui ne rend que la déclaration de la
        DERNIÈRE classe enregistrée pour ce modèle. L'état des charges est
        étendu par le pont de remise sécurisée, dont le `_inherit` ne mentionne
        évidemment pas le régime : le contrôle rapportait un manque qui n'en
        était pas un.
        """
        expected = {
            "bf.property.assembly",
            "bf.property.council.meeting",
            "bf.property.budget",
            "bf.property.fund.call",
            "bf.property.charge.statement",
            "bf.property.attestation",
            "bf.property.disclosure",
            "bf.property.contingency.study",
            "bf.property.maintenance.log",
        }
        for name in expected:
            ancestry = {
                getattr(cls, "_name", None) for cls in type(self.env[name]).__mro__
            }
            self.assertIn(
                "bf.property.syndicat.regime",
                ancestry,
                "%s ne porte pas la garde de régime" % name,
            )


@tagged("post_install", "-at_install")
class TestCronHour(TransactionCase):
    """Les crons de la suite tournent à 05 h 30 UTC.

    Un cron n'a pas de fuseau : posé un soir au Québec, il voyait déjà le
    lendemain, et un loyer dû le jour même passait en retard avant minuit.
    """

    SUITE = ("bf_property", "bf_rental")

    def test_every_installed_cron_of_the_suite_runs_at_dawn_utc(self):
        data = self.env["ir.model.data"].search([("model", "=", "ir.cron")])
        # Un cron horaire, s'il en vient un, n'a pas d'heure fixe à tenir.
        crons = self.env["ir.cron"].browse(
            data.filtered(lambda d: d.module.startswith(self.SUITE)).mapped("res_id")
        ).exists().filtered(lambda c: c.interval_type in ("days", "weeks", "months"))
        self.assertTrue(crons, "au moins le cron du socle")
        late = crons.filtered(lambda c: c.nextcall.time() != CRON_TIME_UTC)
        self.assertFalse(
            late, "crons hors de 05 h 30 UTC : %s" % ", ".join(late.mapped("cron_name"))
        )

    def test_anchoring_moves_a_cron_to_the_next_dawn(self):
        cron = self.env.ref("bf_property_core.cron_property_refresh_current")
        cron.nextcall = fields.Datetime.now() + timedelta(hours=3, minutes=7)
        anchor_crons_at_dawn(self.env, ["bf_property_core.cron_property_refresh_current",
                                        "bf_property_core.absent_de_cette_base"])
        self.assertEqual(cron.nextcall.time(), CRON_TIME_UTC)
        self.assertGreater(cron.nextcall, fields.Datetime.now())
        self.assertLessEqual(cron.nextcall - fields.Datetime.now(), timedelta(days=1))
