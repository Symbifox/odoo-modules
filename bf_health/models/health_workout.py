import base64
import math
from datetime import datetime
from xml.etree import ElementTree

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE


ACTIVITY_TYPES = [
    ("running", "Course"),
    ("cycling", "Vélo"),
    ("walking", "Marche"),
    ("hiking", "Randonnée"),
    ("swimming", "Natation"),
    ("strength", "Musculation"),
    ("yoga", "Yoga"),
    ("cardio", "Cardio"),
    ("sports", "Sport"),
    ("other", "Autre"),
]


class HealthWorkout(models.Model):
    _name = "health.workout"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _description = "Séance d'entraînement"
    _inherit = ["bf.health.note.only", "mail.thread", "mail.activity.mixin", "bf.health.dependent.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(string="Titre", required=True, default="Séance")
    activity_type = fields.Selection(
        ACTIVITY_TYPES,
        string="Type d'activité",
        required=True,
        default="running",
        tracking=True,
    )
    date = fields.Datetime(
        string="Date", required=True, default=fields.Datetime.now, tracking=True
    )
    duration_min = fields.Float(string="Durée (min)", digits=(10, 1))
    distance_km = fields.Float(string="Distance (km)", digits=(10, 2))
    elevation_m = fields.Float(string="Dénivelé + (m)", digits=(10, 1))
    calories_burned = fields.Integer(string="Calories brûlées")
    avg_heart_rate = fields.Integer(string="FC moyenne (bpm)")
    max_heart_rate = fields.Integer(string="FC max (bpm)")
    avg_pace_min_km = fields.Float(
        string="Allure (min/km)",
        compute="_compute_pace_speed",
        store=True,
        digits=(10, 2),
    )
    avg_speed_kmh = fields.Float(
        string="Vitesse moy. (km/h)",
        compute="_compute_pace_speed",
        store=True,
        digits=(10, 2),
    )
    perceived_effort = fields.Selection(
        [
            ("1", "1 — Très facile"),
            ("2", "2"),
            ("3", "3 — Facile"),
            ("4", "4"),
            ("5", "5 — Modéré"),
            ("6", "6"),
            ("7", "7 — Difficile"),
            ("8", "8"),
            ("9", "9 — Très difficile"),
            ("10", "10 — Maximal"),
        ],
        string="Effort perçu (RPE)",
    )
    notes = fields.Text(string="Notes")
    year = fields.Integer(string="Année", compute="_compute_period", store=True)
    month = fields.Integer(string="Mois", compute="_compute_period", store=True)
    # 🔴 Une trace GPX porte le point de départ,
    # souvent le domicile, et les habitudes. Le fichier n'est JAMAIS gardé :
    # champs non stockés, lus à l'enregistrement (`_inverse_gpx_file`) pour
    # remplir la distance, le dénivelé et la durée, puis jetés.
    gpx_file = fields.Binary(
        string="Fichier GPX", compute="_compute_gpx_file",
        inverse="_inverse_gpx_file", store=False)
    gpx_filename = fields.Char(
        string="Nom du fichier GPX", compute="_compute_gpx_file",
        inverse="_inverse_gpx_filename", store=False)

    @api.depends("distance_km", "duration_min")
    def _compute_pace_speed(self):
        for rec in self:
            if rec.distance_km and rec.duration_min:
                rec.avg_pace_min_km = rec.duration_min / rec.distance_km
                rec.avg_speed_kmh = rec.distance_km / (rec.duration_min / 60.0)
            else:
                rec.avg_pace_min_km = 0.0
                rec.avg_speed_kmh = 0.0

    @api.depends("date")
    def _compute_period(self):
        for rec in self:
            if rec.date:
                rec.year = rec.date.year
                rec.month = rec.date.month
            else:
                rec.year = 0
                rec.month = 0

    def _track_subtype(self, init_values):
        """Force all tracking messages to internal notes (private)."""
        self.ensure_one()
        return self.env.ref("mail.mt_note")

    def _message_auto_subscribe_followers(self, updated_values, subtype_ids):
        """Prevent automatic follower subscription for privacy."""
        return []

    # ------------------------------------------------------------------
    # GPX import (stdlib only)
    # ------------------------------------------------------------------
    def _compute_gpx_file(self):
        for rec in self:
            rec.gpx_file = False
            rec.gpx_filename = False

    def _inverse_gpx_file(self):
        """À l'enregistrement : les valeurs calculées seulement, le fichier
        brut n'est écrit nulle part (ni pièce jointe, ni colonne)."""
        for rec in self:
            if rec.gpx_file:
                rec.write(rec._gpx_valeurs(rec.gpx_file))

    def _inverse_gpx_filename(self):
        """Le nom du fichier n'est pas gardé non plus (« maison.gpx »)."""

    def _gpx_valeurs(self, gpx_b64):
        """Distance, dénivelé positif et durée d'une trace GPX en base64."""
        try:
            raw = base64.b64decode(gpx_b64)
            root = ElementTree.fromstring(raw)
        except Exception as exc:  # noqa: BLE001 - surface any parse error to the user
            raise UserError(_("Fichier GPX illisible : %s") % exc)

        points = []
        for elem in root.iter():
            if self._local_tag(elem.tag) != "trkpt":
                continue
            try:
                lat = float(elem.attrib["lat"])
                lon = float(elem.attrib["lon"])
            except (KeyError, ValueError):
                continue
            ele, tstamp = None, None
            for child in elem:
                tag = self._local_tag(child.tag)
                if tag == "ele" and child.text:
                    try:
                        ele = float(child.text)
                    except ValueError:
                        ele = None
                elif tag == "time" and child.text:
                    tstamp = child.text.strip()
            points.append((lat, lon, ele, tstamp))

        if len(points) < 2:
            raise UserError(
                _("Aucune trace exploitable dans ce fichier GPX (moins de deux points).")
            )

        distance_m, elevation_gain, prev = 0.0, 0.0, None
        for lat, lon, ele, _tstamp in points:
            if prev is not None:
                distance_m += self._haversine(prev[0], prev[1], lat, lon)
                if prev[2] is not None and ele is not None and ele > prev[2]:
                    elevation_gain += ele - prev[2]
            prev = (lat, lon, ele)

        vals = {
            "distance_km": round(distance_m / 1000.0, 2),
            "elevation_m": round(elevation_gain, 1),
        }

        times = [self._parse_gpx_time(p[3]) for p in points if p[3]]
        times = [t for t in times if t]
        if len(times) >= 2:
            delta = times[-1] - times[0]
            seconds = delta.total_seconds()
            if seconds > 0:
                vals["duration_min"] = round(seconds / 60.0, 1)
        return vals

    @staticmethod
    def _local_tag(tag):
        """Return the tag name without its XML namespace."""
        return tag.rsplit("}", 1)[-1]

    @staticmethod
    def _haversine(lat1, lon1, lat2, lon2):
        """Great-circle distance between two points, in metres."""
        radius = 6371000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = (
            math.sin(dphi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        )
        return 2 * radius * math.asin(math.sqrt(a))

    @staticmethod
    def _parse_gpx_time(value):
        """Parse an ISO-8601 GPX timestamp (handles trailing 'Z')."""
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
