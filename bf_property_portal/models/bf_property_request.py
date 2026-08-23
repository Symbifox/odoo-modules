"""Demandes d'entretien : le fil, de l'ouverture à la fermeture.

⚠️ **À ne pas confondre avec le carnet d'entretien.** Le carnet
(`bf.property.maintenance.log`, art. 1070.2 C.c.Q. et r. 8.01) est un document
réglementaire qu'un professionnel indépendant établit. Ceci est un billet
qu'un occupant ouvre parce que la porte du garage grince. Les deux se
rejoindront peut-être un jour, ils ne se ressemblent pas.

## Ce que le texte donne, et ce qu'il ne donne pas

**Art. 1039 C.c.Q.** — la collectivité des copropriétaires constitue une
personne morale « ayant pour objet la conservation de l'immeuble, l'entretien
et l'administration des parties communes, la sauvegarde des droits afférents à
l'immeuble ou à la copropriété, ainsi que toutes les opérations d'intérêt
commun ». C'est cet article qui dit ce qui est l'affaire du syndicat, et ce qui
ne l'est pas.

**Art. 1064 C.c.Q.** — trois régimes, pas deux, et le module les porte déjà au
volet financier. Chacun contribue en proportion de la valeur relative de sa
fraction ; les copropriétaires qui ont l'usage d'une partie commune à usage
restreint contribuent **seuls** aux charges liées à son entretien et à ses
réparations **courantes** ; les réparations **majeures** et le **remplacement**
suivent la règle générale, à moins que la déclaration n'en dispose autrement.

D'où la seule chose que le module calcule ici : **qui porte la dépense**, à
partir de la partie visée et de la nature des travaux. Il l'affiche avec son
article, il ne facture rien : la répartition vit au budget.

## ⚠️ Ce qui n'est PAS encodé, et pourquoi

**L'accès à une partie privative pour y exécuter des travaux**, l'avis qui doit
le précéder et l'indemnité qui répare le préjudice causé : le cahier de règles
ne porte aucune de ces dispositions, et rien ici ne sera écrit d'après un
souvenir de leur texte. Le module se contente donc de noter qu'un billet vise
une partie privative, sans rien affirmer du régime d'accès. Question à porter à
la relecture juridique.

**Le délai de réponse n'a aucune source légale.** Aucune disposition n'oblige
le syndicat à répondre à un occupant dans un nombre de jours. Le module ne
prétend donc à aucun délai légal : le syndicat inscrit l'engagement qu'il se
donne, et le module compte les jours de cet engagement-là.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression

PORTIONS = [
    ("common", "Partie commune générale"),
    ("restricted", "Partie commune à usage restreint"),
    ("private", "Partie privative"),
    ("unknown", "À déterminer"),
]
WORK_TYPES = [
    ("maintenance", "Entretien ou réparation courante"),
    ("major", "Réparation majeure ou remplacement"),
    ("unknown", "À déterminer"),
]
CATEGORIES = [
    ("plumbing", "Plomberie"),
    ("electrical", "Électricité"),
    ("heating", "Chauffage, ventilation, climatisation"),
    ("elevator", "Ascenseur"),
    ("envelope", "Toiture, fenêtres, portes"),
    ("cleaning", "Propreté"),
    ("grounds", "Terrain et stationnement"),
    ("pests", "Vermine"),
    ("security", "Sécurité et accès"),
    ("other", "Autre"),
]
STATES = [
    ("submitted", "Reçue"),
    ("acknowledged", "Prise en charge"),
    ("in_progress", "En cours"),
    ("done", "Réglée"),
    ("refused", "Hors de l'objet du syndicat"),
]


class BfPropertyRequest(models.Model):
    _name = "bf.property.request"
    _description = "Demande d'entretien"
    _inherit = ["mail.thread"]
    _order = "priority desc, date_submitted desc, id desc"

    name = fields.Char(string="Numéro", required=True, copy=False, default="Nouvelle")
    active = fields.Boolean(default=True)
    syndicat_id = fields.Many2one(
        "bf.property.syndicat",
        string="Syndicat",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="syndicat_id.company_id", store=True, string="Société"
    )
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        domain="[('syndicat_id', '=', syndicat_id)]",
        index=True,
        help="La vue du concierge se regroupe là-dessus.",
    )
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction visée",
        domain="[('syndicat_id', '=', syndicat_id)]",
        help="Laisser vide quand la demande ne vise aucune fraction en "
             "particulier : un hall, un ascenseur, le terrain.",
    )
    requester_partner_id = fields.Many2one(
        "res.partner", string="Demandeur", required=True, index=True
    )

    category = fields.Selection(
        CATEGORIES, string="Catégorie", required=True, default="other", tracking=True
    )
    portion_type = fields.Selection(
        PORTIONS,
        string="Partie visée",
        required=True,
        default="unknown",
        tracking=True,
        help="Art. 1039 C.c.Q. : l'objet du syndicat est la conservation de "
             "l'immeuble et l'entretien des parties communes.",
    )
    common_area_id = fields.Many2one(
        "bf.property.common.area",
        string="Partie commune visée",
        domain="[('building_id', '=', building_id)]",
    )
    work_type = fields.Selection(
        WORK_TYPES,
        string="Nature des travaux",
        required=True,
        default="unknown",
        tracking=True,
        help="Art. 1064 al. 1 et 2 C.c.Q. : sur une partie commune à usage "
             "restreint, l'entretien et les réparations courantes ne se "
             "répartissent pas comme les réparations majeures.",
    )
    description = fields.Text(string="Description", required=True)
    attachment_ids = fields.Many2many(
        "ir.attachment", string="Photos et pièces", copy=False
    )

    priority = fields.Selection(
        [("0", "Normale"), ("1", "Élevée")], string="Priorité", default="0"
    )
    is_safety = fields.Boolean(
        string="Sécurité des personnes ou de l'immeuble",
        tracking=True,
        help="Ce qui menace les personnes ou la conservation de l'immeuble "
             "passe devant. Art. 1039 C.c.Q. pour la conservation.",
    )

    state = fields.Selection(
        STATES, string="État", default="submitted", required=True, tracking=True
    )
    date_submitted = fields.Datetime(
        string="Reçue le", default=fields.Datetime.now, readonly=True
    )
    date_acknowledged = fields.Datetime(string="Prise en charge le", readonly=True)
    date_done = fields.Datetime(string="Réglée le", readonly=True)
    responsible_user_id = fields.Many2one(
        "res.users", string="Responsable", tracking=True
    )
    resolution = fields.Text(string="Ce qui a été fait")

    acknowledge_deadline = fields.Datetime(
        string="Engagement de prise en charge",
        compute="_compute_acknowledge_deadline",
        store=True,
        help="Aucun article n'impose de délai de réponse. Celui-ci est "
             "l'engagement que le syndicat s'est donné.",
    )
    is_overdue = fields.Boolean(
        string="Engagement dépassé",
        compute="_compute_is_overdue",
        search="_search_is_overdue",
    )
    cost_bearer = fields.Char(
        string="Qui porte la dépense",
        compute="_compute_cost_bearer",
        help="Art. 1064 C.c.Q. Lecture affichée, jamais une facture : la "
             "répartition se fait au budget.",
    )

    # ── Calculs ──

    @api.depends("date_submitted", "syndicat_id.request_acknowledge_days")
    def _compute_acknowledge_deadline(self):
        for request in self:
            days = request.syndicat_id.request_acknowledge_days
            if request.date_submitted and days:
                request.acknowledge_deadline = request.date_submitted + timedelta(
                    days=days
                )
            else:
                request.acknowledge_deadline = False

    @api.depends("acknowledge_deadline", "date_acknowledged", "state")
    def _compute_is_overdue(self):
        """⚠️ Calculé NON stocké : il dépend de l'heure, pas d'une écriture.

        Un stocké figerait l'état au dernier passage et demanderait un cron.
        Et un non stocké sans `search=` verrait son critère IGNORÉ en silence,
        d'où `_search_is_overdue`.
        """
        now = fields.Datetime.now()
        for request in self:
            if not request.acknowledge_deadline or request.state != "submitted":
                request.is_overdue = False
            else:
                request.is_overdue = request.acknowledge_deadline < now

    def _search_is_overdue(self, operator, value):
        if operator not in ("=", "!="):
            raise ValueError(_("Opérateur non pris en charge : %s") % operator)
        overdue = expression.normalize_domain(
            [
                ("state", "=", "submitted"),
                ("acknowledge_deadline", "!=", False),
                ("acknowledge_deadline", "<", fields.Datetime.now()),
            ]
        )
        wants_overdue = (operator == "=") == bool(value)
        return overdue if wants_overdue else ["!"] + overdue

    @api.depends("portion_type", "work_type")
    def _compute_cost_bearer(self):
        """Art. 1064 C.c.Q., et rien d'autre.

        ⚠️ Trois régimes, pas deux : sur une partie commune à usage restreint,
        les réparations majeures et le remplacement suivent la règle générale
        et se répartissent sur TOUTES les fractions, à moins que la déclaration
        n'en dispose autrement. Refaire l'étanchéité d'une terrasse privative
        n'est pas à la charge de ses seuls bénéficiaires.
        """
        for request in self:
            if request.portion_type == "private":
                request.cost_bearer = _(
                    "Partie privative : hors de l'objet que l'art. 1039 C.c.Q. "
                    "donne au syndicat, sauf pour ce qui touche la "
                    "conservation de l'immeuble."
                )
            elif request.portion_type == "common":
                request.cost_bearer = _(
                    "Toutes les fractions, en proportion de leur valeur "
                    "relative (art. 1064 al. 1 C.c.Q.)."
                )
            elif request.portion_type == "restricted":
                if request.work_type == "maintenance":
                    request.cost_bearer = _(
                        "Les seuls copropriétaires qui ont l'usage de cette "
                        "partie (art. 1064 al. 1 in fine C.c.Q.)."
                    )
                elif request.work_type == "major":
                    request.cost_bearer = _(
                        "Toutes les fractions : sur une partie commune à usage "
                        "restreint, les réparations majeures et le "
                        "remplacement suivent la règle générale, à moins que "
                        "la déclaration n'en dispose autrement (art. 1064 "
                        "al. 2 C.c.Q.)."
                    )
                else:
                    request.cost_bearer = _(
                        "À déterminer : sur une partie commune à usage "
                        "restreint, la nature des travaux change qui paie "
                        "(art. 1064 C.c.Q.)."
                    )
            else:
                request.cost_bearer = _(
                    "À déterminer : la partie visée n'est pas encore établie."
                )

    # ── Gardes ──

    @api.constrains("unit_id", "building_id", "syndicat_id", "common_area_id")
    def _check_belongs_together(self):
        """Un billet ne mélange pas deux copropriétés."""
        for request in self:
            if request.unit_id and request.unit_id.syndicat_id != request.syndicat_id:
                raise ValidationError(
                    _("La fraction visée appartient à un autre syndicat.")
                )
            if (
                request.building_id
                and request.building_id.syndicat_id != request.syndicat_id
            ):
                raise ValidationError(
                    _("L'immeuble visé appartient à un autre syndicat.")
                )
            if (
                request.common_area_id
                and request.building_id
                and request.common_area_id.building_id != request.building_id
            ):
                raise ValidationError(
                    _("La partie commune visée est dans un autre immeuble.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        """🔴 La garde côté serveur, pas côté formulaire.

        Le portail crée ces enregistrements. Un formulaire qui poste un
        `unit_id` n'est pas une preuve : on vérifie que le demandeur a bien un
        lien courant avec la fraction et avec le syndicat qu'il nomme. Sans
        cela, n'importe quel utilisateur du portail ouvrirait un billet chez le
        voisin, et le fil de ce billet lui serait ensuite lisible.
        """
        for vals in vals_list:
            if vals.get("name", "Nouvelle") == "Nouvelle":
                # ⚠️ `sudo` sur la SÉQUENCE seulement. Un utilisateur du portail
                # n'a aucun droit de lecture sur `ir.sequence` : sans cela, la
                # toute première demande déposée par un occupant échoue sur un
                # « Access Denied by ACLs ». Le reste de la création garde ses
                # droits, sans quoi la garde du demandeur deviendrait inerte.
                vals["name"] = self.env["ir.sequence"].sudo().next_by_code(
                    "bf.property.request"
                ) or _("Nouvelle")
        requests = super().create(vals_list)
        if self.env.user._is_internal():
            return requests
        for request in requests:
            request._check_requester_is_entitled()
        return requests

    def _check_requester_is_entitled(self):
        self.ensure_one()
        units = self.env["bf.property.unit"].sudo()
        audiences = units._portal_audiences_for(self.requester_partner_id)
        if self.syndicat_id.id not in audiences:
            raise UserError(
                _(
                    "Aucune fraction ne rattache %(who)s à %(syndicat)s. Une "
                    "demande d'entretien s'ouvre là où l'on habite ou l'on "
                    "possède."
                )
                % {
                    "who": self.requester_partner_id.display_name,
                    "syndicat": self.syndicat_id.display_name,
                }
            )
        if self.unit_id:
            owned, occupied = units._portal_units_for(self.requester_partner_id)
            if self.unit_id not in (owned | occupied):
                raise UserError(
                    _("La fraction %s n'est ni possédée ni occupée par le demandeur.")
                    % self.unit_id.display_name
                )

    # ── Le fil ──

    def action_acknowledge(self):
        for request in self:
            if request.state != "submitted":
                raise UserError(
                    _("« %s » a déjà été prise en charge.") % request.name
                )
            request.write(
                {"state": "acknowledged", "date_acknowledged": fields.Datetime.now()}
            )
            request.message_post(body=_("Demande prise en charge."))
        return True

    def action_start(self):
        for request in self:
            if request.state not in ("submitted", "acknowledged"):
                raise UserError(
                    _("« %s » n'est pas dans un état où les travaux commencent.")
                    % request.name
                )
            if not request.date_acknowledged:
                request.date_acknowledged = fields.Datetime.now()
            request.state = "in_progress"
            request.message_post(body=_("Travaux en cours."))
        return True

    def action_done(self):
        """Fermer un billet suppose de dire ce qui a été fait.

        Un fil qui se ferme sur rien ne vaut pas mieux qu'un fil laissé ouvert :
        c'est ce qu'on relira dans deux ans en cherchant quand la fuite a été
        réparée.
        """
        for request in self:
            if not request.resolution:
                raise UserError(
                    _(
                        "Dites ce qui a été fait pour « %s » avant de la "
                        "fermer. Le fil sert à cela."
                    )
                    % request.name
                )
            request.write({"state": "done", "date_done": fields.Datetime.now()})
            request.message_post(
                body=_("Demande réglée : %s") % request.resolution
            )
        return True

    def action_refuse(self):
        for request in self:
            if not request.resolution:
                raise UserError(
                    _(
                        "Dites pourquoi « %s » est hors de l'objet du "
                        "syndicat. Art. 1039 C.c.Q."
                    )
                    % request.name
                )
            request.write({"state": "refused", "date_done": fields.Datetime.now()})
            request.message_post(
                body=_("Hors de l'objet du syndicat : %s") % request.resolution
            )
        return True

    def action_reopen(self):
        for request in self:
            request.write(
                {"state": "submitted", "date_done": False, "date_acknowledged": False}
            )
            request.message_post(body=_("Demande rouverte."))
        return True
