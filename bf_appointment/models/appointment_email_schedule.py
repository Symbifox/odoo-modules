from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from . import _sms_text


class AppointmentEmailSchedule(models.Model):
    _name = "appointment.email.schedule"
    _description = "Appointment Email Schedule"
    _order = "trigger desc, hours"

    type_id = fields.Many2one(
        "resource.booking.type",
        string="Type de rendez-vous",
        required=True,
        ondelete="cascade",
    )
    trigger = fields.Selection(
        [("before", "Avant le rendez-vous"), ("after", "Après le rendez-vous")],
        string="Déclencheur",
        required=True,
    )
    hours = fields.Float(
        string="Délai (heures)",
        required=True,
        help="Nombre d'heures avant/après le rendez-vous.",
    )
    channel = fields.Selection(
        [
            ("email", "Courriel"),
            ("sms", "SMS"),
            ("both", "Courriel + SMS"),
        ],
        string="Canal",
        required=True,
        default="email",
        help="« SMS » exige un numéro connu pour le demandeur ; à défaut, "
             "le courriel prend le relais automatiquement, de sorte qu'un "
             "rappel n'est jamais perdu. « Courriel + SMS » envoie les deux.",
    )
    template_id = fields.Many2one(
        "mail.template",
        string="Modèle de courriel",
        required=True,
        domain="[('model_id.model', '=', 'resource.booking')]",
        help="Toujours requis, y compris sur un canal SMS : c'est le filet "
             "quand le SMS ne peut pas partir.",
    )
    sms_body = fields.Char(
        string="Message SMS",
        help="Un seul segment, 150 caractères max, alphabet GSM-7. "
             "Les champs du rendez-vous s'insèrent avec {{ object.… }}, "
             "p. ex. {{ object.type_id.name }}.",
    )
    active = fields.Boolean(string="Actif", default=True)
    name = fields.Char(compute="_compute_name", store=True)

    @api.depends("trigger", "hours", "channel")
    def _compute_name(self):
        for rec in self:
            direction = "avant" if rec.trigger == "before" else "après"
            if rec.hours >= 24:
                days = rec.hours / 24
                time_str = f"{days:g} jour(s)"
            else:
                time_str = f"{rec.hours:g}h"
            suffix = {"sms": " (SMS)", "both": " (courriel + SMS)"}.get(
                rec.channel, ""
            )
            rec.name = f"{time_str} {direction}{suffix}"

    @api.constrains("channel", "sms_body")
    def _check_sms_body(self):
        """An SMS channel must carry a body VoIP.ms will actually accept.

        Screened here rather than at send time because the cron only learns
        of a refusal as a bare ``False``, long after the author has moved on.
        """
        for rec in self:
            if rec.channel in ("sms", "both"):
                if not (rec.sms_body or "").strip():
                    raise ValidationError(_(
                        "Un canal SMS exige un message SMS."
                    ))
                error = _sms_text.check(rec.sms_body)
                if error:
                    raise ValidationError(error)
