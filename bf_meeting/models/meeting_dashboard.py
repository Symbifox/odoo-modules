import logging
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models, tools

_logger = logging.getLogger(__name__)


class MeetingDashboard(models.AbstractModel):
    """Modèle d'agrégation pour le tableau de bord OWL des rencontres.

    Pas de table : sert d'entrée RPC pour le composant client `meeting_dashboard`
    qui consomme `get_dashboard_data()`. Toutes les lignes proviennent de la
    vue SQL `meeting.dashboard.line` ; on agrège ici en tuiles KPI + tableau
    de complétion.
    """

    _name = 'meeting.dashboard'
    _description = "Tableau de bord des rencontres"
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve (BF #24867).

    @api.model
    def get_dashboard_data(self, limit=60):
        """Aggregate KPIs + cards in a single SQL pass on the dashboard view.

        The expensive part is materializing `meeting_dashboard_line` from
        calendar.event ; we hit it ONCE and compute KPIs in Python from the
        fetched rows. The only extra query is the 30-day completion rate,
        which scans `meeting_record` (fast — small table).

        Per-user horizons (``bf_meeting_dashboard_lookahead_days`` /
        ``bf_meeting_dashboard_lookback_days`` on ``res.users``) narrow the
        view's hard limits of +90 days / -180 days.

        ⚠️ Le SQL brut ci-dessous **ne passe pas par l'ORM**, donc ni les ACL ni
        les ``ir.rule`` ne s'y appliquent : le scoping doit être écrit à la main.
        On reproduit ici, en SQL, exactement les deux garde-fous que l'ORM
        appliquerait — la société active et ``rule_meeting_record_user`` — sinon
        n'importe quel membre de ``group_meeting_user`` lit toutes les lignes de
        la base, toutes sociétés confondues (noms de clients et de projets
        compris). Toute évolution de ``rule_meeting_dashboard_line_user`` dans
        ``security/meeting_security.xml`` doit être répercutée ici.
        """
        user = self.env.user
        lookahead = max(1, min(user.bf_meeting_dashboard_lookahead_days or 90, 90))
        lookback = max(1, min(user.bf_meeting_dashboard_lookback_days or 180, 180))

        # ---- Scoping (voir docstring) ----
        params = {'companies': list(self.env.companies.ids)}
        # Société : une ligne sans société reste visible (convention Odoo pour
        # les enregistrements non rattachés).
        where = ["(dl.company_id IS NULL OR dl.company_id = ANY(%(companies)s))"]
        # Projet : miroir de `rule_meeting_record_user`. Le gestionnaire voit
        # tout (miroir de `rule_meeting_record_manager`).
        if not user.has_group('bf_meeting.group_meeting_manager'):
            params['partner'] = user.partner_id.id
            where.append("""(
                dl.project_id IS NULL
                OR EXISTS (
                    SELECT 1 FROM mail_followers mf
                    WHERE mf.res_model = 'project.project'
                      AND mf.res_id = dl.project_id
                      AND mf.partner_id = %(partner)s
                )
            )""")
        scope_sql = " AND ".join(where)
        # ---- Single query : view + joins, all the data we need ----
        self.env.cr.execute("""
            SELECT
                dl.id,
                dl.event_id,
                dl.agenda_id,
                dl.record_id,
                dl.date,
                dl.name,
                p.id AS project_id,
                p.name AS project_name,
                rp.id AS partner_id,
                rp.name AS partner_name,
                dl.is_past,
                dl.is_upcoming,
                dl.has_agenda_drafted,
                dl.has_agenda_reviewed,
                dl.has_agenda_sent,
                dl.is_waiting_for_meeting,
                dl.has_minutes_drafted,
                dl.has_minutes_reviewed,
                dl.has_minutes_sent,
                dl.completion_score,
                dl.skipped_steps,
                dl.agenda_resp_id,
                dl.minutes_resp_id,
                up_a.name AS agenda_resp_name,
                up_m.name AS minutes_resp_name,
                CASE
                    WHEN dl.is_past AND NOT dl.has_minutes_drafted THEN 1
                    WHEN dl.has_minutes_drafted AND NOT dl.has_minutes_sent THEN 2
                    WHEN dl.is_upcoming AND NOT dl.has_agenda_drafted THEN 3
                    ELSE 4
                END AS priority_bucket,
                (dl.date >= date_trunc('week', NOW() AT TIME ZONE 'UTC')
                 AND dl.date <  date_trunc('week', NOW() AT TIME ZONE 'UTC') + INTERVAL '7 days') AS in_this_week
            FROM meeting_dashboard_line dl
            LEFT JOIN project_project p  ON p.id  = dl.project_id
            LEFT JOIN res_partner rp     ON rp.id = dl.partner_id
            LEFT JOIN res_users ua       ON ua.id = dl.agenda_resp_id
            LEFT JOIN res_partner up_a   ON up_a.id = ua.partner_id
            LEFT JOIN res_users um       ON um.id = dl.minutes_resp_id
            LEFT JOIN res_partner up_m   ON up_m.id = um.partner_id
            WHERE """ + scope_sql + """
            ORDER BY priority_bucket, dl.date DESC NULLS LAST
        """, params)
        all_rows = self.env.cr.dictfetchall()

        # ---- Apply per-user horizons (narrow the view's hard window) ----
        now = fields.Datetime.now()
        upper = now + timedelta(days=lookahead)
        lower = now - timedelta(days=lookback)
        all_rows = [
            r for r in all_rows
            if not r['date'] or (lower <= r['date'] <= upper)
        ]

        # ---- KPIs in Python from the same result set ----
        kpis = {
            'upcoming_total':            sum(1 for r in all_rows if r['is_upcoming']),
            'upcoming_without_agenda':   sum(1 for r in all_rows if r['is_upcoming'] and not r['has_agenda_drafted']),
            'upcoming_agenda_not_sent':  sum(1 for r in all_rows if r['is_upcoming'] and r['has_agenda_drafted'] and not r['has_agenda_sent']),
            'past_without_minutes':      sum(1 for r in all_rows if r['is_past'] and not r['has_minutes_drafted']),
            'minutes_to_review':         sum(1 for r in all_rows if r['has_minutes_drafted'] and not r['has_minutes_reviewed']),
            'minutes_to_send':           sum(1 for r in all_rows if r['has_minutes_reviewed'] and not r['has_minutes_sent']),
            'this_week':                 sum(1 for r in all_rows if r['in_this_week']),
            'total_open':                len(all_rows),
        }

        # ---- 30-day completion rate (small table, fast) ----
        self.env.cr.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE report_state = 'sent') AS sent
            FROM meeting_record
            WHERE active = true
              AND date < NOW() AT TIME ZONE 'UTC'
              AND date >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days'
        """)
        rec = self.env.cr.dictfetchone() or {}
        rec_total = rec.get('total') or 0
        rec_sent = rec.get('sent') or 0
        kpis['completion_pct_30d']   = int(round(100 * rec_sent / rec_total)) if rec_total else 100
        kpis['completion_total_30d'] = rec_total
        kpis['completion_sent_30d']  = rec_sent

        # ---- Cards split into two columns ----
        # LEFT  = upcoming meetings with at least one pending OdJ step (1-3)
        # RIGHT = past meetings with at least one pending minutes step (5-7)
        cards_left = []
        cards_right = []
        for r in all_rows:
            steps = self._build_steps(r)
            # A "current" step exists only if there's something actionable
            # left after honoring skipped steps.
            has_current = any(s['status'] == 'current' for s in steps)
            if not has_current:
                # Nothing actionable — skip card; meeting effectively done.
                continue
            agenda_pending = any(s['status'] == 'current' for s in steps[:3])
            minutes_pending = any(s['status'] == 'current' for s in steps[4:])
            card = {
                'id': r['id'],
                'event_id': r['event_id'] or False,
                'agenda_id': r['agenda_id'] or False,
                'record_id': r['record_id'] or False,
                'date': fields.Datetime.to_string(r['date']) if r['date'] else False,
                'name': r['name'] or '(sans nom)',
                'project_id': r['project_id'] or False,
                'project_name': r['project_name'] or '(Sans projet)',
                'partner_id': r['partner_id'] or False,
                'partner_name': r['partner_name'] or '',
                'is_past': bool(r['is_past']),
                'is_upcoming': bool(r['is_upcoming']),
                'in_this_week': bool(r['in_this_week']),
                'days_overdue': self._days_overdue(r),
                'score': r['completion_score'] or 0,
                'row_state': ['', 'danger', 'warning', 'info', 'ok'][r['priority_bucket']],
                'steps': steps,
                'agenda_resp_id':    r['agenda_resp_id']    or False,
                'agenda_resp_name':  r['agenda_resp_name']  or '',
                'minutes_resp_id':   r['minutes_resp_id']   or False,
                'minutes_resp_name': r['minutes_resp_name'] or '',
            }
            # Past meeting → minutes side. Upcoming → agenda side.
            if r['is_past'] and minutes_pending:
                cards_right.append(card)
            elif r['is_upcoming'] and agenda_pending:
                cards_left.append(card)
            elif r['is_past'] and agenda_pending:
                # Past but only agenda pending (rare) → put on right anyway
                cards_right.append(card)
            elif r['is_upcoming'] and minutes_pending:
                cards_left.append(card)

        half = max(limit // 2, 10)
        return {
            'kpis': kpis,
            'cards_left': cards_left[:half],
            'cards_right': cards_right[:half],
            'cards_left_total': len(cards_left),
            'cards_right_total': len(cards_right),
        }

    @staticmethod
    def _days_overdue(r):
        if not r['is_past'] or not r['date']:
            return 0
        delta = (fields.Datetime.now() - r['date']).days
        return max(delta, 0)

    @staticmethod
    def _parse_skipped(s):
        if not s:
            return set()
        return {int(x) for x in s.split(',') if x.strip().isdigit()}

    @classmethod
    def _build_steps(cls, r):
        """Build the 7-step progression for the OWL stepper.

        Steps that the user has marked as non-required (via `skipped_steps`)
        render with status='skipped' and do not block lifecycle progression —
        a card disappears from the dashboard once every pending step is
        either done or skipped.
        """
        flags = [
            r['has_agenda_drafted'],
            r['has_agenda_reviewed'],
            r['has_agenda_sent'],
            r['is_waiting_for_meeting'] or r['is_past'],
            r['has_minutes_drafted'],
            r['has_minutes_reviewed'],
            r['has_minutes_sent'],
        ]
        labels = [
            ('OdJ', "Rédiger l'OdJ",      'fa-pencil-square-o'),
            ('OdJ', "Réviser l'OdJ",      'fa-check'),
            ('OdJ', "Envoyer l'OdJ",      'fa-paper-plane-o'),
            ('Renc.', 'Tenir la rencontre', 'fa-users'),
            ('CR', 'Rédiger le CR',       'fa-file-text-o'),
            ('CR', 'Réviser le CR',       'fa-search'),
            ('CR', 'Envoyer le CR',       'fa-paper-plane'),
        ]
        skipped = cls._parse_skipped(r.get('skipped_steps') or '')
        # First step that is neither done nor skipped = "current"
        next_idx = None
        for i, f in enumerate(flags):
            if not f and (i + 1) not in skipped:
                next_idx = i
                break
        steps = []
        for i, ((short, full, icon), done) in enumerate(zip(labels, flags)):
            if (i + 1) in skipped:
                status = 'skipped'
            elif done:
                status = 'done'
            elif i == next_idx:
                status = 'current'
            else:
                status = 'pending'
            steps.append({
                'idx': i + 1,
                'short': short,
                'full': full,
                'icon': icon,
                'status': status,
            })
        return steps

    # ---- Click-through actions returned to the OWL component ----

    @api.model
    def open_filtered_list(self, filter_key):
        """Return an act_window action with the given dashboard filter applied."""
        ctx = {'search_default_' + filter_key: 1} if filter_key else {}
        return {
            'type': 'ir.actions.act_window',
            'name': 'Rencontres',
            'res_model': 'meeting.dashboard.line',
            'view_mode': 'list',
            'views': [[False, 'list']],
            'context': ctx,
            'target': 'current',
        }

    @api.model
    def dismiss_event(self, event_id):
        """Mark a calendar.event as excluded from the dashboard."""
        if not event_id:
            return False
        event = self.env['calendar.event'].browse(event_id)
        if event.exists():
            event.bf_skip_dashboard = True
        return True

    @api.model
    def dismiss_events(self, event_ids):
        """Bulk version of dismiss_event."""
        if not event_ids:
            return 0
        events = self.env['calendar.event'].browse(event_ids).exists()
        events.write({'bf_skip_dashboard': True})
        return len(events)

    @api.model
    def toggle_step_skip(self, event_id, step_idx):
        """Toggle whether a given step (1-7) is marked as skipped on this event.

        Skip cascade: when marking a step as skipped, also skip any previous
        pending steps (not Done) so the user doesn't have to click each. Done
        steps are preserved. Unskip is per-step only.
        """
        if not event_id or not (1 <= int(step_idx) <= 7):
            return False
        event = self.env['calendar.event'].browse(event_id)
        if not event.exists():
            return False
        skipped = MeetingDashboard._parse_skipped(event.bf_dashboard_skipped_steps or '')
        step_idx = int(step_idx)
        if step_idx in skipped:
            skipped.discard(step_idx)
        else:
            # Cascade : also skip previous pending (non-Done) steps.
            done_flags = self._compute_done_flags(event)
            for i in range(1, step_idx + 1):
                if not done_flags[i - 1]:
                    skipped.add(i)
        new_val = ','.join(str(i) for i in sorted(skipped))
        event.bf_dashboard_skipped_steps = new_val
        return new_val

    # ---- Daily digest ----

    @api.model
    def _get_digest_buckets(self, user_ids=None):
        """Pour chaque uid demandé, retourne ``{'odj': [...], 'cr': [...]}``.

        Pulls one pass on `meeting_dashboard_line` and buckets rows by user
        responsibility. ``odj`` = upcoming meetings (≤7d) where the user owns
        the agenda and at least one OdJ step (1-3) is pending. ``cr`` = past
        meetings where the user owns the minutes and at least one CR step
        (5-7) is pending. Skipped steps (per `bf_dashboard_skipped_steps`)
        suppress their own pending status.

        Used by the standalone cron AND by `daily_todo_digest` which embeds
        the meeting section into "Votre journée".
        """
        now = fields.Datetime.now()
        horizon_7d = now + timedelta(days=7)

        self.env.cr.execute("""
            SELECT
                dl.event_id, dl.agenda_id, dl.record_id, dl.name, dl.date,
                dl.has_agenda_drafted, dl.has_agenda_reviewed, dl.has_agenda_sent,
                dl.is_waiting_for_meeting,
                dl.has_minutes_drafted, dl.has_minutes_reviewed, dl.has_minutes_sent,
                dl.is_past, dl.is_upcoming,
                COALESCE(dl.skipped_steps, '') AS skipped_steps,
                dl.agenda_resp_id, dl.minutes_resp_id,
                -- project.project.name is translate=True in Odoo 18 (jsonb).
                -- Raw SQL returns the whole {'en_US': ..., 'fr_CA': ...} dict,
                -- which then leaks verbatim into the digest. Extract a single
                -- language here (fr_CA → en_CA → en_US source fallback).
                COALESCE(p.name->>'fr_CA', p.name->>'en_CA', p.name->>'en_US') AS project_name,
                rp.name AS partner_name
            FROM meeting_dashboard_line dl
            LEFT JOIN project_project p ON p.id  = dl.project_id
            LEFT JOIN res_partner rp    ON rp.id = dl.partner_id
        """)
        rows = self.env.cr.dictfetchall()

        wanted = set(user_ids) if user_ids else None
        per_user = {}
        for r in rows:
            skipped = self._parse_skipped(r['skipped_steps'])
            if r['is_upcoming'] and r['date'] and r['date'] <= horizon_7d:
                agenda_flags = [r['has_agenda_drafted'], r['has_agenda_reviewed'], r['has_agenda_sent']]
                if any(not f and (i + 1) not in skipped for i, f in enumerate(agenda_flags)):
                    uid = r['agenda_resp_id']
                    if uid and (wanted is None or uid in wanted):
                        per_user.setdefault(uid, {'odj': [], 'cr': []})['odj'].append(r)
            if r['is_past']:
                minutes_flags = [r['has_minutes_drafted'], r['has_minutes_reviewed'], r['has_minutes_sent']]
                if any(not f and (i + 5) not in skipped for i, f in enumerate(minutes_flags)):
                    uid = r['minutes_resp_id']
                    if uid and (wanted is None or uid in wanted):
                        per_user.setdefault(uid, {'odj': [], 'cr': []})['cr'].append(r)

        for bucket in per_user.values():
            bucket['odj'].sort(key=lambda r: r['date'] or now)
            bucket['cr'].sort(key=lambda r: r['date'] or now, reverse=True)
        return per_user

    @api.model
    def _cron_send_daily_digest(self):
        """Standalone "Rencontres — votre digest du jour" email.

        Kept available but inactive by default since `daily_todo_digest`
        ("🌄 Votre journée") now embeds the meeting section directly.
        """
        group = self.env.ref('bf_meeting.group_meeting_user', raise_if_not_found=False)
        if not group:
            return
        users_by_id = {u.id: u for u in group.users if u.active and u.email}
        if not users_by_id:
            return

        per_user = self._get_digest_buckets(user_ids=list(users_by_id.keys()))

        sent = 0
        for uid, bucket in per_user.items():
            user = users_by_id[uid]
            body = self._render_digest_html(user, bucket['odj'], bucket['cr'])
            if not body:
                continue
            self.env['mail.mail'].sudo().create({
                'subject': "Rencontres — votre digest du jour",
                'body_html': body,
                'email_from': self.env.company.email or 'noreply@example.com',
                'email_to': user.email,
                'auto_delete': True,
            }).send()
            sent += 1
        _logger.info("Meeting daily digest : %d utilisateurs notifiés.", sent)
        return sent

    @api.model
    def _render_digest_html(self, user, odj_rows, cr_rows):
        """Render the per-user HTML digest body. Returns '' if both lists empty."""
        if not odj_rows and not cr_rows:
            return ''
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')

        def _row_html(r, kind):
            dt = fields.Datetime.context_timestamp(self.with_context(tz=user.tz or 'America/Toronto'), r['date'])
            date_str = dt.strftime('%a %d %b %Y, %H:%M') if dt else ''
            project = f' — <span style="color:#666;">{escape(r["project_name"])}</span>' if r['project_name'] else ''
            partner = f' — <span style="color:#666;">{escape(r["partner_name"])}</span>' if r['partner_name'] else ''
            # Link priority : record > agenda > calendar event
            if base_url:
                if r.get('record_id'):
                    url = f'{base_url}/odoo/meeting.record/{r["record_id"]}'
                elif r.get('agenda_id'):
                    url = f'{base_url}/odoo/meeting.agenda/{r["agenda_id"]}'
                elif r.get('event_id'):
                    url = f'{base_url}/odoo/calendar/{r["event_id"]}'
                else:
                    url = '#'
            else:
                url = '#'
            return (
                f'<li style="margin: 0.5em 0;">'
                f'<a href="{url}" style="color:#29ABE1; text-decoration:none;"><strong>{escape(r["name"])}</strong></a>'
                f'{project}{partner}<br/>'
                f'<small style="color:#666;">{date_str}</small>'
                f'</li>'
            )

        odj_html = ''.join(_row_html(r, 'odj') for r in odj_rows)
        cr_html  = ''.join(_row_html(r, 'cr')  for r in cr_rows)

        dashboard_url = f'{base_url}/odoo/action-meeting_dashboard_client_action' if base_url else ''
        link_to_dash = (
            f'<p style="margin-top: 1.5em;">'
            f'<a href="{dashboard_url}" style="color:#29ABE1;">Ouvrir le tableau de bord →</a></p>'
        ) if dashboard_url else ''

        odj_section = (
            f'<h3 style="color:#29ABE1; margin-bottom:0.25em;">📋 OdJ à préparer (7 prochains jours)</h3>'
            f'<ul style="padding-left:1.25em; margin-top:0;">{odj_html}</ul>'
        ) if odj_rows else ''

        cr_section = (
            f'<h3 style="color:#29ABE1; margin-bottom:0.25em;">📝 CR à compléter</h3>'
            f'<ul style="padding-left:1.25em; margin-top:0;">{cr_html}</ul>'
        ) if cr_rows else ''

        return (
            f'<div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 640px;">'
            f'<p>Bonjour {escape(user.name.split()[0] if user.name else "")},</p>'
            f'<p>Voici votre récapitulatif quotidien des rencontres à traiter :</p>'
            f'{odj_section}'
            f'{cr_section}'
            f'{link_to_dash}'
            f'<hr style="border:none; border-top:1px solid #eee; margin: 1.5em 0;"/>'
            f'<p style="color:#999; font-size:0.85em;">'
            f'Vous recevez ce courriel parce que vous êtes responsable d\'au moins un OdJ ou CR. '
            f'Pour retirer une rencontre de ce suivi, ouvrez son événement calendrier et cochez '
            f'« Exclure du tableau de bord ».'
            f'</p>'
            f'</div>'
        )

    @staticmethod
    def _compute_done_flags(event):
        """7-element list of bools : which lifecycle steps are already done."""
        agenda = event.meeting_agenda_id
        record = event.meeting_record_id
        return [
            bool(agenda),                                                       # 1 OdJ drafted
            bool(agenda) and agenda.state in ('confirmed', 'done'),             # 2 OdJ reviewed
            bool(agenda) and bool(agenda.sent_date),                            # 3 OdJ sent
            bool(event.start) and event.start < fields.Datetime.now(),          # 4 meeting happened
            bool(record),                                                       # 5 CR drafted
            bool(record) and record.report_state in ('reviewed', 'sent'),       # 6 CR reviewed
            bool(record) and record.report_state == 'sent',                     # 7 CR sent
        ]

    @api.model
    def open_record(self, model, res_id):
        if not res_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': model,
            'res_id': res_id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }


class MeetingDashboardLine(models.Model):
    """Vue SQL : une ligne par `calendar.event` dans le périmètre dashboard.

    Filtre : événement actif, non all-day, avec participants externes,
    `bf_skip_agenda=False`, et soit (a) date future ≤ +30j, soit (b) date passée
    dont le compte rendu n'a pas encore été envoyé.

    Colonnes booléennes = cases à cocher du tableau de suivi (OdJ rédigé /
    révisé / envoyé, En attente de la rencontre, CR rédigé / révisé / envoyé).
    """

    _name = 'meeting.dashboard.line'
    _description = "Tableau de bord des rencontres"
    _auto = False
    _order = 'date desc, name'

    name = fields.Char(string='Rencontre', readonly=True)
    date = fields.Datetime(string='Date', readonly=True)
    event_id = fields.Many2one('calendar.event', string='Événement', readonly=True)
    project_id = fields.Many2one('project.project', string='Projet', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Client', readonly=True)
    company_id = fields.Many2one('res.company', string='Société', readonly=True)
    agenda_id = fields.Many2one('meeting.agenda', string='Ordre du jour', readonly=True)
    record_id = fields.Many2one('meeting.record', string='Compte rendu', readonly=True)

    is_past = fields.Boolean(string='Passée', readonly=True)
    is_upcoming = fields.Boolean(string='À venir', readonly=True)

    has_agenda_drafted = fields.Boolean(string='OdJ rédigé', readonly=True)
    has_agenda_reviewed = fields.Boolean(string='OdJ révisé', readonly=True)
    has_agenda_sent = fields.Boolean(string='OdJ envoyé', readonly=True)
    is_waiting_for_meeting = fields.Boolean(
        string='En attente de la rencontre', readonly=True,
    )
    has_minutes_drafted = fields.Boolean(string='CR rédigé', readonly=True)
    has_minutes_reviewed = fields.Boolean(string='CR révisé', readonly=True)
    has_minutes_sent = fields.Boolean(string='CR envoyé', readonly=True)

    # Score 0-7 : utile pour le tri "rencontres les plus en retard"
    completion_score = fields.Integer(string='Score complétion', readonly=True)
    skipped_steps = fields.Char(string='Étapes ignorées', readonly=True)

    agenda_resp_id = fields.Many2one('res.users', string="Responsable OdJ", readonly=True)
    minutes_resp_id = fields.Many2one('res.users', string='Responsable CR', readonly=True)

    def init(self):
        # Ensure the columns this view depends on exist BEFORE creating it.
        # init() can run before calendar.event's own _auto_init when the
        # ordering of init_models places this view first.
        self.env.cr.execute("""
            ALTER TABLE calendar_event
                ADD COLUMN IF NOT EXISTS bf_skip_agenda BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS bf_skip_dashboard BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS bf_dashboard_skipped_steps VARCHAR,
                ADD COLUMN IF NOT EXISTS bf_agenda_responsible_id INTEGER,
                ADD COLUMN IF NOT EXISTS bf_minutes_responsible_id INTEGER
        """)
        self.env.cr.execute("""
            ALTER TABLE project_project
                ADD COLUMN IF NOT EXISTS bf_skip_dashboard BOOLEAN DEFAULT FALSE
        """)
        self.env.cr.execute("""
            ALTER TABLE res_partner
                ADD COLUMN IF NOT EXISTS bf_skip_dashboard BOOLEAN DEFAULT FALSE
        """)
        # Same guard as the three above, and for the same reason: `init()` runs
        # per model right after that model's `_auto_init()`, so on a *fresh*
        # install this view can be initialised before `res.users` has been given
        # the columns bf_meeting adds to it. Without this, the lookahead backfill
        # below aborted the whole installation with
        # `column "bf_meeting_dashboard_lookahead_days" does not exist`.
        self.env.cr.execute("""
            ALTER TABLE res_users
                ADD COLUMN IF NOT EXISTS bf_meeting_dashboard_lookahead_days INTEGER
        """)
        # Backfill responsible fields from the event's user_id (organizer).
        # Idempotent : only fills NULL values, plus the rows pointing at a user
        # who cannot be a responsible.
        #
        # Le `WHERE IS NULL` seul ne suffisait pas : à la création de la colonne,
        # l'ORM y applique le défaut du champ pour les lignes existantes, et ce
        # défaut tournait sous OdooBot pendant la mise à jour. Résultat, les
        # lignes valaient déjà `1` (OdooBot) et le backfill ne les voyait jamais.
        # On rattrape ici tout responsable non interne / inactif (OdooBot, compte
        # public du site, utilisateur désactivé depuis).
        # Même règle que `_bf_resolve_responsibles` côté Python : on ne recale que
        # vers un organisateur qui est lui-même un interne actif, sinon on ne
        # ferait que remplacer un mauvais responsable par un autre.
        for _fname in ('bf_agenda_responsible_id', 'bf_minutes_responsible_id'):
            self.env.cr.execute(f"""
                UPDATE calendar_event ce
                SET {_fname} = ce.user_id
                WHERE EXISTS (
                          SELECT 1 FROM res_users o
                          WHERE o.id = ce.user_id
                            AND o.active = true
                            AND o.share = false
                      )
                  AND ce.{_fname} IS DISTINCT FROM ce.user_id
                  AND (
                      ce.{_fname} IS NULL
                      OR EXISTS (
                          SELECT 1 FROM res_users u
                          WHERE u.id = ce.{_fname}
                            AND (u.share = true OR u.active = false)
                      )
                  )
            """)
        # L'horizon « à venir » de la vue passe de +30 à +90 jours. 30 était à la
        # fois le défaut ET le plafond : personne n'a pu le choisir délibérément,
        # donc on remonte au nouveau plafond ceux qui y sont encore. Une valeur
        # autre que 30 est, elle, un vrai choix : on n'y touche pas.
        self.env.cr.execute("""
            UPDATE res_users
            SET bf_meeting_dashboard_lookahead_days = 90
            WHERE bf_meeting_dashboard_lookahead_days = 30
        """)
        # Partial index that matches the dashboard's calendar.event filter —
        # turns the previous 17k-row seq scan into a tiny index scan.
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS bf_meeting_dashboard_calevent_idx
            ON calendar_event (start)
            WHERE active = true AND allday = false AND bf_skip_agenda = false
        """)
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS
            -- ============================================================
            -- Source 1 : rencontres ancrées sur calendar.event
            -- ============================================================
            WITH event_anchored AS (
                SELECT
                    ce.id                                       AS event_id,
                    ce.name                                     AS name,
                    ce.start                                    AS date,
                    (SELECT id FROM meeting_agenda ma
                       WHERE ma.calendar_event_id = ce.id
                         AND ma.active = true
                       ORDER BY ma.id LIMIT 1)                  AS agenda_id,
                    (SELECT id FROM meeting_record mr
                       WHERE mr.calendar_event_id = ce.id
                         AND mr.active = true
                       ORDER BY mr.id LIMIT 1)                  AS record_id,
                    ce.bf_skip_agenda                           AS skip_agenda,
                    ce.bf_skip_dashboard                        AS skip_dashboard,
                    ce.bf_dashboard_skipped_steps               AS skipped_steps,
                    ce.bf_agenda_responsible_id                 AS agenda_resp_id,
                    ce.bf_minutes_responsible_id                AS minutes_resp_id,
                    ce.allday                                   AS allday,
                    ce.active                                   AS active,
                    (
                        (SELECT COUNT(*) FROM calendar_event_res_partner_rel cerp
                         WHERE cerp.calendar_event_id = ce.id) >= 2
                        OR EXISTS (SELECT 1 FROM meeting_agenda ma_e
                                   WHERE ma_e.calendar_event_id = ce.id AND ma_e.active = true)
                        OR EXISTS (SELECT 1 FROM meeting_record mr_e
                                   WHERE mr_e.calendar_event_id = ce.id AND mr_e.active = true)
                    )                                           AS has_partner
                FROM calendar_event ce
            ),
            event_rows AS (
                SELECT
                    b.event_id                                  AS id,
                    b.event_id                                  AS event_id,
                    b.name                                      AS name,
                    b.date                                      AS date,
                    b.agenda_id, b.record_id,
                    COALESCE(mr.project_id, ma.project_id)      AS project_id,
                    COALESCE(mr.partner_id, ma.partner_id)      AS partner_id,
                    COALESCE(mr.company_id, ma.company_id)      AS company_id,
                    b.agenda_resp_id, b.minutes_resp_id,
                    COALESCE(b.skipped_steps, '')               AS skipped_steps,
                    ma.state                                    AS agenda_state,
                    ma.sent_date                                AS agenda_sent_date,
                    mr.report_state                             AS record_state
                FROM event_anchored b
                LEFT JOIN meeting_agenda ma ON ma.id = b.agenda_id
                LEFT JOIN meeting_record mr ON mr.id = b.record_id
                WHERE b.active = true
                  AND b.allday IS NOT TRUE
                  AND b.skip_agenda IS NOT TRUE
                  AND b.skip_dashboard IS NOT TRUE
                  AND b.has_partner = true
                  AND (
                      (b.date >= NOW() AT TIME ZONE 'UTC'
                       AND b.date <= (NOW() AT TIME ZONE 'UTC') + INTERVAL '90 days')
                      OR
                      (b.date < NOW() AT TIME ZONE 'UTC'
                       AND b.date >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '180 days'
                       AND (mr.report_state IS NULL OR mr.report_state != 'sent'))
                  )
            ),
            -- ============================================================
            -- Source 2 : OdJ orphelins (sans calendar_event_id)
            -- ============================================================
            orphan_agenda_rows AS (
                SELECT
                    10000000 + ma.id                            AS id,
                    NULL::int                                   AS event_id,
                    ma.name                                     AS name,
                    ma.date                                     AS date,
                    ma.id                                       AS agenda_id,
                    ma.meeting_record_id                        AS record_id,
                    ma.project_id, ma.partner_id,
                    COALESCE(ma.company_id, mr.company_id)      AS company_id,
                    ma.organizer_id                             AS agenda_resp_id,
                    ma.organizer_id                             AS minutes_resp_id,
                    ''::varchar                                 AS skipped_steps,
                    ma.state                                    AS agenda_state,
                    ma.sent_date                                AS agenda_sent_date,
                    mr.report_state                             AS record_state
                FROM meeting_agenda ma
                LEFT JOIN meeting_record mr ON mr.id = ma.meeting_record_id
                WHERE ma.calendar_event_id IS NULL
                  AND ma.active = true
                  AND ma.state != 'cancelled'
                  AND ma.date IS NOT NULL
                  AND (
                      (ma.date >= NOW() AT TIME ZONE 'UTC'
                       AND ma.date <= (NOW() AT TIME ZONE 'UTC') + INTERVAL '90 days')
                      OR
                      (ma.date < NOW() AT TIME ZONE 'UTC'
                       AND ma.date >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '180 days'
                       AND (mr.report_state IS NULL OR mr.report_state != 'sent'))
                  )
            ),
            -- ============================================================
            -- Source 3 : CR orphelins (sans calendar_event_id NI agenda)
            -- ============================================================
            orphan_record_rows AS (
                SELECT
                    20000000 + mr.id                            AS id,
                    NULL::int                                   AS event_id,
                    mr.name                                     AS name,
                    mr.date                                     AS date,
                    NULL::int                                   AS agenda_id,
                    mr.id                                       AS record_id,
                    mr.project_id, mr.partner_id,
                    mr.company_id                               AS company_id,
                    mr.organizer_id                             AS agenda_resp_id,
                    mr.organizer_id                             AS minutes_resp_id,
                    ''::varchar                                 AS skipped_steps,
                    NULL::varchar                               AS agenda_state,
                    NULL::timestamp                             AS agenda_sent_date,
                    mr.report_state                             AS record_state
                FROM meeting_record mr
                WHERE mr.calendar_event_id IS NULL
                  AND mr.active = true
                  AND (mr.report_state IS NULL OR mr.report_state != 'sent')
                  AND mr.date IS NOT NULL
                  AND mr.date >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '180 days'
                  AND NOT EXISTS (
                      SELECT 1 FROM meeting_agenda a2
                      WHERE a2.meeting_record_id = mr.id
                        AND a2.calendar_event_id IS NULL
                        AND a2.active = true
                  )
            ),
            -- ============================================================
            -- UNION + projection finale
            -- ============================================================
            all_sources AS (
                SELECT * FROM event_rows
                UNION ALL
                SELECT * FROM orphan_agenda_rows
                UNION ALL
                SELECT * FROM orphan_record_rows
            )
            SELECT
                s.id, s.event_id, s.name, s.date,
                s.agenda_id, s.record_id,
                s.project_id, s.partner_id,
                -- Une rencontre porte sa propre société ; à défaut (événement
                -- calendrier nu, `calendar_event` n'ayant pas de company_id),
                -- on retombe sur celle du projet. Reste NULL si ni l'un ni
                -- l'autre : traité comme un enregistrement sans société.
                COALESCE(s.company_id, pj.company_id)           AS company_id,
                s.agenda_resp_id, s.minutes_resp_id,
                s.skipped_steps,
                (s.date < NOW() AT TIME ZONE 'UTC')             AS is_past,
                (s.date >= NOW() AT TIME ZONE 'UTC')            AS is_upcoming,
                (s.agenda_id IS NOT NULL)                       AS has_agenda_drafted,
                (s.agenda_state IN ('confirmed', 'done'))       AS has_agenda_reviewed,
                (s.agenda_sent_date IS NOT NULL)                AS has_agenda_sent,
                (
                    s.agenda_sent_date IS NOT NULL
                    AND s.date >= NOW() AT TIME ZONE 'UTC'
                    AND s.record_id IS NULL
                )                                               AS is_waiting_for_meeting,
                (s.record_id IS NOT NULL)                       AS has_minutes_drafted,
                (s.record_state IN ('reviewed', 'sent'))        AS has_minutes_reviewed,
                (s.record_state = 'sent')                       AS has_minutes_sent,
                (
                    CASE WHEN s.agenda_id IS NOT NULL THEN 1 ELSE 0 END +
                    CASE WHEN s.agenda_state IN ('confirmed', 'done') THEN 1 ELSE 0 END +
                    CASE WHEN s.agenda_sent_date IS NOT NULL THEN 1 ELSE 0 END +
                    CASE WHEN s.record_id IS NOT NULL THEN 1 ELSE 0 END +
                    CASE WHEN s.record_state IN ('reviewed', 'sent') THEN 1 ELSE 0 END +
                    CASE WHEN s.record_state = 'sent' THEN 1 ELSE 0 END
                )                                               AS completion_score
            FROM all_sources s
            LEFT JOIN project_project pj ON pj.id = s.project_id
            LEFT JOIN res_partner rp     ON rp.id = s.partner_id
            WHERE COALESCE(pj.bf_skip_dashboard, FALSE) = FALSE
              AND COALESCE(rp.bf_skip_dashboard, FALSE) = FALSE
        """)

    def action_open_event(self):
        self.ensure_one()
        if not self.event_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'calendar.event',
            'res_id': self.event_id.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }

    def action_open_agenda(self):
        self.ensure_one()
        if not self.agenda_id:
            return self.action_open_event()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'meeting.agenda',
            'res_id': self.agenda_id.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }

    def action_open_record(self):
        self.ensure_one()
        if not self.record_id:
            return self.action_open_event()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'meeting.record',
            'res_id': self.record_id.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }
