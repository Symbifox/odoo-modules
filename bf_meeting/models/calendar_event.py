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
        # ⚠️ Un `onchange` ne se joue que dans un formulaire. Les rencontres
        # arrivent ici par la synchronisation Nextcloud, par la prise de
        # rendez-vous et par import — jamais par un formulaire. Sans ce relais,
        # une étiquette dispensant d'OdJ ne dispenserait de rien dans
        # exactement les cas où on la pose en lot.
        events._bf_apply_tag_skips()
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

    # ------------------------------------------------------------------
    # Deux pastilles sur la vue Calendrier : OdJ et compte rendu
    # ------------------------------------------------------------------

    # Mêmes étapes que le tableau de bord, repliées en trois états par
    # document. Le tableau de bord en montre sept parce qu'il sert à savoir
    # QUOI faire ensuite ; une pastille de calendrier sert à savoir s'il reste
    # quelque chose à faire, et sept nuances dans une icône de dix pixels ne se
    # lisent pas.
    _BF_DOC_STATES = [
        ('none', 'Absent'),
        ('draft', 'Rédigé'),
        ('reviewed', 'Révisé'),
        ('sent', 'Envoyé'),
        ('skipped', 'Non requis'),
    ]

    bf_agenda_state = fields.Selection(
        _BF_DOC_STATES,
        string="État de l'OdJ",
        compute='_compute_bf_doc_states',
        store=True,
        help="Avancement de l'ordre du jour, résumé pour la pastille de la "
             "vue Calendrier. Reprend les étapes 1 à 3 du tableau de bord.",
    )
    bf_minutes_state = fields.Selection(
        _BF_DOC_STATES,
        string='État du compte rendu',
        compute='_compute_bf_doc_states',
        store=True,
        help="Avancement du compte rendu, résumé pour la pastille de la vue "
             "Calendrier. Reprend les étapes 5 à 7 du tableau de bord.",
    )

    # ⚠️ Aucun `default=` sur ces deux champs, et c'est délibéré : un défaut sur
    # un champ calculé stocké remplit la valeur à la création, le champ cesse
    # d'être « à calculer » et le calcul n'est jamais joué. Le symptôme serait
    # le pire possible — une valeur plausible (« Absent ») sur une rencontre qui
    # a bel et bien un OdJ. Le calcul rend déjà 'none' quand il n'y a rien.
    @api.depends('meeting_agenda_ids', 'meeting_agenda_ids.state',
                 'meeting_agenda_ids.email_sent_date',
                 'meeting_record_ids', 'meeting_record_ids.report_state',
                 'bf_skip_agenda', 'bf_skip_dashboard')
    def _compute_bf_doc_states(self):
        """Les deux états, stockés.

        ⚠️ Stockés, et c'est le point : la vue calendrier lit ses lignes par un
        `search_read` sur TOUS les champs déclarés dans l'arch. Un calcul non
        stocké se rejouerait donc par événement affiché — plusieurs centaines
        en vue mois — et chacun traverserait l'OdJ et le compte rendu. Stocké,
        c'est une colonne.

        L'OdJ lit `email_sent_date` et non `sent_date` : le second est posé à
        la simple ouverture du composeur et compterait comme envoyé un OdJ
        qu'on a seulement regardé. Voir `meeting.agenda.send_state`.
        """
        for event in self:
            # La dispense l'emporte, même quand un document existe déjà.
            #
            # C'est ce que fait déjà le tableau de bord : `bf_skip_agenda` en
            # retire la LIGNE ENTIÈRE, sans regarder s'il y a un OdJ. Les deux
            # surfaces doivent dire la même chose, sinon le bouton « Aucune
            # préparation » sort la rencontre du tableau de bord tout en
            # laissant sa pastille allumée dans la grille — ce qui s'est
            # exactement produit à l'essai.
            #
            # Et c'est la bonne lecture : une pastille répond « reste-t-il
            # quelque chose à faire ? ». Sur une rencontre dispensée, non.
            if event.bf_skip_agenda or event.bf_skip_dashboard:
                event.bf_agenda_state = 'skipped'
                event.bf_minutes_state = 'skipped'
                continue

            agenda = event.meeting_agenda_ids[:1]
            record = event.meeting_record_ids[:1]

            if agenda:
                if agenda.email_sent_date:
                    event.bf_agenda_state = 'sent'
                elif agenda.state in ('confirmed', 'done'):
                    event.bf_agenda_state = 'reviewed'
                else:
                    event.bf_agenda_state = 'draft'
            else:
                event.bf_agenda_state = 'none'

            if record:
                if record.report_state == 'sent':
                    event.bf_minutes_state = 'sent'
                elif record.report_state == 'reviewed':
                    event.bf_minutes_state = 'reviewed'
                else:
                    event.bf_minutes_state = 'draft'
            else:
                event.bf_minutes_state = 'none'

    # ------------------------------------------------------------------
    # Étiquettes qui dispensent d'OdJ et de compte rendu
    # ------------------------------------------------------------------

    @api.onchange('categ_ids')
    def _onchange_categ_ids_bf_skip(self):
        """Coche les dispenses portées par les étiquettes choisies.

        Un `onchange` et non un calcul : les deux cases restent modifiables à
        la main sur une rencontre précise, et un champ calculé les rendrait au
        prochain enregistrement. L'étiquette pose la valeur par défaut, elle ne
        la confisque pas.

        Ne décoche jamais. Retirer l'étiquette « Interne » d'une rencontre ne
        veut pas dire qu'on réclame soudain un ordre du jour formel pour elle,
        et une case qui se décoche toute seule ferait réapparaître sur le
        tableau de bord des rencontres qu'on en avait délibérément sorties.
        """
        for event in self:
            tags = event.categ_ids
            if any(tags.mapped('bf_skip_agenda')):
                event.bf_skip_agenda = True
            if any(tags.mapped('bf_skip_dashboard')):
                event.bf_skip_dashboard = True

    def write(self, vals):
        res = super().write(vals)
        if 'categ_ids' in vals:
            self._bf_apply_tag_skips()
        return res

    def _bf_apply_tag_skips(self):
        """Pose les dispenses portées par les étiquettes, sans jamais les retirer.

        Ne décoche jamais : retirer l'étiquette « Interne » d'une rencontre ne
        veut pas dire qu'on réclame soudain un ordre du jour formel pour elle,
        et une case qui se décoche toute seule ferait réapparaître sur le
        tableau de bord des rencontres qu'on en avait délibérément sorties.
        """
        for event in self:
            tags = event.categ_ids
            if not tags:
                continue
            vals = {}
            if any(tags.mapped('bf_skip_agenda')) and not event.bf_skip_agenda:
                vals['bf_skip_agenda'] = True
            if any(tags.mapped('bf_skip_dashboard')) and not event.bf_skip_dashboard:
                vals['bf_skip_dashboard'] = True
            if vals:
                event.write(vals)
