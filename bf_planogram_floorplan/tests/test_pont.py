# -*- coding: utf-8 -*-
import logging
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_floorplan.models.element import BfFloorplanElement

_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install")
class TestPlanogramFloorplan(TransactionCase):
    """The bridge, and only the bridge: what each side does alone is measured in its own
    module."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        env.user.groups_id = [
            Command.link(env.ref("bf_planogram.group_planogram_manager").id),
            Command.link(env.ref("bf_floorplan.group_bf_floorplan_manager").id)]
        cls.plan = env["bf.floorplan"].create({
            "name": "Store, ground floor", "largeur": 2000.0, "profondeur": 1500.0,
            "company_id": cls.company.id})
        cls.gondola = env["bf.floorplan.element"].create({
            "plan_id": cls.plan.id, "name": "G12", "genre": "gondole",
            "x": 100.0, "y": 100.0})
        cls.can = env["product.product"].create({
            "name": "Canned beans", "type": "consu",
            "bf_planogram_unit_width": 10.0, "bf_planogram_unit_height": 12.0})
        cls.nameless = env["product.product"].create({
            "name": "Unmeasured thing", "type": "consu"})

    def fixture(self, **kw):
        return self.env["bf.planogram.fixture"].create(dict({
            "name": "Aisle 3", "kind": "gondola", "width": 122.0, "height": 183.0,
            "depth": 56.0, "floorplan_element_id": self.gondola.id,
        }, **kw))

    def filled(self, fixture, product=None, facings=2, **kw):
        shelf = self.env["bf.planogram.shelf"].create({
            "fixture_id": fixture.id, "height_from_floor": 100.0})
        self.env["bf.planogram.position"].create(dict({
            "shelf_id": shelf.id, "product_id": (product or self.can).id,
            "facings": facings}, **kw))
        return shelf

    # ── the link ─────────────────────────────────────────────────────────────────────
    def test_the_fixture_knows_its_shape_and_its_plan(self):
        fixture = self.fixture()
        self.assertEqual(fixture.floorplan_element_id, self.gondola)
        self.assertEqual(fixture.floorplan_id, self.plan,
                         "le plan se déduit de la forme, et se stocke pour le regroupement")
        self.assertEqual(self.gondola.planogram_fixture_count, 1)
        self.assertIn(fixture, self.gondola.planogram_fixture_ids)

    def test_one_shape_carries_several_sections_of_a_wall_run(self):
        """No unique constraint on the shape: a wall run is drawn as one shape and planned
        in sections. A fixture, on the other hand, stands in one place, which is the
        many2one itself."""
        first = self.fixture(name="Wall, left")
        second = self.fixture(name="Wall, right")
        self.assertEqual(self.gondola.planogram_fixture_count, 2)
        self.assertEqual(first.floorplan_element_id, second.floorplan_element_id)

    def test_deleting_the_shape_leaves_the_planogram_standing(self):
        """ondelete set null: the elevation is not lost because the floor was redrawn."""
        fixture = self.fixture()
        self.gondola.unlink()
        self.assertTrue(fixture.exists())
        self.assertFalse(fixture.floorplan_element_id)
        self.assertFalse(fixture.floorplan_id)

    def test_a_fixture_does_not_stand_on_the_plan_of_another_store(self):
        other = self.env["res.company"].create({"name": "Another store"})
        elsewhere = self.env["bf.floorplan"].create({
            "name": "Their floor", "largeur": 1000.0, "profondeur": 1000.0,
            "company_id": other.id})
        shape = self.env["bf.floorplan.element"].create({
            "plan_id": elsewhere.id, "name": "Theirs", "genre": "gondole"})
        with self.assertRaises(ValidationError):
            self.fixture(floorplan_element_id=shape.id)

    def test_a_plan_without_a_company_takes_any_store(self):
        """An empty company means every company, which is Odoo's own reading of the field:
        refusing it would refuse the plan of a building shared by two stores."""
        self.plan.company_id = False
        fixture = self.fixture()
        self.assertEqual(fixture.floorplan_id, self.plan)

    # ── what the plan says about what is on the shelves ──────────────────────────────
    def test_the_shape_opens_the_planogram_when_it_carries_exactly_one(self):
        fixture = self.fixture()
        target = self.gondola._cible()
        self.assertEqual(target["modele"], "bf.planogram.fixture")
        self.assertEqual(target["id"], fixture.id)
        # Several: the click opens the shape, which lists them, rather than choosing.
        self.fixture(name="Second section")
        self.assertFalse(self.gondola._cible())

    def test_the_shape_says_nothing_of_a_planogram_to_whoever_cannot_read_it(self):
        """🔴 The plan is read without the rights of the planogram, like a notice board. The
        count and the tooltip are read in sudo; the FORM only opens for whoever may read
        it, so without the right the shape announces no target."""
        self.fixture()
        # ⚠️ `bf_floorplan` has ONE group, « Gestion »: reading a plan is base.group_user
        # (« Tout le monde lit : un plan sert d'abord à trouver quelqu'un »). So the reader
        # here is an ordinary employee, who reads the plan and not the planogram.
        reader = new_test_user(self.env, login="plan_only", groups="base.group_user")
        seen = self.gondola.with_user(reader)
        self.assertEqual(seen.planogram_fixture_count, 1,
                         "le tableau d'affichage compte ce qui est là")
        self.assertFalse(seen._cible(), "mais le clic n'ouvre pas une fiche interdite")

    def test_the_plan_flags_a_shelf_asked_to_hold_more_than_it_has(self):
        fixture = self.fixture()
        self.filled(fixture, facings=20)      # 200 cm on a 122 cm shelf
        self.assertTrue(fixture.overflow)
        self.assertEqual(self.gondola._teinte(), "alerte")

    def test_the_plan_flags_a_position_nobody_measured(self):
        fixture = self.fixture()
        self.filled(fixture, product=self.nameless)
        self.assertEqual(self.gondola._teinte(), "attention")

    def test_the_plan_flags_a_fixture_with_nothing_on_it(self):
        self.fixture()
        self.assertEqual(self.gondola._teinte(), "attention",
                         "un meuble dessiné et vide est une intention, pas un planogramme")

    def test_the_plan_says_green_once_a_version_is_frozen(self):
        """Green is not « it is full », it is « it is published »: a compliance round is
        counted against a frozen version, and there is nothing to count before there is
        one."""
        fixture = self.fixture()
        self.filled(fixture)
        self.assertFalse(self.gondola._teinte(), "ni alerte ni attention, mais rien encore")
        fixture.action_freeze()
        self.assertEqual(self.gondola._teinte(), "ok")

    def test_a_frozen_version_does_not_stand_on_the_plan(self):
        """A version does not STAND anywhere: the fixture does. Copied, every version would
        add a phantom fixture to the shape, and yesterday's overflow would colour today's
        plan for ever."""
        fixture = self.fixture()
        self.filled(fixture, facings=20)
        fixture.action_freeze()
        version = fixture.version_ids
        self.assertFalse(version.floorplan_element_id,
                         "le champ ne se copie pas : c'est la raison de copy=False")
        self.assertEqual(self.gondola.planogram_fixture_count, 1,
                         "la version gelée ne se compte donc pas sur le plan")
        fixture.shelf_ids.position_ids.facings = 2
        self.assertFalse(fixture.overflow)
        self.assertNotEqual(self.gondola._teinte(), "alerte",
                            "le débordement d'hier ne colore pas le plan d'aujourd'hui")

    def test_the_tooltip_of_the_shape_names_what_is_on_it(self):
        fixture = self.fixture()
        # deep=2: the capacity (3 x 2 = 6) is then a number that is neither the facings
        # nor the count of shelves or positions, so finding it measures the capacity.
        self.filled(fixture, facings=3, deep=2)
        # In English, the language of the sources: the bench base reads French.
        infos = " · ".join(self.gondola.with_context(lang="en_US")._infos())
        self.assertIn("Gondole", infos, "la nature de la forme reste la première phrase")
        self.assertIn("1 shelf/shelves, 1 position(s), 6 unit(s)", infos,
                      "la capacité est dite, et c'est façades × profondeur")

    # ── going from one to the other ──────────────────────────────────────────────────
    def test_the_fixture_opens_the_plan_with_its_own_shape_highlighted(self):
        fixture = self.fixture()
        action = fixture.action_open_floorplan()
        self.assertEqual(action["res_model"], "bf.floorplan")
        self.assertEqual(action["res_id"], self.plan.id)
        self.assertEqual(action["context"]["bf_floorplan_surligne"], self.gondola.id)
        # And the plan reads that key: it is the mechanism of bf_floorplan itself.
        drawn = self.plan.with_context(
            bf_floorplan_surligne=self.gondola.id).rendu()
        self.assertEqual(drawn["surligne"], self.gondola.id)

    def test_a_fixture_on_no_plan_says_so_instead_of_opening_nothing(self):
        fixture = self.fixture(floorplan_element_id=False)
        with self.assertRaises(ValidationError):
            fixture.action_open_floorplan()

    def test_the_shape_opens_the_elevation(self):
        fixture = self.fixture()
        action = self.gondola.action_open_planogram()
        self.assertEqual(action["res_model"], "bf.planogram.fixture")
        self.assertEqual(action["res_id"], fixture.id)
        self.fixture(name="Second section")
        listed = self.gondola.action_open_planogram()
        self.assertEqual(listed["view_mode"], "list,form")
        self.assertEqual(
            len(self.env["bf.planogram.fixture"].search(listed["domain"])), 2)

    # ── tour 1 du lot 3 : ce que la relecture adverse a mesuré ─────────────────
    def _queries_of_the_drawing(self, plan):
        self.env.flush_all()
        self.env.invalidate_all()
        before = self.env.cr.sql_log_count
        plan.rendu()
        return self.env.cr.sql_log_count - before

    def test_the_drawing_does_not_pay_per_shape_for_what_stands_on_it(self):
        """the three hooks are called one shape at a time by `rendu`, which
        the editor runs after every gesture. What they say of the fixtures is computed for
        the whole plan at once: the cost must not grow with the number of shapes."""
        plan = self.env["bf.floorplan"].create({
            "name": "Big store", "largeur": 5000.0, "profondeur": 5000.0,
            "company_id": self.company.id})
        shapes = self.env["bf.floorplan.element"].create([{
            "plan_id": plan.id, "name": f"G{i}", "genre": "gondole",
            "x": 10.0 + (i % 10) * 200.0, "y": 10.0 + (i // 10) * 200.0}
            for i in range(40)])
        bare = self._queries_of_the_drawing(plan)
        for shape in shapes:
            fixture = self.fixture(name=f"Aisle {shape.name}", floorplan_element_id=shape.id)
            self.filled(fixture, facings=20 if shape.name == "G3" else 2)
        dressed = self._queries_of_the_drawing(plan)
        _logger.info("MESURE rendu de 40 formes : %s requêtes sans meuble, %s avec",
                     bare, dressed)
        self.assertLessEqual(dressed - bare, 12,
                             "le pont coûte un nombre fixe de requêtes, pas une par forme")
        drawn = {e["nom"]: e for e in plan.rendu()["elements"]}
        self.assertEqual(drawn["G3"]["teinte"], "alerte", "le lot dit la même chose")
        self.assertEqual(drawn["G4"]["teinte"], "", "rempli, mesuré, pas encore gelé")

    def test_the_plan_cannot_change_store_under_its_fixtures(self):
        """the company of the PLAN, changed afterwards."""
        self.fixture()
        other = self.env["res.company"].create({"name": "Another store"})
        with self.assertRaises(ValidationError):
            self.plan.company_id = other

    def test_a_shape_cannot_carry_its_fixtures_to_the_plan_of_another_store(self):
        """the SHAPE moved to the plan of another store."""
        self.fixture()
        other = self.env["res.company"].create({"name": "Another store"})
        elsewhere = self.env["bf.floorplan"].create({
            "name": "Their floor", "largeur": 2000.0, "profondeur": 2000.0,
            "company_id": other.id})
        with self.assertRaises(ValidationError):
            self.gondola.plan_id = elsewhere

    def test_the_fixture_cannot_change_store_under_its_shape(self):
        """the company of the FIXTURE (the one door that held)."""
        fixture = self.fixture()
        other = self.env["res.company"].create({"name": "Another store"})
        with self.assertRaises(ValidationError):
            fixture.company_id = other

    def test_a_shared_plan_shows_each_store_only_its_own_fixtures(self):
        """a plan without a company is read by every store, and the fixtures on
        it are not. An employee of another store reads the plan and nothing of the aisle."""
        self.plan.company_id = False
        fixture = self.fixture()
        self.filled(fixture, facings=20)
        self.assertEqual(self.gondola._teinte(), "alerte")
        other = self.env["res.company"].create({"name": "Another store"})
        stranger = new_test_user(self.env, login="other_store", groups="base.group_user",
                                 company_id=other.id, company_ids=[Command.set(other.ids)])
        seen = self.gondola.with_user(stranger)
        self.assertEqual(seen.planogram_fixture_count, 0)
        self.assertEqual(seen._teinte(), "", "l'alerte d'un autre magasin ne se voit pas")
        self.assertIn("unit(s)", " · ".join(self.gondola.with_context(lang="en_US")._infos()),
                      "témoin : le magasin du meuble, lui, lit la capacité")
        self.assertNotIn("unit(s)", " · ".join(seen.with_context(lang="en_US")._infos()))

    def test_a_frozen_fixture_is_never_the_target_of_the_shape(self):
        """a fixture created frozen (create, import, server action) is a
        version, and the shape neither counts it nor opens it."""
        live = self.fixture()
        self.fixture(name="Created frozen", locked=True)
        self.assertEqual(self.gondola.planogram_fixture_count, 1)
        target = self.gondola._cible()
        self.assertTrue(target, "un seul meuble vivant : le clic l'ouvre")
        self.assertEqual(target["id"], live.id)

    def test_the_elevation_button_says_why_instead_of_an_access_error(self):
        """the count is a notice board, the elevation is not. Whoever cannot
        read the fixture is told so, not handed a raw AccessError."""
        self.fixture()
        reader = new_test_user(self.env, login="plan_reader", groups="base.group_user")
        with self.assertRaises(UserError) as caught:
            self.gondola.with_user(reader).action_open_planogram()
        self.assertNotIsInstance(caught.exception, AccessError,
                                 "AccessError hérite de UserError : on mesure la sorte")

    def test_the_red_of_a_fixture_survives_a_bridge_that_does_not_ask_super(self):
        """`bf_floorplan_hosting` answers '' for a device in good order without
        calling `super._teinte`, and it loads after this bridge. Simulated here by a
        hook that answers '' outright: the plan must still draw the shape red."""
        fixture = self.fixture()
        self.filled(fixture, facings=20)
        Element = type(self.env["bf.floorplan.element"])
        with patch.object(Element, "_teinte", lambda self: ""):
            self.assertEqual(self.gondola._teinte(), "", "témoin : le crochet avale bien")
            drawn = {e["id"]: e for e in self.plan.rendu()["elements"]}
        self.assertEqual(drawn[self.gondola.id]["teinte"], "alerte")

    # ── tour 2 du lot 3 : ce que la relecture adverse a mesuré ─────────────────
    def test_an_archived_fixture_still_holds_the_company_of_its_plan(self):
        """`search` sans `active_test=False` ne voit pas un meuble
        archivé, donc la société du PLAN pouvait changer au-dessus de lui. Même geste
        qu'au porte 1 (essai voisin), la seule différence étant l'archivage :
        c'est lui que cet essai mesure."""
        fixture = self.fixture()
        fixture.active = False
        self.assertFalse(self.gondola.planogram_fixture_ids,
                         "témoin : la lecture ordinaire ne voit plus le meuble")
        other = self.env["res.company"].create({"name": "Another store"})
        with self.assertRaises(ValidationError):
            self.plan.company_id = other

    def test_an_archived_fixture_still_blocks_its_shape_from_moving_store(self):
        """le o2m est lu en sudo, mais `active_test` restait allumé,
        et la FORME emportait le meuble archivé au plan d'un autre magasin."""
        fixture = self.fixture()
        fixture.active = False
        other = self.env["res.company"].create({"name": "Another store"})
        elsewhere = self.env["bf.floorplan"].create({
            "name": "Their floor", "largeur": 2000.0, "profondeur": 2000.0,
            "company_id": other.id})
        self.assertFalse(self.gondola.planogram_fixture_ids,
                         "témoin : la lecture ordinaire ne voit plus le meuble")
        with self.assertRaises(ValidationError):
            self.gondola.plan_id = elsewhere

    def test_a_fixture_is_checked_again_when_it_is_put_back_on_its_feet(self):
        """`active` n'était pas surveillé, donc remettre un
        meuble debout ne rejouait aucune garde et l'état fautif était PERMANENT. L'état
        fautif est écrit ici en SQL — un autre outil que celui qui mesure — parce que les
        deux portes refusent désormais de le produire par l'ORM."""
        fixture = self.fixture()
        other = self.env["res.company"].create({"name": "Another store"})
        fixture.active = False
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_planogram_fixture SET company_id = %s WHERE id = %s",
            (other.id, fixture.id))
        self.env.invalidate_all()
        self.assertEqual(fixture.company_id, other,
                         "témoin : l'état fautif est bien en base, et archivé")
        self.assertFalse(fixture.active)
        with self.assertRaises(ValidationError):
            fixture.active = True

    def test_the_plan_keeps_the_worst_of_the_two_colours_and_not_the_planograms(self):
        """`or` est un gagnant unique. Un meuble gelé et net répond « ok », la
        plus DOUCE des couleurs, et elle avalait l'alerte du module d'en dessous — dans
        `_teinte` **et** dans `to_dict`, qui ne relève que sa propre teinte et ne
        répare donc pas ce qu'un `or` a déjà jeté. Le crochet remplacé est celui de la
        classe de BASE : la couche d'en dessous du pont, pas celle d'au-dessus (le 
        est au-dessus, et prend déjà le pire)."""
        fixture = self.fixture()
        self.filled(fixture)
        fixture.action_freeze()
        self.assertEqual(self.gondola._teinte_planogramme(), "ok",
                         "témoin : le planogramme dit « ok », la plus douce des couleurs")
        with patch.object(BfFloorplanElement, "_teinte", lambda self: "alerte"):
            self.assertEqual(self.gondola._teinte(), "alerte",
                             "le pire l'emporte, pas le premier qui parle")
            drawn = {e["id"]: e for e in self.plan.to_dict()["elements"]}
        self.assertEqual(drawn[self.gondola.id]["teinte"], "alerte",
                         "et les trois sorties lisent ce dict")
        # L'autre sens : quand c'est le planogramme qui est le pire, il l'emporte aussi.
        fixture.shelf_ids.position_ids.facings = 20
        self.assertTrue(fixture.overflow)
        with patch.object(BfFloorplanElement, "_teinte", lambda self: "attention"):
            self.assertEqual(self.gondola._teinte(), "alerte")
