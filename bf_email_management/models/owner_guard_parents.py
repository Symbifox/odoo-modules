"""Garde propriétaire sur les fiches filles des règles et répondeurs.

Posée ici, par héritage, pour laisser `bf_email_rule_condition.py` à son seul
rôle d'évaluation. Seuls `rule_id` et `absence_reply_id` sont gardés.
"""
from odoo import api, models

from .owner_guard import garder_parent


class BfEmailAbsenceReplyGuard(models.Model):
    _inherit = "bf.email.absence.reply"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            garder_parent(self, vals, "absence_id", "bf.email.absence")
        return super().create(vals_list)

    def write(self, vals):
        garder_parent(self, vals, "absence_id", "bf.email.absence")
        return super().write(vals)


class BfEmailRuleConditionGuard(models.Model):
    _inherit = "bf.email.rule.condition"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            garder_parent(self, vals, "rule_id", "bf.email.rule")
            garder_parent(self, vals, "absence_reply_id", "bf.email.absence.reply")
        return super().create(vals_list)

    def write(self, vals):
        garder_parent(self, vals, "rule_id", "bf.email.rule")
        garder_parent(self, vals, "absence_reply_id", "bf.email.absence.reply")
        return super().write(vals)
