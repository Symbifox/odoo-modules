# -*- coding: utf-8 -*-
"""The shelf: a height from the floor, a usable width, and what it adds up to.

A shelf is listed from the top down, because that is how the fixture is read when you stand
in front of it.

A shelf is not refused for holding more than it has. A planogram is built by moving things
around, and an editor that refused the intermediate state would force the aisle to be
emptied before it can be filled. What does not fit is COUNTED instead: `free_width` goes
negative and `overflow` says so, on the shelf, on the fixture and in the drawing.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare, float_round

from .fixture import CM

TILT_MAX = 45.0


class BfPlanogramShelf(models.Model):
    _name = "bf.planogram.shelf"
    _description = "Planogram shelf"
    # Top shelf first: the list reads like the fixture looks.
    _order = "fixture_id, height_from_floor desc, id"

    fixture_id = fields.Many2one(
        "bf.planogram.fixture", string="Fixture", required=True, index=True,
        ondelete="cascade")
    name = fields.Char(string="Shelf",
                       help="What the aisle calls it, if it calls it anything. "
                            "Ex.: « Eye level », « Bottom », « Over the cooler ».")
    company_id = fields.Many2one(
        related="fixture_id.company_id", store=True, index=True, string="Company")

    height_from_floor = fields.Float(string="Height from floor (cm)", default=0.0,
                                     digits=(16, 1), required=True)
    # Not required: a shelf created inline on a fixture that is not saved yet has no parent
    # to read, so `create` fills these from the fixture. The constraint below still holds.
    usable_width = fields.Float(string="Usable width (cm)", digits=(16, 1))
    usable_depth = fields.Float(string="Usable depth (cm)", digits=(16, 1))
    tilt = fields.Float(string="Tilt (°)", default=0.0, digits=(16, 1),
                        help="A bread rack or a produce shelf is tilted towards the aisle.")
    max_load = fields.Float(string="Load allowed (kg)", digits=(16, 1),
                            help="What the shelf is rated for. Nothing is refused on it: "
                                 "it is printed on the plan for whoever stocks it.")

    # copy=True pour la meme raison que `shelf_ids` : la copie d'une tablette emporte ce
    # qui est dessus, sinon le gel rendrait des tablettes nues.
    position_ids = fields.One2many("bf.planogram.position", "shelf_id", string="Positions",
                                   copy=True)

    position_count = fields.Integer(compute="_compute_space", string="Positions")
    capacity = fields.Integer(compute="_compute_space", string="Capacity (units)")
    used_width = fields.Float(compute="_compute_space", string="Taken (cm)", digits=(16, 1))
    free_width = fields.Float(compute="_compute_space", string="Left (cm)", digits=(16, 1))
    fill_pct = fields.Float(compute="_compute_space", string="Filled (%)", digits=(16, 1))
    overflow = fields.Boolean(compute="_compute_space", store=True, string="Overflowing")
    unmeasured_count = fields.Integer(compute="_compute_space", string="Without dimensions")

    # 🔴 Stored and flagged, never refused (decision 4 of ): 1220 typed where 122 cm was
    # meant multiplies the linear shelf by ten, and with it the space to sales ratio the
    # README promises lot 4, while the drawing showed half of the shelf and cropped the rest
    #. The width is not checked against the fixture by a constraint -- a shelf
    # narrower than its fixture is ordinary -- so this is the flag that says it out loud.
    wider_than_fixture = fields.Boolean(
        compute="_compute_wider_than_fixture", store=True, string="Wider than the fixture",
        help="The usable width typed here is wider than the fixture itself. In centimetres "
             "that is usually a measurement typed in millimetres.")

    @api.depends("usable_width", "fixture_id.width")
    def _compute_wider_than_fixture(self):
        for shelf in self:
            width = shelf.fixture_id.width
            shelf.wider_than_fixture = bool(width) and float_compare(
                shelf.usable_width, width, precision_digits=CM) > 0

    @api.depends("usable_width", "position_ids.used_width", "position_ids.capacity",
                 "position_ids.measured")
    def _compute_space(self):
        for shelf in self:
            positions = shelf.position_ids
            used = sum(positions.mapped("used_width"))
            shelf.position_count = len(positions)
            shelf.capacity = sum(positions.mapped("capacity"))
            shelf.used_width = used
            shelf.free_width = shelf.usable_width - used
            shelf.fill_pct = (float_round(used / shelf.usable_width * 100.0, 1)
                              if shelf.usable_width else 0.0)
            shelf.overflow = float_compare(used, shelf.usable_width, precision_digits=CM) > 0
            shelf.unmeasured_count = len(positions.filtered(lambda p: not p.measured))

    @api.depends("name", "height_from_floor", "fixture_id")
    def _compute_display_name(self):
        for shelf in self:
            height = _("%s cm from the floor") % float_round(shelf.height_from_floor, 1)
            shelf.display_name = "%s, %s" % (shelf.name, height) if shelf.name else height

    @api.constrains("usable_width", "usable_depth", "height_from_floor", "tilt", "max_load")
    def _check_dimensions(self):
        for shelf in self:
            if shelf.usable_width <= 0 or shelf.usable_depth <= 0:
                raise ValidationError(_("A shelf has a usable width and a usable depth, "
                                        "in centimetres, and both are positive."))
            if shelf.height_from_floor < 0:
                raise ValidationError(_("A shelf does not hang below the floor."))
            if not 0.0 <= shelf.tilt <= TILT_MAX:
                raise ValidationError(_("A shelf is tilted between 0 and %s degrees.")
                                      % int(TILT_MAX))
            if shelf.max_load < 0:
                raise ValidationError(_("A load allowed is not negative."))

    @api.constrains("height_from_floor", "fixture_id")
    def _check_inside_the_fixture(self):
        """A shelf above the top of its fixture is a typo, and it would draw outside the
        frame. The width is NOT checked the same way: a shelf narrower than its fixture is
        ordinary (an end panel, a cooler door), and a shelf wider than it is a measurement
        to fix, not a drawing to refuse."""
        for shelf in self:
            height = shelf.fixture_id.height
            if height and float_compare(shelf.height_from_floor, height,
                                        precision_digits=CM) > 0:
                raise ValidationError(_(
                    "A shelf at %(shelf)s cm does not fit in a fixture %(fixture)s cm high.",
                    shelf=float_round(shelf.height_from_floor, 1),
                    fixture=float_round(height, 1)))

    @api.model_create_multi
    def create(self, vals_list):
        fixtures = self.env["bf.planogram.fixture"].browse(
            [v["fixture_id"] for v in vals_list if v.get("fixture_id")])
        fixtures._assert_open(_("A shelf"))
        for vals in vals_list:
            fixture = self.env["bf.planogram.fixture"].browse(vals.get("fixture_id"))
            if not vals.get("usable_width"):
                vals["usable_width"] = fixture.width
            if not vals.get("usable_depth"):
                vals["usable_depth"] = fixture.depth
        return super().create(vals_list)

    def write(self, vals):
        # Both ends: the fixture it leaves and the one it would land in.
        self.fixture_id._assert_open(_("A shelf"))
        if vals.get("fixture_id"):
            self.env["bf.planogram.fixture"].browse(vals["fixture_id"])._assert_open(
                _("A shelf"))
        return super().write(vals)

    def unlink(self):
        self.fixture_id._assert_open(_("A shelf"))
        return super().unlink()
