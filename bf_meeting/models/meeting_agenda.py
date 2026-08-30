import json
import logging
import os
import secrets
import socket
import threading
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError

from .meeting_record import (
    _MAX_REFINE_MESSAGE,
    _REFINE_STALE_MINUTES,
    _format_meeting_date_display,
)

_logger = logging.getLogger(__name__)

ACTIVE_AGENDA_STATES = ('draft', 'confirmed')
CLOSED_TASK_STATES = ('1_done', '1_canceled')

REMINDER_LEAD_DAYS = 7
REMINDER_ACTIVITY_SUMMARY = "Envoyer l'ordre du jour avant la rencontre"

_DEFAULT_BRIDGE_SOCKET = "/run/claude-bridge/bridge.sock"


_EMPTY_HTML_VALUES = ('', '<p><br></p>', '<p><br/></p>', '<p></p>')


def _html_is_blank(value):
    """True if an Html field is effectively empty (incl. Odoo's <p><br/></p>)."""
    if not value:
        return True
    return value.strip() in _EMPTY_HTML_VALUES


def _wrap_agenda_original(html):
    """Wrap agenda description HTML in a styled blockquote.

    The inline style survives sanitize_style=True and gives notetakers an
    immediate visual cue distinguishing pre-filled context from their own
    annotations. The class hook lets us swap to a CSS asset later.
    """
    return Markup(
        '<div class="bf-agenda-original" '
        'style="border-left-width: 3px; border-left-style: solid; '
        'border-left-color: #29ABE1; padding-left: 0.75em; color: #555; '
        'margin: 0.25em 0;">'
        '<p><small><em>Contexte d\'origine</em></small></p>'
        '%s'
        '</div>'
        '<p><br/></p>'
    ) % Markup(html or '')


def _highlight_user_additions(html_str):
    """Mark notes typed outside the agenda blockquote as user additions.

    Walks top-level nodes and applies a yellow-highlight inline style to any
    block that is not the pre-filled <blockquote class="bf-agenda-original">.
    Empty paragraphs are left untouched so the rendered compte rendu stays
    visually clean.
    """
    if not html_str:
        return html_str
    from lxml import html as _html
    try:
        wrapper = _html.fragment_fromstring(html_str, create_parent='div')
    except Exception:
        return html_str
    highlight = 'background-color: #fff3cd; padding: 0 2px;'
    changed = False
    for child in list(wrapper):
        cls = (child.get('class') or '')
        if 'bf-agenda-original' in cls:
            continue
        if not (child.text_content() or '').strip():
            continue
        existing = (child.get('style') or '').rstrip()
        sep = '; ' if existing and not existing.endswith(';') else ''
        child.set('style', f'{existing}{sep}{highlight}')
        changed = True
    if not changed:
        return html_str
    return ''.join(
        _html.tostring(c, encoding='unicode', with_tail=True) for c in wrapper
    )


def _post_to_bridge(socket_path, endpoint, payload, timeout):
    """Minimal HTTP-over-Unix-socket POST, returns parsed JSON response.

    Mirrors the helper in meeting_record.py to keep this file standalone.
    """
    body = json.dumps(payload).encode()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
        req = (
            f"POST {endpoint} HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode() + body
        sock.sendall(req)
        chunks = []
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks).decode()
        header_end = raw.find("\r\n\r\n")
        if header_end == -1:
            raise ValueError("Malformed HTTP response from bridge")
        status_line = raw[:raw.find("\r\n")]
        status_code = int(status_line.split(" ", 2)[1])
        resp_body = raw[header_end + 4:]
        headers_block = raw[:header_end].lower()
        if "transfer-encoding: chunked" in headers_block:
            decoded = []
            pos = 0
            while pos < len(resp_body):
                nl = resp_body.find("\r\n", pos)
                if nl == -1:
                    break
                chunk_size = int(resp_body[pos:nl], 16)
                if chunk_size == 0:
                    break
                decoded.append(resp_body[nl + 2:nl + 2 + chunk_size])
                pos = nl + 2 + chunk_size + 2
            resp_body = "".join(decoded)
        if status_code >= 400:
            raise ValueError(f"Bridge HTTP {status_code}: {resp_body[:200]}")
        return json.loads(resp_body)
    finally:
        sock.close()


