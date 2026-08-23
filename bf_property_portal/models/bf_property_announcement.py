"""Annonces de l'immeuble, avec leur auditoire et leur fenêtre.

Rien ici ne vient du Code civil : une annonce n'est pas une pièce du registre,
c'est de la vie d'immeuble. Deux prudences quand même.

⚠️ **L'auditoire se règle par annonce.** « L'ascenseur est arrêté jusqu'à
mercredi » vise tout le monde ; « le vote sur la toiture est reporté » ne vise
que les copropriétaires. Un locataire n'a pas à lire les affaires de
l'assemblée, et l'inverse serait une fuite ordinaire.

⚠️ **Une annonce expire.** Sans fenêtre, un portail se remplit d'avis périmés
et personne ne lit plus rien. La date de fin est facultative, mais quand elle
est là, elle sort l'annonce du portail sans que personne n'ait à y penser.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.osv import expression

AUDIENCES = [
    ("owners", "Copropriétaires seulement"),
    ("occupants", "Occupants seulement"),
    ("all", "Copropriétaires et occupants"),
]


class BfPropertyAnnouncement(models.Model):
    _name = "bf.property.announcement"
    _description = "Annonce de l'immeuble"
    _inherit = ["mail.thread"]
    _order = "pinned desc, date_start desc, id desc"

    name = fields.Char(string="Titre", required=True, tracking=True)
    active = fields.Boolean(default=True)
    syndicat_id = fields.Many2one(
        "bf.property.syndicat",
        string="Syndicat",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        related="syndicat_id.company_id", store=True, string="Société"
    )
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        domain="[('syndicat_id', '=', syndicat_id)]",
        help="Laisser vide quand l'annonce vise toute la copropriété.",
    )
    body = fields.Html(string="Texte", sanitize=True)
    audience = fields.Selection(
        AUDIENCES, string="Auditoire", required=True, default="all", tracking=True
    )
    date_start = fields.Date(
        string="Affichée à partir du",
        required=True,
        default=fields.Date.context_today,
    )
    date_end = fields.Date(
        string="Retirée après le",
        help="Facultatif. Sans date de fin, l'annonce reste au portail "
             "jusqu'à ce qu'on l'y retire.",
    )
    pinned = fields.Boolean(
        string="Épinglée",
        help="Reste en tête de liste tant qu'elle est affichée.",
    )
    published = fields.Boolean(
        string="Publiée au portail", default=False, tracking=True, copy=False
    )
    is_visible_now = fields.Boolean(
        string="Affichée en ce moment",
        compute="_compute_is_visible_now",
        search="_search_is_visible_now",
        help="Publiée, et dans sa fenêtre d'affichage.",
    )

    @api.depends("published", "date_start", "date_end")
    def _compute_is_visible_now(self):
        """⚠️ Calculé NON stocké, et c'est voulu.

        La visibilité dépend de la date du jour, pas d'une écriture. Un champ
        stocké figerait l'état au dernier passage et il faudrait un cron pour
        le rafraîchir, comme pour `is_current` de la propriété. Ici le portail
        cherche, et une recherche sur un non stocké est IGNORÉE en silence sans
        `search=` : d'où `_search_is_visible_now` juste en dessous.
        """
        today = fields.Date.context_today(self)
        for announcement in self:
            announcement.is_visible_now = bool(
                announcement.published
                and announcement.date_start
                and announcement.date_start <= today
                and (not announcement.date_end or announcement.date_end >= today)
            )

    def _search_is_visible_now(self, operator, value):
        if operator not in ("=", "!="):
            raise ValueError(_("Opérateur non pris en charge : %s") % operator)
        today = fields.Date.context_today(self)
        # ⚠️ `normalize_domain` AVANT de nier. Le domaine d'Odoo est en notation
        # préfixe et « ! » ne porte que sur UN opérande : sans les « & »
        # explicites, `["!", A, B, "|", C, D]` se lit NOT(A) ET B ET (C OU D),
        # donc la négation ne nie que le premier critère. Le test de la fenêtre
        # a attrapé exactement ça.
        visible = expression.normalize_domain(
            [
                ("published", "=", True),
                ("date_start", "<=", today),
                "|",
                ("date_end", "=", False),
                ("date_end", ">=", today),
            ]
        )
        wants_visible = (operator == "=") == bool(value)
        return visible if wants_visible else ["!"] + visible

    @api.constrains("date_start", "date_end")
    def _check_window(self):
        for announcement in self:
            if (
                announcement.date_end
                and announcement.date_start
                and announcement.date_end < announcement.date_start
            ):
                raise ValidationError(
                    _(
                        "L'annonce « %s » serait retirée avant d'être "
                        "affichée." % announcement.name
                    )
                )

    def action_publish(self):
        for announcement in self:
            announcement.published = True
            announcement.message_post(
                body=_("Publiée au portail, auditoire : %s.")
                % dict(AUDIENCES)[announcement.audience]
            )
        return True

    def action_unpublish(self):
        for announcement in self:
            announcement.published = False
            announcement.message_post(body=_("Retirée du portail."))
        return True
