"""Historique de propriété d'une fraction.

Une fraction change de mains, et peut être détenue en indivision par
plusieurs personnes. Le registre du syndicat doit refléter qui est
copropriétaire à un moment donné (art. 1070 C.c.Q.), pas seulement
qui l'est aujourd'hui.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfPropertyOwnership(models.Model):
    _name = "bf.property.ownership"
    _description = "Propriété d'une fraction"
    _order = "unit_id, date_start desc, id desc"

    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction",
        required=True,
        ondelete="cascade",
        index=True,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Copropriétaire", required=True, index=True
    )
    syndicat_id = fields.Many2one(
        related="unit_id.syndicat_id", store=True, string="Syndicat"
    )
    company_id = fields.Many2one(
        related="unit_id.company_id",
        store=True,
        string="Société",
        help="Porté pour permettre le cloisonnement multi-société : ce modèle "
             "contient des noms de copropriétaires, donc des renseignements "
             "personnels.",
    )
    share = fields.Float(
        string="Part (%)",
        default=100.0,
        digits=(16, 2),
        help="Part détenue dans la fraction. 100 pour un propriétaire unique, "
             "à répartir entre les indivisaires sinon.",
    )
    date_start = fields.Date(
        string="Depuis",
        default=fields.Date.context_today,
        help="Date d'acquisition. Vide vaut « depuis toujours ».",
    )
    date_end = fields.Date(
        string="Jusqu'au",
        help="Date de cession. Vide vaut « toujours propriétaire ».",
    )
    is_current = fields.Boolean(
        string="En cours", compute="_compute_is_current", store=True
    )
    note = fields.Char(string="Note")

    _sql_constraints = [
        (
            "share_range",
            "CHECK(share > 0 AND share <= 100)",
            "Une part doit être supérieure à 0 et au plus 100 %.",
        ),
    ]

    @api.depends("date_start", "date_end")
    def _compute_is_current(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_current = (not rec.date_start or rec.date_start <= today) and (
                not rec.date_end or rec.date_end >= today
            )

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for rec in self:
            if rec.date_start and rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(
                    _("La date de cession ne peut pas précéder la date d'acquisition.")
                )

    @api.constrains("share", "unit_id", "date_start", "date_end")
    def _check_total_share(self):
        """Les parts simultanées d'une même fraction ne peuvent pas dépasser 100 %.

        On ne compare que les périodes qui se chevauchent : deux propriétaires
        successifs à 100 % sont parfaitement normaux.
        """
        for rec in self:
            overlapping = rec.unit_id.ownership_ids.filtered(
                lambda o, r=rec: o.id != r.id and _periods_overlap(o, r)
            )
            total = rec.share + sum(overlapping.mapped("share"))
            if total > 100.0 + 0.01:
                raise ValidationError(
                    _(
                        "Les parts simultanées de la fraction %(unit)s "
                        "totalisent %(total).2f %%, ce qui dépasse 100 %%."
                    )
                    % {"unit": rec.unit_id.display_name, "total": total}
                )

    @api.model
    def _cron_refresh_current(self):
        """Rafraîchit les champs dépendants de la date du jour.

        `is_current` et les propriétaires courants d'une fraction sont stockés
        pour rester cherchables, mais ils dépendent de la date du jour et non
        d'une écriture. Sans ce passage quotidien, une propriété échue le
        31 décembre continuerait d'afficher son ancien titulaire comme
        copropriétaire actuel jusqu'à la prochaine modification du dossier.
        """
        today = fields.Date.context_today(self)
        stale = self.search(
            [
                "|",
                "&", ("is_current", "=", True),
                "|", ("date_end", "<", today), ("date_start", ">", today),
                "&", ("is_current", "=", False),
                "&", "|", ("date_start", "=", False), ("date_start", "<=", today),
                "|", ("date_end", "=", False), ("date_end", ">=", today),
            ]
        )
        if not stale:
            return 0
        stale.modified(["date_start", "date_end"])
        stale.unit_id.modified(["ownership_ids"])
        return len(stale)

    @api.depends("partner_id", "unit_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = (
                f"{rec.partner_id.name} — {rec.unit_id.display_name}"
                if rec.partner_id and rec.unit_id
                else (rec.partner_id.name or "")
            )


def _periods_overlap(a, b):
    """Deux périodes de propriété se chevauchent-elles ?

    Une borne vide est ouverte : pas de date de début vaut « depuis toujours »,
    pas de date de fin vaut « pour toujours ».
    """
    if a.date_end and b.date_start and a.date_end < b.date_start:
        return False
    if b.date_end and a.date_start and b.date_end < a.date_start:
        return False
    return True
