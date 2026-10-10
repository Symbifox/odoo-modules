"""Quitter le foyer : la personne elle-même, après avoir exporté ses fiches.

L'écran montre :

* le bouton d'export, quand le module de déménagement est là : le fichier
  porté par la personne emmène ses fiches dans un autre foyer ;
* les enfants dont elle est la seule parente : leurs fiches resteront fermées si
  elle part sans nommer de second parent.

Le départ archive le compte. Ses fiches privées restent en base, fermées à tous,
jusqu'à l'effacement par la procédure de Blue Fox.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..models.common import is_household_member, neutral_env


class HouseholdLeave(models.TransientModel):
    _name = "bf.household.leave"
    _description = "Leave the household"

    can_export = fields.Boolean(compute="_compute_context")
    children_alone = fields.Char(string="Children only you hold", compute="_compute_context")
    confirm = fields.Boolean(string="I understand: my account closes now, and my records stay closed until "
                                    "Blue Fox erases them.")

    @api.depends_context("uid")
    def _compute_context(self):
        seuls = self.env["bf.household.child"]._bf_held_alone_by(self.env.user)
        export = bool(self._bf_export_action())
        for rec in self:
            rec.can_export = export
            rec.children_alone = ", ".join(seuls.mapped("name")) or False

    @api.model
    def _bf_export_action(self):
        """L'action d'export de ses fiches. Le pont du déménagement la fournit."""
        return False

    def action_export(self):
        action = self._bf_export_action()
        if not action:
            raise UserError(_("Exporting records is not available in this household."))
        return action

    def action_leave(self):
        self.ensure_one()
        user = self.env.user
        if self.env.su or not is_household_member(user) or user.has_group("base.group_system"):
            raise AccessError(_("Only a member of the household leaves it, by themselves."))
        if not self.confirm:
            raise UserError(_("Tick the box to confirm that you leave the household."))
        neutral_env(self.env)["bf.household.departure"].create({
            "user_id": user.id, "kind": "left", "requested_by_id": user.id,
            "effective_on": fields.Date.context_today(self), "state": "done",
        })
        user._bf_household_archive()
        return {"type": "ir.actions.act_url", "url": "/web/session/logout", "target": "self"}
