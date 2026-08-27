# -*- coding: utf-8 -*-
"""Le pas qui conclut un sondage : sur quel créneau fixe-t-on la rencontre ?

🔴 Cet assistant existe parce que le bouton « Fixer la rencontre » ne demandait
rien. Il prenait `slot_ids.filtered("is_viable")[:1]`, et `slot_ids` est trié
par heure : la rencontre tombait sur le premier créneau que personne n'avait
rejeté, même si personne ne l'avait choisi. Un sondage sert à décider ; le geste
qui conclut doit montrer ce qui a été décidé.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AppointmentPollScheduleWizard(models.TransientModel):
    _name = "appointment.poll.schedule.wizard"
    _description = "Choisir le créneau qui fixe la rencontre"

    poll_id = fields.Many2one(
        "appointment.poll", string="Sondage", required=True, readonly=True,
        ondelete="cascade")
    slot_id = fields.Many2one(
        "appointment.poll.slot",
        string="Créneau retenu",
        required=True,
        domain="[('poll_id', '=', poll_id), ('is_viable', '=', True)]",
        help="Présélectionné sur le mieux classé : viable d'abord, puis "
             "complet, puis le plus de « oui ». Rien ne vous y oblige.",
    )
    apercu = fields.Text(
        string="Ce que le sondage a dit",
        compute="_compute_apercu",
        help="Les créneaux du meilleur au moins bon, avec qui a répondu quoi.",
    )
    slot_detail = fields.Char(
        string="Sur ce créneau", compute="_compute_slot_detail")

    @api.model
    def default_get(self, fields_list):
        """Présélectionne le mieux classé, jamais le plus proche dans le temps."""
        valeurs = super().default_get(fields_list)
        poll = self.env["appointment.poll"].browse(
            valeurs.get("poll_id") or self.env.context.get("default_poll_id"))
        if poll.exists() and not valeurs.get("slot_id"):
            meilleur = poll._ranked_slots().filtered("is_viable")[:1]
            if meilleur:
                valeurs["slot_id"] = meilleur.id
        return valeurs

    @api.depends("poll_id")
    def _compute_apercu(self):
        for assistant in self:
            lignes = []
            for rang, creneau in enumerate(assistant.poll_id._ranked_slots(), 1):
                etat = []
                if not creneau.is_viable:
                    etat.append(_("écarté"))
                elif creneau.is_complete:
                    etat.append(_("tous les obligatoires ont répondu"))
                detail = creneau.vote_summary or _("aucune réponse")
                lignes.append("%d. %s %s — %s%s" % (
                    rang, creneau.display_day(), creneau.display_time(), detail,
                    (" (%s)" % ", ".join(etat)) if etat else ""))
            assistant.apercu = "\n".join(lignes) or _("Aucun créneau.")

    @api.depends("slot_id")
    def _compute_slot_detail(self):
        for assistant in self:
            creneau = assistant.slot_id
            assistant.slot_detail = (
                creneau.vote_summary or _("aucune réponse")) if creneau else ""

    def action_confirm(self):
        """Fixe la rencontre et ouvre le rendez-vous qui en sort."""
        self.ensure_one()
        if self.slot_id.poll_id != self.poll_id:
            # ⚠️ Le domaine de la vue n'autorise rien : un client bricolé peut
            # poster l'identifiant d'un créneau d'un autre sondage.
            raise UserError(_("Ce créneau n'appartient pas à ce sondage."))
        self.poll_id.action_schedule(self.slot_id)
        return self.poll_id.action_view_booking()
