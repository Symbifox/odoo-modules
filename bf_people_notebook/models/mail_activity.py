"""Garde 3 : une activité sur une fiche de personne est toujours à sa propriétaire.

Odoo laisse lire une activité à la personne à qui elle est assignée, même sans accès
à la fiche, et lui en envoie l'avis par courriel : le résumé et le nom de la fiche
(``res_name``, calculé en sudo) sortiraient. Assignée à quelqu'un d'autre, une
activité revient donc à la propriétaire, à la création comme à la réassignation.
"""
from odoo import api, models
from odoo.exceptions import UserError

MODELE = "bf.people.person"


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def _bf_people_owner_id(self, res_model, res_id):
        if res_model != MODELE or not res_id:
            return None
        fiche = self.env[MODELE].sudo().with_context(active_test=False).browse(res_id).exists()
        return fiche.user_id.id if fiche else None

    @api.model_create_multi
    def create(self, vals_list):
        modele_id = self.env["ir.model"]._get_id(MODELE)
        for vals in vals_list:
            res_model = vals.get("res_model") or (
                MODELE if vals.get("res_model_id") == modele_id else None)
            proprio = self._bf_people_owner_id(res_model, vals.get("res_id"))
            if proprio:
                vals["user_id"] = proprio
        return super().create(vals_list)

    def write(self, vals):
        # Une activité créée ailleurs puis DÉPLACÉE sur une fiche (res_id) passerait, Odoo ne contrôlant pas la cible d'un déplacement. Déplacer
        # vers une fiche exige d'en être la propriétaire, et l'activité lui revient.
        if not self.env.su and {"res_id", "res_model", "res_model_id"} & set(vals):
            modele = vals.get("res_model")
            if not modele and vals.get("res_model_id"):
                modele = self.env["ir.model"].sudo().browse(vals["res_model_id"]).model
            for activite in self:
                cible = modele or activite.sudo().res_model
                if cible != MODELE:
                    continue
                fiche = self.env[MODELE].browse(vals.get("res_id", activite.sudo().res_id))
                fiche.check_access("write")
                vals = dict(vals, user_id=fiche.sudo().user_id.id)
        if vals.get("user_id") and not self.env.su:
            for activite in self.sudo().filtered(lambda a: a.res_model == MODELE):
                proprio = self._bf_people_owner_id(activite.res_model, activite.res_id)
                if proprio and vals["user_id"] != proprio:
                    raise UserError(self.env._(
                        "A reminder on a private card stays with the card's owner."))
        return super().write(vals)
