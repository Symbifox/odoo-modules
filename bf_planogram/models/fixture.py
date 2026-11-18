# -*- coding: utf-8 -*-
"""The fixture: a run of shelves in centimetres, and the elevation it opens.

A planogram is an ELEVATION, not a floor plan: x runs along the fixture, y rises from the
floor. A floor plan says where the gondola stands; this says what sits on it, shelf by
shelf, facing by facing. The two geometries meet in a single link, and that link lives in
a bridge module (`bf_planogram_floorplan`), so a pharmacy, a hardware store or a library
keeps its shelves without installing office floor plans and employee records with them.

A fixture is always editable. What gets published is a VERSION: a frozen copy of the
fixture with its shelves and its positions, which never changes again. That is what a
compliance round counts against, and a round counted against a drawing that moved under it
measures nothing.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_round

from .company_language import in_company_language
from .kinds import label_of_kind, selection_kinds, size_of_kind

# A fixture is a run of shelving, not a building: beyond this it is a floor plan's job.
SIDE_MAX = 10000.0

# Centimetres, to the millimetre: every width comparison in the module goes through this.
# It does NOT absorb a sub-millimetre measurement -- `digits=(16, 1)` cannot hold one, so a
# position 0.4 mm too wide does not exist. What it absorbs is the float noise of ADDING
# tenths of a centimetre: a shelf's taken width is a sum of rounded widths, and 0.1 + 0.2 is
# not 0.3 in binary. Without it a full shelf reported itself as overflowing.
CM = 1

# What may still be written on a frozen version: archiving it, and what the chatter itself
# writes. Everything else is the drawing, and the drawing is what the freeze protects.
WRITABLE_WHEN_FROZEN = {
    "active", "message_main_attachment_id", "message_follower_ids", "message_ids",
    "message_partner_ids", "activity_ids",
}


class BfPlanogramFixture(models.Model):
    _name = "bf.planogram.fixture"
    _description = "Planogram fixture"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "company_id, aisle, sequence, name, version, id"

    name = fields.Char(string="Fixture", required=True, tracking=True)
    code = fields.Char(string="Reference", tracking=True,
                       help="What the shelf tag or the store map calls it. Ex.: G12.")
    kind = fields.Selection(selection_kinds(), string="Kind", required=True,
                            default="gondola", tracking=True)
    aisle = fields.Char(string="Aisle", tracking=True,
                        help="Ex.: « Aisle 3 », « Back wall », « Front till ».")
    sequence = fields.Integer(string="Order", default=10)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True, index=True,
        default=lambda self: self.env.company,
        help="The store. A cost series, a price and a planogram all belong to one store.")
    active = fields.Boolean(default=True)
    notes = fields.Html(string="Notes")

    # 🔴 No field default, and that is the other half of n° 5: 122 / 183 / 56 were the
    # gondola's size written a SECOND time, so every new fixture arrived already measured and
    # « fill only what is blank » would never have filled anything. The kind's table is the
    # one source of a fixture's scale, and `_onchange_kind` is what pours it in -- which is
    # also why a fixture created from code without its three dimensions is refused by
    # `_check_dimensions` instead of being silently posed as a gondola.
    width = fields.Float(string="Width (cm)", required=True, tracking=True)
    height = fields.Float(string="Height (cm)", required=True, tracking=True)
    depth = fields.Float(string="Depth (cm)", required=True, tracking=True)

    # 🔴 copy=True, et il le faut : `odoo/fields.py` dit « o2m are not copied by default »
    # (One2many.copy = False). Sans ça, geler une version rendait un meuble VIDE -- sans un
    # mot, sans une erreur, et avec sa capacite a zero : la version publiee aurait laisse
    # passer n'importe quelle tournee. Mesuré par l'essai du gel.
    shelf_ids = fields.One2many("bf.planogram.shelf", "fixture_id", string="Shelves",
                                copy=True)

    # --- the freeze -------------------------------------------------------------------
    locked = fields.Boolean(
        string="Frozen", default=False, readonly=True, copy=False, tracking=True,
        help="A frozen version is read and printed, never modified.")
    # 🔴 `set null`, and not `cascade`: a frozen version SURVIVES the deletion of the living
    # fixture it was frozen from. The manager carries `perm_unlink`, so one
    # deletion used to take every version with it -- and a version is what a compliance round
    # was counted against. What is lost is the link, which the deleted fixture no longer
    # needs; the version keeps its number, its date, who froze it and its whole drawing.
    origin_id = fields.Many2one(
        "bf.planogram.fixture", string="Version of", readonly=True, copy=False, index=True,
        ondelete="set null")
    version = fields.Integer(string="Version", readonly=True, copy=False)
    frozen_on = fields.Datetime(string="Frozen on", readonly=True, copy=False)
    frozen_by_id = fields.Many2one("res.users", string="Frozen by", readonly=True, copy=False)
    version_ids = fields.One2many("bf.planogram.fixture", "origin_id", string="Versions")

    # 🔴 L'unicite est posee par la BASE, et plus seulement par le code (hypothese du
    # ) : `action_freeze` numerote par `max(...) + 1` sans verrou, donc deux gels
    # concurrents lisent le meme maximum et posent le meme numero -- le par une
    # autre porte. Postgres ne compare pas les NULL, donc les meubles VIVANTS (origin_id
    # nul, version nulle) ne se genent pas entre eux : la contrainte ne porte que sur les
    # versions d'une meme origine.
    _sql_constraints = [
        ("version_number_unique", "UNIQUE (origin_id, version)",
         "That version number is already taken on this fixture: a round counted against "
         "« version 2 » must have one version 2 to read."),
    ]
    version_count = fields.Integer(compute="_compute_counts", string="Versions")

    # --- what the drawing adds up to ---------------------------------------------------
    shelf_count = fields.Integer(compute="_compute_counts", string="Shelves")
    position_count = fields.Integer(compute="_compute_counts", string="Positions")
    product_count = fields.Integer(compute="_compute_counts", string="Products")
    capacity = fields.Integer(
        compute="_compute_space", string="Capacity (units)",
        help="Facings times deep times stacked, over every position of the fixture.")
    linear_width = fields.Float(
        compute="_compute_space", string="Linear shelf (cm)", digits=(16, 1),
        help="The usable width of every shelf added up. This is the denominator of a "
             "space to sales ratio, and the reason a shelf has a usable width of its own.")
    used_width = fields.Float(compute="_compute_space", string="Taken (cm)", digits=(16, 1))
    free_width = fields.Float(compute="_compute_space", string="Left (cm)", digits=(16, 1))
    fill_pct = fields.Float(compute="_compute_space", string="Filled (%)", digits=(16, 1))
    overflow = fields.Boolean(
        compute="_compute_space", string="Overflowing", search="_search_overflow",
        help="At least one shelf holds more width than it has.")
    unmeasured_count = fields.Integer(
        compute="_compute_space", string="Without dimensions",
        search="_search_unmeasured_count",
        help="Positions whose product has no width: they take zero on the drawing, which "
             "is why they are counted here instead of being quietly treated as fitting.")
    wide_shelf = fields.Boolean(
        compute="_compute_space", string="A shelf wider than the fixture",
        help="At least one shelf carries a usable width wider than the fixture itself, "
             "which in centimetres is usually a measurement typed in millimetres. It is "
             "counted and drawn whole, never refused.")

    @api.depends("shelf_ids", "shelf_ids.position_ids", "shelf_ids.position_ids.product_id",
                 "version_ids")
    def _compute_counts(self):
        for fixture in self:
            positions = fixture.shelf_ids.position_ids
            fixture.shelf_count = len(fixture.shelf_ids)
            fixture.position_count = len(positions)
            fixture.product_count = len(positions.product_id)
            # Archived versions included: the button opens them all (active_test False).
            fixture.version_count = len(
                fixture.with_context(active_test=False).version_ids)

    @api.depends("shelf_ids.usable_width", "shelf_ids.used_width", "shelf_ids.capacity",
                 "shelf_ids.overflow", "shelf_ids.unmeasured_count",
                 "shelf_ids.wider_than_fixture")
    def _compute_space(self):
        for fixture in self:
            shelves = fixture.shelf_ids
            linear = sum(shelves.mapped("usable_width"))
            used = sum(shelves.mapped("used_width"))
            fixture.linear_width = linear
            fixture.used_width = used
            fixture.free_width = linear - used
            fixture.fill_pct = float_round(used / linear * 100.0, 1) if linear else 0.0
            fixture.capacity = sum(shelves.mapped("capacity"))
            fixture.overflow = any(shelves.mapped("overflow"))
            fixture.unmeasured_count = sum(shelves.mapped("unmeasured_count"))
            fixture.wide_shelf = any(shelves.mapped("wider_than_fixture"))

    def _search_unmeasured_count(self, operator, value):
        """🔴 Without this, « Without dimensions » was not a filter but a broken view: Odoo
        refuses a domain on a computed field that carries no search, and it refuses it at
        INSTALL time, with `ParseError: Champ impossible a rechercher`, not the first time
        someone clicks it. Measured on a test database.

        🔴 And it answers an ORDINARY domain on a count, not just « any or none »: it used to
        raise a `UserError` on `>= 0`, `= 1` and `> 1`, so a domain inherited from a parent
        action turned the list itself into an error page. The count of every
        fixture that holds any is read in ONE grouped query; every other fixture holds zero,
        and `0 <operator> value` decides them all without reading one of them."""
        tests = {"=": lambda c, v: c == v, "!=": lambda c, v: c != v,
                 "<": lambda c, v: c < v, "<=": lambda c, v: c <= v,
                 ">": lambda c, v: c > v, ">=": lambda c, v: c >= v}
        if operator not in tests or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise UserError(_(
                "« Without dimensions » counts positions: compare it to a number, with "
                "=, !=, <, <=, > or >=."))
        test = tests[operator]
        counts = dict(
            (fixture.id, count) for fixture, count
            in self.env["bf.planogram.position"]._read_group(
                [("measured", "=", False)], groupby=["fixture_id"], aggregates=["__count"])
            if fixture)
        if test(0, value):
            # Every fixture not named below holds none, and none is wanted: say it the other
            # way round, so a fixture with no position at all is found too.
            return [("id", "not in",
                     [fid for fid, count in counts.items() if not test(count, value)])]
        return [("id", "in", [fid for fid, count in counts.items() if test(count, value)])]

    def _search_overflow(self, operator, value):
        """`overflow` is computed, and a manager looking for what does not fit is the first
        search anyone runs. Without this the filter would raise instead of answering."""
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("A fixture is overflowing or it is not."))
        shelves = self.env["bf.planogram.shelf"].search([("overflow", "=", True)])
        wanted = value if operator == "=" else not value
        return [("id", "in" if wanted else "not in", shelves.fixture_id.ids)]

    @api.depends("name", "code", "locked", "version", "frozen_on")
    def _compute_display_name(self):
        for fixture in self:
            label = "%s (%s)" % (fixture.name, fixture.code) if fixture.code else fixture.name
            if fixture.locked:
                label = _("%(name)s, version %(no)s", name=label, no=fixture.version or 1)
            fixture.display_name = label

    @api.constrains("width", "height", "depth")
    def _check_dimensions(self):
        for fixture in self:
            if fixture.width <= 0 or fixture.height <= 0 or fixture.depth <= 0:
                raise ValidationError(_("A fixture has a width, a height and a depth, "
                                        "in centimetres, and all three are positive."))
            if max(fixture.width, fixture.height, fixture.depth) > SIDE_MAX:
                raise ValidationError(_("A fixture does not go past %s cm on a side.")
                                      % int(SIDE_MAX))

    @api.constrains("height")
    def _check_the_shelves_still_fit(self):
        """🔴 The other direction of the same rule. A shelf above the top of
        its fixture is refused when the SHELF moves, and the FIXTURE's height used to drop
        under an existing shelf without a word: creating that shelf was refused, keeping
        it was not. A shelf left hanging above the top draws outside the frame -- and if it
        is empty it does not even stretch the drawing, so it was cropped in silence while
        it still figured in the table of the PDF.

        The shelf's own constraint is called, so there is ONE message for one rule."""
        self.shelf_ids._check_inside_the_fixture()

    @api.constrains("origin_id")
    def _check_version_of_a_version(self):
        for fixture in self:
            if fixture.origin_id and fixture.origin_id.origin_id:
                raise ValidationError(_("A version is frozen from the fixture itself, "
                                        "never from another version."))

    @api.onchange("kind")
    def _onchange_kind(self):
        """A fixture with no dimensions yet is posed to the scale of its kind.

        🔴 It fills what is BLANK, and nothing else. It used to overwrite all three, so a
        manager who typed 250 × 190 × 60 and then picked the right kind lost the three
        measurements without a word, while the comment above promised the opposite. A measurement typed by hand is the one thing the module cannot
        recover, and the kind's size is a proposal, not a correction.
        """
        for fixture in self:
            if not fixture.kind:
                continue
            w, h, d = size_of_kind(fixture.kind)
            if not fixture.width:
                fixture.width = w
            if not fixture.height:
                fixture.height = h
            if not fixture.depth:
                fixture.depth = d

    @api.model_create_multi
    def create(self, vals_list):
        """🔴 The kind pours its three measurements when a key is ABSENT.

        A fixture created by code -- an action, a script, the import of lot 5 -- behaved
        nothing like the web client: the client sends 122 / 183 / 56 because `_onchange_kind`
        poured them in, and `create` without them died on a `NotNullViolation` of
        Postgres, in English, with the name of the table and the contents of the row, on a
        poisoned cursor -- and in a `create` of two, the good row died with the bad one.
        Arbitrated by the owner: the kind's table is the one source of the
        scale, here as well.

        ⚠️ A key PRESENT at zero is not an absent key: a zero typed on purpose stays
        refused by `_check_dimensions`, which is what tells a typo from a silence."""
        defauts = self.default_get(["kind"])
        for vals in vals_list:
            kind = vals.get("kind") or defauts.get("kind")
            if not kind:
                continue
            sizes = dict(zip(("width", "height", "depth"), size_of_kind(kind)))
            for champ, mesure in sizes.items():
                if champ not in vals:
                    vals[champ] = mesure
        return super().create(vals_list)

    # --- the guard ---------------------------------------------------------------------
    def _assert_open(self, what=None):
        """Called before every change to a fixture, a shelf or a position of it."""
        frozen = self.filtered("locked")
        if not frozen:
            return
        names = ", ".join(frozen.mapped("display_name"))
        if what:
            raise UserError(_(
                "%(what)s belongs to « %(name)s », a frozen version: freeze a new "
                "version instead of changing this one.", what=what, name=names))
        raise UserError(_(
            "« %s » is a frozen version: freeze a new version instead of changing "
            "this one.") % names)

    def write(self, vals):
        if set(vals) - WRITABLE_WHEN_FROZEN:
            self._assert_open()
        return super().write(vals)

    def unlink(self):
        # A frozen version is never deleted, and no longer « deleted with its fixture »
        # either: deleting the living fixture only empties `origin_id`. The
        # test is `locked` alone -- an orphan version is still the reference a round was
        # counted against. Archiving it stays open, which is what WRITABLE_WHEN_FROZEN says.
        self.filtered("locked")._assert_open()
        # 🔴 `position.fixture_id` est un `related` STOCKÉ sur `shelf_id.fixture_id` : effacer
        # le meuble met ses positions en file de RECALCUL, pendant que la cascade de Postgres
        # (`ondelete="cascade"` sur `shelf_id`) les a déjà retirées. L'ORM écrit alors dans des
        # lignes qui n'existent plus : « insert or update on table "bf_planogram_position"
        # violates foreign key constraint ». Mesuré sur une base réelle, dans
        # le rôle du gérant : effacer un meuble rendait une erreur 500.
        # Les enfants partent donc D'ABORD, par l'ORM, qui sait ensuite qu'ils n'existent plus.
        # Pas de `sudo` : qui n'a pas le droit d'effacer une tablette se le fait dire ici.
        tablettes = self.with_context(active_test=False).shelf_ids
        if tablettes:
            tablettes.position_ids.unlink()
            tablettes.unlink()
        return super().unlink()

    # --- freezing ----------------------------------------------------------------------
    def action_freeze(self):
        """Publish the fixture as it stands: a frozen copy, shelves and positions included."""
        self.ensure_one()
        if self.locked:
            raise UserError(_("A version is already frozen."))
        if not self.shelf_ids.position_ids:
            raise UserError(_(
                "« %s » holds no position: a version frozen empty would let a compliance "
                "round pass without counting anything.") % self.display_name)
        # active_test=False: an archived version keeps its number. Without it, freezing
        # after archiving v1 numbered the new one 1 again, and a round could no longer say
        # which of the two it counted against.
        versions = self.with_context(active_test=False).version_ids
        no = max(versions.mapped("version") or [0]) + 1
        # The copy is created open, so the one2many of shelves and positions copies through
        # the same guard as any other write, and is closed right after.
        # `active` is NOT forced: `copy` carries it over, so a version frozen from an
        # archived fixture is archived like its origin. Forced to True it gave an active
        # version whose origin was no longer visible anywhere (forme, ).
        version = self.copy({"name": self.name})
        # What the product measures NOW goes into the version, while it is still open:
        # once closed, its stored widths are recomputed from these copies, never from the
        # product sheet, which keeps changing after the freeze.
        for position in version.shelf_ids.position_ids:
            position.write({
                "frozen_unit_width": position.product_id.bf_planogram_unit_width or 0.0,
                "frozen_unit_height": position.product_id.bf_planogram_unit_height or 0.0,
                # And what it was CALLED that day: the drawing and the PDF of
                # a frozen version followed the product sheet of today, so a renamed
                # product left the old plan naming something that was never on the shelf.
                "frozen_product_name": position.product_id.display_name or "",
                "frozen_product_code": position.product_id.default_code or "",
            })
        version.write({
            "locked": True,
            "origin_id": self.id,
            "version": no,
            "frozen_on": fields.Datetime.now(),
            "frozen_by_id": self.env.user.id,
        })
        # Logged on the re-contexted record, like the rest of the suite: the body is kept,
        # and what the chatter keeps is read in the language of the store.
        said = in_company_language(self, self.company_id)
        said._message_log(body=said.env._(
            "Version %(no)s frozen: %(shelves)s shelf/shelves, %(positions)s "
            "position(s), %(capacity)s unit(s) of capacity.",
            no=no, shelves=version.shelf_count, positions=version.position_count,
            capacity=version.capacity))
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.planogram.fixture",
            "res_id": version.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_positions(self):
        """Every position of the fixture, shelf by shelf. The position carries `fixture_id`
        stored, so this reads one index instead of walking the shelves."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Positions"),
            "res_model": "bf.planogram.position",
            "domain": [("fixture_id", "=", self.id)],
            "context": {"default_fixture_id": self.id, "create": False,
                        "search_default_group_shelf": 1},
            "view_mode": "list,form",
        }

    def action_open_versions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Versions"),
            "res_model": "bf.planogram.fixture",
            "domain": [("origin_id", "=", self.id)],
            "context": {"active_test": False, "create": False},
            "view_mode": "list,form",
        }

    def _frozen_label(self):
        """What the drawing and the PDF print in the corner."""
        self.ensure_one()
        if not self.locked:
            return ""
        # 🔴 `context_timestamp`, et pas l'UTC de la base : un gel a 20 h 17
        # a Toronto s'imprimait « 2026-11-18 01:17:09 », donc le JOUR SUIVANT -- et cette
        # etiquette est l'identite de la version contre laquelle une tournee se compte.
        quand = (fields.Datetime.context_timestamp(self, self.frozen_on)
                 if self.frozen_on else False)
        return _("Version %(no)s, frozen %(date)s", no=self.version or 1,
                 date=fields.Datetime.to_string(quand) if quand else "")

    def _kind_label(self):
        self.ensure_one()
        return label_of_kind(self.kind)

    def _width_compare(self, a, b):
        return float_compare(a, b, precision_digits=CM)
