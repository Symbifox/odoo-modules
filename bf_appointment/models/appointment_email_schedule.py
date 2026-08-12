from odoo import fields, models


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
        [("before", "Avant le rendez-vous"), ("after", "Apr\u00e8s le rendez-vous")],
        string="D\u00e9clencheur",
        required=True,
    )
    hours = fields.Float(
        string="D\u00e9lai (heures)",
        required=True,
        help="Nombre d'heures avant/apr\u00e8s le rendez-vous.",
    )
    template_id = fields.Many2one(
        "mail.template",
        string="Mod\u00e8le de courriel",
        required=True,
        domain="[('model_id.model', '=', 'resource.booking')]",
    )
    active = fields.Boolean(string="Actif", default=True)
    name = fields.Char(compute="_compute_name", store=True)

    def _compute_name(self):
        for rec in self:
            direction = "avant" if rec.trigger == "before" else "apr\u00e8s"
            if rec.hours >= 24:
                days = rec.hours / 24
                time_str = f"{days:g} jour(s)"
            else:
                time_str = f"{rec.hours:g}h"
            rec.name = f"{time_str} {direction}"
