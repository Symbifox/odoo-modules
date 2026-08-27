# -*- coding: utf-8 -*-
"""Les sources citées, avec leur date de vérification.

Une source vérifiée il y a six mois n'est pas une source vérifiée. Le champ
qui compte n'est pas l'URL, c'est ``checked_date``.
"""

from odoo import _, api, fields, models

# Un 403 sur une page canonique de fournisseur est un comportement anti-robot,
# pas un lien mort. On ne le compte pas comme une source perdue.
TOLERATED_STATUS = (200, 201, 202, 203, 204, 301, 302, 307, 308, 403, 405)


class EditorialSource(models.Model):
    _name = "bf.editorial.source"
    _description = "Source citée"
    _order = "sequence, id"

    entry_id = fields.Many2one(
        "bf.editorial.entry", string="Entrée", required=True,
        ondelete="cascade", index=True,
    )
    sequence = fields.Integer(string="Séquence", default=10)
    name = fields.Char(string="Nom", required=True)
    url = fields.Char(string="URL", required=True)
    lang_id = fields.Many2one(
        "res.lang", string="Langue",
        help="Le créneau français cite la page française quand elle existe.",
    )
    description = fields.Char(string="Description")
    http_status = fields.Integer(string="Dernier code HTTP")
    checked_date = fields.Date(string="Vérifiée le")
    is_dead = fields.Boolean(
        string="Morte", compute="_compute_is_dead", store=True,
    )
    archive_url = fields.Char(
        string="Copie archivée",
        help="Instantané Wayback à citer quand l'original a disparu.",
    )

    @api.depends("http_status", "archive_url")
    def _compute_is_dead(self):
        for source in self:
            source.is_dead = bool(
                source.http_status
                and source.http_status not in TOLERATED_STATUS
                and not source.archive_url
            )

    _sql_constraints = [
        (
            "url_not_empty",
            "CHECK (url <> '')",
            "Une source doit porter une URL.",
        ),
    ]
