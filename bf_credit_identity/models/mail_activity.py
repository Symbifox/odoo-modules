"""Une activité sur un rappel de crédit reste à son propriétaire.

Odoo laisse lire une activité à la personne à qui elle est assignée, même sans
accès au rappel, et calcule son ``res_name`` en superutilisateur. Et il ne
contrôle pas la cible quand on DÉPLACE une activité (``res_id``) : une personne
créait une activité chez elle, la déplaçait sur le rappel d'une autre, puis la
relisait. D'où deux gardes :

* poser ou déplacer une activité sur un rappel exige d'en être propriétaire ;
* l'activité d'un rappel n'est assignée qu'à son propriétaire.

Faire l'activité d'un rappel (systray, fil, liste) fait aussi le rappel : il
repart du jour où c'est fait, comme avec son bouton.
"""
from odoo import api, models
from odoo.exceptions import AccessError, UserError

MODELE = "bf.credit.reminder"


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def _bf_credit_cible(self, vals, activite=None):
        """Le rappel visé une fois les valeurs écrites, ou None."""
        modele = vals.get("res_model")
        if not modele and vals.get("res_model_id"):
            modele = self.env["ir.model"].sudo().browse(vals["res_model_id"]).model
        if not modele and activite is not None:
            modele = activite.sudo().res_model
        if modele != MODELE:
            return None
        res_id = vals.get("res_id") or (activite.sudo().res_id if activite is not None else False)
        return self.env[MODELE].browse(res_id) if res_id else None

    def _bf_credit_garder(self, rappel, user_id):
        if not rappel.sudo().exists() or not rappel.has_access("write"):
            raise AccessError(self.env._(
                "An activity can only be placed on your own credit reminder."))
        if user_id and user_id != rappel.sudo().user_id.id:
            raise UserError(self.env._(
                "An activity on a credit reminder stays with the reminder's owner."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                rappel = self._bf_credit_cible(vals)
                if rappel is not None:
                    self._bf_credit_garder(rappel, vals.get("user_id") or self.env.uid)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and {"res_model", "res_model_id", "res_id", "user_id"} & set(vals):
            for activite in self:
                rappel = self._bf_credit_cible(vals, activite)
                if rappel is not None:
                    self._bf_credit_garder(
                        rappel, vals.get("user_id") or activite.sudo().user_id.id)
        return super().write(vals)

    def _action_done(self, feedback=False, attachment_ids=None):
        type_credit = self.env.ref(
            "bf_credit_identity.mail_activity_type_credit", raise_if_not_found=False)
        rappel_ids = [a.res_id for a in self.sudo()
                      if type_credit and a.res_model == MODELE and a.activity_type_id == type_credit]
        res = super()._action_done(feedback=feedback, attachment_ids=attachment_ids)
        if rappel_ids and not self.env.context.get("bf_credit_fait"):
            # En superutilisateur : l'activité ne s'assigne qu'au propriétaire du
            # rappel (garde plus haut), c'est donc lui qui l'a faite.
            self.env[MODELE].sudo().browse(rappel_ids).exists()._fait_aujourd_hui()
        return res
