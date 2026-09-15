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
    _description = "Règle de copie d'un persona"
    _order = "state, rule_type, mandatory desc, id"

    persona_id = fields.Many2one(
        "contact.persona", required=True, ondelete="cascade", index=True,
    )
    rule_type = fields.Selection(
        [("cc", "Mettre en copie"), ("never", "Ne jamais mettre en copie")],
        string="Règle", default="cc", required=True,
    )
    category_id = fields.Many2one(
        "contact.persona.category", ondelete="restrict",
        help="Étiquette seulement : la règle s'applique quel que soit le sujet.",
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
        string="Obligatoire",
        default=False,
        help="Copie obligatoire : le composeur la signale en rouge tant qu'elle manque. "
             "Sinon, il la propose.",
    )
    state = fields.Selection(
        [("suggested", "Suggérée"), ("active", "Active"), ("rejected", "Rejetée")],
        string="État", default="active", required=True, index=True,
    )
    evidence = fields.Char(
        string="Constat",
        readonly=True,
        help="Ce qui a fait suggérer la règle, relevé dans les courriels envoyés.",
    )
    notes = fields.Text()

    @api.depends("rule_type", "cc_partner_ids", "persona_id")
    def _compute_display_name(self):
        for rule in self:
            names = ", ".join(rule.cc_partner_ids.mapped("name"))
            label = dict(self._fields["rule_type"].selection).get(rule.rule_type, "")
            rule.display_name = f"{label} : {names}" if names else label

    def action_confirm(self):
        self.write({"state": "active"})
        return True

    def action_reject(self):
        self.write({"state": "rejected"})
        return True
