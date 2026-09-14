from odoo import _, api, fields, models


class EventEvent(models.Model):
    _inherit = "event.event"

    training_activity_id = fields.Many2one(
        "bf.training.activity", string="Activité de formation", index=True,
        help="L'activité du registre que cette séance fait suivre. Sans elle, "
             "l'événement reste un événement : rien n'est écrit au registre.")
    training_hours = fields.Float(
        string="Heures créditées", digits=(6, 2),
        compute="_compute_training_hours", store=True, readonly=False,
        help="Les heures portées au registre pour qui assiste. Proposées depuis "
             "la durée de la séance, ou à défaut depuis l'activité. Laisser à "
             "zéro consigne une réalisation incomplète, jamais zéro heure.")
    training_requires_signature = fields.Boolean(
        string="Feuille de présence signée", default=True,
        help="La présence doit être signée par la personne. Tant qu'elle ne "
             "l'est pas, la réalisation est écrite mais marquée incomplète.")
    training_record_count = fields.Integer(
        string="Réalisations écrites", compute="_compute_training_record_count")

    @api.depends("date_begin", "date_end", "training_activity_id")
    def _compute_training_hours(self):
        """Proposer les heures, sans jamais les inventer.

        🔴 La durée d'un événement est du temps d'HORLOGE : une séance qui court
        de 9 h à 16 h fait sept heures d'horloge, pauses et dîner compris. Elle
        n'est donc qu'une proposition, et la durée prévue de l'activité la
        précède quand elle existe : c'est celle-là que quelqu'un a établie.
        """
        for seance in self:
            prevue = seance.training_activity_id.duration_hours
            if prevue:
                seance.training_hours = prevue
            elif seance.date_begin and seance.date_end:
                delta = seance.date_end - seance.date_begin
                seance.training_hours = round(delta.total_seconds() / 3600.0, 2)
            else:
                seance.training_hours = 0.0

    def _compute_training_record_count(self):
        Realisation = self.env["bf.training.record"]
        for seance in self:
            seance.training_record_count = Realisation.search_count(
                [("event_id", "=", seance.id)])

    def _jour_de_la_seance(self):
        """La date à porter au registre : celle de la SÉANCE.

        🔴 Surtout pas `event.registration.date_closed`, qui vaut l'instant du
        clic. Une séance de mars pointée en septembre s'écrirait en septembre,
        et le registre daterait la formation du jour où on s'en est souvenu.
        """
        self.ensure_one()
        if not self.date_begin:
            return False
        return fields.Datetime.context_timestamp(self, self.date_begin).date()

    def action_open_training_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Réalisations de la séance"),
            "res_model": "bf.training.record",
            "views": [(False, "list"), (False, "form")],
            "domain": [("event_id", "=", self.id)],
        }

    def action_ecrire_au_registre(self):
        """Écrire au registre toutes les présences de la séance.

        Idempotent : une inscription qui porte déjà sa réalisation est sautée.
        """
        self.ensure_one()
        return self.registration_ids._ecrire_au_registre()
