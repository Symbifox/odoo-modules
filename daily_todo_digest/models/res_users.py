# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .daily_digest import SECTIONS, WEEKDAY_FIELDS

# Fields each user may read/write on their OWN profile (preferences dialog).
# The digest is the recipient's: its hour, its rhythm and its sections are
# theirs to set, and an administrator is no longer in the way.
DIGEST_SELF_FIELDS = [
    "digest_weather_city",
    "digest_weather_latitude",
    "digest_weather_longitude",
    "digest_send_hour",
    "digest_frequency",
    *WEEKDAY_FIELDS,
    *(f"digest_opt_{name}" for name in SECTIONS),
]

# Three states, and the empty one matters: it means "whatever the template
# says". A boolean could not say that.
SECTION_CHOICE = [("yes", "Yes"), ("no", "No")]


class ResUsers(models.Model):
    _inherit = "res.users"

    # Per-user weather location for the daily digest. When a city is set, the
    # digest uses THIS user's coordinates (and their `tz`) instead of the
    # shared config defaults — so a recipient elsewhere gets their own local
    # weather even if the config default points to another city.
    digest_weather_city = fields.Char(
        string="Weather city (digest)",
        help="Leave empty to use the digest's default city. Otherwise, "
             "also fill in the latitude and longitude below.",
    )
    digest_weather_latitude = fields.Float(
        string="Weather latitude (digest)",
        digits=(10, 4),
    )
    digest_weather_longitude = fields.Float(
        string="Weather longitude (digest)",
        digits=(10, 4),
    )

    # ------------------------------------------------------------- delivery
    digest_send_hour = fields.Selection(
        [(str(hour), f"{hour:02d}:00") for hour in range(24)],
        string="Digest hour",
        help="The hour your digest is sent, read in YOUR time zone. Leave "
             "empty to keep the hour of the digest template that covers you.",
    )
    digest_frequency = fields.Selection(
        [("daily", "Every day"),
         ("weekdays", "Weekdays only"),
         ("custom", "Chosen days")],
        string="Digest frequency",
        help="Leave empty to keep the template's frequency. A weekly digest "
             "is \"Chosen days\" with a single day ticked.",
    )
    digest_monday = fields.Boolean(string="Monday (digest)", default=True)
    digest_tuesday = fields.Boolean(string="Tuesday (digest)", default=True)
    digest_wednesday = fields.Boolean(string="Wednesday (digest)", default=True)
    digest_thursday = fields.Boolean(string="Thursday (digest)", default=True)
    digest_friday = fields.Boolean(string="Friday (digest)", default=True)
    digest_saturday = fields.Boolean(string="Saturday (digest)")
    digest_sunday = fields.Boolean(string="Sunday (digest)")

    # ------------------------------------------------------------- sections
    # One three-state per section. Empty = the template decides.
    digest_opt_overdue_activities = fields.Selection(
        SECTION_CHOICE, string="Overdue activities (digest)")
    digest_opt_today_activities = fields.Selection(
        SECTION_CHOICE, string="Today's activities (digest)")
    digest_opt_overdue_tasks = fields.Selection(
        SECTION_CHOICE, string="Overdue tasks (digest)")
    digest_opt_today_tasks = fields.Selection(
        SECTION_CHOICE, string="Today's tasks (digest)")
    digest_opt_upcoming_tasks = fields.Selection(
        SECTION_CHOICE, string="Upcoming tasks (digest)")
    digest_opt_week_preview = fields.Selection(
        SECTION_CHOICE, string="Week preview (digest)")
    digest_opt_meetings = fields.Selection(
        SECTION_CHOICE, string="Meetings (digest)")
    digest_opt_calendar_events = fields.Selection(
        SECTION_CHOICE, string="Calendar events (digest)")
    digest_opt_flux_subjects = fields.Selection(
        SECTION_CHOICE, string="Watched subjects (digest)")
    digest_opt_weather = fields.Selection(
        SECTION_CHOICE, string="Weather (digest)")
    digest_opt_quote = fields.Selection(
        SECTION_CHOICE, string="Inspiring quote (digest)")

    @api.constrains("digest_frequency", *WEEKDAY_FIELDS)
    def _check_digest_custom_days(self):
        """« Jours choisis » veut dire au moins un jour.

        🔴 Décocher les sept est un geste d'un clic dans la boîte des
        préférences, et le briefing ne repartait JAMAIS, sans un mot. Le refus
        arrive au moment de la bévue, là où elle se voit.
        """
        for user in self:
            if user.digest_frequency != "custom":
                continue
            if not any(user[field] for field in WEEKDAY_FIELDS):
                raise ValidationError(self.env._(
                    "Pick at least one day for the morning digest, or change "
                    "its frequency: with no day picked, it would never be sent."
                ))

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + DIGEST_SELF_FIELDS

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + DIGEST_SELF_FIELDS
