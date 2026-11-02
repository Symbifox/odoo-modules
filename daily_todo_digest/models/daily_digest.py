# -*- coding: utf-8 -*-
import logging
import requests
from markupsafe import escape
import pytz
from datetime import datetime, time, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.misc import format_date, format_datetime
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

_logger = logging.getLogger(__name__)

# Default timezone for date conversions
DEFAULT_TZ = 'America/Montreal'

# A task in one of these states is waiting on somebody else: it is not a
# gesture the recipient can make today. `04_waiting_normal` is Odoo's own;
# `05_waiting_client` and `06_waiting_external` come from
# `bf_task_waiting_states`. Only the ones the running database declares are
# used, so the module still works where that addon is absent.
WAITING_STATE_CANDIDATES = (
    "04_waiting_normal", "05_waiting_client", "06_waiting_external",
)
CLOSED_STATES = ("1_done", "1_canceled")

# Sections a template switches on, and that a recipient may override one by
# one in their preferences. The template field is `include_<name>`, the user
# field `digest_opt_<name>`.
SECTIONS = (
    "overdue_activities", "today_activities",
    "overdue_tasks", "today_tasks", "upcoming_tasks",
    "week_preview", "meetings", "calendar_events",
    "flux_subjects", "weather", "quote",
)

# `include_<nom>` du gabarit qui n'est PAS une section : un modificateur.
NON_SECTION_INCLUDES = ("waiting_tasks",)

# Weekday booleans on res.users, Monday first (same order as date.weekday()).
WEEKDAY_FIELDS = (
    "digest_monday", "digest_tuesday", "digest_wednesday", "digest_thursday",
    "digest_friday", "digest_saturday", "digest_sunday",
)


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


