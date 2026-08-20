from odoo import api, fields, models


class AppointmentIntakeField(models.Model):
    _name = "appointment.intake.field"
    _description = "Appointment Intake Form Field"
    _order = "sequence, id"

    type_id = fields.Many2one(
        "resource.booking.type",
        string="Type de rendez-vous",
        required=True,
        ondelete="cascade",
    )
    name = fields.Char(string="Libellé", required=True, translate=True)
    field_type = fields.Selection(
        [
            ("text", "Texte"),
            ("textarea", "Texte (multiligne)"),
            ("email", "Courriel"),
            ("phone", "Téléphone"),
            ("number", "Nombre"),
            ("select", "Liste déroulante"),
        ],
        string="Type de champ",
        default="text",
        required=True,
    )
    required = fields.Boolean(string="Obligatoire", default=False)
    placeholder = fields.Char(string="Texte indicatif", translate=True)
    select_options = fields.Text(
        string="Options (une par ligne)",
        help="Pour les listes déroulantes, saisir une option par ligne.",
    )
    sequence = fields.Integer(default=10)


class AppointmentIntakeAnswer(models.Model):
    _name = "appointment.intake.answer"
    _description = "Appointment Intake Form Answer"

    booking_id = fields.Many2one(
        "resource.booking",
        string="Réservation",
        required=True,
        ondelete="cascade",
    )
    field_id = fields.Many2one(
        "appointment.intake.field",
        string="Champ",
        required=True,
        ondelete="cascade",
    )
    value = fields.Text(string="Réponse")
    field_name = fields.Char(related="field_id.name", string="Question")

    # La description de l'événement d'agenda est construite à partir de ces
    # réponses (`resource.booking._bf_meeting_description`). Elle est posée à la
    # synchro de l'événement ; une réponse écrite ou corrigée après coup doit
    # donc la rafraîchir elle-même, sinon l'agenda garde l'ancien texte.

    @api.model_create_multi
    def create(self, vals_list):
        answers = super().create(vals_list)
        answers.booking_id._bf_sync_meeting_description()
        return answers

    def write(self, vals):
        bookings = self.booking_id
        result = super().write(vals)
        (bookings | self.booking_id)._bf_sync_meeting_description()
        return result

    def unlink(self):
        bookings = self.booking_id
        result = super().unlink()
        bookings.exists()._bf_sync_meeting_description()
        return result
