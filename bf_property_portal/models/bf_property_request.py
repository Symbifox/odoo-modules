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

from odoo.addons.bf_property_core.models.mail_template import bf_mail_layout

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
    _inherit = ["mail.thread", "bf.property.organisation.authority"]
    _order = "priority desc, date_submitted desc, id desc"

    # Ce que l'occupant dépose. L'état, la date de dépôt, la résolution et la
    # personne responsable relèvent du syndicat.
    _portal_creatable_fields = (
        "organisation_id", "building_id", "unit_id", "requester_partner_id",
        "category", "portion_type", "description", "is_safety", "common_area_id",
    )

    name = fields.Char(string="Numéro", required=True, copy=False, default="Nouvelle")
    active = fields.Boolean(default=True)
    organisation_id = fields.Many2one(
        "bf.property.organisation",
        string="Syndicat",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="organisation_id.company_id", store=True, string="Société"
    )
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        domain="[('organisation_id', '=', organisation_id)]",
        index=True,
        help="La vue du concierge se regroupe là-dessus.",
    )
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction visée",
        domain="[('organisation_id', '=', organisation_id)]",
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

    # ── Courriel ──

    def message_post(self, **kwargs):
        """Le courriel part dans la mise en page de marque quand elle est là.

        Sans cela, la réponse du bureau à un résident partait
        dans l'habillage d'Odoo, nue, pendant que les factures et les banques
        d'heures portaient celui de la société. Un envoi qui choisit lui-même
        sa mise en page la garde.
        """
        if not kwargs.get("email_layout_xmlid"):
            kwargs["email_layout_xmlid"] = bf_mail_layout(self.env)
        return super().message_post(**kwargs)

    # ── Calculs ──

    @api.depends("date_submitted", "organisation_id.request_acknowledge_days")
    def _compute_acknowledge_deadline(self):
        for request in self:
            days = request.organisation_id.request_acknowledge_days
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

    @api.constrains("unit_id", "building_id", "organisation_id", "common_area_id")
    def _check_belongs_together(self):
        """Un billet ne mélange pas deux copropriétés."""
        for request in self:
            if request.unit_id and request.unit_id.organisation_id != request.organisation_id:
                raise ValidationError(
                    _("La fraction visée appartient à un autre syndicat.")
                )
            if (
                request.building_id
                and request.building_id.organisation_id != request.organisation_id
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
            # Sans immeuble, la partie commune doit au moins rester dans le
            # syndicat : le contrôle ci-dessus ne jouait que si les deux étaient
            # posés.
            if (
                request.common_area_id
                and request.common_area_id.sudo().building_id.organisation_id
                != request.organisation_id
            ):
                raise ValidationError(
                    _("La partie commune visée appartient à un autre syndicat.")
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
        self._ensure_portal_create_scope(vals_list)
        for vals in vals_list:
            if vals.get("name", "Nouvelle") == "Nouvelle":
                # ⚠️ `sudo` sur la SÉQUENCE seulement. Un utilisateur du portail
                # n'a aucun droit de lecture sur `ir.sequence` : sans cela, la
                # toute première demande déposée par un occupant échoue sur un
                # « Access Denied by ACLs ». Le reste de la création garde ses
                # droits, sans quoi la garde du demandeur deviendrait inerte.
                vals["name"] = self.env["ir.sequence"].sudo().with_context(
                    ir_sequence_date=False).next_by_code(
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
        if self.organisation_id.id not in audiences:
            raise UserError(
                _(
                    "Aucune fraction ne rattache %(who)s à %(syndicat)s. Une "
                    "demande d'entretien s'ouvre là où l'on habite ou l'on "
                    "possède."
                )
                % {
                    "who": self.requester_partner_id.display_name,
                    "syndicat": self.organisation_id.display_name,
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
        self._ensure_organisation_decides(_("Prendre en charge une demande"))
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
        self._ensure_organisation_decides(_("Démarrer les travaux"))
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
        self._ensure_organisation_decides(_("Régler une demande"))
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
            request._bf_tell_requester(_("Demande réglée : %s") % request.resolution)
        return True

    def action_refuse(self):
        self._ensure_organisation_decides(_("Refuser une demande"))
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
            request._bf_tell_requester(
                _("Hors de l'objet du syndicat : %s") % request.resolution
            )
        return True

    def _bf_tell_requester(self, body):
        """La décision, au fil ET à la personne qui a fait la demande.

        🔴 Sans cet envoi, « réglée » et « hors de l'objet du
        syndicat » n'étaient que des notes. L'occupant n'apprenait jamais la
        décision, pas même un refus fondé sur l'art. 1039.
        """
        self.ensure_one()
        reached, _unreached = self._bf_notify_partners(
            self.requester_partner_id, self._message_compute_subject(), body
        )
        if not reached:
            self.message_post(body=body)

    # ── Ce que le courriel dit, et où il mène ──

    def _message_compute_subject(self):
        """« Syndicat Le Belvédère : demande DE/2026/0003 ».

        La référence seule ne disait pas à l'occupant de quel immeuble il
        s'agissait : il peut posséder ici et louer ailleurs.

        ⚠️ Odoo 18 rend une CHAÎNE pour UN enregistrement. Une première
        version rendait un dictionnaire : toute réponse du bureau sans sujet
        explicite mourait sur « 'dict' object has no attribute 'splitlines' ».
        """
        self.ensure_one()
        return _("%(syndicat)s : demande %(name)s") % {
            "syndicat": self.sudo().organisation_id.name, "name": self.name}

    def _notify_get_recipients_groups(self, message, model_description, msg_vals=None):
        """Un bouton vers SES demandes pour l'occupant qui a un compte portail.

        Sans `portal.mixin`, Odoo n'offre aucun lien à un utilisateur du
        portail : le courriel du bureau arrivait nu, sans rien pour y
        répondre. Le bouton mène à la liste, pas à une fiche : le portail n'a
        pas de page par demande, et la liste est bornée par les règles.
        """
        groups = super()._notify_get_recipients_groups(
            message, model_description, msg_vals=msg_vals
        )
        if not self:
            return groups
        url = "%s/my/property/requests" % self[:1].get_base_url()
        for name, _func, data in groups:
            if name == "portal":
                data["active"] = True
                data["has_button_access"] = True
                data["button_access"] = {"url": url, "title": _("Voir mes demandes")}
        return groups

    def action_reopen(self):
        self._ensure_organisation_decides(_("Rouvrir une demande"))
        for request in self:
            request.write(
                {"state": "submitted", "date_done": False, "date_acknowledged": False}
            )
            request.message_post(body=_("Demande rouverte."))
        return True


class BfPropertyRequestIndicators(models.Model):
    """Les délais de la demande, mesurés pour être regroupés.

    ⚠️ **Un délai ne se regroupe pas s'il n'est pas un nombre.** Deux dates ne
    se moyennent pas, ne se pivotent pas et ne se comparent pas à un
    engagement. C'est la seule raison d'être de ces trois champs stockés :
    l'écran a besoin d'un nombre, pas d'un calcul refait à chaque lecture.

    🔴 **Le refus ne se mesure pas.** `bf_property_cx` ne sollicite l'occupant
    que sur une demande RÉGLÉE, jamais sur une demande refusée, parce que
    mesurer un refus de l'art. 1039 revient à mesurer le refus lui-même. La
    même retenue vaut ici : une demande refusée porte pourtant une
    `date_done`, et un délai calculé dessus dirait « en combien de jours ce
    syndicat dit-il non ». Les trois champs sont donc vides sur un refus, et
    un test le tient.

    ⚠️ **Aucun engagement inscrit, aucun résultat.** L'art. 1039 n'impose pas
    de délai de réponse : celui du module est celui que le syndicat s'est
    donné. À zéro, le résultat n'est pas « respecté », il est « aucun
    engagement inscrit » — un tableau de bord qui afficherait un vert par
    défaut inventerait une performance.
    """

    _inherit = "bf.property.request"

    acknowledge_delay_days = fields.Float(
        string="Délai de prise en charge (jours)",
        compute="_compute_delays",
        store=True,
        aggregator="avg",
        help="Du dépôt à la prise en charge. Vide tant que la demande n'est "
             "pas prise en charge, et vide sur une demande refusée.",
    )
    resolution_delay_days = fields.Float(
        string="Délai de règlement (jours)",
        compute="_compute_delays",
        store=True,
        aggregator="avg",
        help="Du dépôt au règlement. Vide sur une demande refusée : une "
             "demande refusée n'a pas été réglée.",
    )
    commitment_result = fields.Selection(
        [
            ("none", "Aucun engagement inscrit"),
            ("met", "Engagement respecté"),
            ("missed", "Engagement dépassé"),
        ],
        string="Contre l'engagement",
        compute="_compute_delays",
        store=True,
        help="Le résultat de la prise en charge face à l'engagement que le "
             "syndicat s'est donné. « Aucun engagement inscrit » est une "
             "réponse, pas un vide : sans engagement, il n'y a rien à tenir.",
    )

    @api.depends("date_submitted", "date_acknowledged", "date_done", "state",
                 "acknowledge_deadline")
    def _compute_delays(self):
        for request in self:
            refused = request.state == "refused"
            submitted = request.date_submitted
            if refused or not submitted:
                request.acknowledge_delay_days = 0.0
                request.resolution_delay_days = 0.0
                request.commitment_result = False
                continue
            if request.date_acknowledged:
                request.acknowledge_delay_days = (
                    request.date_acknowledged - submitted
                ).total_seconds() / 86400.0
            else:
                request.acknowledge_delay_days = 0.0
            if request.date_done:
                request.resolution_delay_days = (
                    request.date_done - submitted
                ).total_seconds() / 86400.0
            else:
                request.resolution_delay_days = 0.0
            if not request.acknowledge_deadline:
                request.commitment_result = "none"
            elif not request.date_acknowledged:
                # Pas encore prise en charge : le sort de l'engagement n'est
                # pas joué. `is_overdue` dit déjà le retard en cours.
                request.commitment_result = False
            else:
                request.commitment_result = (
                    "met"
                    if request.date_acknowledged <= request.acknowledge_deadline
                    else "missed"
                )
