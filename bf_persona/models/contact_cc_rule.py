from odoo import fields, models


class ContactCcRule(models.Model):
    _name = "contact.cc.rule"
    _description = "Règle de mise en c.c. par catégorie de communication"
    _order = "category_id, mandatory desc"

    persona_id = fields.Many2one(
        "contact.persona", required=True, ondelete="cascade", index=True,
    )
    category_id = fields.Many2one(
        "contact.persona.category", required=True, ondelete="restrict",
    )
    cc_partner_ids = fields.Many2many(
        "res.partner",
        relation="contact_cc_rule_partner_rel",
        column1="rule_id",
        column2="partner_id",
        string="À mettre en c.c.",
        required=True,
    )
    mandatory = fields.Boolean(
        default=False,
        help="Si vrai, la mise en c.c. est obligatoire pour cette catégorie.",
    )
    notes = fields.Text()
