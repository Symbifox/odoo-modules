# -*- coding: utf-8 -*-
import logging
import requests
import pytz
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Default timezone for date conversions
DEFAULT_TZ = 'America/Montreal'


def datetime_to_local_date(dt, tz_name=DEFAULT_TZ):
    """Convert a UTC datetime to a local date."""
    if not dt:
        return None
    if dt.tzinfo is None:
        # Assume UTC if no timezone
        dt = pytz.UTC.localize(dt)
    local_tz = pytz.timezone(tz_name)
    local_dt = dt.astimezone(local_tz)
    return local_dt.date()

# French day and month names
JOURS_FR = {
    'Monday': 'Lundi', 'Tuesday': 'Mardi', 'Wednesday': 'Mercredi',
    'Thursday': 'Jeudi', 'Friday': 'Vendredi', 'Saturday': 'Samedi', 'Sunday': 'Dimanche'
}
MOIS_FR = {
    'January': 'janvier', 'February': 'février', 'March': 'mars', 'April': 'avril',
    'May': 'mai', 'June': 'juin', 'July': 'juillet', 'August': 'août',
    'September': 'septembre', 'October': 'octobre', 'November': 'novembre', 'December': 'décembre'
}


def _coerce_translatable(val):
    """Defensive: if a translatable jsonb field leaked as a raw dict
    ({'en_US': ..., 'fr_CA': ...}), resolve it to a single string instead of
    rendering the dict verbatim. The upstream SQL fix in
    bf_meeting._get_digest_buckets is the real fix; this is belt-and-braces."""
    if isinstance(val, dict):
        for key in ("fr_CA", "en_CA", "en_US"):
            if val.get(key):
                return val[key]
        return next((v for v in val.values() if v), "")
    return val


def format_date_fr(date_obj):
    """Format a date in French: 'Jeudi, le 5 février 2026'"""
    day_en = date_obj.strftime('%A')
    month_en = date_obj.strftime('%B')
    day_num = date_obj.day
    year = date_obj.year
    jour = JOURS_FR.get(day_en, day_en)
    mois = MOIS_FR.get(month_en, month_en)
    return f"{jour}, le {day_num} {mois} {year}"

# Default palette. `bg_outer` / `header` / `accent` are overwritten at the
# top of `_generate_html` with the user company's brand colors
# (`res.company.report_brand_dark` / `report_brand_primary`). Status hues
# (`red`/`orange`/`green`) and neutrals stay fixed.
COLORS = {
    "bg_outer": "#212529",
    "header": "#212529",
    "accent": "#714B67",
    "white": "#FFFFFF",
    "text_light": "#E6EDF3",
    "text_gray": "#6B7280",
    "text_dark": "#374151",
    "border": "#e5e7eb",
    "red": "#dc3545",
    "orange": "#ffc107",
    "green": "#198754",
}


class DailyDigestSendLog(models.Model):
    """Per-(config, user) send tracking.

    The digest is sent in each recipient's own timezone (recipients in
    different timezones fire at different UTC instants for the same
    `send_hour`). The legacy config-level `last_sent` can't express that, so
    we track the last send per recipient here to avoid double-sends.
    """

    _name = "daily.digest.send.log"
    _description = "Daily Digest Send Log"

    config_id = fields.Many2one(
        "daily.digest.config", required=True, ondelete="cascade", index=True
    )
    user_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade", index=True
    )
    last_sent = fields.Datetime(string="Dernier envoi (UTC)")

    _sql_constraints = [
        (
            "uniq_config_user",
            "unique(config_id, user_id)",
            "Un seul journal d'envoi par destinataire et par digest.",
        ),
    ]


