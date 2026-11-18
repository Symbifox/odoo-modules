# -*- coding: utf-8 -*-
"""The drawing, in data: what the screen and the PDF both read.

One geometry, two renderings. The client recalculates nothing: it receives centimetres and
draws them, exactly as the floor plans do. That is what keeps the printed plan and the plan
on screen the same plan.

The elevation has no free coordinates, and that is on purpose: a shelf is a comb, not a
canvas. A position's x is derived from its order on the shelf and from the widths before it,
so moving a product is reordering a list, never dragging a box. The constraint is the
feature: it is what makes the width left, and therefore the capacity, computable at all.

🔴 One honest compromise, named here because it is visible: a position whose product has no
width is DRAWN at a nominal 8 cm per facing, hatched. Drawn at its real zero it would be
invisible, and an invisible position is worse than a nominal one. No number ever uses that
nominal: `used_width`, `free_width`, `fill_pct` and `capacity` are computed from the records
alone, which is why the drawing also carries the count of unmeasured positions.
"""
from odoo import _, fields, models
from odoo.tools import float_compare, float_round

from .fixture import CM
from .kinds import label_of_kind

# What an unmeasured facing is drawn at, and nothing more than drawn.
NOMINAL_WIDTH = 8.0
NOMINAL_HEIGHT = 20.0

# The thickness of a shelf board on the drawing, in centimetres of the plan.
BOARD = 2.0

# 🔴 The page the plan is printed on, in CSS pixels at the 96 dpi of its paperformat: a
# letter in landscape leaves ~993 px between its margins, and ~560 px of height once the
# title and the legend have taken theirs. The drawing is fitted in THAT box, the WIDTH
# included, and that is the whole of a height frozen at 380 px drew the default
# gondola (122 × 183) 248 px wide on a 993 px page -- a quarter of the paper, two thirds of
# it white -- and shrank its lettering to 2.40 pt measured in the PDF.
PAPER_WIDTH = 993.0
PAPER_HEIGHT = 560.0

# 🔴 And the letters of the drawing are never smaller than the letters of the table under
# them, which the report sets in 11 px. The font travels in units of the PLAN, so what it
# must never fall under is a size on PAPER: it is read back through the scale the page
# gives the box, instead of being guessed from the width alone (`plan_width / 70`, floored
# at 2.4 units, is what measured 2.40 pt).
MIN_FONT_PX = 11.0


class BfPlanogramFixtureRendering(models.Model):
    _inherit = "bf.planogram.fixture"

    def to_dict(self):
        """The geometry and the facts, with nothing in it that depends on a screen."""
        self.ensure_one()
        # Shelves bottom to top, so that the gap above each one is the next one's height.
        shelves = self.shelf_ids.sorted(key=lambda s: s.height_from_floor)
        heights = [s.height_from_floor for s in shelves]
        drawn = []
        # 🔴 What the drawing really SPANS, which is not always the fixture: a usable width
        # typed in millimetres, or a stack taller than the fixture, used to be cropped by the
        # viewBox -- half a drawing, without a word. Nothing is refused
        # (decision 4): it is flagged, and it is drawn whole.
        plan_width = self.width
        plan_height = self.height
        for index, shelf in enumerate(shelves):
            # 🔴 The next shelf STRICTLY above, and not the next one in the list: two shelves
            # at the same height gave the lower one a gap of ZERO, and `gap > 0` then switched
            # « too tall » off for every position on it. A 150 cm position
            # standing 70 cm past the top of the fixture said nothing at all.
            above = next((height_above for height_above in heights[index + 1:]
                          if float_compare(height_above, shelf.height_from_floor,
                                           precision_digits=CM) > 0), self.height)
            gap = max(above - shelf.height_from_floor, 0.0)
            x = 0.0
            # 🔴 Two accumulators, and that is the whole of n° 4: `x` carries the nominal, so
            # a hatched box stays visible and does not sit under the next one, and `taken`
            # carries the centimetres that EXIST. `outside` is read from `taken`, like every
            # number of the module, so the drawing stops crying « past the end of the shelf »
            # on a shelf the figures report as 90 cm free.
            taken = 0.0
            positions = []
            # 🔴 Trie ICI, et pas sur la foi de `_order` du modele : la valeur d'un o2m
            # en cache garde l'ordre du dernier READ, et ecrire `sequence` sur une position
            # n'invalide que `sequence`. Le dessin servait donc l'ordre d'AVANT le
            # reordonnancement, dans la meme transaction. Mesuré sur une base d'essai.
            for position in shelf.position_ids.sorted(key=lambda p: (p.sequence, p.id)):
                width = position.used_width
                nominal = width <= 0.0
                if nominal:
                    width = NOMINAL_WIDTH * max(position.facings, 1)
                height = position._drawn_unit_height() * max(position.stacked, 1)
                if height <= 0.0:
                    height = NOMINAL_HEIGHT
                positions.append(dict(
                    id=position.id,
                    # 🔴 The label and the code of the FREEZE on a frozen version (,
                    # n° 7): a version is evidence, and a plan reprinted after the product
                    # was renamed no longer named what stood on the shelf. Same pattern as
                    # the widths, same copies, written once by `action_freeze`.
                    product=position._drawn_product_name(),
                    code=position._drawn_product_code(),
                    facings=position.facings, deep=position.deep,
                    stacked=position.stacked, capacity=position.capacity,
                    x=float_round(x, 1), w=float_round(width, 1),
                    h=float_round(height, 1),
                    measured=position.measured, nominal=nominal,
                    # Past the end of the board: the aisle cannot hold it. Read from the
                    # centimetres that exist, never from the nominal -- a hatched box takes
                    # no real width, so it neither IS outside nor pushes anything outside.
                    outside=not nominal and float_compare(
                        taken + width, shelf.usable_width, precision_digits=CM) > 0,
                    # Taller than the gap to the shelf above: it does not go under it.
                    # 🔴 No `gap > 0` any more: a shelf posed AT the top of its
                    # fixture -- which the constraint allows, it compares in `>` -- has no
                    # room above it at all, and `gap > 0` then switched the flag off for
                    # everything on it. A 150 cm stack rose 150 cm past the top of the
                    # fixture without a word. A gap of zero holds nothing, not everything.
                    too_tall=float_compare(height, gap, precision_digits=CM) > 0,
                ))
                x += width
                if not nominal:
                    taken += width
                plan_width = max(plan_width, x)
                plan_height = max(plan_height, shelf.height_from_floor + height)
            plan_width = max(plan_width, shelf.usable_width)
            # 🔴 The board itself, EMPTY or not: `plan_height` used to grow
            # only under a position, so a shelf left hanging above the top of its fixture
            # -- the height of the fixture having been lowered under it -- fell outside the
            # viewBox and was cropped in silence, on the screen and on the paper, while it
            # still figured in the table of the PDF. The drawing shows what it counts.
            plan_height = max(plan_height, shelf.height_from_floor)
            drawn.append(dict(
                id=shelf.id, name=shelf.name or "",
                label=shelf.display_name,
                summary=_(
                    "%(usable)s cm usable, %(left)s left, %(capacity)s unit(s)",
                    usable=float_round(shelf.usable_width, 1),
                    left=float_round(shelf.free_width, 1), capacity=shelf.capacity),
                height=float_round(shelf.height_from_floor, 1),
                # The SAME string on the screen and on the paper (forme, a
                # float printed by QWeb read « 200.0 » and the same number printed by OWL
                # read « 200 ». The geometry stays a number; what is WRITTEN is built once,
                # here, like every other label of the drawing.
                height_label="%s" % float_round(shelf.height_from_floor, 1),
                gap=float_round(gap, 1),
                usable_width=float_round(shelf.usable_width, 1),
                usable_depth=float_round(shelf.usable_depth, 1),
                tilt=float_round(shelf.tilt, 1), max_load=float_round(shelf.max_load, 1),
                used_width=float_round(shelf.used_width, 1),
                free_width=float_round(shelf.free_width, 1),
                fill_pct=float_round(shelf.fill_pct, 1),
                capacity=shelf.capacity, overflow=shelf.overflow,
                unmeasured=shelf.unmeasured_count,
                wider=shelf.wider_than_fixture,
                board=BOARD, positions=positions,
            ))
        # The letters, and the box they live in, are sized ONCE here, from what is really
        # drawn. The label is CUT here too: the screen added an ellipsis and the paper did
        # not, so the same box read « Canned bea… » on one and « Canned bea » on the other.
        margin = max(plan_width * 0.04, 8.0)
        # The box the drawing really occupies, margins included, and the scale the page
        # gives it: `meet` keeps the proportions, so the drawing touches the width or the
        # height of the page and never a quarter of it.
        box_width = (plan_width + 2 * margin) or 1.0
        box_height = (plan_height + 2 * margin) or 1.0
        scale = min(PAPER_WIDTH / box_width, PAPER_HEIGHT / box_height)
        font = MIN_FONT_PX / scale
        for board in drawn:
            for drawn_position in board["positions"]:
                text = drawn_position["code"] or drawn_position["product"]
                room = int(drawn_position["w"] / (font * 0.55))
                drawn_position["label"] = (
                    "%s…" % text[:max(room - 1, 1)] if len(text) > room else text)
                drawn_position["label_fits"] = float_compare(
                    drawn_position["w"], font * 2.2, precision_digits=CM) >= 0
        # Top to bottom for the drawing, which is how the fixture is read standing in
        # front of it.
        drawn.reverse()
        # One sentence, built here: the screen and the PDF both print it as it is.
        summary = _(
            "%(width)s × %(height)s × %(depth)s cm · %(shelves)s shelf/shelves · "
            "%(positions)s position(s) · %(capacity)s unit(s) of capacity · "
            "%(linear)s cm of linear shelf, %(filled)s %% filled",
            width=float_round(self.width, 1), height=float_round(self.height, 1),
            depth=float_round(self.depth, 1), shelves=self.shelf_count,
            positions=self.position_count, capacity=self.capacity,
            linear=float_round(self.linear_width, 1),
            filled=float_round(self.fill_pct, 1))
        return dict(
            fixture_id=self.id, title=self.display_name, summary=summary,
            kind=self.kind, kind_label=label_of_kind(self.kind),
            aisle=self.aisle or "", code=self.code or "",
            width=float_round(self.width, 1), height=float_round(self.height, 1),
            depth=float_round(self.depth, 1),
            linear_width=float_round(self.linear_width, 1),
            used_width=float_round(self.used_width, 1),
            free_width=float_round(self.free_width, 1),
            fill_pct=float_round(self.fill_pct, 1),
            capacity=self.capacity, position_count=self.position_count,
            product_count=self.product_count, shelf_count=self.shelf_count,
            overflow=self.overflow, unmeasured=self.unmeasured_count,
            # One geometry, one box: both renderings read this instead of each doing the
            # margin arithmetic their own way. `plan_*` is what is drawn, which is the
            # fixture unless a shelf or a stack goes past it; y runs DOWN from the top of
            # what is drawn, so a stack taller than the fixture starts above zero.
            wide_shelf=self.wide_shelf,
            plan_width=float_round(plan_width, 1),
            plan_height=float_round(plan_height, 1),
            margin=float_round(margin, 1), font_size=float_round(font, 2),
            # What the PAPER draws the box at, in pixels, computed here with the font that
            # goes in it: the template used to divide a frozen 380 px by the ratio and
            # never read the width of the page at all.
            paper_px_width=int(box_width * scale),
            paper_px_height=int(box_height * scale),
            view_box="%g %g %g %g" % (
                -margin, -(margin + plan_height - self.height),
                plan_width + 2 * margin, plan_height + 2 * margin),
            frozen=self.locked, frozen_label=self._frozen_label(),
            company=self.company_id.display_name or "",
            shelves=drawn,
        )

    def rendu(self):
        """Deliberate RPC surface: this is what the OWL component calls.

        It is read only. There is no handle to offer, frozen or not, because there is
        nothing on the elevation a mouse could move: an order and a width are fields.
        """
        self.ensure_one()
        data = self.to_dict()
        data.update(
            editable=not self.locked,
            empty=_("Add a shelf, then put a product on it: the drawing follows the "
                    "records.") if not self.shelf_ids else "",
            printed_on=fields.Date.to_string(fields.Date.context_today(self)),
        )
        return data
