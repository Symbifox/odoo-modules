from odoo import api, fields, models


class CalendarEvent(models.Model):
    """Extension de calendar.event pour le lien avec les comptes rendus."""
    _inherit = 'calendar.event'

    meeting_record_ids = fields.One2many(
        'meeting.record',
        'calendar_event_id',
        string='Comptes rendus (tous)',
    )
    meeting_record_id = fields.Many2one(
        'meeting.record',
        string='Compte rendu',
        compute='_compute_meeting_record_id',
        inverse='_inverse_meeting_record_id',
        help="Compte rendu lié à cette rencontre. Choisir un compte rendu "
             "existant pour le rattacher à cet événement (ou vider pour détacher).",
    )
    meeting_record_count = fields.Integer(
        string='Comptes rendus',
        compute='_compute_meeting_record_id',
    )

    meeting_agenda_ids = fields.One2many(
        'meeting.agenda',
        'calendar_event_id',
        string='Ordres du jour (tous)',
    )
    meeting_agenda_id = fields.Many2one(
        'meeting.agenda',
        string='Ordre du jour',
        compute='_compute_meeting_agenda_id',
        inverse='_inverse_meeting_agenda_id',
        help="Ordre du jour lié à cette rencontre. Choisir un OdJ existant "
             "pour le rattacher à cet événement (ou vider pour détacher).",
    )
    meeting_agenda_count = fields.Integer(
        string='Ordres du jour',
        compute='_compute_meeting_agenda_id',
    )

    bf_skip_agenda = fields.Boolean(
        string='Sans ordre du jour formel',
        help="Cocher pour les rencontres internes courtes ou récurrentes qui ne "
             "nécessitent pas d'ordre du jour formel.",
    )
    bf_skip_dashboard = fields.Boolean(
        string='Exclure du tableau de bord',
        help="Cocher pour masquer cette rencontre du tableau de bord des "
             "rencontres. Utile pour les rencontres dont on ne souhaite plus "
             "voir le suivi (one-shot, annulée en pratique, etc.) sans pour "
             "autant les marquer comme « sans OdJ formel ».",
    )
    bf_dashboard_skipped_steps = fields.Char(
        string='Étapes ignorées (tableau de bord)',
        default='',
        help="Liste séparée par virgules d'indices d'étapes (1-7) marquées "
             "comme non requises pour cette rencontre dans le tableau de bord. "
             "1=OdJ rédigé, 2=OdJ révisé, 3=OdJ envoyé, 4=Rencontre, "
             "5=CR rédigé, 6=CR révisé, 7=CR envoyé.",
    )
    bf_agenda_responsible_id = fields.Many2one(
        'res.users',
        string="Responsable de l'OdJ",
        default=lambda self: self._bf_default_responsible_id(),
        help="Personne responsable de préparer, réviser et envoyer l'ordre "
             "du jour. Par défaut : organisateur de la rencontre. Pilote le "
             "routage du digest quotidien.",
    )
    bf_minutes_responsible_id = fields.Many2one(
        'res.users',
        string='Responsable du CR',
        default=lambda self: self._bf_default_responsible_id(),
        help="Personne responsable de rédiger, réviser et envoyer le compte "
             "rendu. Par défaut : organisateur de la rencontre. Pilote le "
             "routage du digest quotidien.",
    )
    bf_needs_agenda = fields.Boolean(
        string="Besoin d'un ordre du jour",
        compute='_compute_bf_needs_agenda',
        search='_search_bf_needs_agenda',
        help="Vrai si la rencontre est à venir, n'a pas d'ordre du jour lié et "
             "n'est pas marquée comme dispensée.",
    )

    @api.model
    def _bf_default_responsible_id(self):
        """Utilisateur courant, seulement s'il peut réellement être responsable.

        Deux contextes produisent un « utilisateur courant » qui n'a rien à
        faire dans ce champ : un RDV pris depuis le site public est créé par
        le compte public (`share=True`), et une mise à jour de module tourne
        sous OdooBot (`active=False`). On retourne alors False et `create()`
        retombe sur l'organisateur de la rencontre.
        """
        user = self.env.user
        return user.id if (user.active and not user.share) else False

    def _bf_resolve_responsibles(self):
        """Recale les responsables OdJ/CR sur l'organisateur de la rencontre
        quand la valeur en place n'est pas un utilisateur interne actif."""
        for event in self:
            organizer = event.user_id
            if not (organizer and organizer.active and not organizer.share):
                continue
            vals = {}
            for fname in ('bf_agenda_responsible_id', 'bf_minutes_responsible_id'):
                current = event[fname]
                if not current or current.share or not current.active:
                    vals[fname] = organizer.id
            if vals:
                event.write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        events = super().create(vals_list)
        events._bf_resolve_responsibles()
        return events

    @api.depends('meeting_record_ids')
    def _compute_meeting_record_id(self):
        for event in self:
            event.meeting_record_id = event.meeting_record_ids[:1]
            event.meeting_record_count = len(event.meeting_record_ids)

    def _inverse_meeting_record_id(self):
        for event in self:
            target = event.meeting_record_id
            for existing in event.meeting_record_ids:
                if existing != target:
                    existing.calendar_event_id = False
            if target and target.calendar_event_id != event:
                target.calendar_event_id = event.id

    @api.depends('meeting_agenda_ids')
    def _compute_meeting_agenda_id(self):
        for event in self:
            event.meeting_agenda_id = event.meeting_agenda_ids[:1]
            event.meeting_agenda_count = len(event.meeting_agenda_ids)

    def _inverse_meeting_agenda_id(self):
        for event in self:
            target = event.meeting_agenda_id
            for existing in event.meeting_agenda_ids:
                if existing != target:
                    existing.calendar_event_id = False
            if target and target.calendar_event_id != event:
                target.calendar_event_id = event.id

    @api.depends('meeting_agenda_ids', 'bf_skip_agenda', 'start')
    def _compute_bf_needs_agenda(self):
        now = fields.Datetime.now()
        for event in self:
            event.bf_needs_agenda = bool(
                not event.bf_skip_agenda
                and not event.meeting_agenda_ids
                and event.start
                and event.start >= now
            )

    def _search_bf_needs_agenda(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            return [('id', '=', False)]
        positive = (operator == '=' and value) or (operator == '!=' and not value)
        now = fields.Datetime.now()
        domain = [
            ('bf_skip_agenda', '=', False),
            ('meeting_agenda_ids', '=', False),
            ('start', '>=', now),
        ]
        if positive:
            return domain
        return ['!'] + domain

    def action_create_meeting_record(self):
        """Créer un compte rendu à partir de cet événement calendrier.

        Si l'événement a déjà un OdJ lié, le rattacher au compte rendu pour
        unifier la référence à la même rencontre.
        """
        self.ensure_one()
        partner_ids = self.attendee_ids.mapped('partner_id').ids
        duration_minutes = int((self.duration or 0) * 60)
        agenda = self.meeting_agenda_id

        vals = {
            'date': self.start,
            'room_name': self.name,
            'location': self.location or '',
            'duration_minutes': duration_minutes,
            'calendar_event_id': self.id,
            # Seed Présences (the report's participant source), status « present ».
            'attendance_ids': [
                (0, 0, {'partner_id': pid, 'status': 'present'})
                for pid in partner_ids
            ],
            'organizer_id': self.user_id.id if self.user_id else False,
        }
        if agenda and agenda.project_id:
            vals['project_id'] = agenda.project_id.id
        record = self.env['meeting.record'].create(vals)

        if agenda and not agenda.meeting_record_id:
            agenda.write({
                'meeting_record_id': record.id,
                'state': 'done' if agenda.state in ('draft', 'confirmed') else agenda.state,
            })

        return {
            'type': 'ir.actions.act_window',
            'name': record.name,
            'res_model': 'meeting.record',
            'res_id': record.id,
            'views': [[False, 'form']],
        }

    def action_create_meeting_agenda(self):
        """Créer un OdJ rattaché à cet événement (le projet reste à choisir)."""
        self.ensure_one()
        partner_ids = self.attendee_ids.mapped('partner_id').ids
        duration_minutes = int((self.duration or 0) * 60)
        ctx = {
            'default_calendar_event_id': self.id,
            'default_date': self.start,
            'default_duration_planned': duration_minutes,
            'default_location': self.location or '',
            'default_participant_ids': [(6, 0, partner_ids)],
        }
        if self.user_id:
            ctx['default_organizer_id'] = self.user_id.id
        return {
            'type': 'ir.actions.act_window',
            'name': "Nouvel ordre du jour",
            'res_model': 'meeting.agenda',
            'view_mode': 'form',
            'target': 'current',
            'context': ctx,
        }

    def action_view_meeting_record(self):
        """Ouvrir le compte rendu lié."""
        self.ensure_one()
        if self.meeting_record_id:
            return {
                'type': 'ir.actions.act_window',
                'name': self.meeting_record_id.name,
                'res_model': 'meeting.record',
                'res_id': self.meeting_record_id.id,
                'views': [[False, 'form']],
            }

    def action_view_meeting_agenda(self):
        """Ouvrir l'OdJ lié."""
        self.ensure_one()
        if self.meeting_agenda_id:
            return {
                'type': 'ir.actions.act_window',
                'name': self.meeting_agenda_id.name,
                'res_model': 'meeting.agenda',
                'res_id': self.meeting_agenda_id.id,
                'views': [[False, 'form']],
            }
