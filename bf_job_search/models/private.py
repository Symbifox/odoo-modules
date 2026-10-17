# -*- coding: utf-8 -*-
"""Une recherche d'emploi est privée à la personne qui la tient.

Classe « privé par personne » de l'éventail Perso (comme la santé et le budget) :
le salaire, les refus, le statut de travail, les répondants (des tiers) ne se
partagent pas dans le ménage. La règle d'accès (``security/job_search_rules.xml``)
ne suffit pas : Odoo laisse sortir une fiche par son fil, ses activités, ses
abonnés, ses pièces jointes et son nom. Les portes fermées ici reprennent celles
de Healthy Fox et du module Crédit et identité.

* ``bf.job.private.thread`` (fil) : notes internes seulement, aucun destinataire,
  aucun avis direct, personne d'autre que le propriétaire n'en devient abonné.
  ⚠️ À placer AVANT ``mail.thread`` dans ``_inherit`` : sinon le ``message_post``
  du cœur passe le premier et ce filtre ne joue jamais.
* ``bf.job.private.name`` (nom) : pour qui ne peut pas lire la fiche, son nom est
  « Private record ». Odoo lit des noms en superutilisateur (erreur d'accès en
  mode debug, Many2one, champ Reference).
* ``mail.followers``, ``ir.attachment``, ``mail.compose.message`` : plus bas.
  Les activités : ``mail_activity.py``.
"""
from odoo import SUPERUSER_ID, api, models
from odoo.exceptions import AccessError, UserError

#: Les modèles privés du module. Chacun porte un champ ``user_id`` (la personne).
PRIVATE_MODELS = (
    "bf.job.application",
    "bf.job.interview",
    "bf.job.referee",
    "bf.job.reference",
    "bf.job.criteria",
)
#: Ceux qui ont un fil.
THREAD_MODELS = ("bf.job.application",)


def _refuse(env):
    return AccessError(env._("A job search record is private to the person who keeps it."))


class JobPrivateThread(models.AbstractModel):
    _name = "bf.job.private.thread"
    _description = "Job search: private thread (internal notes only)"

    def message_post(self, **kwargs):
        comment = self.env["ir.model.data"]._xmlid_to_res_id("mail.mt_comment")
        if kwargs.get("subtype_xmlid") == "mail.mt_comment" or (
                kwargs.get("subtype_id") and kwargs["subtype_id"] == comment):
            kwargs.pop("subtype_id", None)
            kwargs["subtype_xmlid"] = "mail.mt_note"
        # Une mention donnerait au partenaire la lecture du message.
        kwargs["partner_ids"] = []
        # Module `sms` : `_notify_thread` texte ces numéros même sans destinataire.
        kwargs.pop("sms_numbers", None)
        kwargs.pop("sms_pid_to_number", None)
        return super().message_post(**kwargs)

    def message_notify(self, **kwargs):
        # L'invitation à suivre et l'activité assignée à autrui passent par ici :
        # le destinataire lisait le message, donc le nom de la fiche.
        return self.env["mail.message"]

    def _notify_get_recipients(self, message, msg_vals=False, **kwargs):
        return []

    def message_subscribe(self, partner_ids=None, subtype_ids=None):
        if not partner_ids:
            return super().message_subscribe(partner_ids=partner_ids, subtype_ids=subtype_ids)
        for rec in self:
            owner = rec.sudo().user_id.partner_id.id
            super(JobPrivateThread, rec).message_subscribe(
                partner_ids=[p for p in partner_ids if p == owner], subtype_ids=subtype_ids)
        return True