def _relative_luminance(hex_color):
    """WCAG relative luminance of a #RRGGBB color."""
    hex_color = hex_color.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(hex_color[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(color_a, color_b):
    la, lb = sorted((_relative_luminance(color_a), _relative_luminance(color_b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def on_white(hex_color, target=4.5, backgrounds=("#FFFFFF", "#E8F6FD")):
    """Darken a brand color until white text on it, and it on the light badge, read at AA.

    A light brand accent fails behind white text: Blue Fox #29ABE1 gives 2.6:1.
    The accent itself stays for bars and link colors; this variant is for the
    table headers and the count badges, where the color carries text.
    """
    try:
        base = hex_color.lstrip("#")
        r, g, b = (int(base[i:i + 2], 16) for i in (0, 2, 4))
    except (AttributeError, ValueError):
        return hex_color
    factor = 1.0
    color = "#%02X%02X%02X" % (r, g, b)
    while min(contrast_ratio(color, bg) for bg in backgrounds) < target and factor > 0:
        factor -= 0.05
        color = "#%02X%02X%02X" % (int(r * factor), int(g * factor), int(b * factor))
    return color


# Weather code descriptions (WMO codes), translated at rendering time in the
# recipient's language.
WEATHER_CODES = {
    0: ("☀️", _lt("Clear sky")),
    1: ("🌤️", _lt("Mainly clear")),
    2: ("⛅", _lt("Partly cloudy")),
    3: ("☁️", _lt("Overcast")),
    45: ("🌫️", _lt("Fog")),
    48: ("🌫️", _lt("Freezing fog")),
    51: ("🌧️", _lt("Light drizzle")),
    53: ("🌧️", _lt("Moderate drizzle")),
    55: ("🌧️", _lt("Dense drizzle")),
    56: ("🌧️", _lt("Light freezing drizzle")),
    57: ("🌧️", _lt("Dense freezing drizzle")),
    61: ("🌧️", _lt("Light rain")),
    63: ("🌧️", _lt("Moderate rain")),
    65: ("🌧️", _lt("Heavy rain")),
    66: ("🧊", _lt("Light freezing rain")),
    67: ("🧊", _lt("Heavy freezing rain")),
    71: ("🌨️", _lt("Light snow")),
    73: ("🌨️", _lt("Moderate snow")),
    75: ("❄️", _lt("Heavy snow")),
    77: ("🌨️", _lt("Snow grains")),
    80: ("🌦️", _lt("Light showers")),
    81: ("🌦️", _lt("Moderate showers")),
    82: ("⛈️", _lt("Violent showers")),
    85: ("🌨️", _lt("Light snow showers")),
    86: ("❄️", _lt("Heavy snow showers")),
    95: ("⛈️", _lt("Thunderstorm")),
    96: ("⛈️", _lt("Thunderstorm with light hail")),
    99: ("⛈️", _lt("Thunderstorm with heavy hail")),
}
WEATHER_UNKNOWN = _lt("Unknown")

# Default palette. `bg_outer` / `header` / `accent` are overwritten at the
# top of `_generate_html` with the user company's brand colors
# (`res.company.report_brand_dark` / `report_brand_primary`). Status hues
# (`red`/`orange`/`green`) and neutrals stay fixed.
COLORS = {
    "bg_outer": "#212529",
    "header": "#212529",
    "accent": "#714B67",
    "accent_on_white": "#714B67",
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
    last_sent = fields.Datetime(string="Last sent (UTC)")

    _sql_constraints = [
        (
            "uniq_config_user",
            "unique(config_id, user_id)",
            "Only one send log per recipient and per digest.",
        ),
    ]


class DailyDigestConfig(models.Model):
    _name = "daily.digest.config"
    _description = "Daily Digest Configuration"
    # The order IS the resolution order: a person receives the first template,
    # by sequence then id, that covers them.
    _order = "sequence, id"

    name = fields.Char(string="Name", required=True, default=lambda self: self.env._("My daily digest"))
    active = fields.Boolean(default=True)
    sequence = fields.Integer(
        string="Sequence",
        default=10,
        help="Order in which templates are tried. A person receives ONE "
             "digest: the first template, by sequence then id, that lists "
             "them or one of their groups.",
    )

    # Recipients
    user_ids = fields.Many2many(
        "res.users",
        string="Recipients",
        help="Users who will receive the daily digest",
    )
    group_ids = fields.Many2many(
        "res.groups",
        string="Recipient groups",
        help="Every member of these groups receives this digest, without "
             "being named. Their own preferences still override it.",
    )
    recipient_user_ids = fields.Many2many(
        "res.users",
        string="All recipients",
        compute="_compute_recipient_user_ids",
        search="_search_recipient_user_ids",
        help="Named recipients plus every member of the recipient groups. "
             "Searchable: a module asking \"does a digest cover this "
             "person?\" must read this and not `user_ids`, which misses "
             "anyone covered only by a group.",
    )

    @api.depends("user_ids", "group_ids", "group_ids.users")
    def _compute_recipient_user_ids(self):
        for config in self:
            config.recipient_user_ids = config.user_ids | config.group_ids.users

    def _search_recipient_user_ids(self, operator, value):
        """« Ce briefing couvre-t-il cette personne ? », posable par un filtre.

        ⚠️ Deux formes se jouaient à côté. `= False` demande « aucun
        destinataire », et la passer telle quelle donnait
        `("user_ids", "in", False)`, qui veut dire « sans destinataire NOMMÉ » :
        un gabarit ne visant qu'un groupe se rendait comme n'en ayant aucun. Et
        une recherche par texte (`ilike`, celle de l'éditeur de filtres) levait
        `NotImplementedError` : elle passe maintenant par les personnes.
        ⚠️ `in []` garde le sens d'Odoo (ne rien rendre) : seul `False` dit
        « aucun ».
        """
        negatif = operator.startswith("not ") or operator == "!="
        brut = operator[4:] if operator.startswith("not ") else (
            "=" if operator == "!=" else operator)
        if brut in ("like", "ilike", "=like", "=ilike"):
            value = self.env["res.users"].sudo().search([("name", brut, value)]).ids
            brut = "in"
        if brut not in ("in", "="):
            raise NotImplementedError(
                self.env._("Unsupported operator %s on recipients.", operator))
        if value is False or value is None:
            # ⚠️ Mesuré : `("group_ids.users", "=", False)` ne rend pas les
            # gabarits SANS groupe — il n'y a alors aucun groupe à comparer, et
            # la traversée ne retient rien. « Aucun destinataire » se lit donc
            # sur le champ calculé lui-même, qui est la vérité de ce champ.
            vides = self.sudo().with_context(active_test=False).search([]).filtered(
                lambda config: not config.recipient_user_ids)
            return [("id", "not in", vides.ids)] if negatif else [("id", "in", vides.ids)]
        positive = ["|", ("user_ids", "in", value), ("group_ids.users", "in", value)]
        return (["!"] + positive) if negatif else positive

    # Schedule
    send_hour = fields.Integer(
        string="Sending hour",
        default=4,
        help="Sending hour (0-23), evaluated in each recipient's time "
             "zone (res.users.tz, default America/Montreal).",
    )

    frequency = fields.Selection(
        [("daily", "Every day"),
         ("weekdays", "Weekdays only"),
         ("custom", "Chosen days")],
        string="Frequency",
        default="daily",
        required=True,
        help="Default frequency for this template. Each recipient may "
             "replace it in their preferences. \"Chosen days\" reads the "
             "weekday boxes of the recipient; a weekly digest is that, with "
             "a single day ticked.",
    )

    # Noise control
    activity_fold_model_ids = fields.Many2many(
        "ir.model",
        string="Fold activities from",
        help="Activities on these models are not listed one by one: one "
             "folded line per model gives the count and a link. Meant for "
             "the recurring reminders a scheduler creates, which are read as "
             "a batch and not acted on one at a time.",
    )
    include_waiting_tasks = fields.Boolean(
        string="List waiting tasks",
        default=False,
        help="Off: tasks waiting on someone else leave the day's list and "
             "are counted in one folded line. On: they are listed like the "
             "others.",
    )

    # Widget toggles
    include_overdue_activities = fields.Boolean(
        string="Overdue activities",
        default=True,
    )
    include_today_activities = fields.Boolean(
        string="Today's activities",
        default=True,
    )
    include_overdue_tasks = fields.Boolean(
        string="Overdue tasks",
        default=True,
    )
    include_today_tasks = fields.Boolean(
        string="Today's tasks",
        default=True,
    )
    include_upcoming_tasks = fields.Boolean(
        string="Upcoming tasks",
        default=False,
        help="Include the tasks of the next days",
    )
    include_week_preview = fields.Boolean(
        string="Week preview",
        default=True,
        help="The seven-day grid counting tasks and activities per day.",
    )
    include_flux_subjects = fields.Boolean(
        string="Watched subjects (RSS)",
        default=True,
        help="Items retained since the last digest by the RSS lists that "
             "watch a name (bf_flux). Silent when that module is absent.",
    )
    upcoming_days = fields.Integer(
        string="Days ahead",
        default=3,
        help="Number of days to preview (1-7)",
    )
    include_weather = fields.Boolean(
        string="Weather",
        default=True,
    )
    weather_city = fields.Char(
        string="Weather city (default)",
        default="Montréal",
        help="Default city. Each recipient can replace it in their "
             "preferences (Weather city (digest) on their user record).",
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
        string="Inspiring quote",
        default=True,
    )
    include_meetings = fields.Boolean(
        string="Meetings (agendas + minutes)",
        default=True,
        help="Include upcoming meetings with an agenda to prepare and "
             "past meetings whose minutes are still to complete "
             "(bf_meeting cycle).",
    )
    include_calendar_events = fields.Boolean(
        string="Calendar events",
        default=True,
        help="Include the events of the recipient's day that they attend "
             "and have not declined, in their time zone.",
    )

    # Company filter
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
        help="Filter tasks by this company only",
    )

    # Tracking
    last_sent = fields.Datetime(string="Last sent", readonly=True)

    def action_send_now(self):
        """Manually send the digest now."""
        self.ensure_one()
        self._send_digest()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Digest sent"),
                "message": _("The digest was sent to the recipients."),
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
                    "title": _("Error"),
                    "message": _("Your user has no email address configured."),
                    "type": "danger",
                },
            }
        self._send_digest(test_user=current_user)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Test sent"),
                "message": _("A test digest was sent to %s.") % current_user.email,
                "type": "success",
            },
        }

    # ------------------------------------------------------------ recipients

    @api.model
    def _resolve_recipients(self):
        """Return [(user, config)] — ONE template per person.

        A person can be named on a template AND belong to a group another
        template targets; without this, they would get two digests the same
        morning. The template that wins is the first by `sequence` then `id`.
        Portal/share users are never recipients.
        """
        pairs = []
        seen = set()
        for config in self.search([("active", "=", True)], order="sequence, id"):
            for user in config.recipient_user_ids.sorted("id"):
                # 🔴 `active` se vérifie ici : un x2many se lit SANS le filtre
                # des actifs, donc `group_ids.users` rend les archivés. Viser
                # `base.group_user` aurait envoyé le briefing — noms de tâches,
                # de projets et de rencontres — à qui est parti.
                if user.id in seen or not user.email or user.share or not user.active:
                    continue
                # Un gabarit borné à une société ne vise pas les usagers des
                # autres, même quand son groupe les contient (viser
                # `base.group_user` les prendrait tous).
                if config.company_id and config.company_id not in user.company_ids:
                    continue
                seen.add(user.id)
                pairs.append((user, config))
        return pairs

    def _send_hour_for(self, user):
        """The recipient's own hour when they set one, else the template's."""
        self.ensure_one()
        own = user.digest_send_hour if "digest_send_hour" in user._fields else False
        return int(own) if own else self.send_hour

    def _frequency_for(self, user):
        """The recipient's own frequency when they set one, else the template's."""
        self.ensure_one()
        own = user.digest_frequency if "digest_frequency" in user._fields else False
        return own or self.frequency or "daily"

    def _section_on(self, user, name):
        """Is section `name` on for this recipient?

        Three states: the person said yes, the person said no, or the person
        said nothing and the template decides.
        """
        self.ensure_one()
        field = f"digest_opt_{name}"
        choice = user[field] if field in user._fields else False
        if choice:
            return choice == "yes"
        return bool(self[f"include_{name}"])

    def _any_section_on(self, user):
        """Une seule section allumée suffit à faire partir le briefing.

        🔴 Les onze satellites greffent leur section pendant `_generate_html`,
        après cette décision. Ne regarder que les sections du cœur aurait coupé
        le courriel de qui ne garde QUE les avis devops, les paliers de banques
        d'heures ou les relances de factures — un geste d'un clic depuis que les
        préférences existent. Chaque satellite déclare son interrupteur
        `include_<nom>` sur le gabarit : on les lit tous.
        """
        self.ensure_one()
        if any(self._section_on(user, name) for name in SECTIONS):
            return True
        for nom in self._fields:
            if not nom.startswith("include_"):
                continue
            section = nom[len("include_"):]
            if section in SECTIONS or section in NON_SECTION_INCLUDES:
                continue
            if self[nom]:
                return True
        return False

    def _heure_due(self, heure, now_local, user_tz):
        """L'heure visée est-elle celle qu'il est, chez cette personne ?

        🔴 Au passage à l'heure avancée, une heure locale n'EXISTE pas (2 h à
        Montréal, 2 h à Auckland) : qui a choisi cette heure-là ne recevait rien
        ce jour-là, en silence, une fois par année — et « 02:00 » est devenu un
        choix d'un clic. L'envoi est alors reporté à la première heure qui
        existe. Une heure manquée pour toute AUTRE raison (cron arrêté) n'est
        pas rattrapée : le briefing du matin arriverait l'après-midi.
        """
        if heure == now_local.hour:
            return True
        if heure > now_local.hour:
            return False
        for intermediaire in range(heure, now_local.hour):
            try:
                user_tz.localize(
                    datetime.combine(now_local.date(), time(intermediaire)),
                    is_dst=None)
            except pytz.NonExistentTimeError:
                continue
            except pytz.AmbiguousTimeError:
                return False
            return False
        return True

    def _is_due(self, user, now_local):
        """Is this the recipient's delivery hour, on a day they asked for?"""
        self.ensure_one()
        user_tz = pytz.timezone(user.tz or DEFAULT_TZ)
        if not self._heure_due(self._send_hour_for(user), now_local, user_tz):
            return False
        frequency = self._frequency_for(user)
        if frequency == "weekdays":
            return now_local.weekday() < 5
        if frequency == "custom":
            field = WEEKDAY_FIELDS[now_local.weekday()]
            return bool(user[field]) if field in user._fields else True
        return True

    @api.model
    def _cron_send_daily_digests(self):
        """Cron job to send daily digests.

        Evaluates the delivery hour, the frequency and the "already sent
        today" guard in EACH recipient's own timezone (`res.users.tz`), so the
        same template delivers at the local morning hour for recipients in
        different timezones.
        """
        now_utc = fields.Datetime.now()

        for user, config in self._resolve_recipients():
            # 🔴 TOUT le tour est dans le point de sauvegarde, y compris la
            # lecture du fuseau et la création du journal d'envoi : une seule
            # erreur SQL (contrainte d'unicité sur le journal, course avec un
            # « Envoyer maintenant », enregistrement disparu) laissait le
            # curseur avorté, et TOUS les destinataires suivants levaient
            # `InFailedSqlTransaction` — y compris les `last_sent` de ceux dont
            # le courriel était déjà parti par SMTP, qui le recevraient deux
            # fois.
            try:
                with self.env.cr.savepoint():
                    tz_name = user.tz or DEFAULT_TZ
                    user_tz = pytz.timezone(tz_name)
                    now_local = pytz.UTC.localize(now_utc).astimezone(user_tz)

                    if not config._is_due(user, now_local):
                        continue

                    # Son briefing est-il déjà parti aujourd'hui, par
                    # n'importe quel gabarit ?
                    # 🔴 La question est « cette PERSONNE a-t-elle reçu son
                    # briefing aujourd'hui ? », pas « ce gabarit le lui a-t-il
                    # envoyé ? ». Si la couverture change entre deux heures
                    # (gabarit archivé, séquence baissée, groupe ajouté), le
                    # journal du gabarit suivant est vierge et un deuxième
                    # briefing partait le même matin.
                    if config._deja_envoye_aujourdhui(user, now_local, user_tz):
                        continue

                    log = config._get_send_log(user)
                    config._send_digest(test_user=user)
                    log.last_sent = now_utc  # store in UTC
                    config.last_sent = now_utc  # legacy: last send of any recipient
                _logger.info(
                    "Daily digest '%s' sent to %s (%s)",
                    config.name, user.name, user.tz or DEFAULT_TZ,
                )
            except Exception:
                _logger.exception(
                    "Failed to send daily digest '%s' to %s",
                    config.name, user.name,
                )

    @api.model
    def _deja_envoye_aujourdhui(self, user, now_local, user_tz):
        """A-t-on déjà envoyé SON briefing aujourd'hui, par n'importe quel gabarit ?"""
        logs = self.env["daily.digest.send.log"].sudo().search([
            ("user_id", "=", user.id), ("last_sent", "!=", False),
        ])
        for log in logs:
            # ⚠️ La question est « aujourd'hui », pas « aujourd'hui ou plus
            # tard » : un journal daté du futur bloquait autant de jours.
            if pytz.UTC.localize(log.last_sent).astimezone(user_tz).date() == now_local.date():
                return True
        return False

    def _get_send_log(self, user):
        """Return (creating if needed) the per-user send log for this config."""
        self.ensure_one()
        # 🔴 En `sudo` : la lecture du journal est ouverte aux employés, pas sa
        # création. Depuis que la section RSS le lit, « Envoyer un essai »
        # levait `AccessError` pour quiconque n'est pas administrateur.
        Log = self.env["daily.digest.send.log"].sudo()
        log = Log.search(
            [("config_id", "=", self.id), ("user_id", "=", user.id)], limit=1
        )
        if not log:
            log = Log.create({
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

        recipients = test_user if test_user else self.recipient_user_ids
        if not recipients:
            _logger.warning("No recipients configured for digest '%s'", self.name)
            return

        for user in recipients:
            if not user.email:
                _logger.warning("User %s has no email, skipping", user.name)
                continue
            # 🔴 In the RECIPIENT's language: the digest is sent by the scheduled
            # job, which carries no language, and used to be written in French
            # for everybody.
            self.with_context(lang=user.lang or "en_US", tz=user.tz)._send_digest_to(user)

    def _send_digest_to(self, user):
        """Build and send one recipient's digest, in the language of the context."""
        self.ensure_one()
        # Gather data per recipient, in the recipient's own timezone, so the
        # "today" window (and week preview) reflect their local day.
        tz_name = user.tz or DEFAULT_TZ
        data = self._gather_digest_data(user, tz_name=tz_name)

        # 🔴 Le briefing part dès qu'une section est allumée, même s'il n'a
        # rien à dire : son propre message « rien pour aujourd'hui » porte la
        # journée vide. Resserrer ce garde sur le contenu réel — ce que la
        # 2.3.0 ne faisait que par accident, ses seaux de tâches étant des
        # dictionnaires toujours vrais — aurait avalé les sections que les
        # ONZE modules satellites greffent pendant le rendu (avis devops,
        # paliers de banques d'heures, relances de factures, billets
        # d'assistance) : la décision de se taire se prend AVANT
        # `_generate_html`, là où ils s'insèrent.
        if not self._any_section_on(user):
            _logger.info(
                "Every section is off for %s on digest '%s'", user.name, self.name
            )
            return

        # Generate personalized HTML
        body_html = self._generate_html(data, user)

        # Subject date in the recipient's local day
        today_local = pytz.UTC.localize(fields.Datetime.now()).astimezone(
            pytz.timezone(tz_name)
        ).date()
        date_str = format_date(self.env, today_local, date_format="full")

        mail_values = {
            "subject": "🌄 %s | %s" % (self.env._("Your day"), date_str),
            # L'en-tête, le logo et le pied prennent la société du
            # DESTINATAIRE : le `De:` la suit, au lieu de celle du cron.
            "email_from": (user.company_id.email or self.env.company.email
                           or self.env.user.email_formatted),
            "email_to": user.email,
            "body_html": body_html,
            "auto_delete": True,
        }
        mail = self.env["mail.mail"].sudo().create(mail_values)
        mail.send()

    def _gather_digest_data(self, users=None, tz_name=None):
        """Gather all data for the digest.

        Args:
            users: THE recipient (exactly one; anything else raises). The
                folded counts, the hidden and waiting tallies are gathered for
                the domain and never re-split per person afterwards.
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

        # ⚠️ UN destinataire par passe, toujours. Les comptes repliés, les
        # tâches non visibles et les tâches en attente sont cueillis pour les
        # personnes du domaine et ne sont PAS redécoupés ensuite : deux
        # destinataires dans une même passe se verraient les compteurs l'un de
        # l'autre. L'invariant est une règle, pas une habitude.
        if users is None or len(users) != 1:
            raise ValueError(
                "_gather_digest_data gathers for exactly one recipient")
        recipient = users

        def on(name):
            return self._section_on(recipient, name)

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
        target_users = users
        user_ids = target_users.ids

        if on("overdue_activities"):
            data["overdue_activities"] = self._get_activities(
                user_ids, [("date_deadline", "<", today)]
            )

        if on("today_activities"):
            data["today_activities"] = self._get_activities(
                user_ids, [("date_deadline", "=", today)]
            )

        # For tasks, date_deadline is a Datetime field - use UTC bounds
        if on("overdue_tasks"):
            data["overdue_tasks"] = self._get_tasks(
                user_ids, [("date_deadline", "<", today_start_utc)], tz_name=tz_name
            )

        if on("today_tasks"):
            data["today_tasks"] = self._get_tasks(
                user_ids, [
                    ("date_deadline", ">=", today_start_utc),
                    ("date_deadline", "<=", today_end_utc),
                ], tz_name=tz_name
            )

        # Upcoming tasks (next N days, excluding today)
        if on("upcoming_tasks"):
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
                ], tz_name=tz_name
            )
            data["upcoming_days"] = days_ahead

        # Week preview: count tasks and activities per day for next 7 days
        week_preview = []
        if on("week_preview"):
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
                    ("state", "not in", CLOSED_STATES),
                ])

                week_preview.append({
                    "date": target_date,
                    "day_name": format_date(self.env, target_date, date_format="EEE"),
                    "day_num": target_date.day,
                    "tasks": task_count,
                    "activities": activity_count,
                    "total": task_count + activity_count,
                })

        data["week_preview"] = week_preview

        # Weather is fetched per-recipient in `_generate_html` (each user can
        # have their own city/coordinates), so it is intentionally NOT gathered
        # here.

        if on("quote"):
            data["quote"] = self.env["daily.digest.quote"].get_random_quote()

        if on("meetings"):
            data["meetings_by_user"] = self._get_meetings_buckets(user_ids)

        if on("flux_subjects"):
            data["flux_by_user"] = self._get_flux_subjects(target_users)

        if on("calendar_events"):
            tomorrow_start_utc = local_tz.localize(
                fields.Datetime.to_datetime(f"{today + timedelta(days=1)} 00:00:00")
            ).astimezone(pytz.UTC).replace(tzinfo=None)
            data["events_by_user"] = self._get_calendar_events(
                target_users, today, today_start_utc, tomorrow_start_utc
            )

        return data

    def _get_calendar_events(self, users, day, day_start_utc, day_end_utc):
        """Return {uid: [event dicts]} for the events of the recipient's local day.

        An event belongs to someone through their attendance, not its
        organizer: events pulled from Nextcloud are organized by the system
        user, so `user_id` misses most of them. Declined events are left out.

        `day_start_utc` is included, `day_end_utc` (the next local midnight) is
        not. All-day events are matched on their dates, which Odoo stores
        without a time zone and with an inclusive end.

        🔴 A cancelled meeting is not archived where bf_calendar_invite is
        installed: it stays active, struck through in the calendar, with
        `bf_event_status = "cancelled"` (Nextcloud's STATUS:CANCELLED lands there
        too). It is left out when the field exists.
        """
        Event = self.env.get("calendar.event")
        if Event is None or not users:
            return {}
        Event = Event.sudo()
        result = {}
        for user in users:
            domain = [
                ("attendee_ids", "any", [
                    ("partner_id", "=", user.partner_id.id),
                    ("state", "!=", "declined"),
                ]),
                "|",
                "&", "&", ("allday", "=", True),
                ("start_date", "<=", day), ("stop_date", ">=", day),
                "&", "&", ("allday", "=", False),
                ("start", "<", day_end_utc),
                "|", ("stop", ">", day_start_utc), ("start", ">=", day_start_utc),
            ]
            if "bf_event_status" in Event._fields:
                domain.append(("bf_event_status", "!=", "cancelled"))
            events = Event.search(domain, order="allday desc, start, id")
            result[user.id] = [{
                "id": ev.id,
                "name": ev.name,
                "allday": ev.allday,
                "start": ev.start,
                "stop": ev.stop,
                "start_date": ev.start_date,
                "stop_date": ev.stop_date,
                "location": ev.location or "",
                "videocall": ev.videocall_location or "",
            } for ev in events]
        return result

    def _get_meetings_buckets(self, user_ids):
        """Delegate to meeting.dashboard so the bucket logic stays in one place."""
        if not user_ids:
            return {}
        Dashboard = self.env.get("meeting.dashboard")
        if Dashboard is None:
            return {}
        return Dashboard.sudo()._get_digest_buckets(user_ids=user_ids)

    def _get_activities(self, user_ids, domain_extra):
        """Activities for these users, with the folded models set aside.

        Returns a dict:
        - 'visible': the activities to list, one line each;
        - 'folded': one entry per folded model — a count and a link to the
          list, instead of as many identical lines. Measured on a real
          inbox: half of one recipient's activity section was 18
          `hosting.maintenance.schedule` reminders whose summary and record
          name are the same words repeated ("Backup verification" six
          times).
        """
        Activity = self.env["mail.activity"].sudo()
        domain = [("user_id", "in", user_ids)] + domain_extra
        activities = Activity.search(domain, order="date_deadline, id")

        fold_models = set(self.activity_fold_model_ids.mapped("model"))
        visible = []
        folded = {}
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")

        for act in activities:
            model_name = act.res_model
            if model_name in fold_models:
                entry = folded.setdefault(model_name, {
                    "model": model_name,
                    "label": self.env["ir.model"]._get(model_name).name or model_name,
                    "count": 0,
                    "link": self._activity_list_link(base_url, model_name),
                })
                entry["count"] += 1
                continue

            record_id = act.res_id
            link = f"{base_url}/web#id={record_id}&model={model_name}&view_type=form"

            # Get record name
            try:
                record = self.env[model_name].sudo().browse(record_id)
                record_name = record.display_name or f"{model_name} #{record_id}"
            except Exception:
                record_name = f"{model_name} #{record_id}"

            summary = act.summary or act.activity_type_id.name or self.env._("Activity")
            visible.append({
                "id": act.id,
                "summary": summary,
                # An activity created by a scheduler names the record after
                # itself: showing both is the same words twice.
                "record_name": "" if record_name == summary else record_name,
                "model": model_name,
                "deadline": act.date_deadline,
                "user": act.user_id.name,
                "user_id": act.user_id.id,
                "link": link,
                "note": act.note or "",
            })

        return {"visible": visible, "folded": list(folded.values())}

    def _digest_action_url(self, base_url, xmlid, active_id=None):
        """L'URL d'une liste filtrée, le domaine porté par l'ACTION.

        🔴 Odoo 18 ne lit aucun domaine passé dans l'URL : le routeur du client
        web ne retient que `resId`, `action`, `active_id` et `model`, et le seul
        fichier qui parle de `state.domain` est le sélecteur de domaines. Pire,
        un `model=` sans `action=` retombe sur `user.homeActionId` : le lien
        menait à l'écran d'accueil. Écrire un domaine « relisible » dans l'URL
        durcissait donc un paramètre mort.

        Le domaine vit côté serveur, dans `data/digest_actions.xml`, et le
        client l'évalue avec le contexte de la personne qui clique : `uid` est
        le sien — jamais celui du cron — et `active_id` vient de l'URL.
        ⚠️ `active_id` n'est porté au contexte que s'il est VRAI : sans lui, une
        action dont le domaine le lit lève une erreur, d'où le repli.
        """
        action = self.env.ref(xmlid, raise_if_not_found=False)
        if not action:
            return f"{base_url}/odoo"
        prefixe = f"/{active_id}" if active_id else ""
        return f"{base_url}/odoo{prefixe}/action-{action.id}"

    def _activity_list_link(self, base_url, model_name):
        """La liste derrière une ligne repliée : MES rappels sur ce modèle.

        ⚠️ Sans borne de date : le client évalue `context_today()` dans le
        fuseau du NAVIGATEUR, et une liste décalée d'un jour est exactement le
        défaut que cette version corrige dans le courriel. Le compte exact, lui,
        est dans la ligne.
        """
        model = self.env["ir.model"]._get(model_name)
        if not model.id:
            return self._digest_action_url(
                base_url, "daily_todo_digest.action_digest_my_activities")
        return self._digest_action_url(
            base_url, "daily_todo_digest.action_digest_folded_activities",
            active_id=model.id)

    @api.model
    def _waiting_states(self):
        """The waiting states this database actually declares."""
        selection = dict(
            self.env["project.task"].fields_get(["state"])["state"]["selection"]
        )
        return [state for state in WAITING_STATE_CANDIDATES if state in selection]

    def _get_tasks(self, user_ids, domain_extra, tz_name=None):
        """Get project tasks matching criteria for specified users.

        Closed tasks are always left out. Tasks waiting on somebody else are
        counted apart unless `include_waiting_tasks` is on: they cannot be
        acted on today, so they do not belong in the day's list.

        `tz_name` is the recipient's time zone, used for the date each line
        shows. Without it the dates read in `America/Montreal` while the
        windows that selected them read in the recipient's zone, so a
        recipient in Auckland saw his tasks of the day dated the day before.

        Returns a dict with:
        - 'visible': list of tasks with display_in_project=True
        - 'hidden_count': count of tasks with display_in_project=False
        - 'waiting_count': count of tasks left out as waiting
        """
        Task = self.env["project.task"].sudo()
        tz_name = tz_name or DEFAULT_TZ
        waiting_states = self._waiting_states()
        domain = [
            ("user_ids", "in", user_ids),
            ("state", "not in", CLOSED_STATES),
        ] + domain_extra

        # Note: No company filter - show all tasks assigned to user regardless of company

        waiting_count = 0
        if waiting_states and not self.include_waiting_tasks:
            waiting_count = Task.search_count(
                domain + [("state", "in", waiting_states)]
            )
            domain += [("state", "not in", waiting_states)]

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
                "project": task.project_id.name if task.project_id else self.env._("No project"),
                "deadline": datetime_to_local_date(task.date_deadline, tz_name),
                "user": ", ".join(task.user_ids.mapped("name")),
                "user_ids": task.user_ids.ids,
                "link": link,
                "parent_task": task.parent_id.name if task.parent_id else None,
                "is_subtask": bool(task.parent_id),
                "priority": task.priority,
            })

        return {
            "visible": visible_tasks,
            "hidden_count": hidden_count,
            "waiting_count": waiting_count,
        }

    def _get_flux_subjects(self, users, limit=10):
        """{uid: [item dicts]} — what the watched-subject RSS lists retained.

        Reads `bf_flux` AT RUNTIME and stays silent when it is absent:
        `daily_todo_digest` is LGPL-3 and `bf_flux` is BUSL-1.1, so a
        manifest dependency would make the permissive licence promise
        something it cannot deliver. Same treatment as `bf_meeting` here.

        Only the lists that watch a NAME are read (those carrying a
        `bf.flux.sujet`). The others — News, Aotearoa and the like — retain
        some two hundred titles a day and already have their own summary
        email; they are reading, not work, and the briefing is for work.
        """
        Retenue = self.env.get("bf.flux.retenue")
        Sujet = self.env.get("bf.flux.sujet")
        if Retenue is None or Sujet is None or not users:
            return {}
        # `active` sur la liste comme sur le sujet : `bf_flux` fait pareil.
        # Sans ça, archiver la liste de veille d'un mandat terminé laissait ses
        # nouvelles revenir chaque matin tant qu'un sujet actif y pendait.
        watch_lists = Sujet.sudo().search([]).mapped("liste_id").filtered("active")
        if not watch_lists:
            return {}

        result = {}
        for user in users:
            # Membership comes from the list (service, project, named person)
            # minus the people who unsubscribed; it is not ours to recompute.
            mine = watch_lists.sudo().filtered(lambda l: user in l.membre_user_ids)
            if not mine:
                continue
            log = self._get_send_log(user)
            since = log.last_sent or (fields.Datetime.now() - timedelta(days=1))
            domaine = [("liste_id", "in", mine.ids),
                       ("etat", "=", "retenu"),
                       ("create_date", ">=", since)]
            # 🔴 Le rattrapage : ajouter un sujet surveillé fait retenir tout
            # l'arrière-catalogue de ses sources. Le résumé de `bf_flux` l'écarte
            # de ses propres courriels ; sans ça, le briefing du lendemain
            # s'ouvrait sur dix vieilles nouvelles — le bruit même qu'on retire.
            if "rattrapage" in Retenue._fields:
                domaine.append(("rattrapage", "=", False))
            items = Retenue.sudo().search(
                domaine, order="date_publication desc, id desc", limit=limit + 1)
            # Ce que la personne a déjà lu (dans Odoo, Discuss ou un résumé
            # précédent) ne revient pas le matin.
            Lecture = self.env.get("bf.flux.lecture")
            if items and Lecture is not None and "element_id" in Retenue._fields:
                lus = set(Lecture.sudo().search([
                    ("user_id", "=", user.id),
                    ("element_id", "in", items.element_id.ids),
                ]).element_id.ids)
                if lus:
                    items = items.filtered(lambda r: r.element_id.id not in lus)
            if not items:
                continue
            result[user.id] = {
                "items": [{
                    "titre": item.titre or "",
                    "lien": item.lien or "",
                    "emetteur": item.emetteur or "",
                    "motifs": item.motifs or "",
                    "liste": item.liste_id.name or "",
                } for item in items[:limit]],
                "more": len(items) > limit,
            }
        return result

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

            current = data.get("current", {})
            daily = data.get("daily", {})

            weather_code = daily.get("weathercode", [0])[0]
            emoji, label = WEATHER_CODES.get(weather_code, ("🌡️", WEATHER_UNKNOWN))

            return {
                "city": city,
                "current_temp": round(current.get("temperature_2m", 0)),
                "high": round(daily.get("temperature_2m_max", [0])[0]),
                "low": round(daily.get("temperature_2m_min", [0])[0]),
                "precipitation": round(daily.get("precipitation_sum", [0])[0], 1),
                "precipitation_prob": daily.get("precipitation_probability_max", [0])[0],
                "emoji": emoji,
                "description": self.env._(label),
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
        today_str = format_date(self.env, today, date_format="full")

        # Weather is per-recipient (own city/coordinates + own timezone).
        # 🔴 Par `_section_on` et non par la case du gabarit : sinon la
        # préférence « Météo : non » ne faisait rien, et « Météo : oui » sur un
        # gabarit qui l'éteint donnait un courriel vide chaque matin (le garde
        # d'envoi, lui, comptait la section comme allumée).
        weather = self._get_weather(user) if self._section_on(user, "weather") else None

        # Pull brand colors from the user's company. Single-threaded write to
        # the module-level palette is safe inside the cron-driven send loop.
        # `report_brand_*` come from bf_onboarding_base, which this module does
        # not depend on: read them only when they exist.
        co = user.company_id
        COLORS["bg_outer"] = getattr(co, "report_brand_dark", False) or "#212529"
        COLORS["header"] = getattr(co, "report_brand_dark", False) or "#212529"
        COLORS["accent"] = getattr(co, "report_brand_primary", False) or "#714B67"
        COLORS["accent_on_white"] = on_white(COLORS["accent"])

        # Filter data for this specific user
        user_data = self._filter_data_for_user(data, user)

        content_parts = []

        # Greeting
        content_parts.append(f"""
            <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:16px;color:{COLORS['text_dark']};margin:0 0 16px 0;">
                {self.env._("Hello %s,", f"<strong>{escape(self._first_name(user))}</strong>")}
            </p>
            <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:15px;color:{COLORS['text_gray']};margin:0 0 24px 0;">
                {self.env._("Here is your agenda for %s.", today_str)}
            </p>
        """)

        # Weather section
        if weather:
            content_parts.append(self._render_weather_section(weather))

        # The day's calendar, before what is due: it is what the day is made of.
        if user_data.get("events"):
            content_parts.append(self._render_calendar_section(user_data["events"], user, today))

        # Overdue activities
        if user_data.get("overdue_activities"):
            content_parts.append(self._render_activity_section(
                self.env._("Overdue activities"),
                user_data["overdue_activities"],
                COLORS["red"],
                is_overdue=True,
            ))
        for entry in user_data.get("overdue_activities_folded") or []:
            content_parts.append(self._render_folded_activities(entry, is_overdue=True))

        # Today's activities
        if user_data.get("today_activities"):
            content_parts.append(self._render_activity_section(
                self.env._("Today's activities"),
                user_data["today_activities"],
                COLORS["accent_on_white"],
                is_overdue=False,
            ))
        for entry in user_data.get("today_activities_folded") or []:
            content_parts.append(self._render_folded_activities(entry, is_overdue=False))

        # Overdue tasks
        if user_data.get("overdue_tasks"):
            content_parts.append(self._render_task_section(
                self.env._("Overdue tasks"),
                user_data["overdue_tasks"],
                COLORS["red"],
                is_overdue=True,
            ))

        # Today's tasks
        if user_data.get("today_tasks"):
            content_parts.append(self._render_task_section(
                self.env._("Today's tasks"),
                user_data["today_tasks"],
                COLORS["accent_on_white"],
                is_overdue=False,
            ))

        # Hidden tasks summary (tasks in folded stages)
        hidden_overdue = user_data.get("overdue_tasks_hidden", 0)
        hidden_today = user_data.get("today_tasks_hidden", 0)
        total_hidden = hidden_overdue + hidden_today
        if total_hidden > 0:
            content_parts.append(self._render_hidden_tasks_summary(hidden_overdue, hidden_today))

        # Tasks waiting on somebody else: counted, not listed.
        total_waiting = (
            user_data.get("overdue_tasks_waiting", 0)
            + user_data.get("today_tasks_waiting", 0)
        )
        if total_waiting > 0:
            content_parts.append(self._render_waiting_tasks_summary(total_waiting, user))

        # Meetings: OdJ to prepare (≤7d) + CR to complete (past pending)
        if user_data.get("meetings_odj") or user_data.get("meetings_cr"):
            content_parts.append(self._render_meetings_section(
                user_data.get("meetings_odj") or [],
                user_data.get("meetings_cr") or [],
                user,
            ))

        # Tasks of the next days
        if user_data.get("upcoming_tasks"):
            content_parts.append(self._render_task_section(
                self.env._("Next %s day(s)", data.get("upcoming_days") or 3),
                user_data["upcoming_tasks"],
                COLORS["accent_on_white"],
                is_overdue=False,
                show_full_date=True,
            ))

        # Watched subjects (RSS): BF or a client in the news.
        if (user_data.get("flux") or {}).get("items"):
            content_parts.append(self._render_flux_section(user_data["flux"]))

        # Week preview (next 7 days summary)
        if data.get("week_preview"):
            content_parts.append(self._render_week_preview(data["week_preview"]))

        # No items message
        if not any([
            user_data.get("overdue_activities"),
            user_data.get("today_activities"),
            user_data.get("overdue_activities_folded"),
            user_data.get("today_activities_folded"),
            user_data.get("overdue_tasks"),
            user_data.get("today_tasks"),
            user_data.get("meetings_odj"),
            user_data.get("meetings_cr"),
        ]) and total_hidden == 0 and total_waiting == 0:
            content_parts.append(f"""
                <div style="background-color:#d1e7dd;border:1px solid #a3cfbb;border-radius:8px;padding:16px;margin:16px 0;">
                    <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;color:{COLORS['green']};margin:0;">
                        <strong>{self.env._("No overdue task or activity, and nothing due today.")}</strong><br/>
                        {self.env._("Have a good day!")}
                    </p>
                </div>
            """)

        # Inspirational quote
        if self._section_on(user, "quote") and data.get("quote"):
            content_parts.append(self._render_quote_section(data["quote"]))

        content = "".join(content_parts)

        # Build preheader summary
        preheader_parts = []
        overdue_count = len(user_data.get("overdue_activities", [])) + len(user_data.get("overdue_tasks", []))
        today_count = len(user_data.get("today_activities", [])) + len(user_data.get("today_tasks", []))
        meetings_count = len(user_data.get("meetings_odj", [])) + len(user_data.get("meetings_cr", []))
        events_count = len(user_data.get("events", []))
        flux_count = len((user_data.get("flux") or {}).get("items") or [])
        if events_count > 0:
            preheader_parts.append(self.env._("%s event(s)", events_count))
        if overdue_count > 0:
            preheader_parts.append(self.env._("%s overdue", overdue_count))
        if today_count > 0:
            preheader_parts.append(self.env._("%s today", today_count))
        if meetings_count > 0:
            preheader_parts.append(self.env._("%s meeting(s)", meetings_count))
        if flux_count > 0:
            preheader_parts.append(self.env._("%s in the news", flux_count))
        if weather:
            w = weather
            preheader_parts.append(f"{w.get('emoji', '')} {w['current_temp']}°C")
        preheader = " | ".join(preheader_parts) if preheader_parts else self.env._("Your agenda for today")

        return self._wrap_email(self.env._("Your day"), content, preheader, company=co)

    @staticmethod
    def _first_name(user):
        """Le prénom, ou le nom entier quand il ne se découpe pas.

        `" ".split()` rend une liste VIDE alors que `" "` est vrai : un nom
        fait d'espaces levait `IndexError` et ce destinataire-là perdait son
        briefing.
        """
        morceaux = (user.name or "").split()
        return morceaux[0] if morceaux else (user.name or "").strip()

    def _filter_data_for_user(self, data, user):
        """Keep this recipient's share of the gathered data.

        Filtering reads the stored ids, never the display name: two
        colleagues who share a first and last name used to receive each
        other's lines, and a name that is a substring of another ("Marc" in
        "Marc-André") matched too.
        """
        user_data = {}

        for bucket in ("overdue_activities", "today_activities"):
            gathered = data.get(bucket)
            if not gathered:
                continue
            user_data[bucket] = [
                act for act in gathered.get("visible", [])
                if act.get("user_id") == user.id
            ]
            user_data[f"{bucket}_folded"] = gathered.get("folded", [])

        for bucket in ("overdue_tasks", "today_tasks", "upcoming_tasks"):
            gathered = data.get(bucket)
            if not gathered:
                continue
            user_data[bucket] = [
                task for task in gathered.get("visible", [])
                if user.id in (task.get("user_ids") or [])
            ]
            user_data[f"{bucket}_hidden"] = gathered.get("hidden_count", 0)
            user_data[f"{bucket}_waiting"] = gathered.get("waiting_count", 0)

        # Meetings: keyed by uid, slice this user's bucket out
        meetings = data.get("meetings_by_user") or {}
        bucket = meetings.get(user.id) or {"odj": [], "cr": []}
        user_data["meetings_odj"] = bucket.get("odj", [])
        user_data["meetings_cr"] = bucket.get("cr", [])

        user_data["events"] = (data.get("events_by_user") or {}).get(user.id, [])
        user_data["flux"] = (data.get("flux_by_user") or {}).get(user.id) or {}

        return user_data

    def _render_weather_section(self, weather):
        """Render the weather section."""
        precip_text = ""
        if weather["precipitation"] > 0 or weather["precipitation_prob"] > 30:
            precip_text = " | " + self.env._("Precipitation: %(mm)s mm (%(prob)s%%)",
                                             mm=weather['precipitation'], prob=weather['precipitation_prob'])

        emoji = weather.get("emoji", "🌡️")

        return f"""
            <div style="background-color:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:16px;margin:0 0 24px 0;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                        <td style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;color:{COLORS['header']};">
                            <strong style="font-size:16px;">{escape(weather['city'] or '')}</strong><br/>
                            <span style="font-size:32px;">{emoji}</span>
                            <span style="font-size:28px;font-weight:600;margin-left:8px;">{weather['current_temp']}°C</span>
                            <span style="color:{COLORS['text_gray']};font-size:14px;margin-left:8px;">
                                {weather['description']}
                            </span>
                        </td>
                        <td align="right" style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:{COLORS['text_gray']};">
                            {self.env._("High")}: <strong>{weather['high']}°C</strong> | {self.env._("Low")}: <strong>{weather['low']}°C</strong>{precip_text}
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
            record_html = (
                f'<br/><span style="font-size:12px;color:{COLORS["text_gray"]};">'
                f'{escape(act["record_name"])}</span>'
            ) if act.get("record_name") else ""
            rows += f"""
                <tr>
                    <td style="padding:12px;border-bottom:1px solid {COLORS['border']};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;">
                        <a href="{escape(act['link'])}" style="color:{COLORS['accent']};text-decoration:none;font-weight:500;">
                            {escape(act['summary'])}
                        </a>
                        {record_html}
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
                                {self.env._("Activity")}
                            </th>
                            <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;width:80px;">
                                {self.env._("Due")}
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
                    jour = format_date(self.env, task["deadline"], date_format="EEE")  # Abbreviated day
                    deadline_str = f"{jour} {task['deadline'].strftime('%d/%m')}"
                else:
                    deadline_str = task["deadline"].strftime("%d/%m")
            else:
                deadline_str = "—"
            subtask_indicator = f'<span style="color:#6B7280;font-size:11px;"> ({self.env._("subtask")})</span>' if task["is_subtask"] else ""
            priority_icon = '<span style="color:#dc3545;">*</span> ' if task["priority"] == "1" else ""

            rows += f"""
                <tr>
                    <td style="padding:12px;border-bottom:1px solid {COLORS['border']};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;">
                        {priority_icon}<a href="{escape(task['link'])}" style="color:{COLORS['accent']};text-decoration:none;font-weight:500;">
                            {escape(task['name'] or '')}
                        </a>{subtask_indicator}
                        <br/>
                        <span style="font-size:12px;color:{COLORS['text_gray']};">
                            {escape(task['project'] or '')}
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
                                {self.env._("Task")}
                            </th>
                            <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;width:80px;">
                                {self.env._("Due")}
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
        """

    def _render_folded_line(self, text, note="", link="", icon="•"):
        """One folded grey line: a count, a note, and where to go to see it.

        Same shape as the hidden-tasks line, which was already the house way
        of saying "there is more, it is not a gesture".
        """
        label = f"<strong>{text}</strong>"
        if link:
            label = (
                f'<a href="{escape(link)}" style="text-decoration:none;color:{COLORS["header"]};">'
                f'<span style="opacity:0.6;margin-right:6px;">{icon}</span>{label}</a>'
            )
        else:
            label = f'<span style="opacity:0.6;margin-right:6px;">{icon}</span>{label}'
        note_html = (
            f'<br/><span style="font-size:12px;font-style:italic;">{note}</span>'
            if note else ""
        )
        return f"""
            <div style="margin:0 0 24px 0;padding:12px 16px;background-color:#f3f4f6;border:1px solid {COLORS['border']};border-radius:8px;border-left:4px solid {COLORS['text_gray']};">
                <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:{COLORS['text_gray']};margin:0;">
                    {label}{note_html}
                </p>
            </div>
        """

    def _render_folded_activities(self, entry, is_overdue=False):
        """The folded line standing for N identical reminders of one model."""
        label = escape(entry.get("label") or entry.get("model") or "")
        count = entry.get("count") or 0
        text = (
            self.env._("%(label)s: %(count)s overdue reminder(s)",
                       label=label, count=count)
            if is_overdue else
            self.env._("%(label)s: %(count)s reminder(s)", label=label, count=count)
        )
        return self._render_folded_line(
            text,
            note=self.env._("Recurring reminders, read as a batch."),
            link=entry.get("link") or "",
            icon="🔁",
        )

    def _render_waiting_tasks_summary(self, total, user):
        """The folded line standing for the tasks waiting on somebody else.

        🔴 The link filters on the RECIPIENT, not on `env.uid`: the digest is
        sent by the scheduled job, so `env.uid` is its own user. The count said
        the recipient's tasks while the link opened somebody else's list.
        """
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        link = self._digest_action_url(
            base_url, "daily_todo_digest.action_digest_waiting_tasks")
        return self._render_folded_line(
            self.env._("%s task(s) waiting on someone else", total),
            note=self.env._("Left out of the day: nothing to do until they move."),
            link=link,
            icon="⏳",
        )

    def _render_flux_section(self, flux):
        """Watched subjects: BF or a client named in the news."""
        items = flux.get("items") or []
        color = COLORS["accent_on_white"]
        rows_html = ""
        for item in items:
            meta_bits = [b for b in (item.get("emetteur"), item.get("motifs")) if b]
            meta = " · ".join(escape(b) for b in meta_bits)
            title = escape(item.get("titre") or "")
            link = (item.get("lien") or "").strip()
            title_html = (
                f'<a href="{escape(link)}" style="color:{COLORS["accent"]};text-decoration:none;font-weight:500;">{title}</a>'
                if link.lower().startswith(("https://", "http://")) else title
            )
            rows_html += f"""
                <tr>
                    <td style="padding:12px;border-bottom:1px solid {COLORS['border']};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;">
                        {title_html}
                        {f'<br/><span style="font-size:12px;color:{COLORS["text_gray"]};">{meta}</span>' if meta else ''}
                    </td>
                </tr>
            """
        more_html = ""
        if flux.get("more"):
            more_html = f"""
                <tr>
                    <td style="padding:10px 12px;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-style:italic;color:{COLORS['text_gray']};">
                        {self.env._("Older items are in the Flux app.")}
                    </td>
                </tr>
            """
        return f"""
            <div style="margin:0 0 24px 0;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;">
                    <tr>
                        <td style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:16px;font-weight:600;color:{COLORS['header']};">
                            📰 {self.env._("Watched subjects")}
                        </td>
                        <td align="right">
                            <span style="display:inline-block;background-color:#e8f6fd;color:{color};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;padding:4px 10px;border-radius:12px;">
                                {len(items)}
                            </span>
                        </td>
                    </tr>
                </table>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid {COLORS['border']};border-radius:8px;overflow:hidden;">
                    <thead>
                        <tr style="background-color:{color};">
                            <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;">
                                {self.env._("In the news since your last digest")}
                            </th>
                        </tr>
                    </thead>
                    <tbody>{rows_html}{more_html}</tbody>
                </table>
            </div>
        """

    def _render_hidden_tasks_summary(self, hidden_overdue, hidden_today):
        """Render a summary of tasks hidden from pipeline (display_in_project=False)."""
        total = hidden_overdue + hidden_today
        parts = []
        if hidden_overdue > 0:
            parts.append(self.env._("%s overdue", hidden_overdue))
        if hidden_today > 0:
            parts.append(self.env._("%s today", hidden_today))
        detail = self.env._(" and ").join(parts)

        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        hidden_tasks_url = self._digest_action_url(
            base_url, "daily_todo_digest.action_digest_hidden_tasks")

        return f"""
            <div style="margin:0 0 24px 0;padding:12px 16px;background-color:#f3f4f6;border:1px solid {COLORS['border']};border-radius:8px;border-left:4px solid {COLORS['text_gray']};">
                <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:{COLORS['text_gray']};margin:0;">
                    <a href="{hidden_tasks_url}" style="text-decoration:none;color:{COLORS['header']};">
                        <span style="opacity:0.6;margin-right:6px;">👁</span>
                        <strong>{self.env._("%s hidden task(s)", total)}</strong>
                    </a>
                    <span style="font-size:12px;"> ({detail})</span><br/>
                    <span style="font-size:12px;font-style:italic;">{self.env._('Subtasks with "Show in project" turned off.')}</span>
                </p>
            </div>
        """

    def _render_week_preview(self, week_data):
        """Render a compact 7-day preview showing task/activity counts per day."""
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")

        # ⚠️ La case compte des tâches ET des activités : aucune action ne peut
        # porter deux modèles, donc le lien ouvre les tâches datées de la
        # personne, et son infobulle le dit. Le détail du jour, lui, est dans
        # les sections au-dessus.
        day_url = self._digest_action_url(
            base_url, "daily_todo_digest.action_digest_deadline_tasks")
        day_title = self.env._("My tasks with a deadline")

        # Build day cells
        day_cells = ""
        for day in week_data:
            total = day["total"]

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

            # Make the cell clickable if there are items
            if total > 0:
                count_html = f"""
                    <a href="{day_url}" title="{day_title}" style="text-decoration:none;">
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
                    📅 {self.env._("Next 7 days at a glance")}
                </p>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                        {day_cells}
                    </tr>
                </table>
                <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:11px;color:{COLORS['text_gray']};margin:12px 0 0 0;text-align:center;">
                    {self.env._("Tasks + activities per day (click to see)")}
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

        env = self.env

        def _row(r):
            dt = r["date"]
            if dt:
                if dt.tzinfo is not None:
                    dt = dt.astimezone(pytz.UTC).replace(tzinfo=None)
                date_str = format_datetime(env, dt, tz=tz_name, dt_format="EEE d/MM, HH:mm")
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
                        <a href="{url}" style="color:{COLORS['accent']};text-decoration:none;font-weight:500;">{escape(r['name'] or env._('(no name)'))}</a>
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
                                    {env._("Meeting")}
                                </th>
                                <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;width:120px;">
                                    {env._("Date")}
                                </th>
                            </tr>
                        </thead>
                        <tbody>{rows_html}</tbody>
                    </table>
                </div>
            """

        odj_block = _block("📋 " + env._("Agendas to prepare (next 7 days)"), odj_rows, COLORS["accent_on_white"], "#e8f6fd")
        cr_block = _block("📝 " + env._("Minutes to complete"), cr_rows, COLORS["red"], "#f8d7da")
        return f'<div style="margin:0 0 24px 0;">{odj_block}{cr_block}</div>'

    @staticmethod
    def _event_join_url(event):
        """The event's call or location, when it is a web address; empty otherwise."""
        for candidate in (event.get("videocall"), event.get("location")):
            candidate = (candidate or "").strip()
            if candidate.lower().startswith(("https://", "http://")) and not any(c.isspace() for c in candidate):
                return candidate
        return ""

    def _tz_city(self, tz_name):
        """City label of a time zone ("Montréal"), in the house wording when bf_timezone is there."""
        BfTimezone = self.env.get("bf.timezone")
        if BfTimezone is not None:
            return BfTimezone.tz_city(tz_name)
        return tz_name.rsplit("/", 1)[-1].replace("_", " ")

    def _render_calendar_section(self, events, user, day):
        """Render the events of the recipient's day.

        Times read in the recipient's time zone. When the recipient set a
        secondary time zone for their calendar (web_calendar_secondary_timezone),
        a second line gives the same times there.
        """
        env = self.env
        base_url = (env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")
        tz_name = user.tz or DEFAULT_TZ
        second_tz = getattr(user, "secondary_tz", False) or False
        if second_tz == tz_name:
            second_tz = False

        def _local_date(dt, tz):
            return pytz.UTC.localize(dt).astimezone(pytz.timezone(tz)).date()

        def _time_range(start, stop, tz):
            # The day is named only when it is not the recipient's day: an event
            # that began yesterday, ends tomorrow, or falls on another date in
            # the secondary time zone.
            start_date, stop_date = _local_date(start, tz), _local_date(stop, tz)
            start_str = format_datetime(env, start, tz=tz, dt_format="HH:mm")
            stop_str = format_datetime(env, stop, tz=tz, dt_format="HH:mm")
            if start_date != day:
                start_str = f"{format_date(env, start_date, date_format='EEE')} {start_str}"
            if stop_date != start_date:
                stop_str = f"{format_date(env, stop_date, date_format='EEE')} {stop_str}"
            return start_str if start == stop else f"{start_str} – {stop_str}"

        rows_html = ""
        for ev in events:
            meta_bits = []
            second = ""
            if ev["allday"]:
                when = escape(env._("All day"))
                if ev["stop_date"] and ev["stop_date"] > day:
                    meta_bits.append(escape(env._(
                        "until %s", format_date(env, ev["stop_date"], date_format="EEE d/MM")
                    )))
            else:
                when = escape(_time_range(ev["start"], ev["stop"], tz_name))
                if second_tz:
                    second = escape(f"{_time_range(ev['start'], ev['stop'], second_tz)} {self._tz_city(second_tz)}")
            join_url = self._event_join_url(ev)
            if ev["location"] and ev["location"].strip() != join_url:
                meta_bits.append(escape(ev["location"]))
            # A call field that is not a web address is still what the person wrote.
            videocall = (ev["videocall"] or "").strip()
            if videocall and videocall != join_url and videocall != (ev["location"] or "").strip():
                meta_bits.append(escape(videocall))
            if join_url:
                meta_bits.append(
                    f'<a href="{escape(join_url)}" style="color:{COLORS["accent"]};text-decoration:none;">'
                    f'{escape(env._("Join"))}</a>'
                )
            meta = " · ".join(str(b) for b in meta_bits)
            second_html = (
                f'<br/><span style="font-size:11px;color:{COLORS["text_gray"]};">{second}</span>'
                if second else ""
            )
            rows_html += f"""
                <tr>
                    <td style="padding:12px;border-bottom:1px solid {COLORS['border']};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;">
                        <a href="{base_url}/odoo/calendar/{ev['id']}" style="color:{COLORS['accent']};text-decoration:none;font-weight:500;">{escape(ev['name'] or env._('(no name)'))}</a>
                        {f'<br/><span style="font-size:12px;color:{COLORS["text_gray"]};">{meta}</span>' if meta else ''}
                    </td>
                    <td style="padding:12px;border-bottom:1px solid {COLORS['border']};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:{COLORS['text_dark']};white-space:nowrap;">
                        {when}{second_html}
                    </td>
                </tr>
            """

        color = COLORS["accent_on_white"]
        return f"""
            <div style="margin:0 0 24px 0;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;">
                    <tr>
                        <td style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:16px;font-weight:600;color:{COLORS['header']};">
                            🗓️ {env._("Today's calendar")}
                        </td>
                        <td align="right">
                            <span style="display:inline-block;background-color:#e8f6fd;color:{color};font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;padding:4px 10px;border-radius:12px;">
                                {len(events)}
                            </span>
                        </td>
                    </tr>
                </table>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid {COLORS['border']};border-radius:8px;overflow:hidden;">
                    <thead>
                        <tr style="background-color:{color};">
                            <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;">
                                {env._("Event")}
                            </th>
                            <th style="padding:10px 12px;text-align:left;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;font-weight:600;color:{COLORS['white']};text-transform:uppercase;width:140px;">
                                {env._("Time")}
                            </th>
                        </tr>
                    </thead>
                    <tbody>{rows_html}</tbody>
                </table>
            </div>
        """

    def _render_quote_section(self, quote):
        """Render the inspirational quote section."""
        return f"""
            <div style="margin:32px 0 0 0;padding:20px;background-color:#f9fafb;border-left:4px solid {COLORS['accent']};border-radius:0 8px 8px 0;">
                <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:15px;font-style:italic;color:{COLORS['text_dark']};margin:0 0 8px 0;line-height:1.5;">
                    "{escape(quote['quote'] or '')}"
                </p>
                <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:{COLORS['text_gray']};margin:0;">
                    {escape(quote['author'] or '')}
                </p>
            </div>
        """

    def _wrap_email(self, title, content, preheader="", company=None):
        """Wrap content in the recipient company's branded email template.

        The logo, name and address are the company's own: they used to be Blue
        Fox's, hard-coded, for every tenant and every recipient.
        """
        company = company or self.env.company
        base_url = (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")
        logo_url = f"{base_url}/logo.png?company={company.id}"
        site_url = company.website or base_url
        company_name = escape(company.name or "")
        company_email = escape(company.email or "")
        contact_html = (f'<a href="mailto:{company_email}" style="color:{COLORS["accent"]};text-decoration:none;">'
                        f'{company_email}</a>') if company.email else ""
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
                                        <a href="{site_url}" style="text-decoration:none;">
                                            <img src="{logo_url}" alt="{company_name}" height="48" style="display:block;border:0;height:48px;width:auto;">
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
                                        <strong style="color:{COLORS['header']};">{company_name}</strong>
                                    </td>
                                    <td align="right" style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;color:#9CA3AF;">
                                        {contact_html}
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
