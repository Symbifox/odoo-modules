"""Une activité ne se pose sur un avis, ou ne s'y déplace, qu'avec le droit d'écrire sur l'avis.

Le responsable d'une activité peut la réécrire sans égard à la fiche visée : déplacée sur un avis
(d'une autre société, au besoin), elle y écrivait au fil en la marquant faite.
"""
from odoo import _, api, models
from odoo.exceptions import AccessError, UserError

WATCHED = "privacy.breach.notice"


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def _breach_check_targets(self):
        ours = self.sudo().filtered(lambda a: a.res_model == WATCHED)
        self.env["mail.message"]._breach_check_register(ours.mapped("res_id"))
        for activity in ours.filtered("user_id"):
            # Marquer faite, annuler ou réassigner écrit au fil : l'assigné doit pouvoir y écrire,
            # sans quoi l'activité resterait en retard sans issue.
            if not activity._breach_assignee_can_write():
                raise UserError(_("Une activité sur un avis de violation se confie à une personne qui "
                                  "peut écrire sur l'avis : %s ne le peut pas.", activity.user_id.name))

    def _breach_assignee_can_write(self):
        """Jugé dans les sociétés de l'assigné, pas dans celles que l'appelant a cochées : une
        société qu'il n'a pas lèverait « société non autorisée » au lieu de répondre."""
        user = self.user_id
        notice = self.env[WATCHED].sudo().browse(self.res_id).exists()
        if not notice:
            return False
        companies = (user.company_ids & notice.company_id) or user.company_id
        try:
            return self.env[WATCHED].with_user(user).with_context(
                allowed_company_ids=companies.ids).browse(notice.id).has_access("write")
        except AccessError:
            return False

    @api.model_create_multi
    def create(self, vals_list):
        activities = super().create(vals_list)
        activities._breach_check_targets()
        return activities

    def write(self, vals):
        res = super().write(vals)
        if {"res_model_id", "res_model", "res_id", "user_id"} & set(vals):
            self._breach_check_targets()
        return res
