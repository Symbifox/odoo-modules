# -*- coding: utf-8 -*-
"""Les activités d'une candidature restent à la personne qui la tient.

Odoo 18 ne vérifie plus que l'assigné peut lire la fiche : une activité assignée à
une autre personne lui donnait son résumé, sa note et le nom de la fiche. Et il ne
contrôle pas la cible quand on DÉPLACE une activité (``res_id``). D'où :

* poser ou déplacer une activité sur une fiche privée exige d'en être propriétaire ;
* elle n'est assignée qu'au propriétaire (ou au compte système) ;

Faire une activité depuis le systray ou le fil fait aussi le geste qu'elle
rappelle : « relance » compte une relance envoyée, « remercier » note le
remerciement, comme leurs boutons.
"""
from odoo import api, models
from odoo.exceptions import AccessError, ValidationError

from .private import PRIVATE_MODELS


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def _bf_job_target(self, vals, activity=None):
        model = vals.get("res_model")
        if not model and vals.get("res_model_id"):
            model = self.env["ir.model"].sudo().browse(vals["res_model_id"]).model
        if not model and activity is not None:
            model = activity.sudo().res_model
        if model not in PRIVATE_MODELS:
            return None
        res_id = vals.get("res_id") or (activity.sudo().res_id if activity is not None else False)
        return self.env[model].browse(res_id) if res_id else None

    def _bf_job_guard(self, rec):
        if not rec.sudo().exists() or not rec.has_access("write"):
            raise AccessError(self.env._(
                "An activity can only be placed on your own job search records."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                rec = self._bf_job_target(vals)
                if rec is not None:
                    self._bf_job_guard(rec)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and {"res_model", "res_model_id", "res_id"} & set(vals):
            for act in self:
                rec = self._bf_job_target(vals, act)
                if rec is not None:
                    self._bf_job_guard(rec)
        return super().write(vals)

    @api.constrains("user_id", "res_model", "res_id")
    def _check_bf_job_owner(self):
        root = self.env.ref("base.user_root")
        for act in self.sudo():
            if act.res_model not in PRIVATE_MODELS or not act.user_id or not act.res_id:
                continue
            rec = self.env[act.res_model].sudo().browse(act.res_id).exists()
            if rec and act.user_id not in (rec.user_id | root):
                raise ValidationError(self.env._(
                    "An activity on a job search record stays with the person who keeps it."))

    def _action_done(self, feedback=False, attachment_ids=None):
        followup = self.env.ref("bf_job_search.mail_act_job_followup", raise_if_not_found=False)
        thanks = self.env.ref("bf_job_search.mail_act_job_thanks", raise_if_not_found=False)
        mine = self.sudo().filtered(lambda a: a.res_model == "bf.job.application")
        followed = [a.res_id for a in mine if followup and a.activity_type_id == followup]
        thanked = self.env["bf.job.interview"].sudo().search(
            [("thanks_activity_id", "in", [a.id for a in mine if thanks and a.activity_type_id == thanks])]
        ) if thanks else self.env["bf.job.interview"]
        res = super()._action_done(feedback=feedback, attachment_ids=attachment_ids)
        if self.env.context.get("bf_job_from_button"):
            return res
        # Dans le rôle de la personne : l'activité ne s'assigne qu'au propriétaire
        # (garde plus haut), c'est donc elle qui l'a faite.
        apps = self.env["bf.job.application"].browse(followed).exists()
        for app in apps.filtered(lambda a: a.stage == "applied" and not a.is_closed):
            app.with_context(bf_job_from_button=True).action_followup_sent()
        for itv in thanked.with_env(self.env).exists():
            if not itv.thanked_date:
                itv.with_context(bf_job_from_button=True).action_thanked()
        return res
