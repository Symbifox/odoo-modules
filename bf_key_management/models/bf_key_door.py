from odoo import _, api, fields, models

LOCK_KINDS = [
    ("mechanical", "Mechanical cylinder"),
    ("ic_core", "Interchangeable core"),
    ("card_reader", "Card reader"),
    ("garage", "Garage or gate opener"),
    ("padlock", "Padlock"),
    ("other", "Other"),
]


class BfKeyDoor(models.Model):
    """A door, a gate, a cabinet: anything a key, card or remote opens.

    What opens it is computed: the key its cylinder is keyed to and every
    master above that key (unless the cylinder is not master keyed), plus the
    types it also accepts (cross keying, an access card profile).
    """
    _name = "bf.key.door"
    _description = "Door or lock"
    _inherit = ["mail.thread"]
    _order = "area_id, name"

    name = fields.Char(required=True, tracking=True)
    area_id = fields.Many2one("bf.key.area", string="Area", index=True, tracking=True)
    system_id = fields.Many2one(
        "bf.key.system", string="Master key system", index=True,
        groups="bf_key_management.group_key_area_manager")
    lock_kind = fields.Selection(LOCK_KINDS, string="Lock", default="mechanical", required=True)
    cylinder_ref = fields.Char(
        string="Cylinder reference", groups="bf_key_management.group_key_area_manager")
    keyed_type_id = fields.Many2one(
        "bf.key.type", string="Keyed to", index=True, tracking=True,
        groups="bf_key_management.group_key_area_manager",
        help="The key its cylinder is keyed to. Every master above that key opens it too.")
    nmk = fields.Boolean(
        string="Not master keyed", tracking=True, groups="bf_key_management.group_key_area_manager",
        help="Only the key it is keyed to opens it: no master does.")
    extra_type_ids = fields.Many2many(
        "bf.key.type", "bf_key_door_extra_type_rel", "door_id", "type_id",
        string="Also opened by", groups="bf_key_management.group_key_area_manager",
        help="Other keys (cross keying) or access card profiles that open this door.")
    cross_keyed = fields.Boolean(
        string="Cross keyed", compute="_compute_openers", groups="bf_key_management.group_key_area_manager")
    opener_type_ids = fields.Many2many(
        "bf.key.type", string="Opened by", compute="_compute_openers",
        groups="bf_key_management.group_key_area_manager")
    holder_assignment_ids = fields.Many2many(
        "bf.key.assignment", string="Who can open it", compute="_compute_openers",
        groups="bf_key_management.group_key_area_manager")
    holder_count = fields.Integer(
        string="Holders", compute="_compute_openers", groups="bf_key_management.group_key_area_manager")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)
    note = fields.Text()

    def _openers(self):
        """The key types that open this door."""
        self.ensure_one()
        door = self.sudo()
        openers = self.env["bf.key.type"].sudo()
        for key_type in door.keyed_type_id | door.extra_type_ids:
            openers |= key_type
            if not door.nmk:
                parent = key_type.parent_id
                while parent and parent not in openers:
                    openers |= parent
                    parent = parent.parent_id
        return openers

    @api.depends("keyed_type_id", "extra_type_ids", "nmk")
    def _compute_openers(self):
        Assignment = self.env["bf.key.assignment"].sudo()
        for door in self:
            openers = door._openers() if door.id or door.keyed_type_id else self.env["bf.key.type"]
            door.opener_type_ids = openers
            door.cross_keyed = any(t.kind == "key" for t in door.sudo().extra_type_ids)
            holders = Assignment.search([
                ("type_id", "in", openers.ids), ("date_returned", "=", False)]) if openers else Assignment
            door.holder_assignment_ids = holders
            door.holder_count = len(holders)

    def action_view_holders(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Who can open %s", self.name),
            "res_model": "bf.key.assignment", "view_mode": "list,form",
            "domain": [("id", "in", self.holder_assignment_ids.ids)],
        }
