from odoo import api, fields, models


class ContactCcRule(models.Model):
    """Who goes in copy when writing to the contact of a persona.

    A rule fires on the recipients of a composer, never on a category: the
    composer has no way to know whether a message is about billing or support,
    so a rule that waited for one never fired. The category stays as a label.

    Rules learned from history arrive as ``suggested`` and do nothing until a
    person confirms them: that someone is usually copied does not mean they
    must be, and plenty of messages rightly go without the usual copy.
    """

    _name = "contact.cc.rule"
    _description = "Copy rule of a persona"
    _order = "state, rule_type, mandatory desc, id"

    persona_id = fields.Many2one(
        "contact.persona", required=True, ondelete="cascade", index=True,
        string="Persona",
    )
    rule_type = fields.Selection(
        [("cc", "Put in copy"), ("never", "Never put in copy")],
        string="Rule", default="cc", required=True,
    )
    category_id = fields.Many2one(
        "contact.persona.category", ondelete="restrict",
        help="Label only: the rule applies whatever the subject.",
        string="Category",
    )
    cc_partner_ids = fields.Many2many(
        "res.partner",
        relation="contact_cc_rule_partner_rel",
        column1="rule_id",
        column2="partner_id",
        string="Contacts",
        required=True,
    )
    mandatory = fields.Boolean(
        string="Mandatory",
        default=False,
        help="Mandatory copy: the composer flags it in red as long as it "
             "is missing. Otherwise it suggests it.",
    )
    state = fields.Selection(
        [("suggested", "Suggested"), ("active", "Active"), ("rejected", "Rejected")],
        string="Status", default="active", required=True, index=True,
    )
    evidence = fields.Char(
        string="Evidence",
        readonly=True,
        help="What led to the rule being suggested, found in the emails sent.",
    )
    notes = fields.Text(string="Notes")

    # Le nom porte le libellé du type de règle : un cache par langue, sinon le premier calcul
    # servait l'autre langue dans la même transaction.
    display_name = fields.Char(compute="_compute_display_name", search="_search_display_name",
                               depends_context=("lang",))

    @api.depends("rule_type", "cc_partner_ids", "persona_id")
    def _compute_display_name(self):
        for rule in self:
            names = ", ".join(rule.cc_partner_ids.mapped("name"))
            label = dict(self._fields["rule_type"]._description_selection(self.env)).get(rule.rule_type, "")
            rule.display_name = f"{label} : {names}" if names else label

    def action_confirm(self):
        self.write({"state": "active"})
        return True

    def action_reject(self):
        self.write({"state": "rejected"})
        return True