class JobPrivateName(models.AbstractModel):
    """Nom neutre, et garde d'écriture commune aux fiches privées.

    🔴 Odoo ne revérifie pas les règles APRÈS un
    ``write``, et ``readonly`` ne protège rien en RPC. Une personne écrivait
    ``user_id`` = une autre sur sa propre fiche (qui apparaissait alors chez l'autre),
    ou un Many2one interne (activité, événement) vers une fiche quelconque de la
    base, puis en relisait le nom, calculé en superutilisateur.
    """
    _name = "bf.job.private.name"
    _description = "Job search: neutral name for whoever cannot read the record"

    #: Champs que seul le module écrit, en superutilisateur. Pas de drapeau de
    #: contexte : un appel RPC peut poser n'importe quelle clé de contexte.
    _bf_job_internal_fields = ()

    def _bf_job_check_vals(self, vals):
        if self.env.su:
            return
        if "user_id" in vals or set(vals) & set(self._bf_job_internal_fields):
            raise _refuse(self.env)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not self.env.su and set(vals) & set(self._bf_job_internal_fields):
                raise _refuse(self.env)
        return super().create(vals_list)

    def write(self, vals):
        self._bf_job_check_vals(vals)
        return super().write(vals)

    @api.constrains(lambda self: self._bf_job_internal_fields)
    def _check_internal_fields(self):
        """🔴 Les valeurs par défaut du contexte
        (``default_prep_activity_id``…) s'ajoutent APRÈS la garde de ``create``.
        La contrainte voit la valeur finale, d'où qu'elle vienne ; le module écrit
        ces champs en superutilisateur."""
        if self.env.su:
            return
        for rec in self:
            if any(rec[f] for f in self._bf_job_internal_fields):
                raise _refuse(self.env)

    def _bf_job_check_m2o(self, values):
        """Chaque Many2one vers une fiche privée, dans ``values`` et dans les
        commandes x2many qu'il porte (lignes créées ou modifiées), doit être
        lisible par la personne qui appelle."""
        for fname, value in (values or {}).items():
            field = self._fields.get(fname)
            if not field:
                continue
            if field.type == "many2one" and field.comodel_name in PRIVATE_MODELS:
                rid = value.get("id") if isinstance(value, dict) else value
                if isinstance(rid, int) and rid:
                    self.env[field.comodel_name].browse(rid).check_access("read")
            elif field.type in ("one2many", "many2many") and isinstance(value, (list, tuple)):
                sub = self.env[field.comodel_name]
                for cmd in value:
                    if isinstance(cmd, (list, tuple)) and len(cmd) == 3 and isinstance(cmd[2], dict) \
                            and hasattr(sub, "_bf_job_check_m2o"):
                        sub._bf_job_check_m2o(cmd[2])

    def onchange(self, values, field_names, fields_spec):
        """🔴 Un onchange avec l'id d'une
        candidature d'autrui en rendait l'employeur, le poste et la personne (les
        champs liés se calculent pendant l'onchange, ``compute_sudo=False``
        compris). Tout Many2one reçu vers une fiche privée doit être lisible :
        dans les valeurs, dans les lignes x2many, et dans les ``default_*`` du
        contexte, que le premier appel lit après cette garde."""
        if not self.env.su:
            self._bf_job_check_m2o(values)
            defaults = {k[8:]: v for k, v in self.env.context.items()
                        if k.startswith("default_") and k[8:] in self._fields}
            self._bf_job_check_m2o(defaults)
        return super().onchange(values, field_names, fields_spec)

    @api.depends_context("uid")
    def _compute_display_name(self):
        super()._compute_display_name()
        if self.env.uid == SUPERUSER_ID:
            return
        real = self.filtered(lambda rec: isinstance(rec.id, int))
        if not real:
            return
        # sudo() garde env.uid : on demande à la règle, telle qu'elle est, si cette
        # personne peut lire la fiche.
        readable = set(real.with_env(real.env(su=False))._filtered_access("read").ids)
        for rec in real:
            if rec.id not in readable:
                rec.display_name = self.env._("Private record")


class MailFollowers(models.Model):
    """Un abonné d'une fiche privée ne peut être que son propriétaire.

    ``message_subscribe`` filtre déjà ; ici, la création directe d'une ligne
    (RPC) vers une fiche d'une autre personne, ou d'un autre partenaire.
    """
    _inherit = "mail.followers"

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                if vals.get("res_model") in THREAD_MODELS:
                    rec = self.env[vals["res_model"]].browse(vals.get("res_id"))
                    if (not rec.sudo().exists() or not rec.has_access("write")
                            or vals.get("partner_id") != rec.sudo().user_id.partner_id.id):
                        raise _refuse(self.env)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and {"res_model", "res_id", "partner_id"} & set(vals):
            for line in self.sudo():
                if vals.get("res_model", line.res_model) in THREAD_MODELS:
                    raise _refuse(self.env)
        return super().write(vals)


class IrAttachment(models.Model):
    """``ir.attachment`` ne contrôle la fiche cible que si ``res_model`` ET
    ``res_id`` sont écrits ensemble : on déposait ainsi un fichier sur la fiche
    d'une autre personne. Déplacer vers une fiche privée exige d'en être
    propriétaire."""
    _inherit = "ir.attachment"

    def write(self, vals):
        if not self.env.su and {"res_model", "res_id"} & set(vals):
            for att in self.sudo():
                model = vals.get("res_model", att.res_model)
                rid = vals.get("res_id", att.res_id)
                if model in PRIVATE_MODELS and rid:
                    rec = self.env[model].browse(rid)
                    if not rec.sudo().exists() or not rec.has_access("write"):
                        raise _refuse(self.env)
        return super().write(vals)


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    def _action_send_mail(self, auto_commit=False):
        """Le mode masse écrit ses courriels lui-même, sans passer par le fil."""
        for wizard in self:
            if wizard.composition_mode == "mass_mail" and wizard.model in PRIVATE_MODELS:
                raise UserError(self.env._("A job application is not sent by email."))
        return super()._action_send_mail(auto_commit=auto_commit)
