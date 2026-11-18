# -*- coding: utf-8 -*-
"""What the plan of a store says about what is on its shelves.

`bf_floorplan` leaves three hooks for exactly this, and says so in `genres.py`: « Le module
seul n'en pose aucune ; un pont en pose d'après ce qu'il sait. » This is that bridge, on the
pattern of `bf_floorplan_hosting`.

🔴 The plan is read without the rights of the planogram, like a notice board: what the shape
SHOWS of a fixture is read in sudo. The fixture's own form only opens for whoever may read
it: without the right, the shape announces no target and the click opens the shape.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

# What a shape can say, from nothing to the worst: the plan keeps the worst of what the
# bridges know -- in `_teinte`, against the layer BELOW this bridge, and again in
# `BfFloorplan.to_dict`, against any layer above it that does not call `super`.
GRAVITE = {"": 0, "ok": 1, "attention": 2, "alerte": 3}


class BfFloorplan(models.Model):
    _inherit = "bf.floorplan"

    @api.constrains("company_id")
    def _check_planogram_company(self):
        """the company of the PLAN, changed after its fixtures stood on
        it. The guard of the fixture watches the fixture's fields, and never fires here.

        `active_test=False`, because an archived fixture still STANDS on the
        shape -- its link is intact, and unarchiving it puts it back on a plan that moved
        under it in the meantime. Without it, archiving was a way through the guard."""
        self.env["bf.planogram.fixture"].sudo().with_context(active_test=False).search(
            [("floorplan_id", "in", self.ids)])._check_same_company()

    def to_dict(self):
        """the plan keeps the WORST of what the bridges say. `_teinte` is a
        single-winner hook, and its winner is whoever loads last: `bf_floorplan_hosting`
        answers '' for a device in good order without asking `super`, which swallowed
        the red of an overflowing fixture on the same shape -- on the screen, the PDF and
        the export, which all read this dict. The planogram's colour is laid over the
        hook's, never under it, whatever the order of loading."""
        d = super().to_dict()
        formes = self.env["bf.floorplan.element"].browse([e["id"] for e in d["elements"]])
        for e, forme in zip(d["elements"], formes):
            mienne = forme._teinte_planogramme()
            if GRAVITE.get(mienne, 0) > GRAVITE.get(e["teinte"], 0):
                e["teinte"] = mienne
        return d


