"""Carnet d'entretien de l'immeuble.

Art. 1070.2 al. 1 C.c.Q. : « Le conseil d'administration fait établir un carnet
d'entretien de l'immeuble, lequel décrit notamment les entretiens faits et à
faire. Il tient ce carnet à jour et le fait réviser périodiquement. »
(2019, c. 28, a. 38; 2024, c. 2, a. 1.)

⚠️ **Le Code ne dit aucun chiffre.** L'alinéa 2 renvoie tout au règlement : la
forme, le contenu, les modalités de tenue et de révision, et les personnes qui
peuvent l'établir et le réviser. Lire l'article seul ne renseigne sur rien. Tous
les seuils ci-dessous viennent du **Règlement établissant diverses règles en
matière de copropriété divise**, RLRQ, c. CCQ, r. 8.01, pris par le
**D. 991-2025** et en vigueur le **14 août 2025**.

Trois choses que la doctrine escamote, et que ce modèle tient séparées :

1. **Quatre ordres professionnels, pas trois.** L'art. 1 du règlement admet les
   ingénieurs, les **évaluateurs agréés**, les architectes et les technologues
   professionnels. La liste courante oublie les évaluateurs agréés.
2. **L'indépendance est la vraie contrainte.** Le par. 3° écarte le membre du
   conseil, le gérant, le copropriétaire, l'occupant, leurs conjoints, et les
   dirigeants ou employés d'une personne morale copropriétaire, occupante ou
   gestionnaire. Le gestionnaire de l'immeuble ne peut donc pas établir le
   carnet de l'immeuble qu'il gère.
3. **Le 10 ans est conditionnel.** L'art. 5 pose 5 ans par défaut, et 10 ans
   seulement si l'immeuble remplit **l'une** de trois conditions. Et le décompte
   des huit parties privatives **exclut** les accessoires, rangements et
   stationnements compris, ce qui change la réponse pour beaucoup d'immeubles.

⚠️ Le module tient le carnet et ses échéances. Il ne se prononce sur la
suffisance de rien : l'estimation de l'état, de la durée de vie utile restante
et du coût des travaux appartient à la personne qui signe.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# r. 8.01, art. 5 al. 1 et 2.
REVISION_YEARS = 5
REVISION_YEARS_SMALL = 10
# r. 8.01, art. 5 al. 2, par. 1° : « au plus 8 parties privatives, EXCLUANT
# celles qui sont accessoires à ces dernières tels les espaces de rangement et
# de stationnement ».
SMALL_BUILDING_UNITS = 8
# r. 8.01, art. 5 al. 2, par. 3°.
SMALL_BUILDING_FLOORS = 3
# r. 8.01, art. 3 : « durant minimalement les 25 prochaines années ».
PLANNING_HORIZON_YEARS = 25
# r. 8.01, art. 4 : mise à jour « minimalement une fois par année ».
UPDATE_MONTHS = 12

# r. 8.01, art. 1, par. 1°. L'ordre des évaluateurs agréés y figure, contre ce
# que répète la doctrine.
CARNET_ORDERS = [
    ("engineer", "Ordre des ingénieurs du Québec"),
    ("appraiser", "Ordre des évaluateurs agréés du Québec"),
    ("architect", "Ordre des architectes du Québec"),
    ("technologist", "Ordre des technologues professionnels du Québec"),
]


class BfPropertyMaintenanceLog(models.Model):
    _name = "bf.property.maintenance.log"
    _description = "Carnet d'entretien"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "syndicat_id, established_date desc, id desc"

    name = fields.Char(string="Carnet", required=True, tracking=True)
    syndicat_id = fields.Many2one(
        "bf.property.syndicat",
        string="Syndicat",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        domain="[('syndicat_id', '=', syndicat_id)]",
        tracking=True,
        help="Le règlement raisonne par immeuble : ses conditions de taille "
             "portent sur le bâti, pas sur le syndicat, qui peut en compter "
             "plusieurs.",
    )
    company_id = fields.Many2one(
        related="syndicat_id.company_id", store=True, string="Société"
    )

    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("established", "Établi"),
            ("superseded", "Remplacé"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
    )

    # ── Qui l'a établi (r. 8.01, art. 1) ──
    author_partner_id = fields.Many2one(
        "res.partner", string="Établi par", tracking=True
    )
    author_order = fields.Selection(
        CARNET_ORDERS, string="Ordre professionnel", tracking=True,
        help="Art. 1, par. 1° du règlement. Quatre ordres, et non trois : "
             "l'Ordre des évaluateurs agréés du Québec en fait partie.",
    )
    author_practice = fields.Boolean(
        string="Pratique immobilière attestée",
        tracking=True,
        help="Art. 1, par. 2° : les activités professionnelles de l'auteur "
             "concernent principalement la gestion, la construction, la "
             "rénovation, l'évaluation ou l'inspection immobilière.",
    )
    author_independent = fields.Boolean(
        string="Indépendance attestée",
        tracking=True,
        help="Art. 1, par. 3° : l'auteur n'est ni membre du conseil "
             "d'administration, ni gérant, copropriétaire ou occupant de "
             "l'immeuble, ni le conjoint d'une telle personne, ni actionnaire, "
             "dirigeant, administrateur ou employé d'une personne morale, "
             "société ou fiducie copropriétaire, occupante ou gestionnaire de "
             "l'immeuble. C'est la condition qui écarte le gestionnaire.",
    )
    site_declaration = fields.Boolean(
        string="Déclaration d'examen sur place",
        tracking=True,
        help="Art. 6 : l'auteur signe une déclaration attestant que les parties "
             "communes et les biens de l'art. 2 ont été examinés sur place par "
             "lui ou par une personne sous sa supervision, et qu'il a pris "
             "connaissance des renseignements du carnet.",
    )
    site_declaration_date = fields.Date(
        string="Déclaration datée du", tracking=True
    )

    # ── Dates ──
    established_date = fields.Date(
        string="Établi le", tracking=True,
        help="Date d'obtention du carnet. C'est d'elle que courent la révision "
             "et la validité.",
    )
    last_update_date = fields.Date(
        string="Dernière mise à jour", tracking=True,
        help="Art. 4 : le conseil met le carnet à jour minimalement une fois "
             "par année.",
    )
    last_revision_date = fields.Date(
        string="Dernière révision", tracking=True,
        help="Art. 5 : la révision est faite par une personne remplissant les "
             "conditions de l'art. 1. Elle est distincte de la mise à jour "
             "annuelle, que le conseil fait lui-même.",
    )

    # ── Échéances calculées ──
    revision_years = fields.Integer(
        string="Intervalle de révision (ans)",
        compute="_compute_schedule",
        store=True,
    )
    revision_rule = fields.Char(
        string="Règle de révision", compute="_compute_schedule", store=True
    )
    next_revision_date = fields.Date(
        string="Révision au plus tard le", compute="_compute_schedule", store=True
    )
    next_update_date = fields.Date(
        string="Mise à jour au plus tard le",
        compute="_compute_schedule",
        store=True,
    )
    revision_state = fields.Selection(
        [
            ("na", "Sans objet"),
            ("current", "À jour"),
            ("due", "Révision échue"),
        ],
        string="État de la révision",
        compute="_compute_schedule",
        store=True,
    )
    update_state = fields.Selection(
        [
            ("na", "Sans objet"),
            ("current", "À jour"),
            ("due", "Mise à jour annuelle échue"),
        ],
        string="État de la mise à jour",
        compute="_compute_schedule",
        store=True,
    )

    item_ids = fields.One2many(
        "bf.property.maintenance.item", "log_id", string="Biens au carnet"
    )
    item_count = fields.Integer(compute="_compute_item_count")
    planning_horizon_date = fields.Date(
        string="Horizon de planification",
        compute="_compute_schedule",
        store=True,
        help="Art. 3 : la description des réparations majeures et des "
             "remplacements porte sur minimalement les 25 prochaines années.",
    )
    note = fields.Text(string="Note")

    _sql_constraints = [
        (
            "unique_name_per_syndicat",
            "UNIQUE(syndicat_id, name)",
            "Ce syndicat porte déjà un carnet de ce nom.",
        ),
    ]

    @api.depends("item_ids")
    def _compute_item_count(self):
        for log in self:
            log.item_count = len(log.item_ids)

    @api.depends(
        "established_date",
        "last_revision_date",
        "last_update_date",
        "state",
        "building_id",
        "building_id.unit_ids.unit_type",
        "building_id.unit_ids.active",
        "building_id.floors_above_ground",
        "building_id.common_areas_in_building",
    )
    def _compute_schedule(self):
        """Art. 5 du règlement, puis art. 4 pour la mise à jour annuelle.

        ⚠️ Les trois conditions du 10 ans sont ALTERNATIVES. Une seule suffit,
        et les cumuler serait plus sévère que le texte.
        """
        today = fields.Date.context_today(self)
        for log in self:
            building = log.building_id
            years, rule = log._revision_interval(building)
            log.revision_years = years
            log.revision_rule = rule
            start = log.last_revision_date or log.established_date
            log.next_revision_date = (
                start + relativedelta(years=years) if start else False
            )
            log.planning_horizon_date = (
                log.established_date
                + relativedelta(years=PLANNING_HORIZON_YEARS)
                if log.established_date
                else False
            )
            update_from = log.last_update_date or log.established_date
            log.next_update_date = (
                update_from + relativedelta(months=UPDATE_MONTHS)
                if update_from
                else False
            )
            if log.state != "established" or not log.established_date:
                log.revision_state = "na"
                log.update_state = "na"
                continue
            log.revision_state = (
                "due"
                if log.next_revision_date and log.next_revision_date < today
                else "current"
            )
            log.update_state = (
                "due"
                if log.next_update_date and log.next_update_date < today
                else "current"
            )

    def _revision_interval(self, building):
        """Rend (années, règle citée). Art. 5 du règlement.

        Sans immeuble rattaché, on ne peut pas vérifier les conditions de
        taille : le module retient l'intervalle de cinq ans, qui est la règle,
        et dit que la dérogation n'a pas été vérifiée. Présumer le contraire
        laisserait un carnet dormir cinq ans de trop.
        """
        self.ensure_one()
        if not building:
            return REVISION_YEARS, _(
                "Art. 5 al. 1 du règlement : révision minimalement tous les "
                "5 ans. Aucun immeuble n'est rattaché, donc les trois "
                "conditions du 10 ans n'ont pas pu être vérifiées."
            )
        reasons = []
        if building.private_unit_count <= SMALL_BUILDING_UNITS:
            reasons.append(
                _(
                    "au plus %(max)d parties privatives hors accessoires "
                    "(%(count)d)"
                )
                % {"max": SMALL_BUILDING_UNITS, "count": building.private_unit_count}
            )
        if not building.common_areas_in_building:
            reasons.append(_("aucune partie commune située dans un bâtiment"))
        if (
            building.floors_above_ground
            and building.floors_above_ground <= SMALL_BUILDING_FLOORS
        ):
            reasons.append(
                _("au plus %(max)d étages entièrement hors sol (%(count)d)")
                % {
                    "max": SMALL_BUILDING_FLOORS,
                    "count": building.floors_above_ground,
                }
            )
        if reasons:
            return REVISION_YEARS_SMALL, _(
                "Art. 5 al. 2 du règlement : révision aux 10 ans, l'immeuble "
                "remplissant %(which)s. Les trois conditions sont alternatives."
            ) % {"which": ", ".join(reasons)}
        return REVISION_YEARS, _(
            "Art. 5 al. 1 du règlement : révision minimalement tous les 5 ans. "
            "Aucune des trois conditions du deuxième alinéa n'est remplie."
        )

    @api.constrains("site_declaration", "site_declaration_date")
    def _check_declaration_date(self):
        for log in self:
            if log.site_declaration and not log.site_declaration_date:
                raise ValidationError(
                    _(
                        "Art. 6 du règlement : la déclaration d'examen sur "
                        "place « doit être datée et incluse au carnet "
                        "d'entretien »."
                    )
                )

    # ── Actions ──

    def action_establish(self):
        """Porte le carnet à l'état établi, après les contrôles du règlement."""
        for log in self:
            missing = log._missing_requirements()
            if missing:
                raise UserError(
                    _(
                        "Le carnet ne peut pas être porté comme établi. Il y "
                        "manque :\n%(list)s"
                    )
                    % {"list": "\n".join("  - %s" % m for m in missing)}
                )
            log.state = "established"
            previous = self.search(
                [
                    ("syndicat_id", "=", log.syndicat_id.id),
                    ("state", "=", "established"),
                    ("id", "!=", log.id),
                ]
            )
            previous.write({"state": "superseded"})
            log.message_post(
                body=_(
                    "Carnet établi le %(date)s. %(rule)s Horizon de "
                    "planification jusqu'au %(horizon)s (art. 3 du règlement, "
                    "minimalement 25 ans)."
                )
                % {
                    "date": log.established_date,
                    "rule": log.revision_rule or "",
                    "horizon": log.planning_horizon_date,
                }
            )
        return True

    def _missing_requirements(self):
        """Ce que le règlement exige avant qu'un carnet en soit un."""
        self.ensure_one()
        missing = []
        if not self.established_date:
            missing.append(_("la date d'établissement"))
        if not self.author_partner_id:
            missing.append(_("l'auteur"))
        if not self.author_order:
            missing.append(
                _("l'ordre professionnel de l'auteur (art. 1, par. 1°)")
            )
        if not self.author_practice:
            missing.append(
                _("l'attestation de pratique immobilière (art. 1, par. 2°)")
            )
        if not self.author_independent:
            missing.append(
                _("l'attestation d'indépendance (art. 1, par. 3°)")
            )
        if not self.site_declaration:
            missing.append(_("la déclaration d'examen sur place (art. 6)"))
        if not self.item_ids:
            missing.append(
                _("l'inventaire des biens (art. 2), qui ne peut pas être vide")
            )
        return missing

    def action_record_update(self):
        """Art. 4 : la mise à jour annuelle, faite par le conseil lui-même."""
        today = fields.Date.context_today(self)
        for log in self:
            if log.state != "established":
                raise UserError(
                    _("Un carnet qui n'est pas établi ne se met pas à jour.")
                )
            log.last_update_date = today
            log.message_post(
                body=_(
                    "Mise à jour annuelle portée au %(date)s (art. 4 du "
                    "règlement). ⚠️ Les travaux requis ou prévus qui n'ont pas "
                    "été effectués doivent être mentionnés au carnet, avec "
                    "leurs raisons."
                )
                % {"date": today}
            )
        return True

    @api.model
    def _cron_refresh_schedule(self):
        """Une échéance naît du passage d'une date, pas d'une écriture.

        Même patron que les crons du socle, de la gouvernance et du volet
        financier : sans ce passage quotidien, un carnet dont la révision vient
        d'expirer se lirait « à jour » jusqu'à ce que quelqu'un le rouvre.
        """
        today = fields.Date.context_today(self)
        stale = self.search(
            [
                ("state", "=", "established"),
                "|",
                "&", ("revision_state", "=", "current"),
                ("next_revision_date", "<", today),
                "&", ("update_state", "=", "current"),
                ("next_update_date", "<", today),
            ]
        )
        if not stale:
            return 0
        stale.modified(["established_date"])
        return len(stale)
