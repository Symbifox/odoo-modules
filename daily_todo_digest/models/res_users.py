# -*- coding: utf-8 -*-
from odoo import fields, models

# Fields each user may read/write on their OWN profile (preferences dialog).
DIGEST_SELF_FIELDS = [
    "digest_weather_city",
    "digest_weather_latitude",
    "digest_weather_longitude",
]


class ResUsers(models.Model):
    _inherit = "res.users"

    # Per-user weather location for the daily digest. When a city is set, the
    # digest uses THIS user's coordinates (and their `tz`) instead of the
    # shared config defaults — so a recipient elsewhere gets their own local
    # weather even if the config default points to another city.
    digest_weather_city = fields.Char(
        string="Ville météo (digest)",
        help="Laisser vide pour utiliser la ville par défaut du digest. "
             "Sinon, renseignez aussi la latitude/longitude ci-dessous.",
    )
    digest_weather_latitude = fields.Float(
        string="Latitude météo (digest)",
        digits=(10, 4),
    )
    digest_weather_longitude = fields.Float(
        string="Longitude météo (digest)",
        digits=(10, 4),
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + DIGEST_SELF_FIELDS

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + DIGEST_SELF_FIELDS
