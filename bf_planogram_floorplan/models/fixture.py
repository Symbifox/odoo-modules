# -*- coding: utf-8 -*-
"""Where the fixture stands, and nothing else: the two geometries meet in one field."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfPlanogramFixture(models.Model):
    _inherit = "bf.planogram.fixture"

    # copy=False, and it matters: a frozen version does not STAND anywhere, the fixture
    # does. Copied, every version would add a phantom fixture to the shape of the plan, and
    # yesterday's overflow would colour today's plan for ever. Nothing is lost: a version
    # keeps its `origin_id`, and the fixture keeps its place.
    floorplan_element_id = fields.Many2one(
        "bf.floorplan.element", string="On the plan", ondelete="set null", index=True,
        tracking=True, copy=False,
        help="The shape of the store plan this fixture stands on. A shape can carry "
             "several fixtures (a wall run split in sections); a fixture stands in one "
             "place.")
    # Stored, so the fixtures of one plan group and search without walking the shape.
    floorplan_id = fields.Many2one(
        related="floorplan_element_id.plan_id", store=True, index=True, string="Floor plan")

    @api.constrains("floorplan_element_id", "company_id", "active")
    def _check_same_company(self):
        """A fixture of one store pinned to the plan of another is a data error, and it
        would put one store's aisle on the other's printed plan. The plan of a floor whose
        company is empty is not refused: an empty company means every company, which is
        Odoo's own reading of the field.

        `active` is watched too. Standing a fixture back up is the moment it
        starts counting on a plan again, and whatever faulty state it slept with -- data
        written before this guard existed, an import, SQL -- must be refused then rather
        than becoming permanent for want of anything replaying the check.
        """
        for fixture in self:
            plan = fixture.floorplan_element_id.plan_id
            if plan and plan.company_id and plan.company_id != fixture.company_id:
                raise ValidationError(_(
                    "« %(fixture)s » belongs to %(store)s and the plan « %(plan)s » to "
                    "%(other)s: a fixture stands on the plan of its own store.",
                    fixture=fixture.display_name, store=fixture.company_id.display_name,
                    plan=plan.display_name, other=plan.company_id.display_name))

    def action_open_floorplan(self):
        """The plan, with this fixture's shape highlighted. The twin of « on the plan » of
        an employee: the key travels in the context, and `bf_floorplan.rendu` reads it."""
        self.ensure_one()
        element = self.floorplan_element_id
        if not element:
            raise ValidationError(_("« %s » does not stand on any plan yet.")
                                  % self.display_name)
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.floorplan",
            "res_id": element.plan_id.id,
            "view_mode": "form",
            "context": dict(self.env.context, bf_floorplan_surligne=element.id),
            "target": "current",
        }
