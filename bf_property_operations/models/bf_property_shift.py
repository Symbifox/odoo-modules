"""Le quart de travail, sa liste, et surtout sa passation.

🔴 **La passation est la seule partie qui vaut.** Une liste de travail sans
passation, c'est déjà ce que fait un kanban : le travail non terminé reste
simplement là, et personne n'a jamais eu à dire ce qu'il en advient. Le quart
est autre chose — il se FERME, et à sa fermeture chaque travail resté ouvert
doit avoir été confié à quelqu'un ou explicitement laissé. C'est ce refus qui
distingue les deux objets, et c'est lui qu'on éprouve.

⚠️ **Le quart ne redit pas l'état du travail.** Terminé, non effectué et sa
raison vivent sur `maintenance.request`, là où le cédule préventif les a posés. Une ligne de
quart ne porte que ce que le quart en sait : le travail était-il sur cette
liste, et ce quart-ci l'a-t-il réglé ou passé au suivant. Deux endroits où lire
le même état, c'est un endroit de trop, et c'est celui qui se désaccorde qu'on
lira — même règle qu'au pont du portail pour l'art. 1064.

⚠️ **Un quart nomme des personnes et enregistre ce qu'elles ont fait.** C'est un
traitement de renseignements personnels de SALARIÉS, et il est inscrit au
registre des traitements par le pont `bf_property_operations_privacy`. Sa base
n'est ni le consentement ni une obligation du C.c.Q. : c'est la relation
d'emploi. Un employeur qui demanderait à un concierge de consentir à ce qu'on
sache qui a fait la ronde laisserait croire qu'il peut refuser.

⚠️ **Ni paie, ni pointage, ni horaire.** Odoo a `planning` et `hr_attendance`
pour cela. Le quart dit quel travail a été fait pendant une plage, pas combien
d'heures quelqu'un a travaillé — recouvrir les deux serait le même défaut qu'un
second registre des biens.

⚠️ **Le secteur, c'est l'équipe.** Le cadrage demandait « un immeuble ou un
secteur ». Le secteur existe déjà : c'est l'ensemble des immeubles dont une
équipe répond. Un quart sans immeuble couvre donc tout le secteur de son
équipe, et un quart avec immeuble s'y restreint. Aucun modèle de secteur à
écrire.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression

SHIFT_STATES = [
    ("draft", "Préparé"),
    ("open", "En cours"),
    ("closed", "Fermé"),
]

LINE_STATES = [
    ("todo", "À faire"),
    ("settled", "Réglé pendant ce quart"),
    ("passed_on", "Passé au quart suivant"),
    ("dropped", "Retiré de la liste"),
]


class BfPropertyShift(models.Model):
    _name = "bf.property.shift"
    _description = "Quart de travail"
    _inherit = ["mail.thread"]
    _order = "date_start desc, id desc"
    _rec_names_search = ["maintenance_team_id.name", "building_id.name"]

    # ⚠️ Rendu à la lecture, jamais stocké : « tout le secteur » se
    # figeait dans la langue de qui avait créé le quart, et l'heure de début
    # dans SON fuseau. Le nom se cherche par ce qu'il montre, voir `_search_name`.
    name = fields.Char(
        string="Quart", compute="_compute_name", search="_search_name",
        help="Composé de l'équipe, du secteur et de la plage : un quart ne se "
             "nomme pas à la main, il se reconnaît.",
    )
    date_start = fields.Datetime(string="Début", required=True, tracking=True)
    date_stop = fields.Datetime(string="Fin", required=True, tracking=True)
    maintenance_team_id = fields.Many2one(
        "maintenance.team",
        string="Équipe",
        required=True,
        index=True,
        tracking=True,
        help="L'équipe qui tient ce quart. Sans immeuble précisé, le quart "
             "couvre tous les immeubles dont elle répond.",
    )
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        index=True,
        tracking=True,
        help="Laisser vide pour un quart qui couvre tout le secteur de "
             "l'équipe. Le secteur n'est pas un modèle : c'est l'ensemble des "
             "immeubles que l'équipe a déjà.",
    )
    user_ids = fields.Many2many(
        "res.users",
        string="Personnes affectées",
        domain="[('share', '=', False)]",
        help="Qui tient le quart. C'est ce champ qui fait du quart un "
             "traitement de renseignements personnels de salariés.",
    )
    company_id = fields.Many2one(
        related="maintenance_team_id.company_id", store=True, index=True,
        string="Société",
    )
    state = fields.Selection(
        SHIFT_STATES, string="État", default="draft", required=True,
        tracking=True, copy=False,
    )

    line_ids = fields.One2many(
        "bf.property.shift.line", "shift_id", string="Liste de travail",
        copy=False,
    )
    line_count = fields.Integer(compute="_compute_counts", string="Travaux")
    open_line_count = fields.Integer(
        compute="_compute_counts", string="Restant à régler"
    )
    planned_duration = fields.Float(
        compute="_compute_counts",
        string="Durée prévue",
        help="La somme des durées prévues des travaux de la liste. C'est ce "
             "qui permet de voir qu'un quart de huit heures en porte douze.",
    )

    # ── Qui, sur ce quart, ne recevra rien ──
    #
    # 🔴 Le défaut de classe à chasser : une liste qui
    # existe et un téléphone qui reste muet. Un compte en mode courriel ne
    # reçoit AUCUNE poussée à l'ouverture d'un travail cédulé — mesuré : zéro
    # — et rien, nulle part, ne le disait. Le module ne le corrige pas
    # d'autorité : le mode de notification est un réglage personnel, et il se
    # DEMANDE. Il le nomme, ce qui suffit à ce que la question se pose.
    silent_user_ids = fields.Many2many(
        "res.users",
        string="Ne recevront pas d'avis",
        compute="_compute_silent_user_ids",
        help="Les personnes affectées dont le compte est en mode courriel. "
             "Elles verront la liste en ouvrant Odoo, et leur téléphone ne "
             "sonnera pas.",
    )
    silent_notice = fields.Char(
        string="Avis muets", compute="_compute_silent_user_ids",
        help="La phrase que porte l'écran du quart. Vide quand tout le monde "
             "est joignable.",
    )

    # ── La passation ──
    previous_shift_id = fields.Many2one(
        "bf.property.shift",
        string="Quart précédent",
        readonly=True,
        copy=False,
        help="Le quart dont celui-ci a reçu la passation.",
    )
    next_shift_id = fields.Many2one(
        "bf.property.shift",
        string="Quart suivant",
        readonly=True,
        copy=False,
        help="Le quart à qui la passation a été faite.",
    )
    handover_note = fields.Text(
        string="Ce qui passe au quart suivant",
        copy=False,
        tracking=True,
        help="Ce que le quart suivant doit savoir et que les enregistrements "
             "ne disent pas : le local qu'on n'a pas pu ouvrir, le locataire "
             "à rappeler, la pièce commandée qui arrive demain.",
    )
    closed_by_user_id = fields.Many2one(
        "res.users", string="Fermé par", readonly=True, copy=False
    )
    closed_date = fields.Datetime(string="Fermé le", readonly=True, copy=False)

    _sql_constraints = [
        (
            "shift_dates",
            "CHECK(date_stop > date_start)",
            "Un quart se termine après avoir commencé.",
        ),
    ]

    # ── Ce qui se lit ──

    @api.depends("maintenance_team_id", "building_id", "date_start")
    @api.depends_context("lang", "tz")
    def _compute_name(self):
        for shift in self:
            secteur = shift.building_id.display_name or _("tout le secteur")
            date = fields.Datetime.context_timestamp(
                shift, shift.date_start
            ).strftime("%Y-%m-%d %H:%M") if shift.date_start else ""
            shift.name = f"{shift.maintenance_team_id.display_name or ''} · {secteur} · {date}".strip(" ·")

    def _search_name(self, operator, value):
        """L'équipe et l'immeuble : les deux parties du nom qui ne dépendent ni
        de la langue ni du fuseau de la personne qui cherche."""
        parts = [
            [("maintenance_team_id.name", operator, value)],
            [("building_id.name", operator, value)],
        ]
        if operator in expression.NEGATIVE_TERM_OPERATORS:
            return expression.AND(parts)
        return expression.OR(parts)

    @api.depends("line_ids.state", "line_ids.request_id.duration")
    def _compute_counts(self):
        for shift in self:
            lines = shift.line_ids
            shift.line_count = len(lines)
            shift.open_line_count = len(lines.filtered(lambda l: l.state == "todo"))
            shift.planned_duration = sum(lines.mapped("request_id.duration"))

    @api.depends("user_ids", "user_ids.notification_type")
    def _compute_silent_user_ids(self):
        """Nommer les comptes muets, sans jamais les corriger.

        ⚠️ **Aucun `sudo` ici, et c'est mesuré, pas supposé.** Le premier jet en
        portait un, au motif que `notification_type` serait réservé à son propre
        compte par `SELF_READABLE_FIELDS`. C'est faux : cette liste ÉLARGIT ce
        qu'on lit sur soi, elle ne restreint pas ce qu'on lit sur autrui, et
        l'ACL de `res.users` accorde la lecture à tout interne. La mutation qui
        retirait le `sudo` n'a rien cassé — le harnais a dit vrai, et c'est le
        commentaire qui mentait.

        Corollaire pour la vie privée : l'écran ne divulgue rien qu'un interne
        ne puisse déjà lire, et il le borne à ce que le quart nomme déjà — les
        personnes qui y sont affectées.
        """
        for shift in self:
            silent = shift.user_ids.filtered(
                lambda user: user.notification_type == "email"
            )
            shift.silent_user_ids = silent
            if not silent:
                shift.silent_notice = False
                continue
            shift.silent_notice = _(
                "%(names)s : le compte est en mode courriel. La liste s'affiche "
                "dans Odoo, mais aucun avis n'est poussé au téléphone. Le mode "
                "de notification est un réglage personnel : il se change dans "
                "les préférences du compte, et cela se demande à la personne.",
                names=", ".join(silent.mapped("name")),
            )

    @api.constrains("building_id", "maintenance_team_id")
    def _check_building_is_in_the_sector(self):
        for shift in self:
            building = shift.building_id
            if not building:
                continue
            # sudo : un concierge tient un quart sans avoir la lecture de la
            # structure de copropriété. Un contrôle de cohérence ne doit pas se
            # muer en refus d'accès sur un quart qu'il a le droit d'ouvrir.
            team = building.sudo().bf_maintenance_team_id
            if team and team != shift.maintenance_team_id:
                raise ValidationError(
                    _(
                        "L'immeuble %(building)s répond de l'équipe "
                        "%(team)s, et ce quart est tenu par %(other)s.",
                        building=building.display_name,
                        team=team.display_name,
                        other=shift.maintenance_team_id.display_name,
                    )
                )

    # ── Composer la liste ──

    def _work_domain(self):
        """Le travail que ce quart a vocation à prendre.

        Les demandes en cours et le préventif échu, sur le secteur de l'équipe
        ou sur l'immeuble si le quart s'y restreint. Les rondes régulières sont
        du préventif cédulé : elles entrent par le même chemin, ce qui évite un
        troisième vocabulaire pour la même chose.
        """
        self.ensure_one()
        domain = [
            ("stage_id.done", "=", False),
            ("archive", "=", False),
            ("request_date", "<=", fields.Date.to_date(self.date_stop)),
        ]
        if self.building_id:
            domain.append(("bf_building_id", "=", self.building_id.id))
        else:
            # sudo : lire les immeubles de l'équipe, pas les montrer.
            buildings = self.maintenance_team_id.sudo().bf_building_ids
            domain.append(("bf_building_id", "in", buildings.ids))
        return domain

    def action_compose(self):
        """Composer la liste au début du quart.

        ⚠️ Elle n'efface rien. Recomposer un quart déjà commencé AJOUTE ce qui
        est apparu depuis ; retirer un travail de la liste est un geste, pas un
        effet de bord d'une recomposition.
        """
        Line = self.env["bf.property.shift.line"]
        created = Line
        for shift in self:
            if shift.state == "closed":
                raise UserError(
                    _(
                        "Le quart « %(shift)s » est fermé. On ne compose pas "
                        "la liste d'un quart passé.",
                        shift=shift.display_name,
                    )
                )
            works = self.env["maintenance.request"].search(shift._work_domain())
            known = shift.line_ids.mapped("request_id")
            vals = [
                {"shift_id": shift.id, "request_id": work.id}
                for work in works - known
            ]
            if vals:
                created |= Line.create(vals)
        return created

    def action_open(self):
        for shift in self:
            if shift.state != "draft":
                raise UserError(
                    _("Le quart « %s » n'est pas préparé.", shift.display_name)
                )
            shift.state = "open"
        return True

    # ── La fermeture, et la passation ──

    def _carried_lines(self):
        """Ce qui reste ouvert à la fermeture, donc ce qui doit être passé."""
        self.ensure_one()
        return self.line_ids.filtered(lambda line: line.state == "todo")

    def _check_handover_stays_in_the_sector(self, next_shift, carried):
        """Ce qui passe la main reste dans un secteur qui en répond.

        🔴 Sans cette garde, le module tranchait dans
        les deux sens. `_check_building_is_in_the_sector` refuse un quart posé
        sur l'immeuble d'une autre équipe, et la passation, elle, envoyait le
        travail de l'immeuble A au quart de l'équipe B sans un mot. Deux règles
        contraires sur la même idée, c'est celle qui ne se voit pas qu'on
        appliquera.

        ⚠️ Un immeuble ne répond que d'une équipe : passer à une autre équipe,
        c'est donc toujours sortir du secteur. La garde le dit en nommant
        l'immeuble plutôt qu'en comparant les équipes, parce que c'est
        l'immeuble qui porte la raison.

        ⚠️ Un travail sans immeuble n'est rattaché à aucun secteur : il passe.
        Le refuser inventerait une règle que la structure ne porte pas.
        """
        self.ensure_one()
        if next_shift.maintenance_team_id == self.maintenance_team_id:
            return
        # sudo : lire le secteur de l'équipe qui reprend, pas le montrer.
        sector = next_shift.maintenance_team_id.sudo().bf_building_ids
        outside = carried.request_id.filtered(
            lambda work: work.bf_building_id and work.bf_building_id not in sector
        )
        if outside:
            raise UserError(
                _(
                    "« %(next)s » est tenu par %(team)s, qui ne répond pas de "
                    "%(buildings)s. Un travail qui change d'équipe change de "
                    "secteur : rattachez l'immeuble à cette équipe, ou passez "
                    "la main à un quart de %(own)s.",
                    next=next_shift.display_name,
                    team=next_shift.maintenance_team_id.display_name,
                    buildings=", ".join(
                        sorted(set(outside.mapped("bf_building_id.display_name")))
                    ),
                    own=self.maintenance_team_id.display_name,
                )
            )

    def action_close(self, next_shift=None):
        """Fermer le quart, et faire la passation.

        🔴 **C'est ici que le quart cesse d'être un kanban.** Un travail resté
        ouvert ne peut pas simplement rester là : il est passé à un quart
        nommé, et le quart sortant doit dire ce que les enregistrements ne
        disent pas. Sans ces deux exigences, fermer un quart ne serait qu'un
        changement d'étiquette.
        """
        for shift in self:
            if shift.state == "closed":
                raise UserError(
                    _("Le quart « %s » est déjà fermé.", shift.display_name)
                )
            # ⚠️ Un quart jamais ouvert n'a rien eu à transmettre. `action_open`
            # gardait son sens de la marche et `action_close` ne gardait que
            # « déjà fermé » : un quart resté « Préparé » se fermait,
            # estampillé de son heure et de son auteur, sans qu'il ait eu lieu.
            if shift.state != "open":
                raise UserError(
                    _(
                        "Le quart « %s » n'a pas été ouvert. On ne ferme, ni "
                        "ne fait passer la main, à un quart qui n'a pas eu "
                        "lieu.",
                        shift.display_name,
                    )
                )
            carried = shift._carried_lines()
            if carried and not next_shift:
                raise UserError(
                    _(
                        "%(count)s travaux de « %(shift)s » ne sont pas "
                        "réglés. Nommez le quart qui les reprend, ou retirez-"
                        "les de la liste en disant pourquoi. Un quart qui se "
                        "ferme en laissant du travail sans destinataire ne "
                        "fait pas de passation : il fait un tableau.",
                        count=len(carried),
                        shift=shift.display_name,
                    )
                )
            if carried and not (shift.handover_note or "").strip():
                raise UserError(
                    _(
                        "« %(shift)s » passe %(count)s travaux au quart "
                        "suivant sans un mot. Ce qui se transmet d'un quart à "
                        "l'autre n'est pas la liste (elle se lit), c'est ce "
                        "que la liste ne dit pas.",
                        shift=shift.display_name,
                        count=len(carried),
                    )
                )
            if next_shift:
                if next_shift == shift:
                    raise UserError(
                        _("Un quart ne se passe pas la main à lui-même.")
                    )
                if next_shift.state == "closed":
                    raise UserError(
                        _(
                            "Le quart « %s » est fermé : on ne lui passe pas "
                            "de travail.",
                            next_shift.display_name,
                        )
                    )
                shift._check_handover_stays_in_the_sector(next_shift, carried)
                self.env["bf.property.shift.line"].create(
                    [
                        {"shift_id": next_shift.id, "request_id": line.request_id.id}
                        for line in carried
                        if line.request_id not in next_shift.line_ids.request_id
                    ]
                )
                carried.write({"state": "passed_on"})
                shift.next_shift_id = next_shift
                next_shift.previous_shift_id = shift
            shift.write(
                {
                    "state": "closed",
                    "closed_by_user_id": self.env.uid,
                    "closed_date": fields.Datetime.now(),
                }
            )
        return True

    def action_view_works(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Travaux du quart"),
            "res_model": "maintenance.request",
            "view_mode": "list,form",
            "domain": [("id", "in", self.line_ids.request_id.ids)],
        }


class BfPropertyShiftLine(models.Model):
    _name = "bf.property.shift.line"
    _description = "Travail porté par un quart"
    _order = "shift_id, sequence, id"

    sequence = fields.Integer(default=10)
    shift_id = fields.Many2one(
        "bf.property.shift", string="Quart", required=True,
        ondelete="cascade", index=True,
    )
    request_id = fields.Many2one(
        "maintenance.request",
        string="Travail",
        required=True,
        # ⚠️ `restrict` : la ligne est la trace de ce qu'un quart a porté.
        # L'effacer parce que quelqu'un supprime un billet ferait perdre la
        # passation qui s'y rattache.
        ondelete="restrict",
        index=True,
    )
    company_id = fields.Many2one(
        related="shift_id.company_id", store=True, index=True, string="Société"
    )
    state = fields.Selection(
        LINE_STATES,
        string="Sur ce quart",
        compute="_compute_state",
        store=True,
        readonly=False,
        copy=False,
        help="Ce que CE quart a fait du travail, pas l'état du travail "
             "lui-même, qui vit sur le billet.",
    )
    drop_reason = fields.Char(
        string="Retiré parce que",
        copy=False,
        help="Un travail qu'on sort de la liste du quart sort pour une "
             "raison. Elle ne remplace pas celle de l'art. 4 : celle-là dit "
             "pourquoi un entretien n'a pas été FAIT, celle-ci pourquoi il "
             "n'était pas à faire pendant ce quart-ci.",
    )

    # Ce que le billet dit de lui-même, montré ici sans être recopié.
    work_done = fields.Boolean(related="request_id.stage_id.done", string="Terminé")
    work_archived = fields.Boolean(related="request_id.archive", string="Annulé")
    work_not_done_reason = fields.Char(
        related="request_id.bf_not_done_reason", string="Non effectué, parce que"
    )

    _sql_constraints = [
        (
            "one_line_per_work_and_shift",
            "UNIQUE(shift_id, request_id)",
            "Ce travail est déjà sur la liste de ce quart.",
        ),
    ]

    @api.depends("request_id.stage_id.done", "request_id.archive")
    def _compute_state(self):
        """L'état de la ligne SUIT le billet, sauf quand une personne a
        tranché.

        ⚠️ `passed_on` et `dropped` sont des gestes : ils ne se recalculent
        pas. Un travail passé au quart suivant reste passé même s'il est
        terminé plus tard — c'est justement ce que le quart sortant voulait
        dire.
        """
        for line in self:
            if line.state in ("passed_on", "dropped"):
                continue
            work = line.request_id
            line.state = "settled" if (work.stage_id.done or work.archive) else "todo"

    @api.constrains("state", "drop_reason")
    def _check_drop_has_a_reason(self):
        for line in self:
            if line.state == "dropped" and not (line.drop_reason or "").strip():
                raise ValidationError(
                    _(
                        "« %s » sort de la liste du quart sans raison. Un "
                        "travail retiré sans motif se relit comme un travail "
                        "oublié.",
                        line.request_id.display_name,
                    )
                )

    def action_drop(self):
        """Retirer un travail de la liste du quart, avec sa raison."""
        for line in self:
            if not (line.drop_reason or "").strip():
                raise UserError(
                    _(
                        "Dites pourquoi « %s » sort de la liste de ce quart.",
                        line.request_id.display_name,
                    )
                )
        self.write({"state": "dropped"})
        return True
