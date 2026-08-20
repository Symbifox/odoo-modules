# -*- coding: utf-8 -*-
"""Les personnes consultées, et la distinction obligatoire / facultatif."""

import logging
import secrets

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Rythme des relances, en jours après l'ouverture. Deux, puis on s'arrête.
_REMINDER_DAYS = (2, 5)


class AppointmentPollParticipant(models.Model):
    _name = "appointment.poll.participant"
    _description = "Participant à un sondage"
    _order = "required desc, id"

    poll_id = fields.Many2one(
        "appointment.poll",
        string="Sondage",
        required=True,
        ondelete="cascade",
        index=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Contact",
        help="Optionnel : un participant peut n'être qu'une adresse. Le "
             "contact est créé au moment de fixer la rencontre, pas avant — "
             "un sondage sans suite ne doit pas laisser de fiches derrière lui.",
    )
    name = fields.Char(string="Nom")
    email = fields.Char(string="Courriel", required=True)
    required = fields.Boolean(
        string="Présence obligatoire",
        default=False,
        help="Un créneau cesse d'être viable dès qu'une personne obligatoire "
             "y répond Non. Les réponses des personnes facultatives comptent "
             "dans le décompte sans jamais écarter un créneau.",
    )

    access_token = fields.Char(
        string="Jeton",
        copy=False,
        groups="base.group_user",
        help="Identifie la personne qui vote. C'est le lien personnel envoyé "
             "dans l'invitation.",
    )
    vote_ids = fields.One2many(
        "appointment.poll.vote", "participant_id", string="Réponses"
    )
    proposed_slot_ids = fields.One2many(
        "appointment.poll.slot", "proposed_by_id", string="Plages proposées"
    )
    proposed_count = fields.Integer(
        compute="_compute_proposed_count", string="Nombre de plages proposées"
    )
    responded_at = fields.Datetime(string="A répondu le", readonly=True)
    reminder_count = fields.Integer(string="Relances envoyées", default=0, readonly=True)
    last_reminder_date = fields.Datetime(string="Dernière relance", readonly=True)

    _sql_constraints = [
        (
            "email_unique_per_poll",
            "UNIQUE(poll_id, email)",
            "Cette adresse est déjà invitée à ce sondage.",
        ),
    ]

    @api.depends("proposed_slot_ids")
    def _compute_proposed_count(self):
        for participant in self:
            participant.proposed_count = len(participant.proposed_slot_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("access_token"):
                vals["access_token"] = secrets.token_urlsafe(24)
            if not vals.get("name") and vals.get("email"):
                vals["name"] = vals["email"].split("@")[0]
        return super().create(vals_list)

    def _vote_url(self):
        self.ensure_one()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return f"{base}/appointment/poll/{self.access_token}"

    def _ensure_partners(self):
        """Rend (en les créant au besoin) les contacts des participants.

        Appelé au moment de fixer la rencontre seulement : tant que le sondage
        n'a pas abouti, une simple adresse reste une simple adresse.
        """
        Partner = self.env["res.partner"].sudo()
        partners = Partner.browse()
        for participant in self:
            partner = participant.partner_id
            if not partner:
                partner = Partner.search(
                    [("email", "=ilike", participant.email)], limit=1
                )
            if not partner:
                partner = Partner.create({
                    "name": participant.name or participant.email,
                    "email": participant.email,
                })
            participant.partner_id = partner
            partners |= partner
        return partners

    # -- Courriels ---------------------------------------------------------

    def _send_invitation(self):
        """Envoie l'invitation à voter, sous la marque du locataire.

        Le gabarit sort de `data/poll_mail_templates.xml` et suit la même
        mise en marque que les autres courriels du module de rendez-vous —
        c'est un livrable client, pas un envoi technique. Un gabarit absent
        (module partiellement désinstallé) est journalisé, pas fatal.
        """
        template = self.env.ref(
            "bf_appointment_poll.mail_template_poll_invitation",
            raise_if_not_found=False,
        )
        if not template:
            _logger.warning("Gabarit d'invitation au sondage introuvable")
            return False
        for participant in self:
            template.send_mail(participant.id, force_send=False)
        return True

    def _send_reminders(self):
        """Relance les personnes sans réponse, deux fois au plus."""
        template = self.env.ref(
            "bf_appointment_poll.mail_template_poll_reminder",
            raise_if_not_found=False,
        )
        if not template:
            return False
        now = fields.Datetime.now()
        for participant in self:
            poll = participant.poll_id
            if poll.state != "open" or participant.responded_at:
                continue
            if participant.reminder_count >= len(_REMINDER_DAYS):
                continue
            opened = poll.date_opened
            if not opened:
                continue
            due_after = _REMINDER_DAYS[participant.reminder_count]
            if (now - opened).days < due_after:
                continue
            template.send_mail(participant.id, force_send=False)
            participant.write({
                "reminder_count": participant.reminder_count + 1,
                "last_reminder_date": now,
            })
        return True

    def _record_response(self):
        self.write({"responded_at": fields.Datetime.now()})
        return True