class DailyDigestConfig(models.Model):
    _name = "daily.digest.config"
    _description = "Daily Digest Configuration"

    name = fields.Char(string="Nom", required=True, default="Mon digest quotidien")
    active = fields.Boolean(default=True)

    # Recipients
    user_ids = fields.Many2many(
        "res.users",
        string="Destinataires",
        help="Utilisateurs qui recevront le digest quotidien",
    )

    # Schedule
    send_hour = fields.Integer(
        string="Heure d'envoi",
        default=4,
        help="Heure d'envoi (0-23), évaluée dans le fuseau horaire de chaque "
             "destinataire (res.users.tz, défaut America/Montreal).",
    )

    # Widget toggles
    include_overdue_activities = fields.Boolean(
        string="Activités en retard",
        default=True,
    )
    include_today_activities = fields.Boolean(
        string="Activités du jour",
        default=True,
    )
    include_overdue_tasks = fields.Boolean(
        string="Tâches en retard",
        default=True,
    )
    include_today_tasks = fields.Boolean(
        string="Tâches du jour",
        default=True,
    )
    include_upcoming_tasks = fields.Boolean(
        string="Tâches à venir",
        default=False,
        help="Inclure les tâches des prochains jours",
    )
    upcoming_days = fields.Integer(
        string="Jours à venir",
        default=3,
        help="Nombre de jours à afficher en aperçu (1-7)",
    )
    include_weather = fields.Boolean(
        string="Météo",
        default=True,
    )
    weather_city = fields.Char(
        string="Ville météo (défaut)",
        default="Montréal",
        help="Ville par défaut. Chaque destinataire peut la remplacer dans "
             "ses préférences (Ville météo (digest) sur sa fiche utilisateur).",
    )
    weather_latitude = fields.Float(
        string="Latitude",
        default=45.5017,
        digits=(10, 4),
    )
    weather_longitude = fields.Float(
        string="Longitude",
        default=-73.5673,
        digits=(10, 4),
    )
    include_quote = fields.Boolean(
        string="Citation inspirante",
        default=True,
    )
    include_meetings = fields.Boolean(
        string="Rencontres (OdJ + CR)",
        default=True,
        help="Inclure les rencontres à venir avec OdJ à préparer et les comptes rendus "
             "des rencontres passées encore à compléter (cycle bf_meeting).",
    )

    # Company filter
    company_id = fields.Many2one(
        "res.company",
        string="Compagnie",
        default=lambda self: self.env.company,
        help="Filtrer les tâches par cette compagnie uniquement",
    )

    # Tracking
    last_sent = fields.Datetime(string="Dernier envoi", readonly=True)

    def action_send_now(self):
        """Manually send the digest now."""
        self.ensure_one()
        self._send_digest()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Digest envoyé"),
                "message": _("Le digest a été envoyé aux destinataires."),
                "type": "success",
            },
        }

    def action_send_test(self):
        """Send a test digest to the current user only."""
        self.ensure_one()
        current_user = self.env.user
        if not current_user.email:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Erreur"),
                    "message": _("Votre utilisateur n'a pas d'adresse courriel configurée."),
                    "type": "danger",
                },
            }
        self._send_digest(test_user=current_user)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Test envoyé"),
                "message": _("Un digest test a été envoyé à %s.") % current_user.email,
                "type": "success",
            },
        }

    @api.model
    def _cron_send_daily_digests(self):
        """Cron job to send daily digests.

        Evaluates `send_hour` and the "already sent today" guard in EACH
        recipient's own timezone (`res.users.tz`), so the same config delivers
        at the local morning hour for recipients in different timezones.
        """
        now_utc = fields.Datetime.now()
        configs = self.search([("active", "=", True)])

        for config in configs:
            for user in config.user_ids:
                if not user.email:
                    continue

                tz_name = user.tz or DEFAULT_TZ
                user_tz = pytz.timezone(tz_name)
                now_local = pytz.UTC.localize(now_utc).astimezone(user_tz)

                # Right hour in the recipient's timezone?
                if config.send_hour != now_local.hour:
                    continue

                # Already sent today (recipient's local day)?
                log = config._get_send_log(user)
                if log.last_sent:
                    last_local = pytz.UTC.localize(log.last_sent).astimezone(user_tz)
                    if last_local.date() >= now_local.date():
                        continue

                try:
                    config._send_digest(test_user=user)
                    log.last_sent = now_utc  # store in UTC
                    config.last_sent = now_utc  # legacy: last send of any recipient
                    _logger.info(
                        "Daily digest '%s' sent to %s (%s)",
                        config.name, user.name, tz_name,
                    )
                except Exception:
                    _logger.exception(
                        "Failed to send daily digest '%s' to %s",
                        config.name, user.name,
                    )

    def _get_send_log(self, user):
        """Return (creating if needed) the per-user send log for this config."""
        self.ensure_one()
        log = self.env["daily.digest.send.log"].search(
            [("config_id", "=", self.id), ("user_id", "=", user.id)], limit=1
        )
        if not log:
            log = self.env["daily.digest.send.log"].create({
                "config_id": self.id,
                "user_id": user.id,
            })
        return log

    def _send_digest(self, test_user=None):
        """Send the digest email to all recipients.

        Args:
            test_user: If provided, send only to this user (for testing)
        """
        self.ensure_one()

        recipients = test_user if test_user else self.user_ids
        if not recipients:
            _logger.warning("No recipients configured for digest '%s'", self.name)
            return

        for user in recipients:
            if not user.email:
                _logger.warning("User %s has no email, skipping", user.name)
                continue

            # Gather data per recipient, in the recipient's own timezone, so the
            # "today" window (and week preview) reflect their local day.
            tz_name = user.tz or DEFAULT_TZ
            data = self._gather_digest_data(user, tz_name=tz_name)

            has_content = any([
                data.get("overdue_activities"),
                data.get("today_activities"),
                data.get("overdue_tasks"),
                data.get("today_tasks"),
                data.get("meetings_by_user"),
            ])
            if not has_content and not self.include_weather and not self.include_quote:
                _logger.info(
                    "Nothing to report for %s on digest '%s'", user.name, self.name
                )
                continue

            # Generate personalized HTML
            body_html = self._generate_html(data, user)

            # Subject date in the recipient's local day
            today_local = pytz.UTC.localize(fields.Datetime.now()).astimezone(
                pytz.timezone(tz_name)
            ).date()
            date_str = format_date_fr(today_local)

            mail_values = {
                "subject": f"🌄 Votre journée | {date_str}",
                "email_from": self.env.company.email or self.env.user.email_formatted,
                "email_to": user.email,
                "body_html": body_html,
                "auto_delete": True,
            }
            mail = self.env["mail.mail"].sudo().create(mail_values)
            mail.send()

    def _gather_digest_data(self, users=None, tz_name=None):
        """Gather all data for the digest.

        Args:
            users: Specific users to gather data for (defaults to self.user_ids)
            tz_name: Timezone used to compute the local "today" window
                (defaults to America/Montreal). Pass the recipient's tz so the
                day boundaries match their locale.
        """
        self.ensure_one()
        tz_name = tz_name or DEFAULT_TZ
        # "Today" in the recipient's local day, not the server's.
        local_tz = pytz.timezone(tz_name)
        today = pytz.UTC.localize(fields.Datetime.now()).astimezone(local_tz).date()
        data = {}

        # Calculate datetime bounds for today in the recipient's timezone.
        # This ensures proper comparison for Datetime fields stored in UTC.
        today_start_local = local_tz.localize(
            fields.Datetime.to_datetime(f"{today} 00:00:00")
        )
        today_end_local = local_tz.localize(
            fields.Datetime.to_datetime(f"{today} 23:59:59")
        )
        # Convert to UTC for database comparison
        today_start_utc = today_start_local.astimezone(pytz.UTC).replace(tzinfo=None)
        today_end_utc = today_end_local.astimezone(pytz.UTC).replace(tzinfo=None)

        # Get activities for all recipients (mail.activity.date_deadline is a Date field)
        target_users = users if users else self.user_ids
        user_ids = target_users.ids

        if self.include_overdue_activities:
            data["overdue_activities"] = self._get_activities(
                user_ids, [("date_deadline", "<", today)]
            )

        if self.include_today_activities:
            data["today_activities"] = self._get_activities(
                user_ids, [("date_deadline", "=", today)]
            )

        # For tasks, date_deadline is a Datetime field - use UTC bounds
        if self.include_overdue_tasks:
            data["overdue_tasks"] = self._get_tasks(
                user_ids, [("date_deadline", "<", today_start_utc), ("state", "not in", ["1_done", "1_canceled"])]
            )

        if self.include_today_tasks:
            data["today_tasks"] = self._get_tasks(
                user_ids, [
                    ("date_deadline", ">=", today_start_utc),
                    ("date_deadline", "<=", today_end_utc),
                    ("state", "not in", ["1_done", "1_canceled"])
                ]
            )

        # Upcoming tasks (next N days, excluding today)
        if self.include_upcoming_tasks:
            days_ahead = min(max(self.upcoming_days or 3, 1), 7)  # Clamp to 1-7
            tomorrow = today + timedelta(days=1)
            end_date = today + timedelta(days=days_ahead)

            tomorrow_start_local = local_tz.localize(
                fields.Datetime.to_datetime(f"{tomorrow} 00:00:00")
            )
            end_date_local = local_tz.localize(
                fields.Datetime.to_datetime(f"{end_date} 23:59:59")
            )
            tomorrow_start_utc = tomorrow_start_local.astimezone(pytz.UTC).replace(tzinfo=None)
            end_date_utc = end_date_local.astimezone(pytz.UTC).replace(tzinfo=None)

            data["upcoming_tasks"] = self._get_tasks(
                user_ids, [
                    ("date_deadline", ">=", tomorrow_start_utc),
                    ("date_deadline", "<=", end_date_utc),
                    ("state", "not in", ["1_done", "1_canceled"])
                ]
            )
            data["upcoming_days"] = days_ahead

        # Week preview: count tasks and activities per day for next 7 days
        week_preview = []
        for day_offset in range(1, 8):  # Days 1-7 from today
            target_date = today + timedelta(days=day_offset)

            # Count activities for this date (Date field)
            activity_count = self.env["mail.activity"].sudo().search_count([
                ("user_id", "in", user_ids),
                ("date_deadline", "=", target_date),
            ])

            # Count tasks for this date (Datetime field - need UTC bounds)
            day_start_local = local_tz.localize(
                fields.Datetime.to_datetime(f"{target_date} 00:00:00")
            )
            day_end_local = local_tz.localize(
                fields.Datetime.to_datetime(f"{target_date} 23:59:59")
            )
            day_start_utc = day_start_local.astimezone(pytz.UTC).replace(tzinfo=None)
            day_end_utc = day_end_local.astimezone(pytz.UTC).replace(tzinfo=None)

            task_count = self.env["project.task"].sudo().search_count([
                ("user_ids", "in", user_ids),
                ("date_deadline", ">=", day_start_utc),
                ("date_deadline", "<=", day_end_utc),
                ("state", "not in", ["1_done", "1_canceled"]),
            ])

            week_preview.append({
                "date": target_date,
                "day_name": JOURS_FR.get(target_date.strftime('%A'), target_date.strftime('%A'))[:3],
                "day_num": target_date.day,
                "tasks": task_count,
                "activities": activity_count,
                "total": task_count + activity_count,
            })

        data["week_preview"] = week_preview

        # Weather is fetched per-recipient in `_generate_html` (each user can
        # have their own city/coordinates), so it is intentionally NOT gathered
        # here.

        if self.include_quote:
            data["quote"] = self.env["daily.digest.quote"].get_random_quote()

        if self.include_meetings:
            data["meetings_by_user"] = self._get_meetings_buckets(user_ids)

        return data

    def _get_meetings_buckets(self, user_ids):
        """Delegate to meeting.dashboard so the bucket logic stays in one place."""
        if not user_ids:
            return {}
        Dashboard = self.env.get("meeting.dashboard")
        if Dashboard is None:
            return {}
        return Dashboard.sudo()._get_digest_buckets(user_ids=user_ids)

    def _get_activities(self, user_ids, domain_extra):
        """Get activities matching criteria for specified users."""
        Activity = self.env["mail.activity"].sudo()
        domain = [("user_id", "in", user_ids)] + domain_extra
        activities = Activity.search(domain, order="date_deadline, id")

        result = []
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")

        for act in activities:
            # Build link to the related record
            model_name = act.res_model
            record_id = act.res_id
            link = f"{base_url}/web#id={record_id}&model={model_name}&view_type=form"

            # Get record name
            try:
                record = self.env[model_name].sudo().browse(record_id)
                record_name = record.display_name or f"{model_name} #{record_id}"
            except Exception:
                record_name = f"{model_name} #{record_id}"

            result.append({
                "id": act.id,
                "summary": act.summary or act.activity_type_id.name or "Activité",
                "record_name": record_name,
                "model": model_name,
                "deadline": act.date_deadline,
                "user": act.user_id.name,
                "link": link,
                "note": act.note or "",
            })

        return result

    def _get_tasks(self, user_ids, domain_extra):
        """Get project tasks matching criteria for specified users.

        Returns a dict with:
        - 'visible': list of tasks with display_in_project=True
        - 'hidden_count': count of tasks with display_in_project=False
        """
        Task = self.env["project.task"].sudo()
        domain = [("user_ids", "in", user_ids)] + domain_extra

        # Note: No company filter - show all tasks assigned to user regardless of company

        tasks = Task.search(domain, order="date_deadline, id")

        visible_tasks = []
        hidden_count = 0
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")

        for task in tasks:
            # Check if task is hidden in pipeline (display_in_project=False)
            # This is the "eye" toggle in Odoo's task form
            if not task.display_in_project:
                hidden_count += 1
                continue

            link = f"{base_url}/web#id={task.id}&model=project.task&view_type=form"

            visible_tasks.append({
                "id": task.id,
                "name": task.name,
                "project": task.project_id.name if task.project_id else "Sans projet",
                "deadline": datetime_to_local_date(task.date_deadline),
                "user": ", ".join(task.user_ids.mapped("name")),
                "link": link,
                "parent_task": task.parent_id.name if task.parent_id else None,
                "is_subtask": bool(task.parent_id),
                "priority": task.priority,
            })

        return {"visible": visible_tasks, "hidden_count": hidden_count}

    def _resolve_weather_location(self, user=None):
        """Resolve (city, latitude, longitude, tz_name) for the weather widget.

        Priority: the recipient's own preferences — active as soon as they set
        a `digest_weather_city` on their user record — then the config's
        default city/coordinates. The tz is the recipient's (so Open-Meteo
        aligns the daily high/low to their local day).
        """
        if user and user.digest_weather_city:
            return (
                user.digest_weather_city,
                user.digest_weather_latitude,
                user.digest_weather_longitude,
                user.tz or DEFAULT_TZ,
            )
        return (
            self.weather_city,
            self.weather_latitude,
            self.weather_longitude,
            (user.tz if user else None) or DEFAULT_TZ,
        )

    def _get_weather(self, user=None):
        """Get weather data from Open-Meteo API (free, no API key needed).

        Resolves the location per-recipient (see `_resolve_weather_location`).
        """
        city, latitude, longitude, tz_name = self._resolve_weather_location(user)
        try:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": latitude,
                "longitude": longitude,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weathercode",
                "current": "temperature_2m,weathercode",
                "timezone": tz_name,
                "forecast_days": 1,
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            # Weather code descriptions (WMO codes) with emojis
            weather_codes = {
                0: ("☀️", "Ciel dégagé"),
                1: ("🌤️", "Principalement dégagé"),
                2: ("⛅", "Partiellement nuageux"),
                3: ("☁️", "Couvert"),
                45: ("🌫️", "Brouillard"),
                48: ("🌫️", "Brouillard givrant"),
                51: ("🌧️", "Bruine légère"),
                53: ("🌧️", "Bruine modérée"),
                55: ("🌧️", "Bruine dense"),
                56: ("🌧️", "Bruine verglaçante légère"),
                57: ("🌧️", "Bruine verglaçante dense"),
                61: ("🌧️", "Pluie légère"),
                63: ("🌧️", "Pluie modérée"),
                65: ("🌧️", "Pluie forte"),
                66: ("🧊", "Pluie verglaçante légère"),
                67: ("🧊", "Pluie verglaçante forte"),
                71: ("🌨️", "Neige légère"),
                73: ("🌨️", "Neige modérée"),
                75: ("❄️", "Neige forte"),
                77: ("🌨️", "Grains de neige"),
                80: ("🌦️", "Averses légères"),
                81: ("🌦️", "Averses modérées"),
                82: ("⛈️", "Averses violentes"),
                85: ("🌨️", "Averses de neige légères"),
                86: ("❄️", "Averses de neige fortes"),
                95: ("⛈️", "Orage"),
                96: ("⛈️", "Orage avec grêle légère"),
                99: ("⛈️", "Orage avec grêle forte"),
            }

            current = data.get("current", {})
            daily = data.get("daily", {})

            weather_code = daily.get("weathercode", [0])[0]
            weather_info = weather_codes.get(weather_code, ("🌡️", "Inconnu"))

            return {
                "city": city,
                "current_temp": round(current.get("temperature_2m", 0)),
                "high": round(daily.get("temperature_2m_max", [0])[0]),
                "low": round(daily.get("temperature_2m_min", [0])[0]),
                "precipitation": round(daily.get("precipitation_sum", [0])[0], 1),
                "precipitation_prob": daily.get("precipitation_probability_max", [0])[0],
                "emoji": weather_info[0],
                "description": weather_info[1],
            }
        except Exception as e:
            _logger.warning("Failed to fetch weather: %s", e)
            return None

    def _generate_html(self, data, user):
        """Generate the HTML email body."""
        self.ensure_one()
        tz_name = user.tz or DEFAULT_TZ
        today = pytz.UTC.localize(fields.Datetime.now()).astimezone(
            pytz.timezone(tz_name)
        ).date()
        today_str = format_date_fr(today)

        # Weather is per-recipient (own city/coordinates + own timezone).
        weather = self._get_weather(user) if self.include_weather else None

        # Pull brand colors from the user's company. Single-threaded write to
        # the module-level palette is safe inside the cron-driven send loop.
        co = user.company_id
        COLORS["bg_outer"] = co.report_brand_dark or "#212529"
        COLORS["header"] = co.report_brand_dark or "#212529"
        COLORS["accent"] = co.report_brand_primary or "#714B67"

        # Filter data for this specific user
        user_data = self._filter_data_for_user(data, user)

        content_parts = []

        # Greeting
        content_parts.append(f"""
            <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:16px;color:{COLORS['text_dark']};margin:0 0 16px 0;">
                Bonjour <strong>{user.name.split()[0] if user.name else 'vous'}</strong>,
            </p>
            <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:15px;color:{COLORS['text_gray']};margin:0 0 24px 0;">
                Voici votre agenda pour le {today_str}.
            </p>
        """)

        # Weather section
        if weather:
            content_parts.append(self._render_weather_section(weather))

        # Overdue activities
        if self.include_overdue_activities and user_data.get("overdue_activities"):
            content_parts.append(self._render_activity_section(
                "Activités en retard",
                user_data["overdue_activities"],
                COLORS["red"],
                is_overdue=True,
            ))

        # Today's activities
        if self.include_today_activities and user_data.get("today_activities"):
            content_parts.append(self._render_activity_section(
                "Activités du jour",
                user_data["today_activities"],
                COLORS["accent"],
                is_overdue=False,
            ))

        # Overdue tasks
        if self.include_overdue_tasks and user_data.get("overdue_tasks"):
            content_parts.append(self._render_task_section(
                "Tâches en retard",
                user_data["overdue_tasks"],
                COLORS["red"],
                is_overdue=True,
            ))

        # Today's tasks
        if self.include_today_tasks and user_data.get("today_tasks"):
            content_parts.append(self._render_task_section(
                "Tâches du jour",
                user_data["today_tasks"],
                COLORS["accent"],
                is_overdue=False,
            ))

        # Hidden tasks summary (tasks in folded stages)
        hidden_overdue = user_data.get("overdue_tasks_hidden", 0)
        hidden_today = user_data.get("today_tasks_hidden", 0)
        total_hidden = hidden_overdue + hidden_today
        if total_hidden > 0:
            content_parts.append(self._render_hidden_tasks_summary(hidden_overdue, hidden_today))

        # Meetings: OdJ to prepare (≤7d) + CR to complete (past pending)
        if self.include_meetings and (user_data.get("meetings_odj") or user_data.get("meetings_cr")):
            content_parts.append(self._render_meetings_section(
                user_data.get("meetings_odj") or [],
                user_data.get("meetings_cr") or [],
                user,
            ))

        # Week preview (next 7 days summary)
        if data.get("week_preview"):
            content_parts.append(self._render_week_preview(data["week_preview"]))

        # No items message
        if not any([
            user_data.get("overdue_activities"),
            user_data.get("today_activities"),
            user_data.get("overdue_tasks"),
            user_data.get("today_tasks"),
            user_data.get("meetings_odj"),
            user_data.get("meetings_cr"),
        ]) and total_hidden == 0:
            content_parts.append(f"""
                <div style="background-color:#d1e7dd;border:1px solid #a3cfbb;border-radius:8px;padding:16px;margin:16px 0;">
                    <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;color:{COLORS['green']};margin:0;">
                        <strong>Aucune tâche ni activité en retard ou prévue aujourd'hui.</strong><br/>
                        Bonne journée!
                    </p>
                </div>
            """)

        # Inspirational quote
        if self.include_quote and data.get("quote"):
            content_parts.append(self._render_quote_section(data["quote"]))

        content = "".join(content_parts)

        # Build preheader summary
        preheader_parts = []
        overdue_count = len(user_data.get("overdue_activities", [])) + len(user_data.get("overdue_tasks", []))
        today_count = len(user_data.get("today_activities", [])) + len(user_data.get("today_tasks", []))
        meetings_count = len(user_data.get("meetings_odj", [])) + len(user_data.get("meetings_cr", []))
        if overdue_count > 0:
            preheader_parts.append(f"{overdue_count} en retard")
        if today_count > 0:
            preheader_parts.append(f"{today_count} aujourd'hui")
        if meetings_count > 0:
            preheader_parts.append(f"{meetings_count} rencontre{'s' if meetings_count > 1 else ''}")
        if weather:
            w = weather
            preheader_parts.append(f"{w.get('emoji', '')} {w['current_temp']}°C")
        preheader = " | ".join(preheader_parts) if preheader_parts else "Votre agenda du jour"

        return self._wrap_email("Votre journée", content, preheader)

    def _filter_data_for_user(self, data, user):
        """Filter activities and tasks for a specific user.

        Uses data already gathered (user_id stored in each record dict)
        to avoid additional database queries.
        """
        user_data = {}
        user_name = user.name

        # Activities already have 'user' field with the user name
        if data.get("overdue_activities"):
            user_data["overdue_activities"] = [
                a for a in data["overdue_activities"]
                if a.get("user") == user_name
            ]

        if data.get("today_activities"):
            user_data["today_activities"] = [
                a for a in data["today_activities"]
                if a.get("user") == user_name
            ]

        # Tasks have 'user' field with comma-separated names
        if data.get("overdue_tasks"):
            task_data = data["overdue_tasks"]
            visible_tasks = task_data.get("visible", [])
            user_data["overdue_tasks"] = [
                t for t in visible_tasks
                if user_name in t.get("user", "")
            ]
            user_data["overdue_tasks_hidden"] = task_data.get("hidden_count", 0)

        if data.get("today_tasks"):
            task_data = data["today_tasks"]
            visible_tasks = task_data.get("visible", [])
            user_data["today_tasks"] = [
                t for t in visible_tasks
                if user_name in t.get("user", "")
            ]
            user_data["today_tasks_hidden"] = task_data.get("hidden_count", 0)

        # Upcoming tasks
        if data.get("upcoming_tasks"):
            task_data = data["upcoming_tasks"]
            visible_tasks = task_data.get("visible", [])
            user_data["upcoming_tasks"] = [
                t for t in visible_tasks
                if user_name in t.get("user", "")
            ]

        # Meetings: keyed by uid, slice this user's bucket out
        meetings = data.get("meetings_by_user") or {}
        bucket = meetings.get(user.id) or {"odj": [], "cr": []}
        user_data["meetings_odj"] = bucket.get("odj", [])
        user_data["meetings_cr"] = bucket.get("cr", [])

        return user_data

    def _render_weather_section(self, weather):
        """Render the weather section."""
        precip_text = ""
        if weather["precipitation"] > 0 or weather["precipitation_prob"] > 30:
            precip_text = f" | Précipitations: {weather['precipitation']} mm ({weather['precipitation_prob']}%)"

        emoji = weather.get("emoji", "🌡️")

        return f"""
            <div style="background-color:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:16px;margin:0 0 24px 0;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                        <td style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;color:{COLORS['header']};">
                            <strong style="font-size:16px;">{weather['city']}</strong><br/>
                            <span style="font-size:32px;">{emoji}</span>
                            <span style="font-size:28px;font-weight:600;margin-left:8px;">{weather['current_temp']}°C</span>
                            <span style="color:{COLORS['text_gray']};font-size:14px;margin-left:8px;">
                                {weather['description']}
                            </span>
                        </td>
                        <td align="right" style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:{COLORS['text_gray']};">
                            Max: <strong>{weather['high']}°C</strong> | Min: <strong>{weather['low']}°C</strong>{precip_text}
                        </td>
                    </tr>
                </table>
            </div>
        """

    def _render_activity_section(self, title, activities, color, is_overdue=False):
        """Render an activities section."""
        badge_bg = "#f8d7da" if is_overdue else "#e8f6fd"
        count = len(activities)

        rows = ""
        for act in activities:
            deadline_str = act["deadline"].strftime("%d/%m") if act["deadline"] else "—"
            rows += f"""
                <tr>
                    <td style="padding:12px;border-bottom:1px solid {COLORS['border']};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;">
                        <a href="{act['link']}" style="color:{COLORS['accent']};text-decoration:none;font-weight:500;">
                            {act['summary']}
                        </a>
                        <br/>
                        <span style="font-size:12px;color:{COLORS['text_gray']};">
                            {act['record_name']}
                        </span>
                    </td>
                    <td style="padding:12px;border-bottom:1px solid {COLORS['border']};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:{COLORS['text_gray']};white-space:nowrap;">
                        {deadline_str}
                    </td>
                </tr>
            """

        return f"""
            <div style="margin:0 0 24px 0;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;">
                    <tr>
                        <td style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:16px;font-weight:600;color:{COLORS['header']};">
                            {title}
                        </td>
                        <td align="right">
                            <span style="display:inline-block;background-color:{badge_bg};color:{color};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;padding:4px 10px;border-radius:12px;">
                                {count}
                            </span>
                        </td>
                    </tr>
                </table>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid {COLORS['border']};border-radius:8px;overflow:hidden;">
                    <thead>
                        <tr style="background-color:{color};">
                            <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;">
                                Activité
                            </th>
                            <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;width:80px;">
                                Échéance
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
        """

    def _render_task_section(self, title, tasks, color, is_overdue=False, show_full_date=False):
        """Render a tasks section."""
        badge_bg = "#f8d7da" if is_overdue else "#e8f6fd"
        count = len(tasks)

        rows = ""
        for task in tasks:
            if task["deadline"]:
                if show_full_date:
                    # Show day name + date for upcoming tasks
                    day_en = task["deadline"].strftime('%A')
                    jour = JOURS_FR.get(day_en, day_en)[:3]  # Abbreviated day
                    deadline_str = f"{jour} {task['deadline'].strftime('%d/%m')}"
                else:
                    deadline_str = task["deadline"].strftime("%d/%m")
            else:
                deadline_str = "—"
            subtask_indicator = '<span style="color:#6B7280;font-size:11px;"> (sous-tâche)</span>' if task["is_subtask"] else ""
            priority_icon = '<span style="color:#dc3545;">*</span> ' if task["priority"] == "1" else ""

            rows += f"""
                <tr>
                    <td style="padding:12px;border-bottom:1px solid {COLORS['border']};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;">
                        {priority_icon}<a href="{task['link']}" style="color:{COLORS['accent']};text-decoration:none;font-weight:500;">
                            {task['name']}
                        </a>{subtask_indicator}
                        <br/>
                        <span style="font-size:12px;color:{COLORS['text_gray']};">
                            {task['project']}
                        </span>
                    </td>
                    <td style="padding:12px;border-bottom:1px solid {COLORS['border']};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:{COLORS['text_gray']};white-space:nowrap;">
                        {deadline_str}
                    </td>
                </tr>
            """

        return f"""
            <div style="margin:0 0 24px 0;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;">
                    <tr>
                        <td style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:16px;font-weight:600;color:{COLORS['header']};">
                            {title}
                        </td>
                        <td align="right">
                            <span style="display:inline-block;background-color:{badge_bg};color:{color};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;padding:4px 10px;border-radius:12px;">
                                {count}
                            </span>
                        </td>
                    </tr>
                </table>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid {COLORS['border']};border-radius:8px;overflow:hidden;">
                    <thead>
                        <tr style="background-color:{color};">
                            <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;">
                                Tâche
                            </th>
                            <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;width:80px;">
                                Échéance
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
        """

    def _render_hidden_tasks_summary(self, hidden_overdue, hidden_today):
        """Render a summary of tasks hidden from pipeline (display_in_project=False)."""
        from urllib.parse import quote

        total = hidden_overdue + hidden_today
        parts = []
        if hidden_overdue > 0:
            parts.append(f"{hidden_overdue} en retard")
        if hidden_today > 0:
            parts.append(f"{hidden_today} aujourd'hui")
        detail = " et ".join(parts)

        # Build link to view hidden tasks in Odoo
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        # Get the action ID for project tasks
        action = self.env.ref('project.action_view_all_task', raise_if_not_found=False)
        action_id = action.id if action else ''
        # Domain for hidden tasks with deadlines
        domain = "[('display_in_project','=',False),('date_deadline','!=',False)]"
        hidden_tasks_url = f"{base_url}/web#action={action_id}&model=project.task&view_type=list&domain={quote(domain)}"

        return f"""
            <div style="margin:0 0 24px 0;padding:12px 16px;background-color:#f3f4f6;border:1px solid {COLORS['border']};border-radius:8px;border-left:4px solid {COLORS['text_gray']};">
                <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:{COLORS['text_gray']};margin:0;">
                    <a href="{hidden_tasks_url}" style="text-decoration:none;color:{COLORS['header']};">
                        <span style="opacity:0.6;margin-right:6px;">👁</span>
                        <strong>{total} tâche(s) non visible(s)</strong>
                    </a>
                    <span style="font-size:12px;"> ({detail})</span><br/>
                    <span style="font-size:12px;font-style:italic;">Sous-tâches avec "Afficher dans le projet" désactivé.</span>
                </p>
            </div>
        """

    def _render_week_preview(self, week_data):
        """Render a compact 7-day preview showing task/activity counts per day."""
        from urllib.parse import quote

        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        action = self.env.ref('project.action_view_all_task', raise_if_not_found=False)
        action_id = action.id if action else ''

        # Build day cells
        day_cells = ""
        for day in week_data:
            total = day["total"]
            date_str = day["date"].strftime("%Y-%m-%d")

            # Color based on count
            if total == 0:
                bg_color = "#f3f4f6"
                text_color = COLORS["text_gray"]
                count_display = "—"
            elif total <= 3:
                bg_color = "#d1fae5"  # Light green
                text_color = COLORS["green"]
                count_display = str(total)
            elif total <= 6:
                bg_color = "#fef3c7"  # Light yellow
                text_color = "#d97706"  # Amber
                count_display = str(total)
            else:
                bg_color = "#fee2e2"  # Light red
                text_color = COLORS["red"]
                count_display = str(total)

            # Build clickable link to tasks for this date
            # Using date_deadline domain filter
            domain = f"[('date_deadline','>=','{date_str} 00:00:00'),('date_deadline','<=','{date_str} 23:59:59')]"
            day_url = f"{base_url}/web#action={action_id}&model=project.task&view_type=list&domain={quote(domain)}"

            # Make the cell clickable if there are items
            if total > 0:
                count_html = f"""
                    <a href="{day_url}" style="text-decoration:none;">
                        <div style="display:inline-block;background-color:{bg_color};color:{text_color};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;font-weight:600;padding:6px 10px;border-radius:8px;min-width:24px;">
                            {count_display}
                        </div>
                    </a>
                """
            else:
                count_html = f"""
                    <div style="display:inline-block;background-color:{bg_color};color:{text_color};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;font-weight:600;padding:6px 10px;border-radius:8px;min-width:24px;">
                        {count_display}
                    </div>
                """

            day_cells += f"""
                <td align="center" style="padding:8px 4px;width:14.28%;">
                    <div style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:11px;color:{COLORS['text_gray']};margin-bottom:4px;">
                        {day['day_name']}
                    </div>
                    <div style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;color:{COLORS['text_gray']};margin-bottom:4px;">
                        {day['day_num']}
                    </div>
                    {count_html}
                </td>
            """

        return f"""
            <div style="margin:24px 0;padding:16px;background-color:#f9fafb;border:1px solid {COLORS['border']};border-radius:8px;">
                <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;font-weight:600;color:{COLORS['header']};margin:0 0 12px 0;">
                    📅 Aperçu des 7 prochains jours
                </p>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                        {day_cells}
                    </tr>
                </table>
                <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:11px;color:{COLORS['text_gray']};margin:12px 0 0 0;text-align:center;">
                    Tâches + activités par jour (cliquez pour voir)
                </p>
            </div>
        """

    def _render_meetings_section(self, odj_rows, cr_rows, user):
        """Render the meetings block (OdJ to prepare + CR to complete).

        Mirrors the layout of `_render_task_section` so the integrated block
        feels native inside Votre journée. OdJ list uses `accent`, CR list
        uses `red` because past+pending is always overdue.
        """
        from markupsafe import escape

        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
        tz_name = user.tz or DEFAULT_TZ

        def _row(r):
            dt = r["date"]
            if dt:
                if dt.tzinfo is None:
                    dt = pytz.UTC.localize(dt)
                local_dt = dt.astimezone(pytz.timezone(tz_name))
                date_str = local_dt.strftime("%a %d/%m, %H:%M")
            else:
                date_str = "—"
            # Link priority : record > agenda > calendar event
            if r.get("record_id"):
                url = f"{base_url}/odoo/meeting.record/{r['record_id']}"
            elif r.get("agenda_id"):
                url = f"{base_url}/odoo/meeting.agenda/{r['agenda_id']}"
            elif r.get("event_id"):
                url = f"{base_url}/odoo/calendar/{r['event_id']}"
            else:
                url = "#"
            meta_bits = []
            if r.get("project_name"):
                meta_bits.append(escape(_coerce_translatable(r["project_name"])))
            if r.get("partner_name"):
                meta_bits.append(escape(_coerce_translatable(r["partner_name"])))
            meta = " · ".join(str(b) for b in meta_bits)
            return f"""
                <tr>
                    <td style="padding:12px;border-bottom:1px solid {COLORS['border']};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;">
                        <a href="{url}" style="color:{COLORS['accent']};text-decoration:none;font-weight:500;">{escape(r['name'] or '(sans nom)')}</a>
                        <br/>
                        <span style="font-size:12px;color:{COLORS['text_gray']};">{meta}</span>
                    </td>
                    <td style="padding:12px;border-bottom:1px solid {COLORS['border']};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:{COLORS['text_gray']};white-space:nowrap;">
                        {date_str}
                    </td>
                </tr>
            """

        def _block(title, rows, color, badge_bg):
            if not rows:
                return ""
            rows_html = "".join(_row(r) for r in rows)
            return f"""
                <div style="margin:0 0 16px 0;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;">
                        <tr>
                            <td style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:16px;font-weight:600;color:{COLORS['header']};">
                                {title}
                            </td>
                            <td align="right">
                                <span style="display:inline-block;background-color:{badge_bg};color:{color};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;padding:4px 10px;border-radius:12px;">
                                    {len(rows)}
                                </span>
                            </td>
                        </tr>
                    </table>
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid {COLORS['border']};border-radius:8px;overflow:hidden;">
                        <thead>
                            <tr style="background-color:{color};">
                                <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;">
                                    Rencontre
                                </th>
                                <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;width:120px;">
                                    Date
                                </th>
                            </tr>
                        </thead>
                        <tbody>{rows_html}</tbody>
                    </table>
                </div>
            """

        odj_block = _block("📋 OdJ à préparer (7 prochains jours)", odj_rows, COLORS["accent"], "#e8f6fd")
        cr_block = _block("📝 CR à compléter", cr_rows, COLORS["red"], "#f8d7da")
        return f'<div style="margin:0 0 24px 0;">{odj_block}{cr_block}</div>'

    def _render_quote_section(self, quote):
        """Render the inspirational quote section."""
        return f"""
            <div style="margin:32px 0 0 0;padding:20px;background-color:#f9fafb;border-left:4px solid {COLORS['accent']};border-radius:0 8px 8px 0;">
                <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:15px;font-style:italic;color:{COLORS['text_dark']};margin:0 0 8px 0;line-height:1.5;">
                    "{quote['quote']}"
                </p>
                <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:{COLORS['text_gray']};margin:0;">
                    — {quote['author']}
                </p>
            </div>
        """

    def _wrap_email(self, title, content, preheader=""):
        """Wrap content in Blue Fox branded email template."""
        # Hidden preheader text for email clients
        preheader_html = f"""
            <div style="display:none;font-size:1px;color:#f8f9fa;line-height:1px;max-height:0px;max-width:0px;opacity:0;overflow:hidden;">
                {preheader}
                {'&nbsp;' * 50}
            </div>
        """ if preheader else ""

        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:{COLORS['bg_outer']};">
    {preheader_html}
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:{COLORS['bg_outer']};">
        <tr>
            <td align="center" style="padding:24px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px;background-color:{COLORS['white']};border-radius:12px;border:1px solid {COLORS['border']};">
                    <!-- Header -->
                    <tr>
                        <td style="background-color:{COLORS['header']};padding:16px 24px;border-radius:12px 12px 0 0;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                                <tr>
                                    <td align="left">
                                        <a href="https://bluefoxconsultant.com" style="text-decoration:none;">
                                            <img src="https://bluefoxconsultant.com/web/image/website/1/logo/Blue%20Fox?unique=803cc14" alt="Blue Fox" height="48" style="display:block;border:0;height:48px;width:auto;">
                                        </a>
                                    </td>
                                    <td align="right" style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:20px;font-weight:700;color:{COLORS['text_light']};">
                                        {title}
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Accent bar -->
                    <tr>
                        <td style="height:4px;line-height:4px;background-color:{COLORS['accent']};">&nbsp;</td>
                    </tr>
                    <!-- Content -->
                    <tr>
                        <td style="padding:24px;">
                            {content}
                        </td>
                    </tr>
                    <!-- Divider -->
                    <tr>
                        <td style="height:1px;line-height:1px;background-color:{COLORS['border']};">&nbsp;</td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="padding:16px 24px;background-color:{COLORS['white']};border-radius:0 0 12px 12px;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                                <tr>
                                    <td style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;color:{COLORS['text_gray']};">
                                        <strong style="color:{COLORS['header']};">Blue Fox</strong><br/>
                                        Solutions éthiques et souveraines pour vos données.
                                    </td>
                                    <td align="right" style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;color:#9CA3AF;">
                                        <a href="mailto:service@example.com" style="color:{COLORS['accent']};text-decoration:none;">service@example.com</a>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
                <!-- Bottom accent bars -->
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px;margin-top:8px;">
                    <tr>
                        <td style="height:2px;line-height:2px;font-size:1px;background-color:{COLORS['accent']};width:50%;">&nbsp;</td>
                        <td style="height:2px;line-height:2px;font-size:1px;background-color:{COLORS['header']};width:50%;">&nbsp;</td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
        """
