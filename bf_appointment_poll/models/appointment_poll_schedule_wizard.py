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
    _description = "Choose the slot for the meeting"

    poll_id = fields.Many2one(
        "appointment.poll", string="Poll", required=True, readonly=True,
        ondelete="cascade")
    slot_id = fields.Many2one(
        "appointment.poll.slot",
        string="Chosen slot",
        required=True,
        domain="[('poll_id', '=', poll_id), ('is_viable', '=', True)]",
        help="Preselected on the best ranked: viable first, then "
             "complete, then the most \"yes\" answers. Nothing forces you "
             "to keep it.",
    )
    apercu = fields.Text(
        string="What the poll said",
        compute="_compute_apercu",
        help="Slots from best to worst, with who answered what.",
    )
    slot_detail = fields.Char(
        string="On this slot", compute="_compute_slot_detail")

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
                    etat.append(_("ruled out"))
                elif creneau.is_complete:
                    etat.append(_("all required people have answered"))
                detail = creneau.vote_summary or _("no answer")
                lignes.append(_(
                    "%(rang)s. %(jour)s %(heure)s: %(detail)s%(etat)s",
                    rang=rang, jour=creneau.display_day(), heure=creneau.display_time(),
                    detail=detail, etat=(" (%s)" % ", ".join(etat)) if etat else ""))
            assistant.apercu = "\n".join(lignes) or _("No slot.")

    @api.depends("slot_id")
    def _compute_slot_detail(self):
        for assistant in self:
            creneau = assistant.slot_id
            assistant.slot_detail = (
                creneau.vote_summary or _("no answer")) if creneau else ""

    def action_confirm(self):
        """Fixe la rencontre et ouvre le rendez-vous qui en sort."""
        self.ensure_one()
        if self.slot_id.poll_id != self.poll_id:
            # ⚠️ Le domaine de la vue n'autorise rien : un client bricolé peut
            # poster l'identifiant d'un créneau d'un autre sondage.
            raise UserError(_("This slot does not belong to this poll."))
        self.poll_id.action_schedule(self.slot_id)
        return self.poll_id.action_view_booking()
