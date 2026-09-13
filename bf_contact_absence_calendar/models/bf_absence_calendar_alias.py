"""Ce qu'on écrit dans le calendrier, et qui n'est le nom de personne.

Un calendrier tenu à la main emploie les raccourcis de celui qui le tient : des
initiales, un surnom, le nom d'un dossier plutôt que d'une personne. Un sigle de trois lettres
ne ressemble à aucune fiche, et c'est normal : c'est un raccourci, pas un nom.

Le module ne le devine pas. On le lui apprend, une ligne à la fois, et il s'en
souvient. C'est la seule façon honnête de traiter un raccourci : demander, plutôt
que de rapprocher des initiales d'un nom qui commence pareil.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfAbsenceCalendarAlias(models.Model):
    _name = "bf.absence.calendar.alias"
    _description = "Raccourci du calendrier"
    _order = "label"

    source_id = fields.Many2one(
        comodel_name="bf.absence.calendar.source",
        string="Calendrier",
        required=True,
        ondelete="cascade",
        index=True,
    )
    label = fields.Char(
        string="Écrit dans le calendrier",
        required=True,
        help="Le raccourci tel qu'il apparaît, par exemple des initiales. "
             "La casse et les accents n'ont pas d'importance.",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Est ce contact",
        required=True,
        ondelete="cascade",
    )

    _sql_constraints = [
        ("bf_absence_alias_uniq", "unique(source_id, label)",
         "Ce raccourci est déjà défini pour ce calendrier."),
    ]

    @api.constrains("label")
    def _check_label(self):
        for alias in self:
            if not (alias.label or "").strip():
                raise ValidationError(_("Un raccourci vide n'apprend rien."))
