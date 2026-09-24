import json
import logging
import re
import threading
import unicodedata
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_ai_bridge.tools import transport

_logger = logging.getLogger(__name__)

# Seuil AA du grand texte : la sur-ligne de bannière est en 8 pt gras espacé,
# donc ce seuil-là et pas 4,5:1.
_SEUIL_SUR_FOND_SOMBRE = 3.0


def _rgb(hexa: str) -> tuple:
    h = (hexa or '').lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb: tuple) -> float:
    canaux = []
    for valeur in rgb:
        v = valeur / 255
        canaux.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2]


def _contraste(a: str, b: str) -> float:
    la, lb = _luminance(_rgb(a)), _luminance(_rgb(b))
    haut, bas = max(la, lb), min(la, lb)
    return (haut + 0.05) / (bas + 0.05)


def _eclaircir_jusquau_contraste(couleur: str, fond: str,
                                 seuil: float = _SEUIL_SUR_FOND_SOMBRE) -> str:
    """Éclaircir `couleur` vers le blanc jusqu'à atteindre `seuil` contre `fond`.

    Pendant exact de `assombrir_pour_texte_blanc` de bluefox_branding, qui,
    lui, résout le cas inverse (texte blanc sur un fond de marque trop pâle).
    Rendu inchangé si le contraste est déjà suffisant.
    """
    if _contraste(couleur, fond) >= seuil:
        return couleur if couleur.startswith('#') else f'#{couleur}'
    r, v, b = _rgb(couleur)
    for pas in range(1, 21):          # 20 pas de 5 % vers le blanc
        facteur = pas / 20
        melange = tuple(round(c + (255 - c) * facteur) for c in (r, v, b))
        candidat = '#%02X%02X%02X' % melange
        if _contraste(candidat, fond) >= seuil:
            return candidat
    return '#FFFFFF'                  # dernier recours : toujours lisible

# Ceiling on the free-text instructions collected by meeting.refine.wizard.
# They are pasted into the `claude -p` prompt, so an unbounded field would
# crowd out the skill body itself.
_MAX_REFINE_INSTRUCTIONS = 4000

# Ceiling on the status detail stored in `refine_message`: it is written by
# remote callers (bridge, meeting-processor) and only ever shown as one line.
_MAX_REFINE_MESSAGE = 500

# Au-delà de ce délai sans signal, une passe « en cours » est réputée perdue
# (voir `_compute_refine_in_progress`). Défaut aligné sur le plafond du pont
# (REFINE_TIMEOUT = 900 s) plus une marge ; surchargeable par le paramètre
# système `bf_meeting.refine_stale_minutes`.
_REFINE_STALE_MINUTES = 20


def _format_meeting_date_display(record):
    """Format record.date with the client's tz first and the originator's tz
    second, rendered in smaller muted characters, when they differ. Returns
    Markup so the styling survives QWeb ``t-out`` rendering (PDF report and
    mail template). record must expose date, partner_id, organizer_id,
    create_uid.

    Timezone resolution, conversion and city labelling live in the shared
    ``bf.timezone`` helper (module ``bf_timezone``)."""
    if not record.date:
        return ''
    tz_helper = record.env['bf.timezone']
    default_tz = tz_helper.default_tz()
    client_tz_name = (record.partner_id.tz if record.partner_id else None) \
        or default_tz
    organizer = record.organizer_id or record.create_uid
    originator_tz_name = (organizer.tz if organizer else None) \
        or default_tz
    fmt = "%Y-%m-%d %H:%M %Z"
    primary = tz_helper.to_tz(record.date, client_tz_name).strftime(fmt)
    if not originator_tz_name or originator_tz_name == client_tz_name:
        return escape(primary)
    secondary = tz_helper.to_tz(record.date, originator_tz_name).strftime(fmt)
    # Client time first (normal); originator time second, smaller and muted.
    return Markup(
        '%s <span style="font-size:0.82em;color:#9CA3AF;">(%s : %s)</span>'
    ) % (primary, tz_helper.tz_city(originator_tz_name), secondary)


