"""Les plages qu'un vendeur accepte de donner.

Un vendeur ne parle pas en horaire hebdomadaire, il parle en moments : « samedi
prochain de 13 h à 16 h, et le mercredi soir ». Les deux formes existent donc
ici, et les deux se déposent dans le même calendrier, parce que le noyau sait
déjà borner une ligne de présence à une date (`date_from` / `date_to`).
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

JOURS = [
    ("0", "Lundi"),
    ("1", "Mardi"),
    ("2", "Mercredi"),
    ("3", "Jeudi"),
    ("4", "Vendredi"),
    ("5", "Samedi"),
    ("6", "Dimanche"),
]


class BfVisitWindow(models.Model):
    _name = "bf.visit.window"
    _description = "Plage offerte pour une visite"
    _order = "date, dayofweek, hour_from"

    listing_id = fields.Many2one(
        "bf.visit.listing", string="Inscription", required=True,
        ondelete="cascade", index=True,
    )
    kind = fields.Selection(
        [("once", "Une seule date"), ("weekly", "Toutes les semaines")],
        default="once", required=True, string="Genre",
    )
    date = fields.Date(string="Date")
    dayofweek = fields.Selection(JOURS, string="Jour")
    hour_from = fields.Float(string="De", required=True, default=13.0)
    hour_to = fields.Float(string="À", required=True, default=16.0)
    source = fields.Selection(
        [
            ("seller", "Donnée par le vendeur"),
            ("broker", "Donnée par le courtier"),
            ("tenant", "Donnée par le locataire"),
        ],
        default="seller", required=True, string="Provenance",
        help="Utile quand quelqu'un demande plus tard d'où venait la plage.",
    )
    note = fields.Char(string="Note")

    @api.depends("kind", "date", "dayofweek", "hour_from", "hour_to")
    def _compute_display_name(self):
        jours = dict(JOURS)
        for rec in self:
            quand = (
                fields.Date.to_string(rec.date)
                if rec.kind == "once" and rec.date
                else jours.get(rec.dayofweek, "")
            )
            rec.display_name = "%s %s à %s" % (
                quand, _format_hour(rec.hour_from), _format_hour(rec.hour_to)
            )

    def _dayofweek(self):
        """Le jour de la semaine, quelle que soit la forme de la plage."""
        self.ensure_one()
        if self.kind == "once" and self.date:
            return str(self.date.weekday())
        return self.dayofweek or "0"

    @api.constrains("hour_from", "hour_to")
    def _check_hours(self):
        for rec in self:
            if rec.hour_to <= rec.hour_from:
                raise ValidationError(
                    _("Une plage se termine après avoir commencé (%s à %s).")
                    % (_format_hour(rec.hour_from), _format_hour(rec.hour_to))
                )

    @api.constrains("kind", "date", "dayofweek")
    def _check_when(self):
        for rec in self:
            if rec.kind == "once" and not rec.date:
                raise ValidationError(_("Une plage à date unique demande sa date."))
            if rec.kind == "weekly" and not rec.dayofweek:
                raise ValidationError(_("Une plage hebdomadaire demande son jour."))

    @api.constrains("hour_from", "hour_to", "listing_id")
    def _check_occupied_bounds(self):
        """L'article 1931 borne la visite d'un logement occupé à 9 h et 21 h.

        La règle est d'ordre public : elle ne se négocie pas avec le locataire,
        et une plage qui la déborde ne se corrige pas en silence non plus. On
        refuse, en disant pourquoi.
        """
        from .bf_visit_listing import OCCUPIED_HOUR_FROM, OCCUPIED_HOUR_TO

        for rec in self:
            if rec.listing_id.occupancy != "tenant":
                continue
            if rec.hour_from < OCCUPIED_HOUR_FROM or rec.hour_to > OCCUPIED_HOUR_TO:
                raise ValidationError(
                    _(
                        "« %(adresse)s » est occupée par un locataire : la loi "
                        "borne les visites à 9 h et 21 h (art. 1931 du Code "
                        "civil). La plage %(plage)s déborde."
                    )
                    % {
                        "adresse": rec.listing_id.name,
                        "plage": rec.display_name,
                    }
                )

    # ------------------------------------------------------------------
    # ORM : toute écriture redescend dans le calendrier
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.mapped("listing_id")._sync_windows()
        return records

    def write(self, vals):
        avant = self.mapped("listing_id")
        result = super().write(vals)
        (avant | self.mapped("listing_id"))._sync_windows()
        return result

    def unlink(self):
        listings = self.mapped("listing_id")
        result = super().unlink()
        listings.exists()._sync_windows()
        return result


def _format_hour(valeur):
    heures = int(valeur or 0)
    minutes = int(round(((valeur or 0) - heures) * 60))
    return "%dh%02d" % (heures, minutes)
