import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class HelpdeskAutoTagRule(models.Model):
    _name = "helpdesk.auto.tag.rule"
    _description = "Règle d'auto-tag par mot-clé"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    team_id = fields.Many2one(
        comodel_name="helpdesk.ticket.team",
        required=True,
        ondelete="cascade",
    )
    keyword_regex = fields.Char(
        string="Regex (i)",
        required=True,
        help="Expression régulière (insensible à la casse) testée contre sujet + description.",
    )
    tag_id = fields.Many2one(
        comodel_name="helpdesk.ticket.tag",
        string="Tag à appliquer",
        required=True,
        ondelete="cascade",
    )
    active = fields.Boolean(default=True)

    @api.constrains("keyword_regex")
    def _check_regex_compiles(self):
        for rule in self:
            if rule.keyword_regex:
                try:
                    re.compile(rule.keyword_regex, re.IGNORECASE)
                except re.error as e:
                    raise ValidationError(f"Regex invalide pour la règle « {rule.name} » : {e}")

    def _matches(self, text):
        self.ensure_one()
        if not self.keyword_regex or not text:
            return False
        try:
            return bool(re.search(self.keyword_regex, text, re.IGNORECASE))
        except re.error:
            return False