class MeetingRecord(models.Model):
    """Compte rendu structuré d'une réunion."""
    _name = 'meeting.record'
    _description = 'Compte rendu de réunion'
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
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Société',
        default=lambda self: self.env.company,
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
    duration_minutes = fields.Integer(
        string='Durée (minutes)',
    )
    room_name = fields.Char(
        string='Salon / Titre',
        tracking=True,
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
        help='Mode de la rencontre. Visio par défaut (cas dominant via Noota). '
             'Téléphonique permet de lier un appel archivé via le module pont.',
    )
    meeting_type_icon = fields.Char(
        string='Icône mode',
        compute='_compute_meeting_type_icon',
        store=True,
        help='Glyphe unicode représentant le mode — utilisé en list/kanban.',
    )
    lang = fields.Selection(
        lambda self: self.env['res.lang'].get_installed(),
        string='Langue',
        compute='_compute_lang',
        store=True,
        readonly=False,
        help="Langue du compte rendu : pilote la langue du rapport PDF et du "
             "courriel d'envoi. Initialisée depuis la langue du client.",
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
        help='Nom de la série récurrente (ex. Comité de direction mensuel)',
        index=True,
    )
    organizer_id = fields.Many2one(
        'res.users',
        string='Organisateur',
    )
    # DEPRECATED (v18.0.3.38.0): the participant list on the report is now
    # driven exclusively by `attendance_ids` (Présences). This field is kept
    # only so historical records still render via the template fallback; it is
    # no longer surfaced in the UI nor populated for new records.
    participant_ids = fields.Many2many(
        'res.partner',
        'meeting_record_participant_rel',
        'meeting_id',
        'partner_id',
        string='Participants (obsolète)',
    )
    invited_ids = fields.Many2many(
        'res.partner',
        'meeting_record_invited_rel',
        'meeting_id',
        'partner_id',
        string='Invités',
    )

    # Content
    summary = fields.Text(
        string='Résumé exécutif',
    )
    structured_notes_json = fields.Text(
        string='Notes structurées (JSON)',
    )
    notes_html = fields.Html(
        string='Notes',
        compute='_compute_notes_html',
        store=True,
        sanitize_style=True,
    )
    verbatim = fields.Text(
        string='Transcription brute',
    )
    verbatim_html = fields.Html(
        string='Transcription HTML',
        sanitize_style=True,
    )
    open_questions_html = fields.Html(
        string='Questions ouvertes',
        compute='_compute_notes_html',
        store=True,
        sanitize_style=True,
    )

    # Relations
    decision_ids = fields.One2many(
        'meeting.decision',
        'meeting_id',
        string='Décisions',
    )
    topic_ids = fields.One2many(
        'meeting.topic',
        'meeting_id',
        string='Sujets',
    )
    task_ids = fields.One2many(
        'project.task',
        'meeting_id',
        string="Éléments d'action",
    )
    # Les tâches qui existaient AVANT la rencontre et dont elle a parlé.
    # `task_ids` ne peut pas les porter : c'est l'inverse de
    # `project.task.meeting_id`, un champ unique, et l'écrire arracherait la
    # tâche au compte rendu de la rencontre qui l'a créée (mesuré : un appel a
    # « volé » une tâche de mars au compte rendu qui l'avait créée). Le
    # meeting-processor les pose quand il verse une action dans une tâche
    # ouverte au lieu d'en créer une neuve.
    discussed_task_ids = fields.Many2many(
        'project.task',
        'meeting_record_discussed_task_rel',
        'meeting_id',
        'task_id',
        string="Tâches existantes discutées",
        help="Tâches déjà ouvertes avant la rencontre, dont la rencontre a parlé. "
             "Elles figurent au compte rendu (PDF, courriel, portail) à côté des "
             "éléments d'action créés par la rencontre.",
    )
    task_count = fields.Integer(
        string='Nombre de tâches',
        compute='_compute_task_count',
    )
    knowledge_item_ids = fields.Many2many(
        'project.knowledge.item',
        'meeting_record_knowledge_item_rel',
        'meeting_id',
        'knowledge_item_id',
        string='Éléments de matrice',
    )
    knowledge_item_count = fields.Integer(
        string='Éléments de matrice',
        compute='_compute_knowledge_item_count',
    )

    # Report
    report_state = fields.Selection([
        ('draft', 'Brouillon'),
        ('reviewed', 'Révisé'),
        ('sent', 'Envoyé'),
    ], string='État du rapport', default='draft', tracking=True)
    report_sent_date = fields.Datetime(
        string="Date d'envoi",
        readonly=True,
    )
    report_sent_manually = fields.Boolean(
        string='Envoi déclaré à la main',
        readonly=True,
        copy=False,
        tracking=True,
        help="Le compte rendu a été transmis hors Odoo (client de messagerie, "
             "clavardage, remise en personne) et l'envoi a été déclaré ici : "
             "aucun courriel n'est parti du système.",
    )
    review_notes = fields.Text(
        string='Notes de révision',
        help='Observations de la révision automatique ou manuelle du compte rendu.',
    )
    report_recipient_ids = fields.Many2many(
        'res.partner',
        'meeting_record_recipient_rel',
        'meeting_id',
        'partner_id',
        string='Destinataires',
    )
    partner_to_ids = fields.Char(
        string='IDs destinataires (technique)',
        compute='_compute_partner_to_ids',
        help="Utilisé par le modèle d'email, contourne la sandbox Jinja d'Odoo 18 qui refuse .mapped/.ids sur les recordsets.",
    )

    @api.depends('report_recipient_ids')
    def _compute_partner_to_ids(self):
        # Recipients come ONLY from « Destinataires ». No participant fallback:
        # auto-filling caused accidental sends to clients (cf. action_send_report_direct).
        for rec in self:
            rec.partner_to_ids = ','.join(str(pid) for pid in rec.report_recipient_ids.ids)

    # Échange entre locataires Symbifox
    exchange_include_json = fields.Boolean(
        string='Joindre la copie lisible par la machine',
        # 🔴 Défaut CONSTANT, et c'est délibéré. Un `default=lambda self:
        # self.env.company.meeting_exchange_default` fait lire `res_company`
        # au moment où Odoo pose la colonne sur les lignes EXISTANTES, et à
        # cet instant la colonne de `res.company` livrée par ce même module
        # n'existe pas encore : `column res_company.meeting_exchange_default
        # does not exist`, registre refusé, mise à jour avortée. Invisible sur
        # une base neuve, où la table est vide et où aucun défaut n'est calculé.
        # La préférence de société est appliquée dans `create` à la place.
        default=False,
        copy=False,
        help="Joint au courriel du compte rendu un fichier .json portant "
             "exactement ce que montre le PDF. Un destinataire qui a lui aussi "
             "Symbifox peut le reprendre dans ses propres Rencontres sans "
             "retaper. Il ne contient ni transcription, ni notes de révision, "
             "ni pièce jointe.",
    )
    exchange_source_ref = fields.Char(
        string="Provenance de l'échange",
        readonly=True,
        copy=False,
        index=True,
        help="Base et identifiant d'origine d'un compte rendu repris d'un "
             "autre locataire. Vide pour un compte rendu rédigé ici.",
    )
    exchange_received_html = fields.Html(
        string='Copie reçue',
        readonly=True,
        copy=False,
        sanitize=False,
        help="Provenance de la copie, et ce que le modèle local n'a pas su "
             "reprendre : éléments d'action et participants non appariés.",
    )

    # Raffinage GenFox (/refine-meeting)
    # L'ordre du jour porte le même indicateur (`meeting.agenda.refine_state`).
    # Ici il est indispensable : côté compte rendu, le pont rend la main DÈS le
    # lancement (fire-and-forget, plusieurs minutes de traitement derrière), donc
    # « la demande est partie » ne dit rien de l'état réel de la passe. Chaque
    # lanceur — bouton Odoo, pont Claude, meeting-processor — écrit son
    # avancement ici via `set_refine_state`.
    refine_state = fields.Selection([
        ('none', 'Non lancé'),
        ('queued', 'En cours'),
        ('done', 'Terminé'),
        ('error', 'Erreur'),
    ], string='Raffinage Gen', default='none', readonly=True, copy=False,
        help="Avancement de la dernière passe /refine-meeting sur ce compte "
             "rendu.")
    refine_date = fields.Datetime(
        string='Raffinage — dernier signal',
        readonly=True,
        copy=False,
        help="Horodatage du dernier changement d'état du raffinage.",
    )
    refine_message = fields.Char(
        string='Raffinage — détail',
        readonly=True,
        copy=False,
        help="Message rendu par le lanceur : cause de l'erreur, modèle "
             "utilisé, etc.",
    )
    refine_in_progress = fields.Boolean(
        string='Raffinage en cours',
        compute='_compute_refine_in_progress',
        help="Vrai seulement pendant la fenêtre où la passe peut encore "
             "aboutir.",
    )

    @api.depends('refine_state', 'refine_date')
    def _compute_refine_in_progress(self):
        """« En cours » n'est vrai que dans la fenêtre où la passe peut encore
        rendre un résultat.

        Sans cette borne, un raffinage tué en vol (plafond du pont, recreate du
        conteneur, redémarrage d'Odoo) laisserait l'état à « En cours » pour
        toujours — et le bouton caché avec lui, rendant le compte rendu
        irraffinable sans passer par la base.
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

    def set_refine_state(self, state, message=''):
        """Journaliser l'avancement du raffinage.

        Méthode publique et sans garde de groupe : les appelants sont des
        services (pont Claude, meeting-processor) qui écrivent par XML-RPC avec
        leur propre compte technique — les droits du modèle s'appliquent
        normalement. Renvoie False au lieu de lever sur un état inconnu : un
        appelant distant ne doit jamais faire échouer sa passe sur un souci
        d'affichage.
        """
        if state not in dict(self._fields['refine_state'].selection):
            _logger.warning("set_refine_state : état inconnu %r", state)
            return False
        self.write({
            'refine_state': state,
            'refine_date': fields.Datetime.now(),
            'refine_message': (message or '').strip()[:_MAX_REFINE_MESSAGE] or False,
        })
        return True

    # Source
    source_filename = fields.Char(
        string='Fichier source',
    )
    source_nc_path = fields.Char(
        string='Chemin Nextcloud',
    )
    source_type = fields.Selection([
        ('audio', 'Audio'),
        ('text', 'Texte'),
        ('talk_recording', 'Enregistrement Talk'),
    ], string='Type de source')

    # Calendar & Agenda
    calendar_event_id = fields.Many2one(
        'calendar.event',
        string='Événement calendrier',
    )
    agenda_ids = fields.One2many(
        'meeting.agenda',
        'meeting_record_id',
        string='Ordres du jour liés',
    )
    agenda_id = fields.Many2one(
        'meeting.agenda',
        string='Ordre du jour',
        compute='_compute_agenda_id',
    )

    # Attendance
    attendance_ids = fields.One2many(
        'meeting.attendance',
        'meeting_id',
        string='Présences',
    )
    attendance_count = fields.Integer(
        string='Présences',
        compute='_compute_attendance_count',
    )
    present_count = fields.Integer(
        string='Présents',
        compute='_compute_attendance_count',
    )

    # Calendar view helper
    date_delay = fields.Float(
        string='Durée (heures)',
        compute='_compute_date_delay',
        store=True,
    )

    active = fields.Boolean(default=True)

    @api.depends('room_name', 'date')
    def _compute_name(self):
        for rec in self:
            if rec.name:
                continue
            parts = []
            if rec.room_name:
                parts.append(rec.room_name)
            if rec.date:
                parts.append(rec.date.strftime('%Y-%m-%d'))
            rec.name = ' — '.join(parts) if parts else 'Nouveau compte rendu'

    @api.depends('structured_notes_json')
    def _compute_notes_html(self):
        for rec in self:
            if not rec.structured_notes_json:
                rec.notes_html = False
                rec.open_questions_html = False
                continue
            try:
                data = json.loads(rec.structured_notes_json)
            except (json.JSONDecodeError, TypeError, ValueError):
                data = None
            # `json.loads('"bonjour"')` réussit et rend une chaîne : sans ce
            # contrôle, le `.get` d'après lève AttributeError DANS un calcul
            # stocké, et la fiche devient illisible en entier.
            if not isinstance(data, dict):
                rec.notes_html = False
                rec.open_questions_html = False
                continue

            rec.notes_html = rec._render_notes_html(data)
            rec.open_questions_html = rec._render_open_questions_html(data)

    @staticmethod
    def _iter_entries(value):
        """Parcourir une valeur du JSON structuré en n'admettant qu'une liste.

        Ces deux gabarits ont été écrits pour du JSON que nous produisons
        nous-mêmes. Depuis l'échange entre locataires, la même
        colonne peut recevoir un fichier venu d'ailleurs, et les formes
        tordues font mal de deux façons mesurées :

        * une chaîne là où une liste est attendue s'itère **caractère par
          caractère** — 100 ko de texte rendaient 1 Mo de HTML, un facteur 10,
          dans un calculé STOCKÉ ;
        * un dictionnaire ou une chaîne à la place de la liste de sujets lève
          `AttributeError` dans le calcul, ce qui rend la fiche entièrement
          illisible tant que la colonne n'est pas réparée à la main.

        Une valeur qui n'est pas une liste est donc ignorée, pas devinée.
        """
        return value if isinstance(value, list) else []

    def _render_notes_html(self, data):
        """Render structured notes JSON to HTML. User-supplied strings are
        escaped to prevent XSS via crafted verbatims / structured notes."""
        parts = []

        for topic in self._iter_entries(data.get('topics')):
            if not isinstance(topic, dict):
                continue
            title = escape(topic.get('title', ''))
            parts.append(f'<h3>{title}</h3>')
            points = self._iter_entries(topic.get('points'))
            if points:
                parts.append('<ul>')
                for point in points:
                    parts.append(f'<li>{escape(point)}</li>')
                parts.append('</ul>')

        deliverables = self._iter_entries(data.get('deliverables'))
        if deliverables:
            parts.append('<h3>Livrables</h3><ul>')
            for d in deliverables:
                desc = d if isinstance(d, str) else (
                    d.get('description', str(d)) if isinstance(d, dict) else str(d))
                parts.append(f'<li>{escape(desc)}</li>')
            parts.append('</ul>')

        return Markup(''.join(parts)) if parts else False

    def _render_open_questions_html(self, data):
        """Render open questions from JSON (escaped)."""
        questions = self._iter_entries(data.get('open_questions'))
        if not questions:
            return False
        parts = ['<ul>']
        for q in questions:
            text = q if isinstance(q, str) else (
                q.get('question', str(q)) if isinstance(q, dict) else str(q))
            parts.append(f'<li>{escape(text)}</li>')
        parts.append('</ul>')
        return Markup(''.join(parts))

    @api.depends('duration_minutes')
    def _compute_date_delay(self):
        for rec in self:
            rec.date_delay = (rec.duration_minutes or 60) / 60.0

    @api.depends('agenda_ids')
    def _compute_agenda_id(self):
        for rec in self:
            rec.agenda_id = rec.agenda_ids[:1]

    @api.depends('attendance_ids', 'attendance_ids.status')
    def _compute_attendance_count(self):
        for rec in self:
            rec.attendance_count = len(rec.attendance_ids)
            rec.present_count = len(rec.attendance_ids.filtered(
                lambda a: a.status == 'present'
            ))

    @api.depends('task_ids')
    def _compute_task_count(self):
        for rec in self:
            rec.task_count = len(rec.task_ids)

    def _discussed_tasks_for_report(self):
        """Les tâches existantes discutées, telles que le compte rendu les montre.

        Une seule définition pour le PDF, le courriel, le portail et la copie
        d'échange : quatre filtres écrits séparément finissent par diverger, et
        le client lirait deux listes différentes du même compte rendu.
        - une tâche déjà listée parmi les éléments d'action n'est pas répétée ;
        - une tâche annulée n'est plus un engagement (même règle que le portail).
        """
        self.ensure_one()
        return (self.discussed_task_ids - self.task_ids).filtered(
            lambda t: t.state != '1_canceled')

    @api.depends('knowledge_item_ids')
    def _compute_knowledge_item_count(self):
        for rec in self:
            rec.knowledge_item_count = len(rec.knowledge_item_ids)

    @api.onchange('calendar_event_id')
    def _onchange_calendar_event_id_fill_attendance(self):
        """Seed Présences from the linked calendar event's invitees.

        Présences (`attendance_ids`) is the single source for the participant
        list on the report, so we seed it (status « present ») rather than the
        deprecated `participant_ids`.
        """
        for rec in self:
            if rec.calendar_event_id and not rec.attendance_ids:
                existing = rec.attendance_ids.mapped('partner_id')
                cmds = [
                    (0, 0, {'partner_id': p.id, 'status': 'present'})
                    for p in rec.calendar_event_id.partner_ids
                    if p not in existing
                ]
                if cmds:
                    rec.attendance_ids = cmds

    def _bf_project_default_recipients(self, project_id):
        """Commandes m2m pour les destinataires par défaut d'un projet.

        Rend None quand il n'y a rien à poser, pour que l'appelant puisse
        laisser la valeur d'origine intacte plutôt que de l'écraser par une
        liste vide.
        """
        if not project_id:
            return None
        project = self.env['project.project'].browse(project_id).exists()
        if not project:
            return None
        # sudo : le compte rendu d'un appel est créé par le meeting-processor,
        # qui n'a pas toujours le projet en lecture selon la société portée par
        # la requête. Sans sudo, le champ rendrait une liste vide — donc un
        # compte rendu sans destinataire, et un envoi qui refuse de partir sans
        # qu'on sache pourquoi.
        partners = project.sudo().meeting_report_recipient_ids
        return [(6, 0, partners.ids)] if partners else None

    @api.private
    def report_accent_on_dark(self):
        """Couleur d'accent lisible SUR la bannière sombre du rapport.

        La sur-ligne « COMPTE RENDU » est écrite en couleur primaire sur la
        bannière `report_brand_dark`. Avec une primaire claire le couple passe
        (#29ABE2 sur #2E3132 = 5,0:1), alors le gabarit posait la primaire telle
        quelle. Une société dont la primaire est foncée ne s'en tire pas : un
        Deep Teal #135466 sur un Deep Navy #2C3448 donne **1,47:1** — mesuré,
        illisible.

        On éclaircit donc la primaire par pas jusqu'à 3:1 (seuil AA du grand
        texte), sans jamais toucher à la primaire elle-même : elle reste vraie
        partout où elle est posée sur du clair, où elle est excellente
        (#135466 sur blanc = 8,4:1).

        Publique parce que le rendu QWeb refuse les méthodes préfixées, mais
        `@api.private` : rien à faire pour un appelant RPC.
        """
        self.ensure_one()
        company = self.company_id or self.env.company
        primaire = (company.report_brand_primary or '#714B67').strip()
        fond = (company.report_brand_dark or '#212529').strip()
        try:
            return _eclaircir_jusquau_contraste(primaire, fond)
        except (ValueError, IndexError):
            # Une couleur mal saisie ne doit pas faire tomber le rapport :
            # on rend la primaire d'origine, comme avant ce correctif.
            return primaire

    def _bf_project_company(self, project_id):
        """Société portée par le projet, quand il en porte une.

        Le compte rendu se rend à la marque de SA société : le PDF comme le
        courriel lisent `company_id` (logo de bannière, couleurs de rapport,
        nom au pied). Un projet tenu pour un client qui a sa propre société
        dans la base doit donc l'imposer — sinon le compte rendu d'un appel
        sort aux couleurs de la société du compte qui l'a créé, ce qui n'a rien
        à voir avec le sujet.

        La plupart des projets n'ont aucune société : ils rendent None ici et
        rien ne change pour eux.
        """
        if not project_id:
            return None
        project = self.env['project.project'].browse(project_id).exists()
        if not project:
            return None
        company = project.sudo().company_id
        return company.id if company else None

    @api.model_create_multi
    def create(self, vals_list):
        """Appliquer la préférence de société sur la copie d'échange, et
        hériter des destinataires par défaut du projet.

        Le défaut du champ ne peut pas la lire (voir le commentaire sur
        `exchange_include_json`), et `default_get` ne servirait que la saisie à
        l'écran : un compte rendu créé par le meeting-processor ou par XML-RPC
        n'y passe pas. C'est donc ici, où la colonne existe forcément.
        """
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if not vals.get('report_recipient_ids'):
                cmds = self._bf_project_default_recipients(vals.get('project_id'))
                if cmds:
                    vals['report_recipient_ids'] = cmds
            company = self._bf_project_company(vals.get('project_id'))
            if company:
                vals['company_id'] = company
            if 'exchange_include_json' in vals:
                continue
            company = self.env['res.company'].browse(vals['company_id']) \
                if vals.get('company_id') else self.env.company
            vals['exchange_include_json'] = bool(company.meeting_exchange_default)
        return super().create(vals_list)

    def write(self, vals):
        """Cascade `project_id` change to linked action-item tasks.

        When the user moves a meeting record to a different project, the
        action items that came out of that meeting should follow. Users can
        still re-route individual tasks afterwards if needed.
        """
        cascade = 'project_id' in vals
        if cascade:
            old_by_record = {rec.id: rec.project_id.id for rec in self}
        res = super().write(vals)
        if cascade and 'company_id' not in vals:
            # Même raison qu'à la création : la marque du compte rendu suit la
            # société de son projet, et le projet arrive souvent après coup.
            for rec in self:
                company = rec._bf_project_company(rec.project_id.id)
                if company and rec.company_id.id != company:
                    rec.company_id = company
        if cascade and not vals.get('report_recipient_ids'):
            # Le projet arrive souvent APRÈS la création : le meeting-processor
            # crée le compte rendu d'un appel sans projet, /refine-meeting le
            # range quelques minutes plus tard, et l'épinglage de la ligne VoIP
            # le corrige au passage suivant. L'héritage posé dans create() ne
            # verrait donc jamais rien — d'où la même reprise ici.
            for rec in self:
                if rec.report_recipient_ids or rec.report_state == 'sent':
                    continue
                cmds = rec._bf_project_default_recipients(rec.project_id.id)
                if cmds:
                    rec.report_recipient_ids = cmds
        if cascade:
            new_pid = vals.get('project_id')
            for rec in self:
                if new_pid == old_by_record.get(rec.id):
                    continue
                # Only move tasks that were on the OLD project (don't drag
                # tasks already manually re-routed elsewhere).
                old_pid = old_by_record.get(rec.id)
                to_move = rec.task_ids.filtered(
                    lambda t, old=old_pid: t.project_id.id == old
                ) if old_pid else rec.task_ids
                if to_move and new_pid:
                    to_move.write({'project_id': new_pid})
        return res

    def action_view_tasks(self):
        """Ouvrir les tâches liées à ce meeting."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f"Tâches — {self.name}",
            'res_model': 'project.task',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('meeting_id', '=', self.id)],
            'context': {
                'default_meeting_id': self.id,
                'default_project_id': self.project_id.id,
            },
        }

    def action_view_knowledge_items(self):
        """Ouvrir les éléments de matrice liés."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f"Matrice — {self.name}",
            'res_model': 'project.knowledge.item',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('id', 'in', self.knowledge_item_ids.ids)],
        }

    def action_set_reviewed(self):
        """Marquer le rapport comme révisé."""
        self.write({'report_state': 'reviewed'})

    def report_date_display_html(self):
        """Date (tz client en premier, tz organisateur en plus petit) en Markup,
        pour le gabarit de courriel du compte rendu."""
        self.ensure_one()
        return _format_meeting_date_display(self)

    def action_send_report(self):
        """Ouvrir l'assistant d'envoi du rapport par courriel."""
        self.ensure_one()
        template = self.env.ref(
            'bf_meeting.meeting_report_mail_template', raise_if_not_found=False
        )
        ctx = {
            'default_model': 'meeting.record',
            'default_res_ids': self.ids,
            'default_composition_mode': 'comment',
            'default_email_layout_xmlid': 'mail.mail_notification_light',
        }
        if template:
            ctx['default_template_id'] = template.id
        attachment = self._bf_exchange_attachment()
        if attachment:
            # `default_attachment_ids` ne survit pas : `_compute_attachment_ids`
            # du composeur se déclenche sur `template_id` et ÉCRASE la valeur
            # par défaut par les pièces du gabarit. D'où la clé de contexte, que
            # `mail.compose.message` relit après le calcul d'origine.
            ctx['bf_meeting_exchange_attachment_id'] = attachment.id
        return {
            'type': 'ir.actions.act_window',
            'name': 'Envoyer le rapport',
            'res_model': 'mail.compose.message',
            'views': [[False, 'form']],
            'target': 'new',
            'context': ctx,
        }

    def action_send_report_direct(self):
        """Envoyer le rapport directement sans assistant de composition."""
        self.ensure_one()
        template = self.env.ref(
            'bf_meeting.meeting_report_mail_template', raise_if_not_found=False
        )
        if not template:
            _logger.warning("Meeting report mail template not found")
            return True

        # Only send to the recipients explicitly listed in « Destinataires ».
        # No participant fallback: auto-filling caused accidental sends to
        # clients who were never meant to be on the recipient list.
        if not self.report_recipient_ids:
            raise UserError(
                "Aucun destinataire : ajoutez au moins une personne dans le "
                "champ « Destinataires » avant d'envoyer le compte rendu."
            )

        # Multi-company guard: the QWeb report reads project_id.name etc., which
        # is blocked by ir.rule when the project lives in a company that is not
        # in allowed_company_ids on the current request. Resolve the project's
        # company via sudo (the project ref itself is otherwise unreadable in a
        # mismatched company context) and force it into context before rendering.
        target_company = (
            self.sudo().project_id.company_id
            or self.sudo().company_id
            or self.env.company
        )
        # ⚠️ L'ORDRE compte : `env.company` est le PREMIER de
        # `allowed_company_ids`, pas un simple membre. Un `set()` ici replaçait
        # la société principale (id 1) devant la société visée et défaisait en
        # silence le `with_company()` posé plus bas : le PDF du compte rendu
        # sortait alors aux couleurs de la société principale.
        context_ids = self.env.context.get('allowed_company_ids') or [self.env.company.id]
        allowed_ids = [target_company.id] + [
            cid for cid in context_ids if cid != target_company.id
        ]
        # force_send=False: the SMTP roundtrip took 13-22s per send and, with a
        # small worker pool, stalled every other request. The mail is queued
        # (state « outgoing ») and the scheduler cron is triggered so it leaves
        # within seconds without holding an HTTP worker.
        email_values = {}
        attachment = self._bf_exchange_attachment()
        if attachment:
            # `send_mail_batch` écrase `attachment_ids` avec `email_values`,
            # mais le PDF du compte rendu ne passe PAS par là : il arrive en
            # `attachments` (nom, données), sorti du dictionnaire après la mise
            # à jour. Le rapport survit donc à cette ligne — vérifié au banc.
            email_values['attachment_ids'] = [(4, attachment.id)]
        # Pas de `with_company()` ici : il serait écrasé par le `with_context`
        # qui suit. La société visée est déjà en tête d'`allowed_ids`.
        template.with_context(
            allowed_company_ids=allowed_ids,
        ).send_mail(self.id, force_send=False, email_values=email_values or None)
        self.env.ref('mail.ir_cron_mail_scheduler_action')._trigger()
        self.write({
            'report_state': 'sent',
            'report_sent_date': fields.Datetime.now(),
            'report_sent_manually': False,
        })
        return True

    def action_send_report_auto(self):
        """Envoyer le compte rendu sans intervention, après la revue Gen.

        Seule porte du flux automatique : le pont l'appelle sur verdict
        « send », et rien d'autre ne mène à un envoi non demandé. La garde vit
        ici, en base, et pas chez l'appelant — une consigne donnée à Gen ou une
        condition écrite dans le pont ne garde rien le jour où le déclencheur
        se trompe de compte rendu.

        Rend True si le courriel est parti, False si la rencontre n'est pas
        éligible. Jamais None : XML-RPC ne sait pas le sérialiser, et l'erreur
        de marshalling se lit alors comme un échec d'envoi.
        """
        self.ensure_one()
        # sudo sur le projet seulement : le drapeau doit être lisible même
        # quand la requête porte une autre société, sans pour autant élargir
        # les droits de l'envoi lui-même.
        project = self.sudo().project_id
        if not project or not project.meeting_autosend:
            _logger.info(
                "Envoi automatique refusé pour meeting.record #%s : le projet "
                "%s ne le permet pas.",
                self.id, project.display_name if project else '(aucun)')
            return False
        if self.report_state == 'sent':
            _logger.info(
                "Envoi automatique ignoré pour meeting.record #%s : déjà "
                "envoyé le %s.", self.id, self.report_sent_date)
            return False
        if not self.report_recipient_ids:
            _logger.info(
                "Envoi automatique refusé pour meeting.record #%s : aucun "
                "destinataire.", self.id)
            return False
        self.action_send_report_direct()
        self.message_post(
            body=Markup(
                "<p>🤖 Compte rendu <strong>envoyé automatiquement</strong> "
                "après la revue Gen, à : %s.</p>"
            ) % escape(', '.join(self.report_recipient_ids.mapped('name'))),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return True

    def action_mark_report_sent_manually(self):
        """Déclarer le compte rendu envoyé hors Odoo.

        Le compte rendu part parfois autrement que par le bouton : copié dans
        un courriel, déposé dans un fil de clavardage, remis en personne. Sans
        cette porte, son état restait « brouillon / révisé » indéfiniment et
        tous les compteurs (tableau de bord, relances) le comptaient comme
        resté à faire.
        """
        for rec in self:
            if rec.report_state == 'sent' and not rec.report_sent_manually:
                continue
            rec.write({
                'report_state': 'sent',
                'report_sent_date': rec.report_sent_date or fields.Datetime.now(),
                'report_sent_manually': True,
            })
            rec.message_post(
                body=Markup(
                    "<p>✉️ Compte rendu déclaré <strong>envoyé à la main</strong> "
                    "par %s : la transmission a eu lieu hors Odoo.</p>"
                ) % escape(self.env.user.name),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        return True

    def action_unmark_report_sent_manually(self):
        """Retirer une déclaration d'envoi manuel posée par erreur."""
        for rec in self.filtered('report_sent_manually'):
            rec.write({
                'report_state': 'reviewed',
                'report_sent_date': False,
                'report_sent_manually': False,
            })
            rec.message_post(
                body=Markup(
                    "<p>↩️ Déclaration d'envoi manuel retirée par %s : "
                    "le compte rendu repasse à « révisé ».</p>"
                ) % escape(self.env.user.name),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        return True

    def action_open_refine_wizard(self):
        """Ouvrir l'assistant de raffinage (champ libre de consignes).

        Remplace l'ancien dialogue `confirm=` du bouton : au lieu d'un simple
        oui/non, le gestionnaire peut signaler ce que la passe automatique
        a raté (un sigle massacré par la transcription, un prénom douteux,
        un client mal routé). La revue reste complète — les consignes s'y
        ajoutent.
        """
        self.ensure_one()
        self._check_refine_access()
        return {
            "type": "ir.actions.act_window",
            "name": "Raffiner avec Gen",
            "res_model": "meeting.refine.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_meeting_id": self.id},
        }

    def _bridge_tenant(self):
        """Locataire annoncé au pont Claude.

        Le module tourne sur plusieurs bases et le pont n'accepte qu'une
        liste fermée de locataires ; l'ancien littéral « bf » faisait donc
        passer les demandes d'un autre locataire pour des demandes BF dans les
        journaux du pont. Le paramètre système `bf_meeting.bridge_tenant`
        porte la valeur réelle par base.
        """
        return (self.env['ir.config_parameter'].sudo()
                .get_param('bf_meeting.bridge_tenant', 'bf') or 'bf').strip()

    def _check_refine_access(self):
        """Garde-fou commun à l'assistant et au lancement.

        Réservé aux gestionnaires (`bf_meeting.group_meeting_manager`) car le
        bridge spawn `claude -p --dangerously-skip-permissions`, qui contourne
        toute vérification de permissions côté Claude.
        """
        if not self.env.user.has_group("bf_meeting.group_meeting_manager"):
            raise UserError(
                "Le raffinement automatique est réservé aux gestionnaires "
                "(groupe « Rencontres / Gestionnaire »)."
            )

    def action_refine_meeting(self, instructions=None):
        """Lancer le skill /refine-meeting via le bridge Claude.

        Le bridge est appelé en arrière-plan (thread) parce que /refine-meeting
        peut prendre plusieurs minutes ; le résultat est posté au chatter.

        `instructions` : texte libre saisi dans `meeting.refine.wizard`. Il est
        posté au chatter en note interne (trace durable, et second canal de
        lecture pour le skill) puis transmis au bridge, qui le colle dans le
        prompt entre les marqueurs `<<<CONSIGNES` / `CONSIGNES>>>`.
        """
        self.ensure_one()
        self._check_refine_access()

        instructions = (instructions or "").strip()[:_MAX_REFINE_INSTRUCTIONS]
        if instructions:
            # mt_note : note interne, aucun courriel aux abonnés. Postée avant
            # le lancement pour que le skill la retrouve même si le prompt a
            # été tronqué côté bridge.
            self.message_post(
                body=Markup(
                    # <div> et non <blockquote> : le sanitizer d'Odoo
                    # marque toute blockquote comme citation de courriel
                    # (data-o-mail-quote) et le chatter la replie derrière
                    # un « … » — les consignes deviendraient invisibles.
                    "<p><b>Consignes de raffinage</b> — transmises à "
                    "Gen par %s :</p>"
                    "<div style=\"border-left:3px solid #29ABE2;"
                    "padding-left:.75em;margin:.25em 0;color:#444;\">%s</div>"
                ) % (
                    self.env.user.name,
                    escape(instructions).replace("\n", Markup("<br/>")),
                ),
                subtype_xmlid="mail.mt_note",
            )

        ICP = self.env["ir.config_parameter"].sudo()
        timeout = int(ICP.get_param("bf_meeting.bridge_timeout", "480"))

        # Lève un UserError nommant le paramètre à corriger si la socket manque.
        self.env["bf.ai.bridge"].check_available()
        # Capturé ici : le fil détaché ci-dessous survit à ce curseur, il ne
        # peut donc plus relire la configuration.
        socket_path = self.env["bf.ai.bridge"].socket_path()

        record_id = self.id
        db_name = self.env.cr.dbname
        uid = self.env.user.id
        triggered_by = self.env.user.login
        tenant = self._bridge_tenant()

        # Marqué « en cours » avant le lancement : le formulaire montre
        # l'indicateur dès le retour du clic, et le bouton se retire le temps
        # de la passe. La transaction de la requête commite pour nous.
        self.set_refine_state(
            'queued', f"Lancement demandé par {self.env.user.name}")

        def _run():
            from odoo import api as _api, registry as _registry
            try:
                resp = transport.post(
                    socket_path, "/refine-meeting",
                    {
                        "meeting_id": record_id,
                        "tenant": tenant,
                        "triggered_by": triggered_by,
                        "user_notes": instructions,
                    },
                    timeout,
                )
                status = resp.get("status", "?")
                msg = resp.get("message", "")
            except Exception as exc:
                status, msg = "error", f"{type(exc).__name__}: {exc}"

            with _registry(db_name).cursor() as new_cr:
                new_env = _api.Environment(new_cr, uid, {})
                rec = new_env["meeting.record"].browse(record_id).exists()
                if rec:
                    # `ok` ne veut dire QUE « le pont a pris la demande » : la
                    # passe elle-même dure plusieurs minutes et c'est le pont
                    # qui écrira `done`/`error` en la terminant. On garde donc
                    # « en cours » ici, et on n'écrase jamais un état terminal
                    # déjà posé (course théorique sur une passe très courte).
                    if status == "ok":
                        if rec.refine_state == 'queued':
                            rec.set_refine_state('queued', msg)
                    else:
                        rec.set_refine_state('error', msg or status)
                    body = (
                        f"<p><b>Raffinement /refine-meeting</b> — statut : "
                        f"<code>{escape(status)}</code></p>"
                    )
                    if msg:
                        body += f"<p>{escape(msg)}</p>"
                    rec.message_post(body=Markup(body), message_type="comment")

        threading.Thread(target=_run, daemon=True).start()

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "title": "Raffinement lancé",
                "message": (
                    "Le skill /refine-meeting est en cours d'exécution"
                    + (" avec vos consignes" if instructions else "")
                    + ". Le résultat apparaîtra au chatter dans quelques "
                    "minutes."
                ),
                "sticky": False,
            },
        }

    def action_import_attendance(self):
        """Importer les invités de l'événement calendrier comme présences."""
        self.ensure_one()
        existing = self.attendance_ids.mapped('partner_id')
        source = self.calendar_event_id.partner_ids if self.calendar_event_id \
            else self.env['res.partner']
        for partner in source:
            if partner not in existing:
                self.env['meeting.attendance'].create({
                    'meeting_id': self.id,
                    'partner_id': partner.id,
                    'status': 'present',
                })

    def action_view_agenda(self):
        """Ouvrir l'ordre du jour lié."""
        self.ensure_one()
        if self.agenda_id:
            return {
                'type': 'ir.actions.act_window',
                'name': self.agenda_id.name,
                'res_model': 'meeting.agenda',
                'res_id': self.agenda_id.id,
                'views': [[False, 'form']],
            }

    def action_view_calendar_event(self):
        """Ouvrir l'événement calendrier lié."""
        self.ensure_one()
        if self.calendar_event_id:
            return {
                'type': 'ir.actions.act_window',
                'name': self.calendar_event_id.name,
                'res_model': 'calendar.event',
                'res_id': self.calendar_event_id.id,
                'views': [[False, 'form']],
            }

    def _bf_exchange_slug(self):
        """Fragment de nom de fichier, sûr sur les trois systèmes de fichiers."""
        self.ensure_one()
        base = self.room_name or self.name or 'compte-rendu'
        base = unicodedata.normalize('NFKD', base).encode('ascii', 'ignore').decode()
        base = re.sub(r'[^A-Za-z0-9]+', '-', base).strip('-').lower()
        return (base or 'compte-rendu')[:60]

    def _bf_exchange_attachment(self):
        """Pièce jointe portant la copie lisible par la machine, ou rien.

        Rattachée au compte rendu, pas au courriel : elle reste consultable
        après coup, et un renvoi la régénère plutôt que d'en empiler une
        deuxième.
        """
        self.ensure_one()
        if not self.exchange_include_json:
            return self.env['ir.attachment']
        payload = self.env['meeting.exchange'].build_payload(self)
        raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
        date_part = (self.date or fields.Datetime.now()).strftime('%Y-%m-%d')
        name = f"compte-rendu-{self._bf_exchange_slug()}-{date_part}.json"
        existing = self.env['ir.attachment'].search([
            ('res_model', '=', 'meeting.record'),
            ('res_id', '=', self.id),
            ('name', '=', name),
        ], limit=1)
        values = {
            'name': name,
            'raw': raw.encode('utf-8'),
            'mimetype': 'application/json',
            'res_model': 'meeting.record',
            'res_id': self.id,
            'type': 'binary',
        }
        if existing:
            existing.write(values)
            return existing
        return self.env['ir.attachment'].create(values)

    def _get_report_data(self):
        """Prepare data for the PDF report template."""
        self.ensure_one()
        data = {}
        if self.structured_notes_json:
            try:
                loaded = json.loads(self.structured_notes_json)
            except (json.JSONDecodeError, TypeError, ValueError):
                loaded = None
            if isinstance(loaded, dict):
                data = loaded

        return {
            'today': fields.Date.context_today(self).strftime('%Y-%m-%d'),
            'date_display': _format_meeting_date_display(self),
            # Le gabarit PDF fait `t-foreach` là-dessus : une chaîne s'y
            # itérerait caractère par caractère.
            'topics': self._iter_entries(data.get('topics')),
            'deliverables': self._iter_entries(data.get('deliverables')),
            'open_questions': self._iter_entries(data.get('open_questions')),
            'decision_count': len(self.decision_ids),
            'task_count': len(self.task_ids),
            'participant_count': len(self.attendance_ids) or len(self.participant_ids),
        }
