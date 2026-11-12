"""The gap in metres, what the person is told about it, and what the coordinate opens.

⚠️ Alice sits at 45.52 / -73.58 in the fixtures, which is exactly ``HERE``: a mark made
there has a gap of 0 m, and that is a real answer, not a missing one. ``position_check``
is what tells a true zero from nothing to compare.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.bf_route.models import bf_route_day as day_model

from .common import MONDAY, RouteCase

HERE = {"latitude": 45.52, "longitude": -73.58, "accuracy": 12}
# About 1.4 km north east of Alice, well beyond the 150 m floor.
AWAY = {"latitude": 45.531, "longitude": -73.572, "accuracy": 9}
# At Alice's very door, but a phone that admits it could be 400 m out.
VAGUE = {"latitude": 45.52, "longitude": -73.58, "accuracy": 400}
# About 160 m from Alice, with a tight margin: the honest "away" is marginal here.
JUST_OUT = {"latitude": 45.5214, "longitude": -73.58, "accuracy": 150}


class PositionCase(RouteCase):
    """Positions on, notice read: the state every test below starts from."""

    def setUp(self):
        super().setUp()
        self.company.bf_route_position_mode = "stops"
        self.env["bf.route.notice.ack"].with_user(self.worker).app_acknowledge(
            self.company.bf_route_notice_version)

    def mark(self, position=HERE, partner=None, state="done", on=None):
        # ⚠️ A route has one day per date (UNIQUE(route_id, date)): a test that marks twice
        # asks for a second date, it does not get a second day on the same one.
        day = self.make_day(on) if on else self.make_day()
        self.start(day)
        stop = day.stop_ids.sorted("sequence")[0]
        if partner is not None:
            stop.with_user(self.manager).partner_id = partner
        stop.with_user(self.worker).app_mark(state, position=position)
        return stop


@tagged("post_install", "-at_install")
class TestPositionGap(PositionCase):

    def test_a_mark_at_the_place_is_near_with_its_gap(self):
        stop = self.mark()
        self.assertEqual(stop.position_check, "near")
        self.assertEqual(stop.position_gap_m, 0)

    def test_a_mark_away_from_the_place_says_how_far(self):
        stop = self.mark(AWAY)
        self.assertEqual(stop.position_check, "far")
        self.assertGreater(stop.position_gap_m, 1000)
        self.assertLess(stop.position_gap_m, 2000)

    def test_a_reading_at_the_door_with_a_wide_margin_is_not_dressed_in_green(self):
        """🔴 The gap is 0 and the phone admits it could be 400 m out. That is not proof.

        The earlier form compared the gap alone to the 150 m block and only then to the
        margin, so it was lax in one direction and strict in the other. This very reading
        came out green "At the place", while the module simultaneously REFUSED to move a pin
        on a map with it (``action_propose_place`` caps accuracy at 50 m). Two standards on
        one number, and the strict one protected the customer's record while the lax one
        judged the person.
        """
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co"})
        stop = self.mark(VAGUE)
        self.assertEqual(stop.position_check, "vague")
        self.assertEqual(stop.position_gap_m, 0, "the centre really is at the door")
        with self.assertRaises(UserError):
            self.mark(VAGUE, partner=nowhere,
                      on=MONDAY + timedelta(days=7)).with_user(
                          self.manager).action_propose_place()

    def test_the_more_precise_reading_never_gets_the_worse_verdict(self):
        """🔴 It did: 151 m with a 150 m margin was orange, 120 m with 3 km was green.

        A reading is a disc. The three answers are about that disc against the block, so
        they are symmetric, and a badge that claims something in either direction has to be
        earned by the margin as well as by the distance.
        """
        tight = self.mark(JUST_OUT)
        loose = self.mark(dict(AWAY, accuracy=4000), on=MONDAY + timedelta(days=7))
        self.assertGreater(loose.position_gap_m, tight.position_gap_m,
                           "the loose reading is the one further away")
        self.assertEqual(tight.position_check, "vague",
                         "its own margin reaches the block, so it says nothing")
        self.assertEqual(loose.position_check, "vague",
                         "and neither does a 1.4 km gap a phone is unsure of by 4 km")
        self.assertNotEqual(tight.position_check, "far",
                            "the most precise reading does not get the worst verdict")

    def test_a_gap_inside_the_phone_s_own_margin_is_neither_near_nor_far(self):
        """A 1.4 km reading the phone says it is unsure by 4 km proves nothing either way.

        ⚠️ It used to come out as "At the place", in green. That over-claims in the one
        direction that costs a person something: it hands them an alibi the data cannot
        support, and the day it is relied on against another reading it falls apart. "Away
        from the place" would over-claim the other way. So a state of its own, and a
        sentence that says why.
        """
        stop = self.mark(dict(AWAY, accuracy=4000))
        self.assertEqual(stop.position_check, "vague")
        self.assertGreater(stop.position_gap_m, 1000)
        told = stop.with_user(self.worker)._app_position()
        self.assertIn("a margin of 4,000 m", told["label"],
                      "the margin is named, and four figures are grouped")
        self.assertIn("neither that you were there nor that you were not", told["label"])

    def test_no_position_and_no_place_are_told_apart(self):
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co"})
        self.assertEqual(self.mark(position=None).position_check, "none")
        self.assertEqual(self.mark(partner=nowhere, on=MONDAY + timedelta(days=7)).position_check,
                         "unplaced")

    def test_the_gap_dies_with_the_position(self):
        stop = self.mark(AWAY)
        self.assertEqual(stop.position_check, "far")
        stop.done_at = fields.Datetime.now() - timedelta(days=91)
        self.env["bf.route.day.stop"]._purge_positions()
        self.assertFalse(stop.latitude)
        self.assertEqual(stop.position_gap_m, 0, "no coarser copy survives the purge")
        self.assertEqual(stop.position_check, "erased",
                         "and the person is still told a position had been read")

    def test_the_desk_columns_are_the_route_manager_s_alone(self):
        """🔴 The gap, its state and the margin leave by the ORM when they carry no groups.

        They are stored computed columns, so without field-level rights they are readable,
        SEARCHABLE and GROUPABLE by anyone the record rules let near the record. And those
        rules key on who HOLDS the day (``day_id.user_id``), not on who made the mark: the
        new holder of a reassigned day read "Away from the place, 2,043 m" plus the margin
        off the standard Route days screen, for a stop they never marked. Section 27 is a
        right over one's OWN information, and it is served by the sentence on the phone.
        """
        stop = self.mark(AWAY)
        mine = stop.with_user(self.worker)
        for field in ("latitude", "longitude", "position_gap_m", "position_check", "accuracy"):
            with self.assertRaises(AccessError, msg="%s left by the ORM" % field):
                mine.read([field])
        with self.assertRaises(AccessError, msg="the state enumerates by search"):
            self.env["bf.route.day.stop"].with_user(self.worker).search(
                [("position_check", "!=", "none")])
        # The positive witness, without which this test would pass on a broken feature.
        read = stop.with_user(self.manager).read(
            ["position_gap_m", "position_check", "accuracy"])[0]
        self.assertEqual(read["position_check"], "far")
        self.assertGreater(read["position_gap_m"], 1000)
        self.assertTrue(stop.with_user(self.worker)._app_position(),
                        "and the person still reads their own reading, in words")

    def test_the_sentence_carries_the_margin_the_column_no_longer_shows(self):
        """A gap without the margin it was measured with cannot be read honestly.

        The person does not get the columns, so the sentence has to carry all three: how
        far, what that means, and how sure the phone was.
        """
        told = self.mark(AWAY).with_user(self.worker)._app_position()
        self.assertEqual(told["state"], "far")
        self.assertGreater(told["gap_m"], 1000)
        self.assertEqual(told["accuracy"], 9)
        self.assertIn("To within 9 m.", told["label"])

    def test_a_longitude_of_exactly_zero_is_a_place_not_a_blank(self):
        """⚠️ ``0.0 == False`` in Python, so a guard written as ``value in (None, False)``
        reads the Greenwich meridian as a missing coordinate. Whether a point exists is the
        caller's business, which is where it is now tested, on both points.
        """
        greenwich = self.env["res.partner"].create({
            "name": "Greenwich Co", "partner_latitude": 45.52, "partner_longitude": 0.001})
        stop = self.mark({"latitude": 45.52, "longitude": 0.0, "accuracy": 5},
                         partner=greenwich)
        self.assertEqual(stop.position_check, "near",
                         "a reading on the meridian is a reading, not a blank")
        self.assertGreater(stop.position_gap_m, 50, "and its gap is really measured")

    def test_the_phone_is_told_about_its_own_position(self):
        stop = self.mark(AWAY)
        told = stop.with_user(self.worker)._app_position()
        self.assertEqual(told["state"], "far")
        self.assertEqual(told["accuracy"], 9)
        self.assertEqual(told["kept_days"], self.company.bf_route_position_days)
        self.assertNotIn("latitude", told, "the coordinates never reach the phone again")
        # Whole sentences, built by the server: a template that assembled them around
        # <t t-esc> exported as fragments nobody could translate.
        self.assertIn("m from the customer's place.", told["label"])
        self.assertIn("To within 9 m.", told["label"])
        # ⚠️ Through the helper, never "%s days": the sentence says "1 day" in the
        # singular, and a hard-coded plural here would pass only for the base's current
        # setting and fail the day a company declares another term.
        kept = self.env["bf.route.notice.ack"]._retention_text(
            self.company.bf_route_position_days)
        self.assertIn("Kept %s." % kept, told["label"])

    def test_the_person_is_told_the_position_was_erased(self):
        stop = self.mark(AWAY)
        stop.done_at = fields.Datetime.now() - timedelta(days=91)
        self.env["bf.route.day.stop"]._purge_positions()
        told = stop.with_user(self.worker)._app_position()
        self.assertEqual(told["state"], "erased")
        self.assertEqual(told["label"], "The position read here has been erased.")

    def test_a_mark_at_the_place_does_not_read_as_zero_metres_away(self):
        """"Read 0 m from the place" is true and unreadable: the near case says it plainly."""
        told = self.mark().with_user(self.worker)._app_position()
        self.assertIn("at the customer's place", told["label"])
        self.assertNotIn("0 m", told["label"], "and the zero is not printed either")

    def test_a_state_with_nothing_to_say_says_nothing(self):
        """🔴 "none" had no branch and fell into the "far" default, printing
        "Position read 0 m from the customer's place." about a stop with no reading at all.
        Unreachable from both callers today, which is why nobody saw it."""
        Stop = self.env["bf.route.day.stop"]
        self.assertEqual(Stop._position_sentence("none", 0, 0, 90), "")
        self.assertEqual(Stop._position_sentence(False, 0, 0, 90), "")

    def test_four_figure_metres_are_grouped(self):
        """"4000 m" beside "1 to 3,650 days" is two conventions in one module, and four
        figures arrive as soon as a phone reads badly indoors."""
        told = self.mark(dict(AWAY, accuracy=4000)).with_user(self.worker)._app_position()
        self.assertIn("4,000 m", told["label"])

    def test_the_phone_is_told_nothing_when_no_position_was_read(self):
        self.assertFalse(self.mark(position=None).with_user(self.worker)._app_position())

    def test_the_map_carries_the_read_positions(self):
        stop = self.mark(AWAY)
        data = stop.day_id.with_user(self.worker).app_map()
        self.assertEqual(len(data["read"]), 1)
        read = data["read"][0]
        self.assertEqual(read["stop_id"], stop.id)
        self.assertEqual(read["check"], "far")
        self.assertAlmostEqual(read["lat"], AWAY["latitude"])
        # The sentence comes built from the server. Assembled in the template out of loose
        # numbers, the tooltip printed "read at 0 m (± 0 m)" whenever there was nothing to
        # compare with and whenever the phone had reported no margin.
        self.assertIn("m from the customer's place.", read["note"])
        self.assertNotIn("gap_m", read, "the whole phrase replaces the loose numbers")
        stop.done_at = fields.Datetime.now() - timedelta(days=91)
        self.env["bf.route.day.stop"]._purge_positions()
        self.assertEqual(stop.day_id.with_user(self.worker).app_map()["read"], [],
                         "an erased position is left out, not drawn at (0, 0)")

    def test_the_road_distance_comes_from_the_positions(self):
        stop = self.mark(AWAY)
        second = stop.day_id.stop_ids.sorted("sequence")[1]
        second.with_user(self.worker).app_mark("done", position=HERE)
        with patch.object(type(self.env["bf.route.engine"]), "_route",
                          return_value={"distance": 12345.0, "duration": 900.0, "legs": []}), \
             patch.object(type(self.env["bf.route.engine"]), "_url", return_value="http://osrm"):
            stop.day_id.with_user(self.worker).app_finish()
        self.assertAlmostEqual(stop.day_id.distance_road_km, 12.3, places=1)

    def test_a_day_finishes_even_when_the_engine_is_down(self):
        stop = self.mark(AWAY)
        stop.day_id.stop_ids.sorted("sequence")[1].with_user(self.worker).app_mark(
            "done", position=HERE)
        with patch.object(type(self.env["bf.route.engine"]), "_route",
                          side_effect=UserError("no road")), \
             patch.object(type(self.env["bf.route.engine"]), "_url", return_value="http://osrm"):
            stop.day_id.with_user(self.worker).app_finish()
        self.assertEqual(stop.day_id.state, "done")
        self.assertFalse(stop.day_id.distance_road_km)

    def test_only_a_manager_measures_the_road(self):
        stop = self.mark(AWAY)
        with self.assertRaises(AccessError):
            stop.day_id.with_user(self.worker).action_road_distance()


@tagged("post_install", "-at_install")
class TestProposePlace(PositionCase):

    def test_a_position_becomes_the_place_of_a_customer_without_one(self):
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co"})
        stop = self.mark(partner=nowhere)
        stop.with_user(self.manager).action_propose_place()
        self.assertAlmostEqual(nowhere.bf_route_street_latitude, HERE["latitude"])
        self.assertAlmostEqual(nowhere.bf_route_street_longitude, HERE["longitude"])
        self.assertFalse(nowhere.partner_latitude,
                         "the street fallback, never the door: it plans the kilometres")
        self.assertTrue(nowhere.message_ids, "the contact says where its place came from")
        self.assertIn("read at the stop", nowhere.message_ids[0].body)

    def test_a_customer_already_placed_is_never_moved(self):
        stop = self.mark()
        with self.assertRaises(UserError):
            stop.with_user(self.manager).action_propose_place()

    def test_a_customer_placed_by_the_geocoder_is_never_moved_either(self):
        """🔴 A street point IS a place. The refusal used to look only at the door.

        The geocoder writes ``bf_route_street_*`` when it found the street but not the
        number, and an earlier proposal writes those same two fields. Testing only
        ``partner_latitude`` let a reading overwrite both without a word, which is how a
        point this module had itself placed got replaced on a test base by a reading taken
        196 km away. ``position_check == "unplaced"`` is now the single test, the very one
        the button's own condition uses.
        """
        nowhere = self.env["res.partner"].create({
            "name": "Street only Co", "bf_route_street_latitude": 45.60,
            "bf_route_street_longitude": -73.70})
        stop = self.mark(partner=nowhere)
        self.assertNotEqual(stop.position_check, "unplaced")
        with self.assertRaises(UserError):
            stop.with_user(self.manager).action_propose_place()
        self.assertAlmostEqual(nowhere.bf_route_street_latitude, 45.60,
                               msg="the point already placed stays where it was")

    def test_an_erased_position_cannot_become_a_place(self):
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co"})
        stop = self.mark(partner=nowhere)
        stop.done_at = fields.Datetime.now() - timedelta(days=91)
        self.env["bf.route.day.stop"]._purge_positions()
        self.assertEqual(stop.position_check, "erased")
        with self.assertRaises(UserError) as caught:
            stop.with_user(self.manager).action_propose_place()
        # The message has to name the case: "already has a place" would be false here, and
        # the person reading it would go looking for a place that does not exist.
        self.assertIn("erased", str(caught.exception))
        self.assertFalse(nowhere.bf_route_street_latitude)

    def test_the_contact_carries_the_date_its_place_came_from_a_reading(self):
        """⚠️ Without it, the gap shown at a stop compares a reading with a reading.

        Reading the gap and placing a customer from a reading undercut each other: once the
        place came from a reading taken at that same door, the gap against it is near zero
        by construction and confirms nothing at all. The office cannot be asked to remember
        which places came from where.
        """
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co"})
        stop = self.mark(partner=nowhere)
        self.assertFalse(nowhere.bf_route_street_from_position)
        stop.with_user(self.manager).action_propose_place()
        self.assertTrue(nowhere.bf_route_street_from_position)

    def test_accepting_a_place_stops_every_stop_of_that_customer_claiming_anything(self):
        """🔴 One click of the office re-judged stops nobody touched, in both directions.

        Measured on a test base with two visits to the same unplaced customer, 178 m apart:
        accepting the first reading turned that stop green "at the place, 0 m", a verdict
        the click itself manufactured, and turned the second stop orange "away from the
        place, 178 m" although nobody touched it and someone else may have marked it. The
        verdict spread to every stop of that customer, past and future, and outlived the
        erasure of the reading it was measured against.

        The honest answer is that neither stop proves anything now: the place and the
        reading came from the same kind of phone at the same door.
        """
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co"})
        first = self.mark(partner=nowhere)
        second = self.mark({"latitude": 45.5216, "longitude": -73.58, "accuracy": 20},
                           partner=nowhere, on=MONDAY + timedelta(days=7))
        self.assertEqual(first.position_check, "unplaced")
        self.assertEqual(second.position_check, "unplaced")
        first.with_user(self.manager).action_propose_place()
        self.assertEqual(first.position_check, "from_reading",
                         "the stop that was accepted claims nothing by itself")
        self.assertEqual(second.position_check, "from_reading",
                         "and neither does the stop nobody touched")
        self.assertGreater(second.position_gap_m, 100, "the gap is still measured and shown")
        told = second.with_user(self.worker)._app_position()
        self.assertIn("compare a reading with a reading", told["label"])
        # And it survives the erasure of the reading the place came from.
        first.sudo().done_at = fields.Datetime.now() - timedelta(days=91)
        self.env["bf.route.day.stop"]._purge_positions()
        self.assertEqual(first.position_check, "erased")
        self.assertEqual(second.position_check, "from_reading",
                         "the place is still a reading, even once its source is gone")

    def test_a_customer_the_geocoder_later_places_is_an_independent_reference_again(self):
        """The door wins over a stale street point, whatever the stamp still says."""
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co"})
        stop = self.mark(partner=nowhere)
        stop.with_user(self.manager).action_propose_place()
        self.assertEqual(stop.position_check, "from_reading")
        nowhere.write({"partner_latitude": 45.52, "partner_longitude": -73.58})
        self.assertTrue(nowhere.bf_route_street_from_position, "the stamp is still there")
        self.assertEqual(stop.position_check, "near",
                         "but the comparison is against the door now")

    def test_the_stamp_goes_with_the_point_when_the_customer_moves(self):
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co", "city": "Laval"})
        stop = self.mark(partner=nowhere)
        stop.with_user(self.manager).action_propose_place()
        # Not with_user(manager): a route manager has no right to edit a contact, and what
        # is under test is that the coordinates follow the address, not who may move it.
        nowhere.write({"city": "Québec"})
        self.assertFalse(nowhere.bf_route_street_latitude,
                         "coordinates follow the address, as they already did")
        self.assertFalse(nowhere.bf_route_street_from_position,
                         "and so does what says where they came from")

    def test_a_vague_reading_is_refused(self):
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co"})
        stop = self.mark(VAGUE, partner=nowhere)
        with self.assertRaises(UserError):
            stop.with_user(self.manager).action_propose_place()
        self.assertFalse(nowhere.bf_route_street_latitude)

    def test_a_reading_without_accuracy_is_refused(self):
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co"})
        stop = self.mark({"latitude": 45.52, "longitude": -73.58}, partner=nowhere)
        with self.assertRaises(UserError):
            stop.with_user(self.manager).action_propose_place()

    def test_a_worker_cannot_propose_a_place(self):
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co"})
        stop = self.mark(partner=nowhere)
        with self.assertRaises(AccessError):
            stop.with_user(self.worker).action_propose_place()


@tagged("post_install", "-at_install")
class TestRoadMeasured(PositionCase):
    """What the figure printed in the kilometre register actually measures."""

    def mark_out_of_order(self):
        """Three stops marked third, first, second, with distinct marking times.

        ⚠️ The times are written by hand: three marks in the same second give three equal
        ``done_at`` values, and the ordering under test would then be decided by the ids,
        which is the planned order again.
        """
        day = self.make_day()
        self.start(day)
        planned = day.stop_ids.sorted("sequence")
        order = [planned[2], planned[0], planned[1]]
        places = [{"latitude": 45.49, "longitude": -73.55, "accuracy": 10},
                  {"latitude": 45.52, "longitude": -73.58, "accuracy": 10},
                  {"latitude": 45.53, "longitude": -73.60, "accuracy": 10}]
        base = fields.Datetime.now() - timedelta(hours=4)
        for rank, (stop, place) in enumerate(zip(order, places)):
            stop.with_user(self.worker).app_mark("done", position=place)
            stop.sudo().done_at = base + timedelta(minutes=20 * rank)
        return day, order, places

    def test_the_road_is_measured_in_the_order_the_stops_were_marked(self):
        """🔴 The field's help, the README and the printed register all promise this order.

        The measurement took ``sequence``, the order the OFFICE planned. The two part ways
        as soon as a worker takes the stops differently, which is the ordinary day on a road
        with a closed street: measured on a three-stop day done out of order, the planned
        order gave 31 % more than the road actually driven. The figure is printed in the
        kilometre register, so the promise has to be the one that is kept.
        """
        day, order, places = self.mark_out_of_order()
        seen = []

        def capture(engine, points, **kwargs):
            seen.append([list(point) for point in points])
            return {"distance": 1000.0, "duration": 60.0, "legs": []}

        with patch.object(type(self.env["bf.route.engine"]), "_route", capture), \
             patch.object(type(self.env["bf.route.engine"]), "_url", return_value="http://osrm"):
            day.with_user(self.manager).action_road_distance()
        self.assertEqual(len(seen), 1, "one measurement, one request")
        # The first and last points are the depot, which is a point of the route and not a
        # reading: what is under test here is the order of the readings between them.
        measured = [round(point[1], 4) for point in seen[0][1:-1]]
        self.assertEqual(measured, [round(place["latitude"], 4) for place in places],
                         "the order the stops were marked")
        planned = [round(stop.sudo().latitude, 4) for stop in day.stop_ids.sorted("sequence")]
        self.assertNotEqual(measured, planned,
                            "and the fixture really does distinguish the two orders")

    def test_the_measured_road_covers_the_day_and_not_only_the_stops(self):
        """🔴 It covered first mark to last mark, while the odometer covers the whole day.

        No position is read at the depot, so the figure omitted the way out and the way
        back. Measured on a test base: 31.0 km between the marked stops against 84.6 km for
        the same day, so the register printed the odometer 173 % too high on an honest day,
        under a note inviting the office to compare the two.
        """
        day, order, places = self.mark_out_of_order()
        seen = []

        def capture(engine, points, **kwargs):
            seen.append([list(point) for point in points])
            return {"distance": 1000.0, "duration": 60.0, "legs": []}

        with patch.object(type(self.env["bf.route.engine"]), "_route", capture), \
             patch.object(type(self.env["bf.route.engine"]), "_url", return_value="http://osrm"):
            day.with_user(self.manager).action_road_distance()
        points = seen[0]
        self.assertEqual(len(points), 5, "depot, three stops, depot")
        depot = [self.depot.partner_longitude, self.depot.partner_latitude]
        self.assertEqual([round(c, 4) for c in points[0]], [round(c, 4) for c in depot])
        self.assertEqual([round(c, 4) for c in points[-1]], [round(c, 4) for c in depot])

    def test_a_day_whose_depot_is_nowhere_is_still_measured(self):
        """A depot nobody located is simply absent, like anywhere else in this module."""
        day, order, places = self.mark_out_of_order()
        self.depot.write({"partner_latitude": 0.0, "partner_longitude": 0.0})
        seen = []

        def capture(engine, points, **kwargs):
            seen.append([list(point) for point in points])
            return {"distance": 1000.0, "duration": 60.0, "legs": []}

        with patch.object(type(self.env["bf.route.engine"]), "_route", capture), \
             patch.object(type(self.env["bf.route.engine"]), "_url", return_value="http://osrm"):
            day.with_user(self.manager).action_road_distance()
        self.assertEqual(len(seen[0]), 3, "the three marked stops, and nothing invented")

    def test_the_measured_road_outlives_the_positions_it_came_from(self):
        """The printed note used to claim the column empties with them. It does not, and
        three other texts of the lot say so: it is a distance, not a place."""
        day, order, places = self.mark_out_of_order()
        with patch.object(type(self.env["bf.route.engine"]), "_route",
                          return_value={"distance": 12345.0, "duration": 900.0, "legs": []}), \
             patch.object(type(self.env["bf.route.engine"]), "_url", return_value="http://osrm"):
            day.with_user(self.manager).action_road_distance()
        self.assertAlmostEqual(day.distance_road_km, 12.3, places=1)
        day.stop_ids.sudo().write({"done_at": fields.Datetime.now() - timedelta(days=91)})
        self.env["bf.route.day.stop"]._purge_positions()
        self.assertFalse(day.stop_ids.sudo()[0].latitude, "the positions did leave")
        self.assertAlmostEqual(day.distance_road_km, 12.3, places=1,
                               msg="and the distance stayed, as every other text says")

    def test_the_map_still_draws_the_stops_in_the_planned_order(self):
        """The map numbers the stops as the office planned them: that is what a rank is."""
        day, order, places = self.mark_out_of_order()
        read = day.with_user(self.manager).app_map()["read"]
        self.assertEqual([entry["rank"] for entry in read], [1, 2, 3])
        self.assertEqual([round(entry["lat"], 4) for entry in read],
                         [round(stop.sudo().latitude, 4)
                          for stop in day.stop_ids.sorted("sequence")])

    def test_a_button_that_measured_nothing_says_so(self):
        """⚠️ Silence here reads as the measurement confirming the figure already shown.

        The routing engine ANSWERS in this test, on purpose: if it failed, the button would
        refuse for that reason instead and the guard under test ("it takes two positions
        read") would never be the cause.
        """
        day = self.make_day()
        self.start(day)
        day.stop_ids.sorted("sequence")[0].with_user(self.worker).app_mark("done", position=HERE)
        with patch.object(type(self.env["bf.route.engine"]), "_route",
                          return_value={"distance": 9999.0, "duration": 600.0, "legs": []}), \
             patch.object(type(self.env["bf.route.engine"]), "_url", return_value="http://osrm"):
            with self.assertRaises(UserError):
                day.with_user(self.manager).action_road_distance()
        self.assertFalse(day.distance_road_km,
                         "and one position never produces a figure")

    def test_no_more_days_than_the_ceiling_are_measured_at_once(self):
        """One request to the routing engine per day: a selection of a thousand is refused.

        The constant is patched rather than the fixture grown to 201 days: what is under
        test is that a ceiling exists and is applied, not the number itself.
        """
        day = self.make_day()
        second = self.make_day(MONDAY + timedelta(days=7))
        with patch.object(day_model, "MAX_TO_MEASURE", 1):
            with self.assertRaises(UserError):
                (day | second).with_user(self.manager).action_road_distance()

    def test_the_automatic_path_stays_silent(self):
        """A day must be able to finish with one position, or none at all."""
        day = self.make_day()
        self.start(day)
        day.stop_ids.sorted("sequence")[0].with_user(self.worker).app_mark("done", position=HERE)
        with patch.object(type(self.env["bf.route.engine"]), "_url", return_value="http://osrm"):
            day.with_user(self.worker).app_finish()
        self.assertEqual(day.state, "done")
        self.assertFalse(day.distance_road_km)


@tagged("post_install", "-at_install")
class TestPositionRights(PositionCase):
    """Who may read a reading, and who may act on one.

    🔴 Both adversarial reviews reproduced the same hole: a public method that
    checked a GROUP and then went on in sudo. A group says what kind of work someone does,
    never which records they may touch, and the companies of one base may share their
    contacts.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_company = cls.env["res.company"].create({"name": "Elsewhere Inc"})
        cls.foreign_manager = cls.env["res.users"].create({
            "name": "Manager Elsewhere", "login": "route_manager_elsewhere",
            "company_id": cls.other_company.id,
            "company_ids": [(6, 0, [cls.other_company.id])],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("bf_route.group_route_manager").id])],
        })

    def test_a_manager_of_another_company_cannot_propose_a_place(self):
        """A shared contact is the way in: the stop is elsewhere, the contact is common."""
        nowhere = self.env["res.partner"].create({"name": "Unplaced Co"})
        stop = self.mark(partner=nowhere)
        with self.assertRaises(AccessError):
            stop.with_user(self.foreign_manager).action_propose_place()
        self.assertFalse(nowhere.bf_route_street_latitude,
                         "and nothing of the reading reached the contact")

    def test_a_manager_of_another_company_cannot_measure_a_day(self):
        stop = self.mark(AWAY)
        with self.assertRaises(AccessError):
            stop.day_id.with_user(self.foreign_manager).action_road_distance()

    def test_a_reassigned_day_does_not_hand_over_its_readings(self):
        """🔴 ``done_by`` holds the truth, the day's holder does not.

        The office reassigns a finished day when someone leaves or when a mistake is
        corrected. The screen then told the new holder "YOUR position was read 1,400 m from
        the customer's place" about a reading taken by someone else, and the map handed them
        that person's raw coordinates. The right of access of s. 27 is a right over one's
        own information.
        """
        stop = self.mark(AWAY)
        self.assertEqual(stop.sudo().done_by, self.worker)
        stop.day_id.with_user(self.manager).user_id = self.other
        self.assertFalse(stop.with_user(self.other)._app_position(),
                         "a reading someone else made says nothing to the new holder")
        self.assertEqual(stop.day_id.with_user(self.other).app_map()["read"], [],
                         "and it is not drawn on their map either")
        self.assertTrue(stop.with_user(self.worker)._app_position(),
                        "the person who made it still reads their own")
        # 🔴 The payloads were filtered and the ORM was not: the new holder read the gap,
        # its state and the margin straight off the standard screen.
        for field in ("position_gap_m", "position_check", "accuracy"):
            with self.assertRaises(AccessError, msg="%s reached the new holder" % field):
                stop.with_user(self.other).read([field])
        self.assertEqual(len(stop.day_id.with_user(self.manager).app_map()["read"]), 1,
                         "and the route manager still sees the day as it happened")

    def test_the_manager_s_map_does_not_say_your_position(self):
        stop = self.mark(AWAY)
        read = stop.day_id.with_user(self.manager).app_map()["read"][0]
        self.assertNotIn("Your position", read["note"])
        self.assertIn("Position read", read["note"])
        self.assertNotIn("Kept", read["note"],
                         "a retention period is the person's business, not a line on a map")

    def test_the_manager_s_own_screen_does_not_say_your_position_either(self):
        """The voice is chosen per reader, and the map is not the only place it shows."""
        stop = self.mark(AWAY)
        told = stop.with_user(self.manager)._app_position()
        self.assertTrue(told, "a route manager does read someone else's stop")
        self.assertNotIn("Your position", told["label"])
        self.assertIn("Position read", told["label"])
