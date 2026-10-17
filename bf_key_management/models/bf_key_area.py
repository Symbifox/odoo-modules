from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfKeyArea(models.Model):
    """A building, a floor, a wing: the unit a person is responsible for.

    The people responsible for an area approve the key requests for it and see
    its keys. Responsibility flows down: whoever answers for the building also
    answers for its floors.
    """
    _name = "bf.key.area"
    _description = "Key area"
    _parent_store = True
    _order = "complete_name"
    _rec_name = "complete_name"

    name = fields.Char(required=True, translate=False)
    complete_name = fields.Char(compute="_compute_complete_name", store=True, recursive=True)
    parent_id = fields.Many2one("bf.key.area", string="Part of", index=True, ondelete="restrict")
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("bf.key.area", "parent_id", string="Sub-areas")
    manager_ids = fields.Many2many(
        "res.users", "bf_key_area_manager_rel", "area_id", "user_id", string="Responsible",
        domain=lambda self: [("groups_id", "in", self.env.ref("bf_key_management.group_key_area_manager").ids)],
        help="The people who approve the key requests for this area and its sub-areas, and see their keys.")
    all_manager_ids = fields.Many2many(
        "res.users", "bf_key_area_all_manager_rel", "area_id", "user_id",
        string="Responsible, inherited", compute="_compute_all_manager_ids", store=True,
        recursive=True)
    door_ids = fields.One2many("bf.key.door", "area_id", string="Doors")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)
    note = fields.Text()

    @api.depends("name", "parent_id.complete_name")
    def _compute_complete_name(self):
        for area in self:
            if area.parent_id:
                area.complete_name = f"{area.parent_id.complete_name} / {area.name}"
            else:
                area.complete_name = area.name

    @api.depends("manager_ids", "parent_id.all_manager_ids")
    def _compute_all_manager_ids(self):
        for area in self:
            area.all_manager_ids = area.manager_ids | area.parent_id.all_manager_ids

    @api.constrains("manager_ids")
    def _check_managers(self):
        group = self.env.ref("bf_key_management.group_key_area_manager")
        for area in self:
            outside = area.manager_ids.filtered(lambda u: group not in u.groups_id)
            if outside:
                raise ValidationError(_(
                    "%s must be in the \"Area manager\" group to approve requests and see the "
                    "keys of this area.", ", ".join(outside.mapped("name"))))

    @api.constrains("parent_id")
    def _check_parent(self):
        if self._has_cycle():
            raise ValidationError(_("An area cannot be part of itself."))