class BfFloorplanElement(models.Model):
    _inherit = "bf.floorplan.element"

    @api.constrains("plan_id")
    def _check_planogram_company(self):
        """the SHAPE moved to the plan of another store, carrying its
        fixtures with it. Read in sudo: whoever redraws the floor may not read the
        planogram, and the guard must hold for him too.

        and with `active_test=False`, because a one2many hides an archived
        record, which the shape carries along all the same."""
        self.sudo().with_context(active_test=False).planogram_fixture_ids \
            ._check_same_company()

    planogram_fixture_ids = fields.One2many(
        "bf.planogram.fixture", "floorplan_element_id", string="Fixtures")
    planogram_fixture_count = fields.Integer(
        compute="_compute_planogram", string="Fixtures", compute_sudo=False)
    # What the shape says of its fixtures, for every shape of the plan AT ONCE.
    # `bf_floorplan.rendu` calls the three hooks one shape at a time, and the editor
    # renders after every gesture: read one by one through the fixture's unstored sums,
    # 200 shapes cost 1008 queries and 1.5 s. A computed
    # field is computed for the whole prefetch set of the first shape read, which is the
    # whole plan: the hooks then read a dict.
    planogram_resume = fields.Json(
        compute="_compute_planogram", compute_sudo=False, string="Planogram summary")

    @api.depends(
        "planogram_fixture_ids", "planogram_fixture_ids.locked",
        "planogram_fixture_ids.company_id", "planogram_fixture_ids.display_name",
        "planogram_fixture_ids.overflow", "planogram_fixture_ids.unmeasured_count",
        "planogram_fixture_ids.position_count", "planogram_fixture_ids.shelf_count",
        "planogram_fixture_ids.capacity", "planogram_fixture_ids.version_count")
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_planogram(self):
        # 🔴 The plan is read without the rights of the planogram, like a notice board, so
        # the fixtures are read in sudo -- but only those of the READER's stores
        #. A plan without a company is read by every store, and the rule of the
        # fixture is strictly by company: unbounded, the sudo showed an employee of another
        # store the count, the red and the capacity of an aisle that is not his. The
        # companies are taken from the caller's environment, before any sudo.
        companies = self.env.companies
        ids = [i for i in self.ids if isinstance(i, int)]
        # Frozen versions never stand anywhere: not counted, not opened.
        fixtures = self.env["bf.planogram.fixture"].sudo().search([
            ("floorplan_element_id", "in", ids), ("locked", "=", False),
            ("company_id", "in", companies.ids)]) if ids else \
            self.env["bf.planogram.fixture"].sudo()
        # The form opens only for whoever may read it, asked in the CALLER's environment
        # (asked of a sudo recordset, the question answers yes to everyone), in one go.
        readable = set(fixtures.with_env(self.env)._filtered_access("read").ids)
        par_forme = {}
        for fixture in fixtures:      # one prefetch set: the unstored sums run once
            par_forme.setdefault(fixture.floorplan_element_id.id, []).append({
                "id": fixture.id, "nom": fixture.display_name,
                "lisible": fixture.id in readable,
                "overflow": fixture.overflow, "unmeasured": fixture.unmeasured_count,
                "shelves": fixture.shelf_count, "positions": fixture.position_count,
                "capacity": fixture.capacity, "versions": fixture.version_count})
        for element in self:
            meubles = par_forme.get(element._origin.id or element.id, [])
            element.planogram_fixture_count = len(meubles)
            element.planogram_resume = meubles

    # --- the hooks of bf_floorplan ----------------------------------------------------
    def _cible(self):
        self.ensure_one()
        meubles = self.planogram_resume or []
        # Exactly one: the click opens it, because that is what one came for. Several: the
        # click opens the shape, which lists them, rather than choosing for the reader.
        if len(meubles) == 1 and meubles[0]["lisible"]:
            return {"modele": "bf.planogram.fixture", "id": meubles[0]["id"],
                    "nom": meubles[0]["nom"]}
        return super()._cible()

    def _teinte_planogramme(self):
        """What the fixtures alone say: '', 'alerte', 'attention' or 'ok'."""
        self.ensure_one()
        meubles = self.planogram_resume or []
        if any(m["overflow"] for m in meubles):
            return "alerte"
        if any(m["unmeasured"] or not m["positions"] for m in meubles):
            return "attention"
        if any(m["versions"] for m in meubles):
            return "ok"
        return ""

    def _teinte(self):
        """the WORST of the two, the way `bf_floorplan_hosting` does since
        . An `or` is a single winner: as soon as the planogram had a colour, even the
        mildest one ('ok', which only says « a version is published »), the colour of the
        module underneath was lost -- and the worst-wins of `to_dict` never repairs it,
        since it only ever raises its OWN colour. On a tie the planogram wins, as in
        `bf_floorplan_hosting`."""
        self.ensure_one()
        return max(self._teinte_planogramme(), super()._teinte(),
                   key=lambda t: GRAVITE.get(t, 0))

    def _infos(self):
        infos = super()._infos()
        meubles = self.planogram_resume or []
        if not meubles:
            return infos
        positions = sum(m["positions"] for m in meubles)
        infos.append(_(
            "%(shelves)s shelf/shelves, %(positions)s position(s), %(capacity)s unit(s)",
            shelves=sum(m["shelves"] for m in meubles), positions=positions,
            capacity=sum(m["capacity"] for m in meubles)))
        unmeasured = sum(m["unmeasured"] for m in meubles)
        if unmeasured:
            infos.append(_("%s position(s) without dimensions") % unmeasured)
        if any(m["overflow"] for m in meubles):
            infos.append(_("a shelf holds more than it has"))
        if not positions:
            infos.append(_("nothing on the shelves yet"))
        frozen = sum(m["versions"] for m in meubles)
        if frozen:
            infos.append(_("%s version(s) frozen") % frozen)
        return infos

    def action_open_planogram(self):
        """The elevation of what stands here: only what the reader may open. The
        count is a notice board read in sudo; the elevation is not, and a raw AccessError
        is not an answer."""
        self.ensure_one()
        fixtures = self.env["bf.planogram.fixture"].browse(
            [m["id"] for m in self.planogram_resume or [] if m["lisible"]])
        if not fixtures:
            raise UserError(_(
                "Nothing on « %s » that you may open: the fixtures standing here belong to "
                "the planogram, which your rights do not open.") % self.display_name)
        if len(fixtures) == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "bf.planogram.fixture",
                "res_id": fixtures.id,
                "view_mode": "form",
                "target": "current",
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Fixtures"),
            "res_model": "bf.planogram.fixture",
            "domain": [("id", "in", fixtures.ids)],
            "context": {"default_floorplan_element_id": self.id},
            "view_mode": "list,form",
        }
