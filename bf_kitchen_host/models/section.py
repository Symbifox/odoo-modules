from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class HostSection(models.Model):
    """A part of the floor one server takes care of during a service. The host seats the sections in turn."""
    _name = "bf.host.section"
    _description = "Host section"
    _order = "config_id, sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    config_id = fields.Many2one("pos.config", "Register", required=True, ondelete="cascade", index=True,
                                domain=[("module_pos_restaurant", "=", True)])
    company_id = fields.Many2one(related="config_id.company_id", store=True, index=True)
    table_ids = fields.Many2many(
        "restaurant.table", "bf_host_section_table_rel", "section_id", "table_id", string="Tables",
        domain="[('floor_id.pos_config_ids', 'in', config_id)]")
    employee_id = fields.Many2one(
        "hr.employee", "Server", check_company=True,
        help="Who serves this section now. The host changes it at the start of a service.")
    color = fields.Integer(default=0)
    active = fields.Boolean(default=True)

    @api.constrains("table_ids", "config_id", "active")
    def _check_one_section_per_table(self):
        for section in self.filtered("active"):
            others = self.search([("config_id", "=", section.config_id.id), ("id", "!=", section.id)])
            shared = section.table_ids & others.table_ids
            if shared:
                raise ValidationError(_("Table %(table)s is already in the section %(section)s.",
                                        table=shared[0].display_name,
                                        section=others.filtered(lambda s: shared[0] in s.table_ids)[0].name))
            foreign = section.table_ids.filtered(lambda t: section.config_id not in t.floor_id.pos_config_ids)
            if foreign:
                raise ValidationError(_("Table %s is not on a floor of this register.", foreign[0].display_name))
