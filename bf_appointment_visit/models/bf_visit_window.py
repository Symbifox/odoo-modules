"""Les plages qu'un vendeur accepte de donner.

Un vendeur ne parle pas en horaire hebdomadaire, il parle en moments : « samedi
prochain de 13 h à 16 h, et le mercredi soir ». Les deux formes existent donc
ici, et les deux se déposent dans le même calendrier, parce que le noyau sait
déjà borner une ligne de présence à une date (`date_from` / `date_to`).
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

JOURS = [
    ("0", "Monday"),
    ("1", "Tuesday"),
    ("2", "Wednesday"),
    ("3", "Thursday"),
    ("4", "Friday"),
    ("5", "Saturday"),
    ("6", "Sunday"),
]


class BfVisitWindow(models.Model):
    _name = "bf.visit.window"
    _description = "Window offered for a showing"
    _order = "date, dayofweek, hour_from"

    listing_id = fields.Many2one(
        "bf.visit.listing", string="Listing", required=True,
        ondelete="cascade", index=True,
    )
    kind = fields.Selection(
        [("once", "One date only"), ("weekly", "Every week")],
        default="once", required=True, string="Kind",
    )
    date = fields.Date(string="Date")
    dayofweek = fields.Selection(JOURS, string="Day")
    hour_from = fields.Float(string="From", required=True, default=13.0)
    hour_to = fields.Float(string="To", required=True, default=16.0)
    source = fields.Selection(
        [
            ("seller", "Given by the seller"),
            ("broker", "Given by the broker"),
            ("tenant", "Given by the tenant"),
        ],
        default="seller", required=True, string="Source",
        help="Useful when someone asks later where the window came from.",
    )
    note = fields.Char(string="Note")

    # ⚠️ `depends_context=("lang",)` : sans lui, le cache d'un calculé NON stocké
    # ignore la langue, et la PREMIÈRE lecture gagne pour toutes les autres. Odoo
    # ne le pose pas sur le `display_name` automatique. Depuis que le libellé de
    # genre se lit par le catalogue, ça se voit : un rapport rendu dans
    # une autre langue que celle de la session recevait la valeur déjà calculée.
    display_name = fields.Char(
        compute="_compute_display_name", search="_search_display_name",
        depends_context=("lang",))

    @api.depends("kind", "date", "dayofweek", "hour_from", "hour_to")
    def _compute_display_name(self):
        # ⚠️ Le jour se lit par la LISTE DU CHAMP, pas par la table brute `JOURS` :
        # celle-ci n'est traduite nulle part, et depuis que la source du module est
        # anglaise un lecteur français y aurait lu « Saturday ». La liste
        # d'un champ, elle, passe par le catalogue.
        jours = dict(self._fields["dayofweek"]._description_selection(self.env))
        for rec in self:
            quand = (
                fields.Date.to_string(rec.date)
                if rec.kind == "once" and rec.date
                else jours.get(rec.dayofweek, "")
            )
            rec.display_name = self.env._("%s %s to %s") % (
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
                    _("A window ends after it starts (%s to %s).")
                    % (_format_hour(rec.hour_from), _format_hour(rec.hour_to))
                )

    @api.constrains("kind", "date", "dayofweek")
    def _check_when(self):
        for rec in self:
            if rec.kind == "once" and not rec.date:
                raise ValidationError(_("A single-date window needs its date."))
            if rec.kind == "weekly" and not rec.dayofweek:
                raise ValidationError(_("A weekly window needs its day."))

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
                        "\"%(adresse)s\" is occupied by a tenant: the law "
                        "holds showings between 9 a.m. and 9 p.m. (art. "
                        "1931 of the Civil Code). Window %(plage)s goes "
                        "beyond that."
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