class MeetingAgenda(models.Model):
    """Ordre du jour d'une réunion."""
    _name = 'meeting.agenda'
    _description = 'Ordre du jour'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'bf.meeting.document.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(
        string='Titre',
        compute='_compute_name',
        store=True,
        readonly=False,
    )
    project_id = fields.Many2one(
        'project.project',
        string='Projet',
        required=True,
        index=True,
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Client',
        related='project_id.partner_id',
        store=True,
        readonly=True,
    )
    date = fields.Datetime(
        string='Date',
        required=True,
        default=fields.Datetime.now,
        tracking=True,
    )
    duration_planned = fields.Integer(
        string='Durée prévue (minutes)',
    )
    location = fields.Char(
        string='Lieu',
    )
    meeting_type = fields.Selection(
        [
            ('in_person', 'Présentiel'),
            ('video', 'Visio'),
            ('phone', 'Téléphonique'),
            ('hybrid', 'Hybride'),
            ('async', 'Asynchrone'),
        ],
        string='Mode',
        default='video',
        required=True,
        index=True,
        tracking=True,
        help='Mode prévu pour la rencontre. Propagé au compte rendu à la création.',
    )
    meeting_type_icon = fields.Char(
        string='Icône mode',
        compute='_compute_meeting_type_icon',
        store=True,
    )
    lang = fields.Selection(
        lambda self: self.env['res.lang'].get_installed(),
        string='Langue',
        compute='_compute_lang',
        store=True,
        readonly=False,
        help="Langue de l'ordre du jour : pilote le rapport PDF et le "
             "courriel d'envoi. Propagée au compte rendu.",
    )

    @api.depends('meeting_type')
    def _compute_meeting_type_icon(self):
        glyphs = {
            'in_person': '👥',
            'video': '🎥',
            'phone': '📞',
            'hybrid': '🔀',
            'async': '💬',
        }
        for rec in self:
            rec.meeting_type_icon = glyphs.get(rec.meeting_type, '')

    @api.depends('partner_id')
    def _compute_lang(self):
        default_lang = self.env.lang or 'fr_CA'
        for rec in self:
            if rec.lang:
                continue
            rec.lang = (rec.partner_id and rec.partner_id.lang) or default_lang
    series_name = fields.Char(
        string='Série',
        index=True,
    )
    organizer_id = fields.Many2one(
        'res.users',
        string='Organisateur',
        default=lambda self: self.env.user,
    )
    participant_ids = fields.Many2many(
        'res.partner',
        'meeting_agenda_participant_rel',
        'agenda_id',
        'partner_id',
        string='Participants',
    )

    # Content
    objectives = fields.Text(
        string='Objectifs',
        help='Ce que nous souhaitons accomplir lors de cette rencontre.',
    )
    context_html = fields.Html(
        string='Contexte',
        sanitize_style=True,
        help='Contexte et état des lieux avant la rencontre.',
    )
    preparation_html = fields.Html(
        string='Préparation',
        sanitize_style=True,
        help='Liste de vérification pour la préparation.',
    )
    live_notes_html = fields.Html(
        string='Notes en direct',
        sanitize_style=True,
        help="Scratchpad pour la prise de notes pendant la rencontre. "
             "N'apparaît pas dans le PDF de l'ordre du jour ; sera transféré "
             "dans le résumé du compte rendu à sa création.",
    )

    # Relations
    topic_ids = fields.One2many(
        'meeting.agenda.topic',
        'agenda_id',
        string='Sujets',
    )
    live_decision_ids = fields.One2many(
        'meeting.decision',
        'agenda_id',
        string='Décisions en direct',
        help="Décisions capturées pendant la rencontre. Transférées au "
             "compte rendu à sa création.",
    )
    topic_count = fields.Integer(
        string='Nombre de sujets',
        compute='_compute_topic_count',
    )
    agenda_task_ids = fields.Many2many(
        'project.task',
        string="Éléments d'action à discuter",
        compute='_compute_agenda_task_ids',
        help="Tâches ouvertes rattachées à cet ordre du jour : lien explicite "
             "ou tag « Prochaine rencontre » résolvant à cet agenda.",
    )
    agenda_task_count = fields.Integer(
        compute='_compute_agenda_task_ids',
    )
    meeting_record_id = fields.Many2one(
        'meeting.record',
        string='Compte rendu',
        index=True,
    )
    calendar_event_id = fields.Many2one(
        'calendar.event',
        string='Événement calendrier',
    )

    # Email
    auto_send_on_confirm = fields.Boolean(
        string='Envoyer automatiquement à la confirmation',
        default=False,
    )
    sent_date = fields.Datetime(
        string="Envoi amorcé le",
        readonly=True,
        help="Horodatage posé dès qu'un envoi est amorcé — bouton « Envoyer » "
             "ou simple ouverture du composeur. Il ouvre la fenêtre de "
             "contributions publiques ; il ne prouve PAS qu'un courriel est "
             "parti : voir « Envoyé le ».",
    )
    email_sent_date = fields.Datetime(
        string='Envoyé le',
        readonly=True,
        copy=False,
        help="Départ réel de l'ordre du jour : courriel émis par Odoo, ou "
             "envoi fait hors Odoo et déclaré à la main.",
    )
    sent_manually = fields.Boolean(
        string='Envoi déclaré à la main',
        readonly=True,
        copy=False,
        help="L'ordre du jour est parti par un autre canal qu'Odoo (client de "
             "messagerie, clavardage, remise en personne).",
    )
    send_state = fields.Selection(
        [
            ('not_sent', 'Non envoyé'),
            ('prepared', 'Envoi non confirmé'),
            ('sent', 'Envoyé'),
            ('manual', 'Envoyé à la main'),
        ],
        string="Envoi",
        compute='_compute_send_state',
        store=True,
        tracking=True,
        default='not_sent',
        help="« Envoi non confirmé » = un envoi a été amorcé mais aucun "
             "courriel n'a quitté Odoo — typiquement le composeur ouvert puis "
             "abandonné.",
    )

    @api.depends('sent_date', 'email_sent_date', 'sent_manually')
    def _compute_send_state(self):
        for rec in self:
            if rec.email_sent_date:
                rec.send_state = 'manual' if rec.sent_manually else 'sent'
            elif rec.sent_date:
                rec.send_state = 'prepared'
            else:
                rec.send_state = 'not_sent'
    recipient_ids = fields.Many2many(
        'res.partner',
        'meeting_agenda_recipient_rel',
        'agenda_id',
        'partner_id',
        string='Destinataires',
        help='Si vide, les participants recevront le courriel.',
    )
    partner_to_ids = fields.Char(
        string='IDs destinataires (technique)',
        compute='_compute_partner_to_ids',
    )

    @api.depends('recipient_ids', 'participant_ids')
    def _compute_partner_to_ids(self):
        for rec in self:
            recipients = rec.recipient_ids or rec.participant_ids
            rec.partner_to_ids = ','.join(str(pid) for pid in recipients.ids)

    # Public contributions (after send, until confirmation)
    allow_contributions = fields.Boolean(
        string='Autoriser les contributions des destinataires',
        default=True,
        help="Si activé, le courriel d'ordre du jour inclut un lien permettant "
             "aux destinataires de proposer des sujets et d'ajouter des notes "
             "jusqu'à la confirmation de l'ordre du jour.",
    )
    access_token = fields.Char(
        string="Jeton d'accès public",
        copy=False,
        index=True,
        readonly=True,
        groups='bf_meeting.group_meeting_user',
        help="Jeton secret donnant accès à la page publique de contributions. "
             "Frappé à l'envoi, jamais exposé en lecture portail/publique.",
    )
    contributions_open = fields.Boolean(
        string='Contributions ouvertes',
        compute='_compute_contributions_open',
        store=True,
        help="Vrai entre l'envoi et la confirmation de l'ordre du jour : "
             "les destinataires peuvent alors proposer des sujets et des notes.",
    )
    contribution_url = fields.Char(
        string='Lien de contribution',
        compute='_compute_contribution_url',
        groups='bf_meeting.group_meeting_user',
    )

    contribution_pending_count = fields.Integer(
        string='Sujets proposés à examiner',
        compute='_compute_contribution_pending_count',
    )

    @api.depends('sent_date', 'state', 'allow_contributions')
    def _compute_contributions_open(self):
        for rec in self:
            rec.contributions_open = bool(
                rec.allow_contributions and rec.sent_date and rec.state == 'draft'
            )

    @api.depends('topic_ids.moderation_state')
    def _compute_contribution_pending_count(self):
        for rec in self:
            rec.contribution_pending_count = len(rec.topic_ids.filtered(
                lambda t: t.moderation_state == 'pending'))

    published_topic_ids = fields.One2many(
        'meeting.agenda.topic',
        compute='_compute_published_topic_ids',
        string='Sujets publiés',
        help="Sujets acceptés uniquement : ceux qui paraissent dans le "
             "courriel et le PDF de l'ordre du jour (les propositions en "
             "modération sont exclues).",
    )

    @api.depends('topic_ids.moderation_state', 'topic_ids.sequence')
    def _compute_published_topic_ids(self):
        for rec in self:
            rec.published_topic_ids = rec.topic_ids.filtered(
                lambda t: t.moderation_state == 'accepted').sorted('sequence')

    def _compute_contribution_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param(
            'web.base.url', '').rstrip('/')
        for rec in self:
            rec.contribution_url = (
                "%s/meeting/agenda/%s" % (base, rec.access_token)
                if base and rec.access_token else False
            )

    def _ensure_access_token(self):
        """Mint a public access token lazily; idempotent.

        Called at send time (not at create) so draft agendas never sent carry
        no live public capability — a smaller exposure surface.
        """
        for rec in self:
            if not rec.access_token:
                rec.access_token = secrets.token_urlsafe(32)  # 256 bits

    def _notify_contribution(self, kind, who, excerpt):
        """Post a chatter note for a public contribution and nudge the organizer.

        Called from the public controller under ``sudo()``. Followers (the
        organizer is normally one) are notified by the note; an actionable
        activity is scheduled once (idempotent via summary) so the organizer
        gets a single « go triage » item rather than a flood.
        """
        self.ensure_one()
        label = ("a proposé un sujet" if kind == 'topic'
                 else "a laissé un commentaire")
        body = Markup("<p>📥 <strong>%s</strong> %s : <em>%s</em></p>") % (
            escape(who or 'Anonyme'), label, escape((excerpt or '')[:120]))
        self.sudo().message_post(
            body=body, message_type='comment', subtype_xmlid='mail.mt_note')
        organizer = self.organizer_id
        if organizer and not organizer.share and organizer.active:
            summary = "Contributions reçues sur l'ordre du jour"
            already = self.env['mail.activity'].sudo().search_count([
                ('res_model', '=', 'meeting.agenda'),
                ('res_id', '=', self.id),
                ('summary', '=', summary),
            ])
            if not already:
                self.sudo().activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=summary,
                    note="Des destinataires ont proposé des sujets / "
                         "commentaires. Réviser avant de confirmer l'ordre "
                         "du jour.",
                    user_id=organizer.id,
                )

    # State
    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('confirmed', 'Confirmé'),
        ('done', 'Terminé'),
        ('cancelled', 'Annulé'),
    ], string='État', default='draft', tracking=True)

    # Source
    nc_path = fields.Char(
        string='Chemin Nextcloud',
    )

    company_id = fields.Many2one(
        'res.company',
        string='Société',
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)
    refine_state = fields.Selection([
        ('none', 'Non lancé'),
        ('queued', "En cours"),
        ('done', 'Terminé'),
        ('error', 'Erreur'),
    ], string="Pré-remplissage Gen", default='none', readonly=True, copy=False)
    refine_date = fields.Datetime(
        string='Pré-remplissage — dernier signal',
        readonly=True,
        copy=False,
    )
    refine_message = fields.Char(
        string='Pré-remplissage — détail',
        readonly=True,
        copy=False,
    )
    refine_in_progress = fields.Boolean(
        string='Pré-remplissage en cours',
        compute='_compute_refine_in_progress',
        help="Vrai seulement pendant la fenêtre où la passe peut encore "
             "aboutir.",
    )

    @api.depends('refine_state', 'refine_date')
    def _compute_refine_in_progress(self):
        """Voir `meeting.record._compute_refine_in_progress` : même borne, même
        raison — un pré-remplissage tué en vol cachait le bouton pour toujours.
        """
        try:
            ceiling = int(self.env['ir.config_parameter'].sudo().get_param(
                'bf_meeting.refine_stale_minutes', _REFINE_STALE_MINUTES))
        except (TypeError, ValueError):
            ceiling = _REFINE_STALE_MINUTES
        cutoff = fields.Datetime.now() - timedelta(minutes=ceiling)
        for rec in self:
            rec.refine_in_progress = bool(
                rec.refine_state == 'queued'
                and rec.refine_date
                and rec.refine_date > cutoff
            )
    is_today = fields.Boolean(
        string="Aujourd'hui",
        compute='_compute_is_today',
        search='_search_is_today',
        help="True si la rencontre tombe aujourd'hui (timezone utilisateur).",
    )

    @api.depends('date')
    def _compute_is_today(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.date:
                rec.is_today = False
                continue
            rec_date = fields.Datetime.context_timestamp(rec, rec.date).date()
            rec.is_today = rec_date == today

    def _search_is_today(self, operator, value):
        today = fields.Date.context_today(self)
        start = fields.Datetime.to_string(
            fields.Datetime.from_string(f"{today} 00:00:00")
        )
        end = fields.Datetime.to_string(
            fields.Datetime.from_string(f"{today} 23:59:59")
        )
        in_range = operator in ('=', '!=')
        if not in_range:
            return [('id', '=', 0)]
        match = bool(value) if operator == '=' else not bool(value)
        if match:
            return [('date', '>=', start), ('date', '<=', end)]
        return ['|', ('date', '<', start), ('date', '>', end)]

    @api.depends('project_id', 'date')
    def _compute_name(self):
        for rec in self:
            if rec.name:
                continue
            parts = []
            if rec.project_id:
                parts.append(rec.project_id.name)
            parts.append('OdJ')
            if rec.date:
                parts.append(rec.date.strftime('%Y-%m-%d'))
            rec.name = ' — '.join(parts) if parts else 'Nouvel ordre du jour'

    @api.depends('topic_ids')
    def _compute_topic_count(self):
        for rec in self:
            rec.topic_count = len(rec.topic_ids)

    def _compute_agenda_task_ids(self):
        Task = self.env['project.task']
        Agenda = self.env['meeting.agenda']
        now = fields.Datetime.now()

        # Partition self: only active/future agendas can carry tasks.
        active = self.filtered(
            lambda a: a.state in ACTIVE_AGENDA_STATES and a.date and a.date >= now
        )
        for rec in self - active:
            rec.agenda_task_ids = False
            rec.agenda_task_count = 0

        if not active:
            return

        # Hard-linked tasks: single query, grouped by agenda.
        hard_tasks = Task.search([
            ('bf_meeting_agenda_id', 'in', active.ids),
            ('state', 'not in', CLOSED_TASK_STATES),
        ])
        hard_by_agenda = {}
        for t in hard_tasks:
            hard_by_agenda.setdefault(t.bf_meeting_agenda_id.id, Task)
            hard_by_agenda[t.bf_meeting_agenda_id.id] |= t

        # Resolve winners: earliest upcoming draft/confirmed agenda per
        # partner_id and per project_id across the whole database (not just self).
        partner_ids = active.mapped('partner_id').ids
        partner_commercial_ids = active.mapped('partner_id').commercial_partner_id.ids
        project_ids = active.mapped('project_id').ids
        partner_winner = {}
        if partner_ids:
            candidates = Agenda.search([
                ('partner_id', 'in', partner_ids),
                ('state', 'in', ACTIVE_AGENDA_STATES),
                ('date', '>=', now),
            ], order='date asc, id asc')
            for cand in candidates:
                partner_winner.setdefault(cand.partner_id.id, cand.id)
        project_winner = {}
        if project_ids:
            candidates = Agenda.search([
                ('project_id', 'in', project_ids),
                ('state', 'in', ACTIVE_AGENDA_STATES),
                ('date', '>=', now),
            ], order='date asc, id asc')
            for cand in candidates:
                project_winner.setdefault(cand.project_id.id, cand.id)

        # Tagged tasks: 2 batched searches (one per scope) instead of 4.
        # Partition by tag in Python. Per-agenda filtering afterwards.
        client_tagged = Task
        if partner_ids:
            # commercial_partner_id is stored, so a task filed against a child
            # contact is reachable here. Keep this domain at least as wide as
            # _match_partner below: anything the filter would accept has to be
            # in the candidate set first, or the rollup never gets a say.
            client_tagged = Task.search([
                ('bf_discuss_tag', 'in', ('next_client', 'all_client')),
                ('bf_meeting_agenda_id', '=', False),
                ('state', 'not in', CLOSED_TASK_STATES),
                '|', '|',
                ('partner_id', 'in', partner_ids),
                ('partner_id.commercial_partner_id', 'in', partner_commercial_ids),
                ('project_id.partner_id', 'in', partner_ids),
            ])
        project_tagged = Task
        if project_ids:
            project_tagged = Task.search([
                ('bf_discuss_tag', 'in', ('next_project', 'all_project')),
                ('bf_meeting_agenda_id', '=', False),
                ('state', 'not in', CLOSED_TASK_STATES),
                ('project_id', 'in', project_ids),
            ])

        # Company behind each agenda contact, resolved once for the loop below.
        agenda_commercial = {
            p.id: p.commercial_partner_id.id for p in active.mapped('partner_id')
        }

        def _match_partner(t, pid):
            # Both sides roll up to the company. A task filed against a child
            # contact ("Acme Inc., a named person") belongs on the agenda held
            # with the company ("Acme Inc."). The search above already accepts
            # that task through project_id.partner_id; without the same rollup
            # here it is dropped again, so it reaches neither agenda_task_ids
            # nor the "Éléments d'action à discuter" section of the PDF.
            target = agenda_commercial.get(pid, pid)
            partner = t.partner_id or t.project_id.partner_id
            return bool(partner) and target == partner.commercial_partner_id.id

        # Constant — avoid recomputing each task during the sort.
        # date_deadline is Datetime in Odoo 18; keep types aligned.
        sort_max_date = fields.Datetime.now().replace(year=9999)

        for rec in active:
            collected = Task
            collected |= hard_by_agenda.get(rec.id, Task)
            if rec.partner_id:
                pid = rec.partner_id.id
                is_partner_winner = partner_winner.get(pid) == rec.id
                collected |= client_tagged.filtered(
                    lambda t, pid=pid, win=is_partner_winner: _match_partner(t, pid) and (
                        t.bf_discuss_tag == 'all_client'
                        or (t.bf_discuss_tag == 'next_client' and win)
                    )
                )
            if rec.project_id:
                prj = rec.project_id.id
                is_project_winner = project_winner.get(prj) == rec.id
                collected |= project_tagged.filtered(
                    lambda t, prj=prj, win=is_project_winner: t.project_id.id == prj and (
                        t.bf_discuss_tag == 'all_project'
                        or (t.bf_discuss_tag == 'next_project' and win)
                    )
                )
            collected = collected.sorted(
                key=lambda t: (-int(t.priority or '0'),
                               t.date_deadline or sort_max_date,
                               t.id)
            )
            rec.agenda_task_ids = collected
            rec.agenda_task_count = len(collected)

    @api.onchange('calendar_event_id')
    def _onchange_calendar_event_id_fill_participants(self):
        for rec in self:
            if rec.calendar_event_id and not rec.participant_ids:
                attendees = rec.calendar_event_id.partner_ids
                if attendees:
                    rec.participant_ids = [(6, 0, attendees.ids)]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        ICP = self.env["ir.config_parameter"].sudo()
        socket_path = ICP.get_param("bf_meeting.bridge_socket", _DEFAULT_BRIDGE_SOCKET)
        auto = ICP.get_param("bf_meeting.agenda_auto_refine", "1") in ("1", "true", "True")
        if not auto or not os.path.exists(socket_path):
            return records
        for rec in records:
            if rec.state == 'draft' and rec.project_id and not self.env.context.get('skip_auto_refine'):
                try:
                    rec._launch_refine_agenda(silent=True)
                except Exception as e:
                    _logger.warning("Auto-refine agenda %s skipped: %s", rec.id, e)
        return records

    def action_confirm(self):
        """Confirmer l'ordre du jour."""
        self.write({'state': 'confirmed'})
        for rec in self:
            if rec.auto_send_on_confirm:
                rec.action_send_agenda()

    def action_clone_from_last_series(self):
        """Pré-remplir l'OdJ à partir de la dernière occurrence de la série.

        Cherche le dernier ``meeting.agenda`` confirmé/terminé avec même
        ``series_name`` et ``project_id`` antérieur à la date de cet OdJ,
        et reprend : sujets (nom, durée, présentateur, séquence, description),
        participants, durée planifiée, objectifs, contexte et préparation —
        sauf si les champs cibles sont déjà remplis.
        """
        self.ensure_one()
        if not self.series_name:
            raise UserError(
                "L'ordre du jour doit avoir un nom de série pour cloner "
                "depuis une rencontre précédente."
            )
        previous = self.search([
            ('id', '!=', self.id),
            ('series_name', '=', self.series_name),
            ('project_id', '=', self.project_id.id),
            ('state', 'in', ('confirmed', 'done')),
            ('date', '<', self.date or fields.Datetime.now()),
        ], order='date desc', limit=1)
        if not previous:
            raise UserError(
                f"Aucune rencontre antérieure trouvée dans la série "
                f"« {self.series_name} » pour ce projet."
            )

        copy_text = lambda src, dst: dst if dst else src
        updates = {
            'objectives': copy_text(previous.objectives, self.objectives),
            'context_html': copy_text(previous.context_html, self.context_html),
            'preparation_html': copy_text(previous.preparation_html, self.preparation_html),
            'duration_planned': self.duration_planned or previous.duration_planned,
        }
        if not self.participant_ids and previous.participant_ids:
            updates['participant_ids'] = [(6, 0, previous.participant_ids.ids)]
        if not self.location and previous.location:
            updates['location'] = previous.location
        if self.meeting_type == 'video' and previous.meeting_type != 'video':
            updates['meeting_type'] = previous.meeting_type
        self.write(updates)

        # Topics: only copy if this agenda has no topics yet
        if not self.topic_ids and previous.topic_ids:
            for src_topic in previous.topic_ids.sorted('sequence'):
                self.env['meeting.agenda.topic'].create({
                    'agenda_id': self.id,
                    'sequence': src_topic.sequence,
                    'name': src_topic.name,
                    'duration_planned': src_topic.duration_planned,
                    'presenter_id': src_topic.presenter_id.id if src_topic.presenter_id else False,
                    'description': src_topic.description,
                })

        self.message_post(
            body=f"Cloné depuis « {previous.name} » ({previous.date.strftime('%Y-%m-%d') if previous.date else 'sans date'}).",
            message_type='comment',
        )
        return True

    def _bridge_tenant(self):
        """Locataire annoncé au pont Claude.

        Voir `meeting.record._bridge_tenant` : même paramètre système
        (`bf_meeting.bridge_tenant`), même raison — le module tourne sur
        plusieurs bases et le littéral « bf » les faisait toutes passer pour BF.
        """
        return (self.env['ir.config_parameter'].sudo()
                .get_param('bf_meeting.bridge_tenant', 'bf') or 'bf').strip()

    def _set_refine_state(self, state, message=''):
        """Journaliser l'avancement du pré-remplissage.

        `skip_auto_refine` est conservé : `create` relance une passe quand il
        voit passer un OdJ brouillon, et rien ne garantit qu'un appelant futur
        n'écrira pas cet état depuis ce chemin-là.
        """
        if state not in dict(self._fields['refine_state'].selection):
            _logger.warning("_set_refine_state : état inconnu %r", state)
            return False
        self.with_context(skip_auto_refine=True).write({
            'refine_state': state,
            'refine_date': fields.Datetime.now(),
            'refine_message': (message or '').strip()[:_MAX_REFINE_MESSAGE] or False,
        })
        return True

    def action_refine_agenda(self):
        """Lancer le skill /refine-agenda via le bridge Claude.

        Réservé aux gestionnaires (`bf_meeting.group_meeting_manager`) car le
        bridge spawn `claude -p --dangerously-skip-permissions`, qui contourne
        toute vérification de permissions côté Claude.
        """
        self.ensure_one()
        if not self.env.user.has_group("bf_meeting.group_meeting_manager"):
            raise UserError(
                "Le pré-remplissage Gen est réservé aux gestionnaires "
                "(groupe « Rencontres / Gestionnaire »)."
            )
        if not self.project_id:
            raise UserError("Sélectionner d'abord un projet pour le pré-remplissage.")
        ICP = self.env["ir.config_parameter"].sudo()
        socket_path = ICP.get_param("bf_meeting.bridge_socket", _DEFAULT_BRIDGE_SOCKET)
        if not os.path.exists(socket_path):
            raise UserError(
                f"Bridge Claude non disponible (socket introuvable : {socket_path})."
            )
        self._launch_refine_agenda(silent=False)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "title": "Pré-remplissage lancé",
                "message": (
                    "Gen pré-remplit l'ordre du jour. "
                    "Le résultat apparaîtra dans le formulaire dans quelques minutes."
                ),
                "sticky": False,
            },
        }

    def _launch_refine_agenda(self, silent=False):
        """Spawn the bridge call in a background thread and post status to chatter."""
        self.ensure_one()
        ICP = self.env["ir.config_parameter"].sudo()
        socket_path = ICP.get_param("bf_meeting.bridge_socket", _DEFAULT_BRIDGE_SOCKET)
        timeout = int(ICP.get_param("bf_meeting.bridge_timeout", "480"))

        agenda_id = self.id
        db_name = self.env.cr.dbname
        uid = self.env.user.id
        triggered_by = self.env.user.login
        tenant = self._bridge_tenant()

        # Mark queued so the form shows progress; safe to commit (we're in a
        # request transaction). The thread will write the final state.
        self._set_refine_state('queued', f"Lancement demandé par {self.env.user.name}")

        def _run():
            from odoo import api as _api, registry as _registry
            try:
                resp = _post_to_bridge(
                    socket_path, "/refine-agenda",
                    {
                        "agenda_id": agenda_id,
                        "tenant": tenant,
                        "triggered_by": triggered_by,
                    },
                    timeout,
                )
                status = resp.get("status", "?")
                msg = resp.get("message", "")
            except Exception as exc:
                status, msg = "error", f"{type(exc).__name__}: {exc}"

            with _registry(db_name).cursor() as new_cr:
                new_env = _api.Environment(new_cr, uid, {})
                rec = new_env["meeting.agenda"].browse(agenda_id).exists()
                if not rec:
                    return
                # `/refine-agenda` est SYNCHRONE côté pont : la réponse arrive
                # une fois la passe finie, donc `ok` vaut bien « terminé » ici
                # (contrairement au compte rendu, où le pont rend la main au
                # lancement).
                final = 'done' if status == 'ok' else 'error'
                rec._set_refine_state(final, msg or status)
                if not silent or status != 'ok':
                    body = (
                        f"<p><b>Pré-remplissage /refine-agenda</b> — statut : "
                        f"<code>{escape(status)}</code></p>"
                    )
                    if msg:
                        body += f"<p>{escape(msg)}</p>"
                    rec.message_post(body=Markup(body), message_type="comment")

        threading.Thread(target=_run, daemon=True).start()

    def action_done(self):
        """Marquer l'ordre du jour comme terminé."""
        self.write({'state': 'done'})

    def action_cancel(self):
        """Annuler l'ordre du jour.

        Pour chaque tâche hard-linkée à l'agenda :
        - Efface `bf_meeting_agenda_id`.
        - Si la tâche n'a pas de tag soft (`bf_discuss_tag`), crée une activité
          « À faire » due aujourd'hui sur le premier assigné (fallback
          organisateur) pour réassigner la tâche à un OdJ.
        Les tâches avec tag soft basculent automatiquement vers le prochain
        agenda admissible via la résolution calculée.
        """
        Task = self.env['project.task']
        for agenda in self:
            hard = Task.search([('bf_meeting_agenda_id', '=', agenda.id)])
            if not hard:
                continue
            tz_date = fields.Datetime.context_timestamp(agenda, agenda.date) \
                if agenda.date else None
            date_str = tz_date.strftime('%Y-%m-%d') if tz_date else ''
            for task in hard:
                task.bf_meeting_agenda_id = False
                if task.bf_discuss_tag:
                    continue
                assignee = task.user_ids[:1]
                user_id = assignee.id if assignee else (
                    agenda.organizer_id.id if agenda.organizer_id else self.env.uid
                )
                task.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=fields.Date.context_today(agenda),
                    summary="Rencontre annulée — réassigner à un OdJ",
                    note=(f"La rencontre « {agenda.name} » prévue le {date_str} "
                          "a été annulée. Réassigner cette tâche à un ordre "
                          "du jour si applicable."),
                    user_id=user_id,
                )
        self.write({'state': 'cancelled'})

    def action_reset_draft(self):
        """Remettre en brouillon."""
        self.write({'state': 'draft'})

    def _message_post_after_hook(self, message, msg_values):
        """Horodater le départ RÉEL d'un courriel sur l'ordre du jour.

        `action_send_agenda_wizard` estampille `sent_date` à l'OUVERTURE du
        composeur — il n'a pas le choix : le bloc d'appel aux contributions
        est rendu à partir de `contributions_open`, qui en dérive. Fermer le
        composeur sans envoyer laissait donc l'OdJ marqué « envoyé » pour le
        cron de rappel comme pour l'œil. Le message que le composeur poste
        réellement passe, lui, par `message_post` avec des destinataires
        explicites : c'est là que se pose l'horodatage confirmé.

        Ne comptent pas : les notes internes (contributions publiques, notes
        de suivi) et les notifications aux abonnés, dont les destinataires
        sont résolus plus tard et n'apparaissent pas dans `partner_ids`.
        """
        res = super()._message_post_after_hook(message, msg_values)
        recipients = (msg_values or {}).get('partner_ids') or message.partner_ids
        if (recipients
                and message.message_type in ('comment', 'email')
                and not self.email_sent_date):
            self.sudo().write({'email_sent_date': fields.Datetime.now()})
        return res

    def action_mark_sent_manually(self):
        """Déclarer que l'ordre du jour a été transmis hors Odoo."""
        for rec in self:
            if rec.email_sent_date:
                continue
            rec.write({
                'email_sent_date': fields.Datetime.now(),
                'sent_manually': True,
            })
            rec.message_post(
                body=Markup(
                    "<p>✉️ Ordre du jour déclaré <strong>envoyé à la main</strong> "
                    "par %s : la transmission a eu lieu hors Odoo.</p>"
                ) % escape(self.env.user.name),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        return True

    def action_unmark_sent_manually(self):
        """Retirer une déclaration d'envoi manuel posée par erreur."""
        for rec in self.filtered('sent_manually'):
            rec.write({'email_sent_date': False, 'sent_manually': False})
            rec.message_post(
                body=Markup(
                    "<p>↩️ Déclaration d'envoi manuel retirée par %s : "
                    "l'ordre du jour repasse à « non envoyé ».</p>"
                ) % escape(self.env.user.name),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        return True

    def action_send_agenda(self):
        """Envoyer l'ordre du jour par courriel."""
        self.ensure_one()
        template = self.env.ref(
            'bf_meeting.meeting_agenda_mail_template', raise_if_not_found=False
        )
        if not template:
            _logger.warning("Agenda mail template not found")
            return

        recipients = self.recipient_ids or self.participant_ids
        if not recipients:
            _logger.warning("No recipients for agenda %s", self.name)
            return

        # Mint the public contribution token AND stamp sent_date before
        # rendering: the CTA block is gated on `contributions_open`, which is
        # computed from `sent_date`. Stamping after send_mail() left the flag
        # False at render time, so the link never made it into the email
        # (parity with action_send_agenda_wizard).
        if self.allow_contributions:
            self._ensure_access_token()
        if not self.sent_date:
            self.write({'sent_date': fields.Datetime.now()})
        self.flush_recordset(['sent_date', 'contributions_open'])

        template.send_mail(self.id, force_send=True)
        # Le courriel est parti : `sent_date` ne dit que « un envoi a été
        # amorcé », c'est ici que se pose la preuve d'envoi.
        self.write({
            'email_sent_date': fields.Datetime.now(),
            'sent_manually': False,
        })

    def action_send_agenda_wizard(self):
        """Ouvrir l'assistant d'envoi de l'ordre du jour."""
        self.ensure_one()
        template = self.env.ref(
            'bf_meeting.meeting_agenda_mail_template', raise_if_not_found=False
        )
        # Parity with action_send_agenda: mint the contribution token (so the CTA
        # link renders in the composer) and stamp sent_date so the contribution
        # window opens for a draft agenda even when sent via the options wizard.
        if self.allow_contributions:
            self._ensure_access_token()
        if not self.sent_date:
            self.write({'sent_date': fields.Datetime.now()})
        ctx = {
            'default_model': 'meeting.agenda',
            'default_res_ids': self.ids,
            'default_composition_mode': 'comment',
            'default_email_layout_xmlid': 'mail.mail_notification_light',
        }
        if template:
            ctx['default_template_id'] = template.id
        return {
            'type': 'ir.actions.act_window',
            'name': "Envoyer l'ordre du jour",
            'res_model': 'mail.compose.message',
            'views': [[False, 'form']],
            'target': 'new',
            'context': ctx,
        }

    def report_date_display_html(self):
        """Date (tz client d'abord, tz organisateur en plus petit) en Markup,
        pour le gabarit de courriel de l'ordre du jour — parité avec le PDF et
        le courriel de compte rendu."""
        self.ensure_one()
        return _format_meeting_date_display(self)

    def _cron_remind_unsent_agenda(self):
        """Daily : pour les OdJ draft/confirmed dont la rencontre est dans les 7
        prochains jours et qui n'ont pas encore été envoyés, créer une activité
        « À faire » due aujourd'hui sur l'organisateur de la rencontre s'il est
        un utilisateur interne. Idempotent via le summary."""
        now = fields.Datetime.now()
        horizon = now + timedelta(days=REMINDER_LEAD_DAYS)
        agendas = self.search([
            ('state', 'in', ACTIVE_AGENDA_STATES),
            ('email_sent_date', '=', False),
            ('date', '>=', now),
            ('date', '<=', horizon),
        ])
        if not agendas:
            return

        Activity = self.env['mail.activity']
        activity_type = self.env.ref(
            'mail.mail_activity_data_todo', raise_if_not_found=False
        )
        if not activity_type:
            _logger.warning("mail.mail_activity_data_todo not found, skipping agenda reminders")
            return
        model_id = self.env['ir.model']._get_id('meeting.agenda')

        for agenda in agendas:
            user = agenda.calendar_event_id.user_id or agenda.organizer_id
            if not user or user.share or not user.active:
                continue
            existing = Activity.search_count([
                ('res_model', '=', 'meeting.agenda'),
                ('res_id', '=', agenda.id),
                ('summary', '=', REMINDER_ACTIVITY_SUMMARY),
            ])
            if existing:
                continue
            tz_date = fields.Datetime.context_timestamp(agenda, agenda.date)
            date_str = tz_date.strftime('%Y-%m-%d %H:%M') if tz_date else ''
            safe_name = escape(agenda.name or '')
            note = (
                f"L'ordre du jour « {safe_name} » n'a pas encore été envoyé "
                f"et la rencontre approche ({date_str}). Réviser et envoyer."
            )
            Activity.create({
                'activity_type_id': activity_type.id,
                'summary': REMINDER_ACTIVITY_SUMMARY,
                'note': note,
                'date_deadline': fields.Date.context_today(agenda),
                'user_id': user.id,
                'res_model_id': model_id,
                'res_id': agenda.id,
            })

    def action_start_meeting(self):
        """Démarrer la rencontre : confirme l'OdJ si encore draft, pré-remplit
        les zones de notes en direct avec le contenu des sujets, loggue
        l'heure de début au chatter, retourne une notification pointant vers
        l'onglet « Notes en direct ».
        """
        self.ensure_one()
        if self.state == 'draft':
            self.action_confirm()

        # Pré-remplit per-topic live notes (idempotent : ne touche que les
        # boîtes vides pour ne jamais écraser ce que le notetaker a déjà saisi).
        # Seuls les sujets acceptés alimentent la rencontre : les propositions
        # contribuées en attente de modération ne doivent pas y entrer.
        for topic in self.topic_ids.filtered(lambda t: t.moderation_state == 'accepted'):
            if _html_is_blank(topic.live_notes_html) and not _html_is_blank(topic.description):
                topic.live_notes_html = _wrap_agenda_original(topic.description)

        # Pré-remplit le scratchpad maître avec un dump de tous les sujets,
        # même ceux sans description (heading + paragraphe vide). Idempotent
        # de la même façon.
        accepted_topics = self.topic_ids.filtered(lambda t: t.moderation_state == 'accepted')
        if _html_is_blank(self.live_notes_html) and accepted_topics:
            parts = []
            for topic in accepted_topics.sorted('sequence'):
                parts.append(Markup('<h3>%s</h3>') % (topic.name or ''))
                if not _html_is_blank(topic.description):
                    parts.append(_wrap_agenda_original(topic.description))
                else:
                    parts.append(Markup('<p><br/></p>'))
            self.live_notes_html = Markup('').join(parts)

        self.message_post(
            body=Markup(
                "<p><b>▶️ Rencontre démarrée</b> — "
                f"prise de notes en direct lancée par {escape(self.env.user.name)}.</p>"
            ),
            message_type='comment',
        )
        # Re-ouvre le formulaire pour forcer le reload des champs Html
        # pré-remplis (display_notification seul ne rafraîchit pas la vue).
        return {
            "type": "ir.actions.act_window",
            "res_model": "meeting.agenda",
            "res_id": self.id,
            "view_mode": "form",
            "views": [[False, "form"]],
            "target": "current",
            "context": {"default_active_tab": "live_notes"},
        }

    def action_quick_add_task(self):
        """Quick-create d'une tâche action item hard-linkée à l'OdJ.

        Ouvre un form modal de project.task pré-rempli (projet + lien hard
        vers l'agenda) — capture rapide pendant la rencontre sans quitter
        le formulaire OdJ.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': "Ajouter une action",
            'res_model': 'project.task',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {
                'default_project_id': self.project_id.id,
                'default_bf_meeting_agenda_id': self.id,
                'default_partner_id': self.partner_id.id if self.partner_id else False,
            },
        }

    def action_view_meeting_record(self):
        """Smart button : ouvrir le compte rendu lié à cet OdJ."""
        self.ensure_one()
        if not self.meeting_record_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': self.meeting_record_id.name,
            'res_model': 'meeting.record',
            'res_id': self.meeting_record_id.id,
            'views': [[False, 'form']],
        }

    def action_view_agenda_tasks(self):
        """Smart button : ouvrir la liste des tâches à discuter."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': "Éléments d'action à discuter",
            'res_model': 'project.task',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.agenda_task_ids.ids)],
            'context': {'default_project_id': self.project_id.id},
        }

    def _get_report_data(self):
        """Prepare data dict for the QWeb agenda report."""
        self.ensure_one()
        # Only accepted topics enter the official PDF — recipient-proposed
        # topics held in moderation (pending/rejected) never leak into the
        # artifact until the organizer accepts them.
        official_topics = self.topic_ids.filtered(
            lambda t: t.moderation_state == 'accepted'
        ).sorted('sequence')
        topics = []
        for idx, t in enumerate(official_topics, 1):
            topics.append({
                'index': idx,
                'name': t.name,
                'duration': t.duration_planned,
                'presenter': t.presenter_id.name if t.presenter_id else '',
                'description': t.description or '',
            })
        tag_labels = dict(self.env['project.task']._fields['bf_discuss_tag'].selection)
        tagged_tasks = []
        for idx, t in enumerate(self.agenda_task_ids, 1):
            if t.bf_discuss_tag:
                source = tag_labels.get(t.bf_discuss_tag, '')
            elif t.bf_meeting_agenda_id:
                source = 'Épinglée'
            else:
                source = ''
            tagged_tasks.append({
                'index': idx,
                'name': t.name,
                'project': t.project_id.name or '',
                'assignees': ', '.join(t.user_ids.mapped('name')),
                'deadline': t.date_deadline,
                'stage': t.stage_id.name or '',
                'source': source,
            })
        return {
            'topic_count': len(topics),
            'topics': topics,
            'participant_count': len(self.participant_ids),
            'tagged_tasks': tagged_tasks,
            'tagged_task_count': len(tagged_tasks),
            'date_display': _format_meeting_date_display(self),
        }

    def action_create_meeting_record(self):
        """Créer un compte rendu à partir de cet ordre du jour."""
        self.ensure_one()
        if self.meeting_record_id:
            raise UserError(
                "Un compte rendu existe déjà pour cet ordre du jour : "
                f"« {self.meeting_record_id.name} »."
            )
        if self.state == 'cancelled':
            raise UserError(
                "Impossible de créer un compte rendu : l'ordre du jour est annulé."
            )
        vals = {
            'project_id': self.project_id.id,
            'date': self.date,
            'room_name': self.name,
            'location': self.location,
            'meeting_type': self.meeting_type,
            'lang': self.lang,
            'series_name': self.series_name,
            'organizer_id': self.organizer_id.id if self.organizer_id else False,
            'participant_ids': [(6, 0, self.participant_ids.ids)],
            'calendar_event_id': self.calendar_event_id.id if self.calendar_event_id else False,
            'duration_minutes': self.duration_planned,
        }
        # Live notes scratchpad → summary text (strip tags for plain text).
        if self.live_notes_html:
            from lxml import html as _html
            try:
                vals['summary'] = _html.fromstring(self.live_notes_html).text_content().strip()
            except Exception:
                vals['summary'] = self.live_notes_html
        record = self.env['meeting.record'].create(vals)

        # Create topics from agenda topics — carry per-topic live notes
        # into the record's points_html. Apply track-changes-style highlight
        # so user additions (typed outside the agenda blockquote) render
        # with a yellow background in the compte rendu.
        # Only accepted topics become record points (pending proposals excluded).
        for topic in self.topic_ids.filtered(lambda t: t.moderation_state == 'accepted'):
            self.env['meeting.topic'].create({
                'meeting_id': record.id,
                'sequence': topic.sequence,
                'name': topic.name,
                'points_html': _highlight_user_additions(topic.live_notes_html) or False,
            })

        # Transfer live decisions captured on the agenda to the new record.
        if self.live_decision_ids:
            self.live_decision_ids.write({
                'meeting_id': record.id,
                'agenda_id': False,
            })

        # Transfer hard-linked tasks to the new meeting record. Soft-tagged
        # tasks are left alone — they will naturally resolve to the next
        # upcoming agenda.
        hard = self.env['project.task'].search([
            ('bf_meeting_agenda_id', '=', self.id),
        ])
        if hard:
            hard.write({
                'meeting_id': record.id,
                'bf_meeting_agenda_id': False,
                'bf_discuss_tag': False,
            })

        self.write({
            'meeting_record_id': record.id,
            'state': 'done',
        })

        return {
            'type': 'ir.actions.act_window',
            'name': record.name,
            'res_model': 'meeting.record',
            'res_id': record.id,
            'views': [[False, 'form']],
        }
