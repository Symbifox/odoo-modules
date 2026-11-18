# -*- coding: utf-8 -*-
import os
import re
from datetime import datetime
from unittest.mock import patch

from lxml import etree
from psycopg2.errors import ForeignKeyViolation, UniqueViolation

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestPlanogram(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        env.user.groups_id = [
            Command.link(env.ref("bf_planogram.group_planogram_manager").id)]
        cls.Fixture = env["bf.planogram.fixture"]
        cls.Shelf = env["bf.planogram.shelf"]
        cls.Position = env["bf.planogram.position"]
        cls.Product = env["product.product"]

        def product(name, width=0.0, height=0.0, depth=0.0, **kw):
            return cls.Product.create(dict({
                "name": name, "type": "consu",
                "bf_planogram_unit_width": width,
                "bf_planogram_unit_height": height,
                "bf_planogram_unit_depth": depth,
            }, **kw))

        cls.product = staticmethod(product)
        # 10 cm wide, 25 high: four of them take 40 cm of a 100 cm shelf.
        cls.can = product("Canned beans", width=10.0, height=12.0, depth=8.0)
        cls.box = product("Cereal box", width=20.0, height=30.0, depth=8.0)
        # Measured by nobody: the trap this module refuses to fall into quietly.
        cls.mystery = product("Unmeasured thing")

    # ── helpers ──────────────────────────────────────────────────────────────────────
    def fixture(self, **kw):
        return self.Fixture.create(dict({
            "name": "Aisle 3", "kind": "gondola",
            "width": 100.0, "height": 180.0, "depth": 50.0,
        }, **kw))

    def shelf(self, fixture, height=100.0, **kw):
        return self.Shelf.create(dict({
            "fixture_id": fixture.id, "height_from_floor": height,
        }, **kw))

    def _source_du_module(self, rel):
        """Le fichier tel qu'il part en production : les formes n° 9 et n° 10 portent sur
        du JavaScript, qu'aucun essai Python n'exécute. Lu sur le disque du module, pas
        dans un paquet d'actifs qui pourrait servir une version en cache."""
        racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open("%s/%s" % (racine, rel), encoding="utf-8") as fh:
            return fh.read()

    def position(self, shelf, product, **kw):
        return self.Position.create(dict({
            "shelf_id": shelf.id, "product_id": product.id,
        }, **kw))

    # ── the three numbers that make a capacity ───────────────────────────────────────
    def test_capacity_is_facings_times_deep_times_stacked(self):
        """The one number the rest of the suite waits for: days of supply at the shelf is
        capacity over daily velocity, and neither exists before a position does."""
        shelf = self.shelf(self.fixture())
        position = self.position(shelf, self.can, facings=3, deep=4, stacked=2)
        self.assertEqual(position.capacity, 24)
        position.facings = 1
        self.assertEqual(position.capacity, 8, "le calcul suit la façade retirée")

    def test_the_width_taken_is_read_from_the_product_not_typed(self):
        shelf = self.shelf(self.fixture())
        position = self.position(shelf, self.can, facings=4)
        self.assertAlmostEqual(position.used_width, 40.0, places=1)
        self.assertTrue(position.measured)
        self.can.bf_planogram_unit_width = 12.0
        self.assertAlmostEqual(position.used_width, 48.0, places=1,
                               msg="une dimension corrigée sur le produit bouge la position")

    def test_a_product_without_a_width_is_counted_not_assumed_to_fit(self):
        """🔴 The trap named in of the conception: a product with no width takes zero, and a
        shelf full of them would read as empty. Nothing is silently treated as fitting."""
        shelf = self.shelf(self.fixture())
        position = self.position(shelf, self.mystery, facings=5)
        self.assertFalse(position.measured)
        self.assertAlmostEqual(position.used_width, 0.0, places=1)
        self.assertEqual(shelf.unmeasured_count, 1)
        self.assertEqual(shelf.fixture_id.unmeasured_count, 1)
        self.assertEqual(position.capacity, 5, "la capacité, elle, reste comptée")

    # ── what a shelf and a fixture add up to ─────────────────────────────────────────
    def test_a_shelf_adds_up_what_it_holds(self):
        fixture = self.fixture()
        shelf = self.shelf(fixture)
        self.assertAlmostEqual(shelf.usable_width, 100.0, places=1,
                               msg="la largeur utile par défaut est celle du meuble")
        self.position(shelf, self.can, facings=4)       # 40 cm
        self.position(shelf, self.box, facings=2)       # 40 cm
        self.assertAlmostEqual(shelf.used_width, 80.0, places=1)
        self.assertAlmostEqual(shelf.free_width, 20.0, places=1)
        self.assertAlmostEqual(shelf.fill_pct, 80.0, places=1)
        self.assertFalse(shelf.overflow)

    def test_a_shelf_that_holds_more_than_it_has_is_counted_not_refused(self):
        """A planogram is built by moving things around. An editor that refused the
        intermediate state would force the aisle to be emptied before it can be filled."""
        shelf = self.shelf(self.fixture())
        self.position(shelf, self.box, facings=6)       # 120 cm on a 100 cm shelf
        self.assertTrue(shelf.overflow)
        self.assertAlmostEqual(shelf.free_width, -20.0, places=1)
        self.assertTrue(shelf.fixture_id.overflow)

    def test_the_fixture_adds_up_its_shelves(self):
        fixture = self.fixture()
        low = self.shelf(fixture, height=40.0)
        high = self.shelf(fixture, height=120.0, usable_width=60.0)
        self.position(low, self.can, facings=3, deep=2)      # 30 cm, 6 units
        self.position(high, self.box, facings=2, stacked=3)  # 40 cm, 6 units
        self.assertEqual(fixture.shelf_count, 2)
        self.assertEqual(fixture.position_count, 2)
        self.assertEqual(fixture.product_count, 2)
        self.assertEqual(fixture.capacity, 12)
        self.assertAlmostEqual(fixture.linear_width, 160.0, places=1,
                               msg="le linéaire est la somme des largeurs UTILES, "
                                   "pas la largeur du meuble")
        self.assertAlmostEqual(fixture.used_width, 70.0, places=1)
        self.assertAlmostEqual(fixture.free_width, 90.0, places=1)

    def test_two_positions_of_the_same_product_are_legitimate(self):
        """The milk at the back and an impulse bin at the till: keeps this open on
        purpose. Nothing here refuses it, and lot 4 is where the split rule gets decided."""
        fixture = self.fixture()
        back = self.shelf(fixture, height=40.0)
        till = self.shelf(fixture, height=120.0)
        self.position(back, self.can, facings=4)
        self.position(till, self.can, facings=1)
        self.assertEqual(fixture.position_count, 2)
        self.assertEqual(fixture.product_count, 1)
        self.assertEqual(fixture.capacity, 5)

    # ── the searches a manager actually runs ─────────────────────────────────────────
    def test_overflowing_fixtures_can_be_searched_for(self):
        """`overflow` is computed on the fixture, and the first filter anyone clicks. Without
        its search method the filter would raise instead of answering."""
        calm = self.fixture(name="Calm")
        full = self.fixture(name="Full")
        self.position(self.shelf(calm), self.can, facings=2)
        self.position(self.shelf(full), self.box, facings=9)
        found = self.Fixture.search([("overflow", "=", True)])
        self.assertIn(full, found)
        self.assertNotIn(calm, found)
        self.assertIn(calm, self.Fixture.search([("overflow", "=", False)]))

    def test_products_not_measured_for_the_shelf_can_be_searched_for(self):
        templates = self.env["product.template"]
        measured = templates.search([("bf_planogram_measured", "=", True)])
        self.assertIn(self.can.product_tmpl_id, measured)
        self.assertNotIn(self.mystery.product_tmpl_id, measured)
        self.assertIn(self.mystery.product_tmpl_id,
                      templates.search([("bf_planogram_measured", "=", False)]))

    def test_the_count_of_unmeasured_positions_answers_an_ordinary_domain(self):
        """🔴 `> 0` répondait juste, mais `>= 0`, `= 1` et `> 1` levaient une
        `UserError`. Un domaine hérité d'une action parente transformait la liste en page
        d'erreur. Les quatre opérateurs, et la RÉPONSE, pas seulement l'absence de levée."""
        clean = self.fixture(name="Clean")
        one = self.fixture(name="One")
        two = self.fixture(name="Two")
        self.position(self.shelf(clean), self.can)
        one_shelf = self.shelf(one)
        self.position(one_shelf, self.mystery)
        self.position(one_shelf, self.can)
        two_shelf = self.shelf(two)
        self.position(two_shelf, self.mystery)
        self.position(two_shelf, self.product("Another mystery"))
        self.assertEqual(
            [clean.unmeasured_count, one.unmeasured_count, two.unmeasured_count], [0, 1, 2])
        ours = (clean + one + two).ids

        def found(operator, value):
            return set(self.Fixture.search(
                [("id", "in", ours), ("unmeasured_count", operator, value)]).ids)

        self.assertEqual(found(">", 0), set((one + two).ids))
        self.assertEqual(found(">=", 0), set(ours), "trivialement vrai, et non une erreur")
        self.assertEqual(found("=", 1), set(one.ids))
        self.assertEqual(found(">", 1), set(two.ids))
        self.assertEqual(found("=", 0), set(clean.ids))
        self.assertEqual(found("!=", 0), set((one + two).ids))
        self.assertEqual(found("<", 2), set((clean + one).ids))
        self.assertEqual(found("<=", 1), set((clean + one).ids))
        with self.assertRaises(UserError):
            self.Fixture.search([("unmeasured_count", "like", "2")])

    def test_the_count_of_positions_on_a_product_follows_what_is_added_after_it_is_read(self):
        """🔴 le compteur n'avait aucun `@api.depends`, donc il n'était jamais
        invalidé dans la transaction. L'essai d'avant créait les positions AVANT la première
        lecture : il ne mesurait pas l'invalidation. Et le bouton « Voir les positions » est
        invisible quand le compteur vaut zéro, donc il restait caché."""
        template = self.box.product_tmpl_id
        self.assertEqual(template.bf_planogram_position_count, 0, "lu AVANT, et à zéro")
        self.position(self.shelf(self.fixture()), self.box, facings=2)
        self.assertEqual(template.bf_planogram_position_count, 1,
                         "le compteur suit la position ajoutée dans la même transaction")

    def test_the_product_says_how_many_shelves_it_sits_on(self):
        self.position(self.shelf(self.fixture()), self.can, facings=2)
        self.position(self.shelf(self.fixture(name="Another")), self.can)
        self.assertEqual(self.can.product_tmpl_id.bf_planogram_position_count, 2)
        self.assertEqual(self.box.product_tmpl_id.bf_planogram_position_count, 0)
        action = self.can.product_tmpl_id.action_bf_planogram_positions()
        self.assertEqual(action["res_model"], "bf.planogram.position")
        self.assertEqual(len(self.Position.search(action["domain"])), 2)

    # ── what is refused, and why ─────────────────────────────────────────────────────
    def test_a_fixture_has_three_positive_dimensions(self):
        for field in ("width", "height", "depth"):
            with self.assertRaises(ValidationError, msg=field):
                self.fixture(**{field: 0.0})

    def test_a_shelf_does_not_hang_above_its_fixture(self):
        """A shelf at 200 cm in a fixture 180 high is a typo, and it would draw outside the
        frame. The WIDTH is not checked the same way: a shelf narrower than its fixture is
        ordinary, and a wider one is a measurement to fix, not a drawing to refuse."""
        fixture = self.fixture()
        with self.assertRaises(ValidationError):
            self.shelf(fixture, height=200.0)
        wide = self.shelf(fixture, usable_width=140.0)
        self.assertAlmostEqual(wide.usable_width, 140.0, places=1)

    def test_a_shelf_is_tilted_between_zero_and_forty_five_degrees(self):
        fixture = self.fixture()
        with self.assertRaises(ValidationError):
            self.shelf(fixture, tilt=60.0)
        with self.assertRaises(ValidationError):
            self.shelf(fixture, tilt=-5.0)

    def test_a_position_holds_at_least_one_of_each(self):
        shelf = self.shelf(self.fixture())
        for field in ("facings", "deep", "stacked"):
            with self.assertRaises(ValidationError, msg=field):
                self.position(shelf, self.can, **{field: 0})

    def test_a_dimension_in_metres_is_refused_as_a_typo(self):
        """The fields are centimetres. 1.8 typed as 180 is a shelf; 180 typed as 18000 is a
        unit nothing in a store could hold, and it would stretch the drawing off the page."""
        with self.assertRaises(ValidationError):
            self.product("Giant", width=400.0)
        with self.assertRaises(ValidationError):
            self.can.bf_planogram_unit_height = -1.0

    def test_a_product_on_a_shelf_is_not_deleted_under_the_planogram(self):
        """restrict, not cascade: a product on a shelf does not vanish from the drawing
        because someone deleted it in the catalogue. It is archived, and an archived product
        keeps its position until the position is taken off the shelf."""
        self.position(self.shelf(self.fixture()), self.can)
        # The constraint aimed at is named: the foreign key of the position, and not any
        # raise along the path.
        with self.assertRaises(ForeignKeyViolation):
            with mute_logger("odoo.sql_db"):
                self.can.unlink()

    # ── the freeze ───────────────────────────────────────────────────────────────────
    def test_freezing_copies_the_whole_drawing_and_closes_it(self):
        fixture = self.fixture()
        low = self.shelf(fixture, height=40.0)
        high = self.shelf(fixture, height=120.0)
        self.position(low, self.can, facings=3, deep=2)
        self.position(high, self.box, facings=2)
        fixture.action_freeze()
        version = fixture.version_ids
        self.assertEqual(len(version), 1)
        self.assertTrue(version.locked)
        self.assertEqual(version.version, 1)
        self.assertEqual(version.origin_id, fixture)
        self.assertEqual(version.frozen_by_id, self.env.user)
        self.assertTrue(version.frozen_on)
        self.assertEqual(version.shelf_count, 2, "les tablettes suivent")
        self.assertEqual(version.position_count, 2, "et les positions avec")
        self.assertEqual(version.capacity, fixture.capacity)
        self.assertFalse(fixture.locked, "le meuble vivant reste modifiable")

    def test_a_frozen_version_refuses_every_change_to_the_drawing(self):
        """What a round is counted against does not move under it."""
        fixture = self.fixture()
        shelf = self.shelf(fixture)
        self.position(shelf, self.can, facings=2)
        fixture.action_freeze()
        version = fixture.version_ids
        with self.assertRaises(UserError):
            version.width = 150.0
        with self.assertRaises(UserError):
            version.shelf_ids[0].usable_width = 50.0
        with self.assertRaises(UserError):
            version.shelf_ids.position_ids[0].facings = 9
        with self.assertRaises(UserError):
            self.position(version.shelf_ids[0], self.box)
        with self.assertRaises(UserError):
            self.shelf(version, height=10.0)
        with self.assertRaises(UserError):
            version.shelf_ids.position_ids.unlink()
        with self.assertRaises(UserError):
            version.shelf_ids.unlink()
        with self.assertRaises(UserError):
            version.unlink()
        with self.assertRaises(UserError):
            version.action_freeze()

    def test_a_frozen_version_does_not_move_when_the_product_sheet_changes(self):
        """. The stored widths of a position are recomputed by the ORM, not
        written: the freeze guard never sees them. Measured by SQL, not by the ORM cache,
        which is the very tool that would recompute them."""
        product = self.product("Frozen box", width=20.0, height=30.0)
        fixture = self.fixture()
        shelf = self.shelf(fixture, height=100.0)
        # 40 cm to the shelf above: 30 cm high stands under it, 50 would not.
        self.shelf(fixture, height=140.0)
        self.position(shelf, product, facings=3)
        fixture.action_freeze()
        version = fixture.version_ids
        frozen = version.shelf_ids.position_ids

        def in_base():
            self.env.flush_all()
            self.env.cr.execute(
                "SELECT used_width, measured FROM bf_planogram_position WHERE id IN %s",
                [tuple(frozen.ids)])
            return self.env.cr.fetchall()

        self.assertEqual(in_base(), [(60.0, True)])
        product.bf_planogram_unit_width = 40.0
        product.bf_planogram_unit_height = 50.0
        self.assertEqual(in_base(), [(60.0, True)], "la version garde ses 60 cm")
        live = fixture.shelf_ids.position_ids
        self.env.cr.execute("SELECT used_width FROM bf_planogram_position WHERE id IN %s",
                            [tuple(live.ids)])
        self.assertEqual(self.env.cr.fetchall(), [(120.0,)],
                         "le meuble vivant, lui, suit la fiche produit")
        self.env.invalidate_all()
        self.assertFalse(version.overflow)
        self.assertEqual(version.free_width, 140.0, "deux tablettes de 100, 60 pris")
        drawn = version.to_dict()["shelves"]
        low = [s for s in drawn if s["positions"]][0]["positions"][0]
        self.assertEqual((low["w"], low["h"], low["too_tall"]), (60.0, 30.0, False),
                         "le dessin garde la largeur et la hauteur du gel")
        product.bf_planogram_unit_width = 0.0
        self.assertEqual(in_base(), [(60.0, True)],
                         "une fiche vidée ne rend pas la version « sans dimensions »")

    def test_the_position_views_show_the_width_of_the_freeze_on_a_frozen_version(self):
        """Forme restée du (journal A) : `unit_width` et `unit_height` sont des
        *related* VIVANTS de la fiche produit. Sur une version gelée, la vue montrait la
        fiche d'aujourd'hui à côté de chiffres calculés sur la copie du gel."""
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.box, facings=2)      # 20 cm × 30 cm
        fixture.action_freeze()
        frozen = fixture.version_ids.shelf_ids.position_ids
        self.box.bf_planogram_unit_width = 40.0
        self.assertTrue(frozen.fixture_locked, "la position sait qu'elle est gelée")
        self.assertAlmostEqual(frozen.frozen_unit_width, 20.0, places=1)
        self.assertAlmostEqual(frozen.unit_width, 40.0, places=1,
                               msg="le related, lui, suit la fiche : c'est pourquoi il se cache")
        for name in ("bf_planogram_position_view_form", "bf_planogram_position_view_list"):
            arch = etree.fromstring(self.env.ref("bf_planogram.%s" % name).arch)
            live = arch.xpath("//field[@name='unit_width']")
            copied = arch.xpath("//field[@name='frozen_unit_width']")
            self.assertTrue(live, "la largeur vivante est dans %s" % name)
            self.assertTrue(copied, "et la copie du gel aussi (%s)" % name)
            self.assertEqual(live[0].get("invisible"), "fixture_locked",
                             "la largeur vivante se cache sur une version gelée (%s)" % name)
            self.assertEqual(copied[0].get("invisible"), "not fixture_locked",
                             "et la copie du gel ne paraît que là (%s)" % name)

    def test_a_frozen_version_can_still_be_archived(self):
        """Archiving is not changing the drawing: an aisle that no longer exists stops
        showing up without its history being rewritten."""
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.can)
        fixture.action_freeze()
        version = fixture.version_ids
        version.active = False
        self.assertFalse(version.active)

    def test_a_position_does_not_move_into_a_frozen_version(self):
        """Both ends of a move are checked: the fixture a shelf leaves, and the one it would
        land in. A guard on the record it leaves only would let the drawing be filled from
        outside."""
        live = self.fixture()
        shelf = self.shelf(live)
        self.position(shelf, self.can)
        live.action_freeze()
        version = live.version_ids
        other = self.fixture(name="Elsewhere")
        loose = self.shelf(other)
        with self.assertRaises(UserError):
            loose.fixture_id = version.id
        position = self.position(loose, self.box)
        with self.assertRaises(UserError):
            position.shelf_id = version.shelf_ids[0].id

    def test_the_second_version_is_numbered_two(self):
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.can, facings=2)
        fixture.action_freeze()
        fixture.shelf_ids.position_ids[0].facings = 5
        fixture.action_freeze()
        self.assertEqual(sorted(fixture.version_ids.mapped("version")), [1, 2])
        latest = fixture.version_ids.filtered(lambda f: f.version == 2)
        self.assertEqual(latest.shelf_ids.position_ids[0].facings, 5,
                         "la deuxième version porte le dessin du moment où elle est gelée")
        first = fixture.version_ids.filtered(lambda f: f.version == 1)
        self.assertEqual(first.shelf_ids.position_ids[0].facings, 2,
                         "et la première n'a pas bougé avec lui")

    def test_an_archived_version_keeps_its_number(self):
        """the next number is read over every version, archived included."""
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.can)
        fixture.action_freeze()
        fixture.version_ids.active = False
        fixture.action_freeze()
        every = self.Fixture.with_context(active_test=False).search(
            [("origin_id", "=", fixture.id)])
        self.assertEqual(sorted(every.mapped("version")), [1, 2])
        fixture.invalidate_recordset(["version_count"])
        self.assertEqual(fixture.version_count, 2, "le compte suit le bouton, archivées comprises")

    def test_an_empty_planogram_does_not_freeze(self):
        """A version frozen empty would let a compliance round pass without counting
        anything, which is worse than no version at all."""
        fixture = self.fixture()
        with self.assertRaises(UserError):
            fixture.action_freeze()
        self.shelf(fixture)
        with self.assertRaises(UserError):
            fixture.action_freeze()

    def test_a_version_is_never_frozen_from_another_version(self):
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.can)
        fixture.action_freeze()
        version = fixture.version_ids
        with self.assertRaises(ValidationError):
            self.Fixture.create({
                "name": "Nested", "kind": "gondola", "width": 100.0, "height": 180.0,
                "depth": 50.0, "origin_id": version.id})

    def test_a_frozen_version_survives_the_deletion_of_its_fixture(self):
        """🔴 `ondelete="cascade"` sur `origin_id`, et le gérant a le droit de
        supprimer : effacer le meuble vivant détruisait toutes ses versions gelées, c'est-à-dire
        exactement les dessins contre lesquels des tournées avaient été comptées. Décision
        du propriétaire : la version survit, et seul le lien vers le meuble effacé est vidé."""
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.can, facings=3)
        fixture.action_freeze()
        version_id = fixture.version_ids.id
        fixture.unlink()
        survivor = self.Fixture.with_context(active_test=False).browse(version_id)
        self.assertTrue(survivor.exists(), "la version gelée est toujours là")
        self.assertTrue(survivor.locked)
        self.assertEqual(survivor.version, 1, "avec son numéro")
        self.assertTrue(survivor.frozen_on, "et sa date")
        self.assertFalse(survivor.origin_id, "seul le lien vers le meuble effacé est vidé")
        self.assertEqual(survivor.position_count, 1, "le dessin gelé est entier")
        self.assertAlmostEqual(survivor.shelf_ids.position_ids.used_width, 30.0, places=1)
        with self.assertRaises(UserError):
            survivor.unlink()

    def test_the_freeze_is_logged_in_the_language_of_the_company(self):
        """A chatter body is KEPT and read later by someone else. Odoo 18 reads the language
        off the context and nowhere else, and outside a web request the context carries none,
        so a body written by a scheduled action would freeze an English sentence in the
        thread of a store that reads French, for ever.

        ⚠️ What is measured is the language the body is RENDERED under, never a French string:
        the pass runs without an upgrade, so activating a language here imports no catalogue,
        and comparing to French would be red for the wrong reason."""
        from odoo.addons.bf_planogram.models.company_language import in_company_language
        self.env["res.lang"]._activate_lang("fr_CA")
        self.assertFalse(self.env.context.get("lang"),
                         "le contexte de l'essai ne porte pas de langue, comme un cron")
        self.company.partner_id.lang = "fr_CA"
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.can, facings=2)
        fixture.action_freeze()
        said = in_company_language(fixture, self.company)
        self.assertEqual(said.env.context.get("lang"), "fr_CA",
                         "le corps se rend sous la langue du commerce")
        expected = said.env._(
            "Version %(no)s frozen: %(shelves)s shelf/shelves, %(positions)s "
            "position(s), %(capacity)s unit(s) of capacity.",
            no=1, shelves=1, positions=1, capacity=2)
        self.assertIn(expected, " ".join(fixture.message_ids.mapped("body")))

    def test_the_freeze_log_is_a_log_and_notifies_nobody(self):
        """`_message_log`, never `message_post`: the suite paid for this one in lot 1, where a
        thread body took the business action down with it for a manager with no email
        address. A log sends no mail and raises no bell."""
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.can)
        before = self.env["mail.mail"].search([])
        fixture.action_freeze()
        self.assertFalse(self.env["mail.mail"].search([]) - before,
                         "geler n'a mis aucun courriel en file")
        logged = fixture.message_ids.filtered(
            lambda m: m.message_type == "notification" and not m.partner_ids)
        self.assertTrue(logged, "le gel laisse une note, sans destinataire")

    # ── the drawing ──────────────────────────────────────────────────────────────────
    def test_the_drawing_derives_x_from_the_order_and_the_widths(self):
        """The elevation has no free coordinates: a shelf is a comb, not a canvas. Moving a
        product is reordering a list, which is why nothing here is draggable."""
        fixture = self.fixture()
        shelf = self.shelf(fixture, height=100.0)
        first = self.position(shelf, self.can, facings=2, sequence=1)    # 20 cm
        second = self.position(shelf, self.box, facings=3, sequence=2)   # 60 cm
        data = fixture.to_dict()
        drawn = data["shelves"][0]["positions"]
        self.assertEqual([p["id"] for p in drawn], [first.id, second.id])
        self.assertAlmostEqual(drawn[0]["x"], 0.0, places=1)
        self.assertAlmostEqual(drawn[0]["w"], 20.0, places=1)
        self.assertAlmostEqual(drawn[1]["x"], 20.0, places=1,
                               msg="la deuxième commence où la première finit")
        self.assertAlmostEqual(drawn[1]["w"], 60.0, places=1)
        second.sequence = 0
        reordered = fixture.to_dict()["shelves"][0]["positions"]
        self.assertEqual([p["id"] for p in reordered], [second.id, first.id])
        self.assertAlmostEqual(reordered[1]["x"], 60.0, places=1)

    def test_the_drawing_reads_top_down_and_knows_the_gap_above_each_shelf(self):
        fixture = self.fixture()
        self.shelf(fixture, height=40.0)
        self.shelf(fixture, height=120.0)
        shelves = fixture.to_dict()["shelves"]
        self.assertEqual([s["height"] for s in shelves], [120.0, 40.0],
                         "de haut en bas, comme on lit le meuble devant lequel on est")
        gaps = {s["height"]: s["gap"] for s in shelves}
        self.assertAlmostEqual(gaps[40.0], 80.0, places=1, msg="jusqu'à la tablette du dessus")
        self.assertAlmostEqual(gaps[120.0], 60.0, places=1, msg="jusqu'au haut du meuble")

    def test_the_drawing_shows_an_unmeasured_position_without_lying_in_the_numbers(self):
        """🔴 The one honest compromise, and the reason it is named in the module: a position
        drawn at its real zero would be invisible, and an invisible position is worse than a
        nominal one. No number ever uses the nominal."""
        fixture = self.fixture()
        shelf = self.shelf(fixture)
        self.position(shelf, self.mystery, facings=2)
        data = fixture.to_dict()
        drawn = data["shelves"][0]["positions"][0]
        self.assertTrue(drawn["nominal"])
        self.assertGreater(drawn["w"], 0.0, "elle est dessinée")
        self.assertAlmostEqual(data["used_width"], 0.0, places=1,
                               msg="et elle ne compte pour rien")
        self.assertEqual(data["unmeasured"], 1)

    def test_the_drawing_flags_what_runs_off_the_shelf_and_what_is_too_tall(self):
        """🔴 Chaque drapeau sur SA position, et l'autre drapeau éteint : dans le montage
        d'avant les DEUX positions étaient `outside`, et l'assertion (« il y en a au moins
        une ») passait en désignant la mauvaise."""
        fixture = self.fixture()                        # 100 × 180
        low = self.shelf(fixture, height=10.0)          # 90 cm de dégagement au-dessus
        high = self.shelf(fixture, height=100.0)        # 80 cm jusqu'au haut du meuble
        off = self.position(low, self.box, facings=6)   # 120 cm sur une tablette de 100
        tall = self.position(high, self.can, stacked=9)  # 10 cm de large, 108 de haut
        drawn = {p["id"]: p for s in fixture.to_dict()["shelves"] for p in s["positions"]}
        self.assertTrue(drawn[off.id]["outside"],
                        "120 cm dépassent le bout d'une tablette de 100")
        self.assertFalse(drawn[off.id]["too_tall"],
                         "et 30 cm de haut passent sous 90 cm de dégagement")
        self.assertTrue(drawn[tall.id]["too_tall"], "108 cm ne passent pas sous 80 cm")
        self.assertFalse(drawn[tall.id]["outside"], "et 10 cm de large ne dépassent rien")

    def test_a_nominal_width_does_not_push_the_next_position_off_the_shelf(self):
        """🔴 la largeur nominale de 8 cm par façade entrait dans l'accumulateur
        des x, donc dans `outside` de TOUTES les positions suivantes. Le dessin criait « ça
        dépasse », encadré de rouge, sur une tablette que les chiffres donnaient libre à
        90 cm — alors que le fichier jure qu'aucun chiffre n'emploie ce nominal."""
        fixture = self.fixture()
        shelf = self.shelf(fixture)                               # 100 cm utiles
        blind = self.position(shelf, self.mystery, facings=12, sequence=1)  # 96 cm nominaux
        real = self.position(shelf, self.can, facings=1, sequence=2)        # 10 cm réels
        self.assertAlmostEqual(shelf.used_width, 10.0, places=1)
        self.assertAlmostEqual(shelf.free_width, 90.0, places=1)
        self.assertFalse(shelf.overflow, "les chiffres ne voient aucun débordement")
        drawn = {p["id"]: p for p in fixture.to_dict()["shelves"][0]["positions"]}
        self.assertTrue(drawn[blind.id]["nominal"])
        self.assertAlmostEqual(drawn[blind.id]["w"], 96.0, places=1, msg="elle reste visible")
        self.assertAlmostEqual(drawn[real.id]["x"], 96.0, places=1,
                               msg="et elle garde sa place dans le dessin")
        self.assertFalse(drawn[real.id]["outside"],
                         "le dessin dit ce que disent les chiffres : il reste 90 cm")
        self.assertFalse(drawn[blind.id]["outside"],
                         "une position hachurée ne prend aucun centimètre réel")

    def test_two_shelves_at_the_same_height_still_flag_what_is_too_tall(self):
        """🔴 l'écart au-dessus se mesure à la tablette STRICTEMENT plus haute.
        Deux tablettes à la même hauteur donnaient un écart de zéro à la première, et
        `gap > 0` éteignait « trop haut » pour tout ce qui était dessus. Aucun des essais
        d'avant ne posait deux tablettes à la même hauteur."""
        fixture = self.fixture()                    # 180 cm de haut
        first = self.shelf(fixture, height=100.0)
        second = self.shelf(fixture, height=100.0)
        tall = self.product("Tall thing", width=10.0, height=150.0)
        on_first = self.position(first, tall)
        on_second = self.position(second, tall)
        shelves = fixture.to_dict()["shelves"]
        gaps = {s["id"]: s["gap"] for s in shelves}
        self.assertAlmostEqual(gaps[first.id], 80.0, places=1,
                               msg="jusqu'au haut du meuble, pas jusqu'à sa jumelle")
        self.assertAlmostEqual(gaps[second.id], 80.0, places=1)
        drawn = {p["id"]: p for s in shelves for p in s["positions"]}
        for which, position in (("première", on_first), ("seconde", on_second)):
            self.assertAlmostEqual(drawn[position.id]["h"], 150.0, places=1)
            self.assertTrue(drawn[position.id]["too_tall"],
                            "150 cm sur une tablette à 100 dépassent le meuble de 70 "
                            "(tablette %s)" % which)

    def test_a_shelf_wider_than_its_fixture_is_flagged_and_drawn_whole(self):
        """🔴 rien n'est refusé. 1220 tapé là où 122 cm était
        voulu gonfle le linéaire, donc le ratio espace/ventes promis au lot 4 — alors c'est
        SIGNALÉ, et le dessin s'agrandit au lieu de couper la moitié de la tablette."""
        fixture = self.fixture(width=122.0)
        shelf = self.shelf(fixture, usable_width=1220.0)
        self.position(shelf, self.box, facings=2)
        self.assertTrue(shelf.wider_than_fixture, "la tablette est signalée")
        self.assertTrue(fixture.wide_shelf, "et le meuble le dit aussi")
        data = fixture.to_dict()
        self.assertTrue(data["shelves"][0]["wider"], "le dessin porte le drapeau")
        self.assertGreaterEqual(data["plan_width"], 1220.0,
                                "le dessin s'étend jusqu'au bout de la tablette")
        x, _y, w, _h = (float(v) for v in data["view_box"].split())
        self.assertLessEqual(x, 0.0)
        self.assertGreaterEqual(x + w, 1220.0,
                                "le viewBox contient réellement la tablette : %s"
                                % data["view_box"])
        report = self.env["ir.actions.report"]._render_qweb_html(
            "bf_planogram.report_planogram", fixture.ids)[0]
        html = report.decode() if isinstance(report, bytes) else report
        tag = re.search(r"<svg\b[^>]*>", html).group(0)
        px, _py, pw, _ph = (
            float(v) for v in re.search(r'viewBox="([^"]+)"', tag).group(1).split())
        self.assertGreaterEqual(px + pw, 1220.0,
                                "et le papier n'est pas tronqué non plus : %s" % tag)
        plain = self.fixture(name="Plain", width=122.0)
        self.position(self.shelf(plain), self.box, facings=2)
        self.assertFalse(plain.wide_shelf)
        self.assertAlmostEqual(plain.to_dict()["plan_width"], 122.0, places=1,
                               msg="une tablette ordinaire ne fait grandir aucune boîte")

    def test_the_drawing_of_an_empty_fixture_says_so_instead_of_drawing_nothing(self):
        fixture = self.fixture()
        data = fixture.rendu()
        self.assertTrue(data["empty"])
        self.assertEqual(data["shelves"], [])
        self.assertTrue(data["editable"])
        self.shelf(fixture)
        self.assertFalse(fixture.rendu()["empty"])

    def test_the_drawing_of_a_frozen_version_offers_no_handle(self):
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.can)
        fixture.action_freeze()
        data = fixture.version_ids.rendu()
        self.assertFalse(data["editable"])
        self.assertTrue(data["frozen"])
        self.assertTrue(data["frozen_label"])

    def test_the_printed_plan_renders_the_same_geometry(self):
        """The PDF and the screen read one `to_dict`. ⚠️ The HTML is rendered and not the
        PDF: wkhtmltopdf is not a dependency of a test, and what is measured here is the
        template, not the printer."""
        fixture = self.fixture()
        shelf = self.shelf(fixture, height=60.0)
        self.position(shelf, self.can, facings=4)
        self.position(shelf, self.mystery, facings=1)
        report = self.env["ir.actions.report"]._render_qweb_html(
            "bf_planogram.report_planogram", fixture.ids)[0]
        html = report.decode() if isinstance(report, bytes) else report
        self.assertIn("<svg", html)
        self.assertIn("Canned beans", html)
        self.assertIn("url(#hatch)", html,
                      "la position sans dimension est hachurée sur le papier aussi")
        # « px » paraît sept fois HORS du dessin (styles en ligne), donc on lit
        # la balise <svg> elle-même. Sans largeur et hauteur en pixels, wkhtmltopdf rend un
        # SVG de hauteur ZÉRO (la régression déjà payée dans le plan de bureau).
        tag = re.search(r"<svg\b[^>]*>", html).group(0)
        for side in ("width", "height"):
            size = re.search(r'\s%s="([0-9.]+)px"' % side, tag)
            self.assertTrue(size, "%s en pixels sur la balise du dessin : %s" % (side, tag))
            self.assertGreater(float(size.group(1)), 0.0)

    # ── who may do what ──────────────────────────────────────────────────────────────
    def test_the_shelf_reader_reads_and_does_not_redraw(self):
        """Whoever stocks the shelf reads the plan and prints it. A facing moved by the person
        restocking, without the plan being redrawn, is how a planogram stops describing the
        aisle."""
        fixture = self.fixture()
        shelf = self.shelf(fixture)
        position = self.position(shelf, self.can, facings=2)
        reader = new_test_user(self.env, login="shelf_reader",
                               groups="base.group_user,bf_planogram.group_planogram_user")
        seen = fixture.with_user(reader)
        self.assertEqual(seen.position_count, 1, "il lit le plan")
        self.assertEqual(seen.to_dict()["capacity"], 2)
        with self.assertRaises(AccessError):
            seen.width = 150.0
        with self.assertRaises(AccessError):
            position.with_user(reader).facings = 3
        with self.assertRaises(AccessError):
            self.Shelf.with_user(reader).create({"fixture_id": fixture.id})

    def test_the_manager_can_type_the_dimensions_the_module_asks_for(self):
        """🔴 The lesson of lot 1, applied up front: the retail manager group had no right at
        all on products, so the central button of the module raised "Access Denied by ACLs for
        operation: write" on the very role the module creates. Here the manager types the six
        dimensions, so read and write on the product come from this module's own ACL lines."""
        manager = new_test_user(
            self.env, login="plano_manager",
            groups="base.group_user,bf_planogram.group_planogram_manager")
        product = self.can.with_user(manager)
        product.bf_planogram_unit_width = 11.0
        self.assertAlmostEqual(product.bf_planogram_unit_width, 11.0, places=1)
        with self.assertRaises(AccessError):
            self.Product.with_user(manager).create({"name": "Not his job", "type": "consu"})

    def test_a_store_does_not_read_the_aisles_of_another(self):
        """⚠️ The rule is measured through a user who belongs to ONE company, and not
        through the one running the test: `res.company.create` adds the new company to the
        allowed companies of whoever created it, so the test author sees both and would
        have measured nothing. The first version of this test did exactly that and passed
        for the wrong reason."""
        other = self.env["res.company"].create({"name": "Another store"})
        mine = self.fixture()
        theirs = self.Fixture.sudo().create({
            "name": "Their aisle", "kind": "gondola", "width": 100.0, "height": 180.0,
            "depth": 50.0, "company_id": other.id})
        shelf = self.Shelf.sudo().create({"fixture_id": theirs.id, "height_from_floor": 50.0})
        self.Position.sudo().create({"shelf_id": shelf.id, "product_id": self.can.id})
        self.assertEqual(shelf.company_id, other,
                         "la société de la tablette est celle du meuble, stockée pour la règle")
        clerk = new_test_user(
            self.env, login="one_store_only",
            groups="base.group_user,bf_planogram.group_planogram_manager")
        clerk.write({"company_ids": [Command.set(self.company.ids)],
                     "company_id": self.company.id})
        found = self.Fixture.with_user(clerk).search([])
        self.assertIn(mine, found)
        self.assertNotIn(theirs, found, "une autre société ne se lit pas")
        self.assertNotIn(shelf, self.Shelf.with_user(clerk).search([]))
        self.assertFalse(
            self.Position.with_user(clerk).search([("company_id", "=", other.id)]))

    # ── the kinds table ──────────────────────────────────────────────────────────────
    def test_a_fixture_posed_from_its_kind_is_already_to_scale(self):
        fixture = self.Fixture.new({"kind": "checkout"})
        fixture._onchange_kind()
        self.assertAlmostEqual(fixture.width, 60.0, places=1)
        self.assertAlmostEqual(fixture.height, 140.0, places=1)
        self.assertAlmostEqual(fixture.depth, 25.0, places=1)

    def test_picking_a_kind_leaves_the_dimensions_typed_by_hand(self):
        """🔴 l'enregistrement part REMPLI, et c'est tout le constat — celui
        d'avant partait d'un vierge et ne pouvait pas attraper l'écrasement. Un gérant qui
        tapait 250 × 190 × 60 puis choisissait le bon genre perdait ses trois mesures sans
        un mot, pendant que le commentaire de la méthode promettait l'inverse."""
        measured = self.Fixture.new({
            "kind": "gondola", "width": 250.0, "height": 190.0, "depth": 60.0})
        measured.kind = "cooler"
        measured._onchange_kind()
        self.assertAlmostEqual(measured.width, 250.0, places=1, msg="la largeur tapée reste")
        self.assertAlmostEqual(measured.height, 190.0, places=1)
        self.assertAlmostEqual(measured.depth, 60.0, places=1)
        # Et ce qui est VIDE est rempli par le genre : c'est à ça qu'il sert.
        half = self.Fixture.new({"kind": "cooler", "width": 250.0})
        half._onchange_kind()
        self.assertAlmostEqual(half.width, 250.0, places=1)
        self.assertAlmostEqual(half.height, 200.0, places=1, msg="le vide, lui, se remplit")
        self.assertAlmostEqual(half.depth, 80.0, places=1)
        # Et la promesse du README, mesurée dans l'ordre du client : les valeurs par défaut,
        # puis l'onchange. Les trois dimensions n'ont PLUS de défaut de champ -- c'était la
        # taille de la gondole écrite une seconde fois, et elle rendait « ne remplir que le
        # vide » impossible à satisfaire.
        blank = self.Fixture.default_get(["kind", "width", "height", "depth"])
        self.assertEqual(blank.get("kind"), "gondola")
        for side in ("width", "height", "depth"):
            self.assertFalse(blank.get(side), "%s n'a plus de défaut de champ" % side)
        posed = self.Fixture.new(blank)
        posed._onchange_kind()
        self.assertAlmostEqual(posed.width, 122.0, places=1,
                               msg="un meuble neuf est à l'échelle de son genre")
        self.assertAlmostEqual(posed.height, 183.0, places=1)
        self.assertAlmostEqual(posed.depth, 56.0, places=1)

    def test_the_drawing_names_the_kind_instead_of_its_code(self):
        data = self.fixture(kind="cooler").to_dict()
        self.assertEqual(data["kind"], "cooler")
        self.assertEqual(data["kind_label"], "Cooler")

    # ── le papier ───────────────────────────────────────────────────────
    def test_the_printed_plan_fills_the_page_and_keeps_a_legible_body(self):
        """🔴 `px_w = min(960, int(380 / ratio))` figeait la HAUTEUR du dessin
        et ne lisait jamais la largeur de la page. La gondole par défaut (122 × 183, le
        genre de 5 des 8 de la table) sortait à 248 px sur les 993 px qu'offre une lettre
        en paysage — un quart de la page, deux tiers blancs — et son corps mesurait
        **2,40 pt dans le vrai PDF**, lu par wkhtmltopdf 0.12.6.1.

        Deux genres au moins, dont un plus haut que large et un plus large que haut."""
        from odoo.addons.bf_planogram.models.rendering import (
            MIN_FONT_PX, PAPER_HEIGHT, PAPER_WIDTH)
        cas = (
            ("plus haut que large", 122.0, 183.0, (30.0, 75.0, 120.0, 165.0)),
            ("plus large que haut", 200.0, 100.0, (40.0, 90.0)),
        )
        for quoi, width, height, hauteurs in cas:
            fixture = self.fixture(name=quoi, width=width, height=height)
            for y in hauteurs:
                shelf = self.shelf(fixture, height=y)
                self.position(shelf, self.can, facings=4)
                self.position(shelf, self.box, facings=2)
            data = fixture.to_dict()
            box_w = data["plan_width"] + 2 * data["margin"]
            box_h = data["plan_height"] + 2 * data["margin"]
            px_w, px_h = data["paper_px_width"], data["paper_px_height"]
            self.assertLessEqual(px_w, PAPER_WIDTH, "%s : dans la page" % quoi)
            self.assertLessEqual(px_h, PAPER_HEIGHT, "%s : dans la page" % quoi)
            echelle = px_w / box_w
            self.assertAlmostEqual(px_h / box_h, echelle, places=2,
                                   msg="%s : les proportions du dessin sont gardées" % quoi)
            # La boîte TOUCHE un bord de la page : plus de hauteur figée qui laisse les
            # trois quarts de la largeur blancs.
            self.assertTrue(
                abs(px_w - PAPER_WIDTH) <= 1.0 or abs(px_h - PAPER_HEIGHT) <= 1.0,
                "%s : le dessin touche la largeur ou la hauteur de la page, "
                "il mesure %s × %s px" % (quoi, px_w, px_h))
            # Et le corps, lu en pixels DE LA PAGE : jamais plus petit que les lettres du
            # tableau en dessous, que le rapport pose à 11 px.
            corps = data["font_size"] * echelle
            self.assertGreaterEqual(
                round(corps, 1), MIN_FONT_PX - 0.1,
                "%s : le corps du plan vaut %.2f px de page (minimum %s), "
                "corps de plan %s à l'échelle %.3f"
                % (quoi, corps, MIN_FONT_PX, data["font_size"], echelle))
            # Le papier emploie bien ces pixels-là, et les calcule une seule fois.
            report = self.env["ir.actions.report"]._render_qweb_html(
                "bf_planogram.report_planogram", fixture.ids)[0]
            html = report.decode() if isinstance(report, bytes) else report
            tag = re.search(r"<svg\b[^>]*>", html).group(0)
            for side, attendu in (("width", px_w), ("height", px_h)):
                lu = re.search(r'\s%s="([0-9.]+)px"' % side, tag)
                self.assertTrue(lu, "%s en pixels : %s" % (side, tag))
                self.assertAlmostEqual(float(lu.group(1)), attendu, places=1,
                                       msg="%s : %s vient du serveur (%s)" % (quoi, side, tag))

    # ── les gardes de hauteur ──────────────────────────────────
    def test_the_top_shelf_still_says_what_rises_past_the_fixture(self):
        """🔴 une tablette posée À la hauteur du meuble est acceptée (la
        contrainte compare en `>`), son écart au-dessus vaut ZÉRO, et `gap > 0` éteignait
        « trop haut » pour tout ce qui était dessus. Une pile de 150 cm montait 150 cm
        au-dessus du meuble sans un mot. Le correctif du ne couvrait que deux
        tablettes à la MÊME hauteur, pas celle du sommet."""
        fixture = self.fixture(height=180.0)
        sommet = self.shelf(fixture, height=180.0)
        tall = self.product("Tall thing", width=10.0, height=150.0)
        dessus = self.position(sommet, tall)
        shelves = fixture.to_dict()["shelves"]
        au_sommet = [s for s in shelves if s["id"] == sommet.id][0]
        self.assertAlmostEqual(au_sommet["gap"], 0.0, places=1,
                               msg="rien au-dessus de la tablette du sommet")
        drawn = {p["id"]: p for s in shelves for p in s["positions"]}
        self.assertAlmostEqual(drawn[dessus.id]["h"], 150.0, places=1)
        self.assertTrue(drawn[dessus.id]["too_tall"],
                        "un écart de zéro ne tient RIEN : 150 cm sur la tablette du "
                        "sommet dépassent le meuble de 150 cm")

    def test_the_height_of_a_fixture_does_not_drop_under_its_own_shelves(self):
        """🔴 créer une tablette au-dessus du sommet est
        refusé, baisser le MEUBLE sous une tablette existante était accepté — aucune
        contrainte ne relisait les tablettes."""
        fixture = self.fixture(height=180.0)
        haute = self.shelf(fixture, height=170.0)
        # ⚠️ Un signet : `write` pose l'UPDATE AVANT de valider, donc la levée laisse la
        # mutation dans la transaction. C'est l'état d'après le refus qu'on mesure ici.
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            fixture.height = 150.0
        self.env.invalidate_all()
        self.assertAlmostEqual(fixture.height, 180.0, places=1,
                               msg="le meuble n'a pas bougé")
        fixture.height = 171.0         # au-dessus de la tablette : ordinaire
        self.assertAlmostEqual(haute.height_from_floor, 170.0, places=1)

    def test_an_empty_shelf_above_the_top_is_drawn_and_not_cropped(self):
        """🔴 une tablette VIDE ne faisait pas grandir
        `plan_height`, donc son plateau tombait hors du viewBox — coupé en silence à
        l'écran et au papier, alors qu'elle figure encore au tableau du PDF."""
        fixture = self.fixture(height=180.0)
        # La garde neuve refuse de poser l'état fautif par le meuble : il se pose par la
        # tablette, du bas vers le haut, ce que rien n'interdit.
        haute = self.shelf(fixture, height=180.0)
        data = fixture.to_dict()
        self.assertGreaterEqual(data["plan_height"], 180.0)
        # Et une tablette plus haute que le meuble, telle qu'elle existe déjà en base
        # (l'ancienne porte), reste dans la boîte.
        self.env.cr.execute(
            "update bf_planogram_shelf set height_from_floor = 200.0 where id = %s",
            (haute.id,))
        self.env.invalidate_all()
        data = fixture.to_dict()
        self.assertGreaterEqual(data["plan_height"], 200.0,
                                "le dessin s'étend jusqu'au plateau qu'il compte")
        x, y, w, h = (float(v) for v in data["view_box"].split())
        self.assertLessEqual(y, 180.0 - 200.0,
                             "le viewBox monte au-dessus du meuble : %s" % data["view_box"])
        self.assertGreaterEqual(h, 200.0)
        self.assertEqual(len(data["shelves"]), 1, "et elle figure bien au tableau")

    # ── l'étiquette du gel ─────────────────────────────────────────────
    def test_the_label_of_the_freeze_prints_the_hour_of_the_store(self):
        """🔴 `to_string` sans `context_timestamp` imprimait l'UTC de la base.
        Un gel à 20 h 17 à Toronto s'imprimait « 2026-11-18 01:17:09 » — le JOUR SUIVANT —
        et cette étiquette est l'identité de la version contre laquelle une tournée se
        compte."""
        self.env.user.tz = "America/Toronto"
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.can)
        soir = datetime(2026, 11, 18, 1, 17, 9)     # 20 h 17 le 17, heure de Toronto
        with patch.object(fields.Datetime, "now", staticmethod(lambda: soir)):
            fixture.action_freeze()
        version = fixture.version_ids
        self.assertEqual(version.frozen_on, soir, "la base garde l'UTC, elle")
        label = version.with_context(tz=None)._frozen_label()
        self.assertIn("2026-11-17 20:17:09", label,
                      "l'étiquette porte l'heure du magasin : %s" % label)
        self.assertNotIn("2026-11-18", label,
                         "un gel du soir n'imprime pas le jour suivant : %s" % label)
        self.assertEqual(version.to_dict()["frozen_label"], label,
                         "et le papier lit la même étiquette")

    # ── toutes les vues qui montrent la largeur vivante ────────────────
    def test_every_view_of_the_module_hides_the_live_width_on_a_frozen_version(self):
        """🔴 le correctif du n'a tenu que sur les deux vues de position,
        et la liste `position_ids` encastrée dans le formulaire de TABLETTE montrait encore
        `unit_width`, *related* VIVANT, à côté d'un `used_width` calculé sur la copie du
        gel. Les vues sont trouvées par RECHERCHE, pas nommées une à une : c'est la vue
        qu'on oublie de nommer qui contredit la leçon."""
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.box, facings=2)      # 20 cm
        fixture.action_freeze()
        frozen = fixture.version_ids.shelf_ids.position_ids
        self.box.bf_planogram_unit_width = 40.0
        self.assertAlmostEqual(frozen.frozen_unit_width, 20.0, places=1)
        self.assertAlmostEqual(frozen.unit_width, 40.0, places=1,
                               msg="le related suit la fiche : c'est pourquoi il se cache")
        self.assertAlmostEqual(frozen.used_width, 40.0, places=1,
                               msg="et les chiffres, eux, restent sur la copie du gel")
        data = self.env["ir.model.data"].search([
            ("module", "=", "bf_planogram"), ("model", "=", "ir.ui.view")])
        views = self.env["ir.ui.view"].browse(data.mapped("res_id"))
        self.assertTrue(views, "des vues ont été trouvées")
        vus = []
        for view in views:
            arch = etree.fromstring(view.arch)
            for live in arch.xpath("//field[@name='unit_width' or @name='unit_height']"):
                nom = live.get("name")
                vus.append("%s/%s" % (view.name, nom))
                self.assertEqual(
                    live.get("invisible"), "fixture_locked",
                    "%s : %s se cache sur une version gelée" % (view.name, nom))
                parent = live.getparent()
                copie = parent.xpath("./field[@name='frozen_%s']" % nom)
                self.assertTrue(copie, "%s : la copie du gel est au même endroit que %s"
                                       % (view.name, nom))
                self.assertEqual(
                    copie[0].get("invisible"), "not fixture_locked",
                    "%s : frozen_%s ne paraît que là" % (view.name, nom))
                self.assertTrue(
                    arch.xpath("//field[@name='fixture_locked']"),
                    "%s : le drapeau que lisent ces deux modificateurs est dans la vue"
                    % view.name)
        self.assertGreaterEqual(len(vus), 4,
                                "les vues trouvées par recherche : %s" % vus)
        self.assertTrue(
            any("shelf" in nom for nom in vus),
            "la vue VOISINE, celle de la tablette, est dans le lot que la recherche "
            "rend : c'est elle que le correctif du avait manquée (%s)" % vus)

    # ── l'hypothèse du l'unicité par la base ─────────────────────────────────
    def test_two_versions_cannot_share_a_number_even_by_the_back_door(self):
        """🔴 Hypothèse du `action_freeze` numérote par `max(...) + 1` SANS verrou,
        et `pg_constraint` ne portait aucune contrainte d'unicité sur la table. Deux gels
        concurrents lisent le même maximum et posent le même numéro — le par une
        autre porte. Non reproductible à deux transactions sur une base partagée : la
        contrainte se mesure là où elle vit, dans la BASE."""
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.can)
        fixture.action_freeze()
        fixture.action_freeze()
        un, deux = fixture.with_context(active_test=False).version_ids.sorted("version")
        self.assertEqual((un.version, deux.version), (1, 2))
        with self.assertRaises(UniqueViolation), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "update bf_planogram_fixture set version = %s where id = %s",
                    (un.version, deux.id))
        self.env.invalidate_all()
        self.assertEqual(deux.version, 2, "le numéro n'a pas bougé")
        # Et les meubles VIVANTS, eux, ne se gênent pas : origin_id nul, et Postgres ne
        # compare pas les NULL.
        autre = self.fixture(name="Vivant")
        self.assertFalse(autre.origin_id)
        self.env.flush_all()

    # ── le gel garde les libellés ───────────────
    def test_a_frozen_plan_keeps_the_name_and_the_code_of_that_day(self):
        """🔴 le dessin et le papier d'une version gelée suivaient le nom et
        le code du produit d'AUJOURD'HUI. Renommer un produit réécrivait tous les plans
        gelés où il paraissait."""
        thing = self.product("Old name", width=10.0, height=12.0, default_code="OLD-1")
        fixture = self.fixture()
        position = self.position(self.shelf(fixture), thing, facings=2)
        fixture.action_freeze()
        version = fixture.version_ids
        thing.write({"name": "New name", "default_code": "NEW-9"})
        gelee = version.to_dict()["shelves"][0]["positions"][0]
        self.assertIn("Old name", gelee["product"],
                      "le plan réimprimé nomme ce qui était sur la tablette : %s"
                      % gelee["product"])
        self.assertNotIn("New name", gelee["product"])
        self.assertEqual(gelee["code"], "OLD-1")
        # Et le papier le lit du même endroit.
        report = self.env["ir.actions.report"]._render_qweb_html(
            "bf_planogram.report_planogram", version.ids)[0]
        html = report.decode() if isinstance(report, bytes) else report
        self.assertIn("Old name", html)
        self.assertNotIn("New name", html)
        # Le meuble VIVANT, lui, suit bien le produit : c'est le même dessin, deux états.
        vivant = fixture.to_dict()["shelves"][0]["positions"][0]
        self.assertIn("New name", vivant["product"])
        self.assertEqual(vivant["code"], "NEW-9")
        self.assertEqual(position.frozen_product_name, False,
                         "rien n'est copié sur la position vivante")

    # ── un meuble créé par code ─────────────────
    def test_a_fixture_created_by_code_is_posed_to_the_scale_of_its_kind(self):
        """🔴 un meuble créé par code sans dimensions mourait sur une
        `NotNullViolation` de Postgres — message anglais, nom de table, contenu de ligne,
        curseur empoisonné — et dans un `create` de deux, la bonne ligne mourait avec la
        mauvaise. Le genre verse ses trois mesures quand la clé est ABSENTE, comme le fait
        le client web par `_onchange_kind`."""
        nu = self.Fixture.create({"name": "Par action serveur", "kind": "cooler"})
        self.assertAlmostEqual(nu.width, 150.0, places=1, msg="le genre a versé sa largeur")
        self.assertAlmostEqual(nu.height, 200.0, places=1)
        self.assertAlmostEqual(nu.depth, 80.0, places=1)
        # Sans `kind` du tout : le défaut du champ est le genre, et c'est sa table qui parle.
        sans_genre = self.Fixture.create({"name": "Sans genre"})
        self.assertEqual(sans_genre.kind, "gondola")
        self.assertAlmostEqual(sans_genre.width, 122.0, places=1)
        self.assertAlmostEqual(sans_genre.height, 183.0, places=1)
        # ⚠️ Une clé PRÉSENTE à zéro n'est pas une clé absente : un zéro tapé exprès reste
        # refusé, et c'est ce qui distingue une faute de frappe d'un silence.
        for vals in ({"width": 0.0}, {"height": 0.0}, {"depth": 0.0}):
            with self.assertRaises(ValidationError, msg=str(vals)), \
                    self.env.cr.savepoint():
                self.Fixture.create(
                    dict({"name": "Zéro tapé", "kind": "cooler"}, **vals))
        # Et un `create` de deux : la bonne ligne ne meurt plus avec la mauvaise, parce
        # qu'il n'y a plus de mauvaise ligne à poser.
        deux = self.Fixture.create([
            {"name": "A", "kind": "checkout"},
            {"name": "B", "kind": "wall", "width": 250.0},
        ])
        self.assertEqual(len(deux), 2)
        self.assertAlmostEqual(deux[0].width, 60.0, places=1)
        self.assertAlmostEqual(deux[1].width, 250.0, places=1,
                               msg="une mesure donnée n'est jamais corrigée")
        self.assertAlmostEqual(deux[1].height, 220.0, places=1)

    # ── les formes ────────────────────────────────────────────────
    def test_the_screen_and_the_paper_write_the_same_number(self):
        """Forme, `200` / `150` dans Chrome, `200.0` / `150.0` dans le PDF —
        un float imprimé par QWeb contre un nombre JSON imprimé par OWL. La cote est
        ÉCRITE une fois, par le serveur, comme toutes les autres étiquettes du dessin."""
        fixture = self.fixture()
        shelf = self.shelf(fixture, height=150.0)
        self.position(shelf, self.can)
        drawn = fixture.to_dict()["shelves"][0]
        self.assertEqual(drawn["height_label"], "150.0",
                         "la cote est une chaîne, bâtie par le serveur")
        report = self.env["ir.actions.report"]._render_qweb_html(
            "bf_planogram.report_planogram", fixture.ids)[0]
        html = report.decode() if isinstance(report, bytes) else report
        svg = re.search(r"<svg\b.*?</svg>", html, re.S).group(0)
        self.assertTrue(re.search(r">\s*150\.0\s*<", svg),
                        "le papier écrit la chaîne du serveur : %s" % svg[:400])
        self.assertFalse(re.search(r">\s*150\s*<", svg),
                         "et pas le nombre écrit autrement")
        source = self._source_du_module("static/src/elevation/elevation.xml")
        self.assertIn("shelf.height_label", source,
                      "et l'écran lit la MÊME chaîne, pas le nombre")
        self.assertNotIn('t-esc="shelf.height"', source)

    def test_the_component_carries_no_dead_code(self):
        """Forme, `openShelf` n'était appelé nulle part — exactement ce
        pour quoi `INK` avait été retiré au tour 1."""
        source = self._source_du_module("static/src/elevation/elevation.js")
        self.assertNotIn("openShelf", source)
        # Et ce qui RESTE est appelé : le gabarit nomme chaque poignée du composant.
        gabarit = self._source_du_module("static/src/elevation/elevation.xml")
        for appel in ("openPosition", "toggleLabels", "zoom"):
            self.assertIn(appel, gabarit, "%s est appelé par le gabarit" % appel)

    def test_a_frozen_version_is_read_only_on_the_screen_not_after_the_save(self):
        """Forme, sur une version gelée, `name`, `code`, `aisle`, `kind`, les
        trois dimensions, `notes` et la poignée de tri restaient modifiables : on tapait,
        on sauvait, et on recevait la levée de la garde APRÈS le geste."""
        form = etree.fromstring(
            self.env.ref("bf_planogram.bf_planogram_fixture_view_form").arch)
        for nom in ("name", "code", "aisle", "kind", "width", "height", "depth", "notes",
                    "shelf_ids"):
            champs = form.xpath("//field[@name='%s']" % nom)
            self.assertTrue(champs, "%s est dans le formulaire" % nom)
            self.assertEqual(champs[0].get("readonly"), "locked",
                             "%s est en lecture seule sur une version gelée" % nom)
        liste = etree.fromstring(
            self.env.ref("bf_planogram.bf_planogram_fixture_view_list").arch)
        poignee = liste.xpath("//field[@name='sequence']")[0]
        self.assertEqual(poignee.get("widget"), "handle")
        self.assertEqual(poignee.get("readonly"), "locked",
                         "la poignée de tri comprise")
        # Et la garde reste la garde : la vue ne la remplace pas.
        fixture = self.fixture()
        self.position(self.shelf(fixture), self.can)
        fixture.action_freeze()
        with self.assertRaises(UserError):
            fixture.version_ids.write({"name": "Renommée"})

    def test_a_refused_domain_on_a_product_is_a_user_error(self):
        """Forme, `product_template.py` levait une `ValidationError` depuis
        une méthode de RECHERCHE, là où son jumeau `fixture.py` lève une `UserError`. Une
        méthode de recherche refuse un domaine, elle ne valide pas un enregistrement."""
        Template = self.env["product.template"]
        with self.assertRaises(UserError):
            Template.search([("bf_planogram_measured", ">", 0)])
        with self.assertRaises(UserError):
            Template.search([("bf_planogram_measured", "=", "oui")])
        # Le jumeau, pour la même raison et de la même façon.
        with self.assertRaises(UserError):
            self.Fixture.search([("unmeasured_count", "like", "1")])

    # ── ce que la liste AFFICHE, pas seulement ce que le champ vaut ──────────────────
    def test_le_rempli_ne_passe_pas_par_le_gadget_de_pourcentage(self):
        """🔴 `fill_pct` vaut DÉJÀ 0 à 100, et le gadget `percentage` d'Odoo multiplie par
        cent : la liste des meubles affichait « 5920% » là où la tablette est remplie à
        59,2 %. Mesuré sur une base semée, après deux tours de relecture,
        27 mutants et un parcours réel — parce qu'aucun essai ne rendait une LISTE, et que
        le parcours lisait la ligne du dessin (« rempli à 59,2 % »), pas la colonne.

        Un essai qui lit la valeur du champ ne voit rien ici : ce qu'il faut lire est
        l'arch de la vue, c'est-à-dire ce que la personne a sous les yeux."""
        for modele in ("bf.planogram.fixture", "bf.planogram.shelf"):
            for mode in ("list", "form"):
                vues = self.env[modele].get_views([(False, mode)])["views"]
                noeuds = etree.fromstring(vues[mode]["arch"]).xpath("//field[@name='fill_pct']")
                self.assertTrue(noeuds, "%s n'affiche pas le rempli en %s" % (modele, mode))
                for n in noeuds:
                    self.assertNotEqual(
                        n.get("widget"), "percentage",
                        "%s (%s) : le gadget `percentage` multiplie par cent un champ qui "
                        "porte déjà des pourcents" % (modele, mode))
