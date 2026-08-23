"""Réservation d'une partie commune : salle communautaire, gym, monte-charge.

## Pourquoi un modèle natif plutôt que `bf_appointment`

🔴 **`bf_appointment` dépend de `resource_booking`, qui est AGPL-3.** La suite
`bf_property` est sous BUSL-1.1, et le projet a déjà tranché ce cas exact en
écartant OCA `contract` : une dépendance déclarée n'est pas plus sûre qu'une
copie, parce qu'un module Odoo qui `depends` d'un module AGPL-3 et importe ses
modèles forme un tout dont la distribution déclenche le copyleft. S'y ajoute que
`resource_booking` n'existe pas au dépôt public, donc la dépendance y serait
insatisfiable.

Et la forme ne correspond pas : `resource.booking` modèle un rendez-vous avec
une personne sur un calendrier de ressource. Réserver la salle de 14 h à 17 h
samedi n'a pas cette forme, et tient en un modèle.

## Ce que le texte donne

**Art. 1043 C.c.Q.** distingue la partie commune à usage restreint, dont la
jouissance est réservée à certaines fractions. ⚠️ Conséquence directe ici : la
terrasse attachée à d'autres fractions ne se réserve pas par n'importe qui. Le
module refuse, et il nomme les fractions bénéficiaires.

**Le règlement de l'immeuble** fixe les conditions d'usage des parties communes.
Le module n'en invente aucune : il porte le texte que le syndicat y inscrit, et
rappelle que le modifier relève de la majorité de l'**art. 1096 C.c.Q.**, qui
vise expressément les décisions « visant à modifier le règlement de l'immeuble ».

⚠️ **Aucun renseignement sur qui a réservé.** Un occupant voit un créneau occupé,
jamais le nom de qui l'occupe. L'art. 1070 al. 1 ne met au registre les
renseignements personnels autres que le nom et l'adresse qu'avec le consentement
exprès de la personne ; afficher l'agenda social de l'immeuble ferait l'inverse.
Le syndicat, lui, voit tout depuis le bureau.

## 🔴 Le double créneau, et comment il est vraiment fermé

Une vérification de chevauchement écrite en Python et rien d'autre laisse une
course : deux transactions concurrentes lisent chacune un créneau libre, et
toutes deux écrivent. Odoo travaille en `REPEATABLE READ`, donc aucune des deux
ne voit l'insertion de l'autre.

La garde prend donc un **verrou de ligne sur la partie commune** (`SELECT … FOR
UPDATE`) avant de compter les chevauchements. Les réservations d'un même espace
se sérialisent, celles d'espaces différents ne se gênent pas. C'est la seule
raison d'être de ce verrou, et il est pris dans la contrainte plutôt qu'au
contrôleur pour couvrir aussi les écritures qui ne passent pas par le portail.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

STATES = [
    ("requested", "Demandée"),
    ("confirmed", "Confirmée"),
    ("refused", "Refusée"),
    ("cancelled", "Annulée"),
]
# Une réservation demandée tient le créneau : sans cela, deux personnes
# attendraient une confirmation sur le même samedi.
BLOCKING_STATES = ("requested", "confirmed")


class BfPropertyBooking(models.Model):
    _name = "bf.property.booking"
    _description = "Réservation d'une partie commune"
    _inherit = ["mail.thread"]
    _order = "date_start desc, id desc"

    name = fields.Char(string="Numéro", required=True, copy=False, default="Nouvelle")
    active = fields.Boolean(default=True)
    common_area_id = fields.Many2one(
        "bf.property.common.area",
        string="Espace",
        required=True,
        ondelete="cascade",
        index=True,
        domain="[('bookable', '=', True)]",
    )
    building_id = fields.Many2one(
        related="common_area_id.building_id", store=True, string="Immeuble", index=True
    )
    syndicat_id = fields.Many2one(
        related="common_area_id.syndicat_id", store=True, string="Syndicat", index=True
    )
    company_id = fields.Many2one(
        related="common_area_id.company_id", store=True, string="Société"
    )
    partner_id = fields.Many2one(
        "res.partner", string="Réservé par", required=True, index=True
    )
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Au titre de la fraction",
        domain="[('syndicat_id', '=', syndicat_id)]",
    )
    date_start = fields.Datetime(string="Début", required=True, tracking=True)
    date_stop = fields.Datetime(string="Fin", required=True, tracking=True)
    duration_minutes = fields.Integer(
        string="Durée (minutes)", compute="_compute_duration", store=True
    )
    state = fields.Selection(
        STATES, string="État", default="requested", required=True, tracking=True
    )
    note = fields.Text(string="Motif ou précisions")
    decision_reason = fields.Text(string="Motif de la décision")

    @api.depends("date_start", "date_stop")
    def _compute_duration(self):
        for booking in self:
            if booking.date_start and booking.date_stop:
                delta = booking.date_stop - booking.date_start
                booking.duration_minutes = int(delta.total_seconds() // 60)
            else:
                booking.duration_minutes = 0

    # ── Gardes ──

    @api.constrains("date_start", "date_stop")
    def _check_window(self):
        for booking in self:
            if booking.date_stop <= booking.date_start:
                raise ValidationError(
                    _("« %s » se terminerait avant d'avoir commencé.") % booking.name
                )

    @api.constrains("date_start", "date_stop", "common_area_id")
    def _check_area_rules(self):
        """Les bornes que le syndicat s'est données, et rien de plus.

        ⚠️ Aucune de ces bornes n'est légale : ce sont des réglages d'espace.
        Le module ne prétend à aucun délai ni à aucune durée du Code civil.
        """
        for booking in self:
            area = booking.common_area_id
            if not area.bookable:
                raise ValidationError(
                    _("« %s » n'est pas déclarée réservable.") % area.name
                )
            if area.booking_max_minutes and (
                booking.duration_minutes > area.booking_max_minutes
            ):
                raise ValidationError(
                    _(
                        "« %(area)s » se réserve au plus %(max)d minutes à la "
                        "fois ; cette réservation en demande %(asked)d."
                    )
                    % {
                        "area": area.name,
                        "max": area.booking_max_minutes,
                        "asked": booking.duration_minutes,
                    }
                )
            if area.booking_horizon_days and booking.date_start:
                horizon = fields.Datetime.now() + timedelta(
                    days=area.booking_horizon_days
                )
                if booking.date_start > horizon:
                    raise ValidationError(
                        _(
                            "« %(area)s » ne se réserve pas plus de %(days)d "
                            "jours à l'avance."
                        )
                        % {"area": area.name, "days": area.booking_horizon_days}
                    )

    @api.constrains("common_area_id", "partner_id", "unit_id")
    def _check_requester_may_book(self):
        """⚠️ Art. 1043 : une partie commune à usage restreint n'est pas à tous.

        Sa jouissance est réservée à certaines fractions. Laisser n'importe quel
        occupant réserver la terrasse attachée à d'autres fractions donnerait un
        droit que la déclaration ne donne pas.
        """
        units_model = self.env["bf.property.unit"].sudo()
        for booking in self:
            if self.env.user._is_internal():
                continue
            audiences = units_model._portal_audiences_for(booking.partner_id)
            if booking.syndicat_id.id not in audiences:
                raise ValidationError(
                    _("%s n'a aucune fraction dans cette copropriété.")
                    % booking.partner_id.display_name
                )
            # 🔴 `sudo` sur la partie commune, et c'est la seule chose qui
            # rende cette garde efficace. Lue avec les droits du demandeur,
            # `restricted_unit_ids` est filtrée par la règle d'accès des
            # fractions : la fraction du VOISIN en disparaît, la liste revient
            # vide, et la garde conclut que l'espace n'est restreint pour
            # personne. Une garde qui lit avec les droits de celui qu'elle
            # garde se désarme toute seule, sans rien dire.
            area = booking.common_area_id.sudo()
            if area.area_type == "restricted" and area.restricted_unit_ids:
                owned, occupied = units_model._portal_units_for(booking.partner_id)
                if not (owned | occupied) & area.restricted_unit_ids:
                    raise ValidationError(
                        _(
                            "« %(area)s » est une partie commune à usage "
                            "restreint (art. 1043 C.c.Q.) : sa jouissance est "
                            "réservée aux fractions %(units)s."
                        )
                        % {
                            "area": area.name,
                            "units": ", ".join(
                                area.restricted_unit_ids.mapped("name")
                            ),
                        }
                    )

    @api.constrains("common_area_id", "date_start", "date_stop", "state")
    def _check_no_overlap(self):
        """🔴 Le verrou est ici, et il est la seule chose qui ferme la course.

        Sans lui, deux transactions concurrentes lisent chacune le créneau libre
        et écrivent toutes deux : Odoo travaille en `REPEATABLE READ`, donc
        aucune ne voit l'insertion de l'autre. Le verrou de ligne sur la partie
        commune sérialise les réservations d'un même espace, et laisse les
        autres espaces tranquilles.
        """
        for booking in self:
            if booking.state not in BLOCKING_STATES:
                continue
            self.env.cr.execute(
                "SELECT id FROM bf_property_common_area WHERE id = %s FOR UPDATE",
                (booking.common_area_id.id,),
            )
            # 🔴 `sudo` sur la RECHERCHE, deuxième fois que ce piège mord dans
            # ce même fichier. La règle d'accès du portail borne les
            # réservations à `partner_id = user.partner_id` : cherchée avec les
            # droits du demandeur, la réservation du VOISIN est invisible, la
            # requête ne rend rien, et la garde conclut que le créneau est
            # libre. Elle était donc inerte pour le cas exact qui la justifie,
            # deux personnes différentes sur la même salle.
            clash = self.sudo().search(
                [
                    ("id", "!=", booking.id),
                    ("common_area_id", "=", booking.common_area_id.id),
                    ("state", "in", BLOCKING_STATES),
                    ("date_start", "<", booking.date_stop),
                    ("date_stop", ">", booking.date_start),
                ],
                limit=1,
            )
            if clash:
                raise ValidationError(
                    _(
                        "« %(area)s » est déjà retenue de %(start)s à %(stop)s. "
                        "Un créneau demandé retient l'espace au même titre "
                        "qu'un créneau confirmé, sans quoi deux personnes "
                        "attendraient une confirmation sur le même samedi."
                    )
                    % {
                        "area": booking.common_area_id.name,
                        "start": fields.Datetime.to_string(clash.date_start),
                        "stop": fields.Datetime.to_string(clash.date_stop),
                    }
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "Nouvelle") == "Nouvelle":
                # ⚠️ `sudo` sur la séquence : un utilisateur du portail n'a
                # aucun droit de lecture sur `ir.sequence`.
                vals["name"] = self.env["ir.sequence"].sudo().next_by_code(
                    "bf.property.booking"
                ) or _("Nouvelle")
        bookings = super().create(vals_list)
        for booking in bookings:
            if not booking.common_area_id.booking_requires_approval:
                booking.state = "confirmed"
        return bookings

    # ── Décisions ──

    def _ensure_syndicat_decides(self, what):
        """🔴 Une règle d'enregistrement borne QUI voit quoi, pas CE QU'ON PEUT FAIRE.

        Toute méthode sans souligné initial est appelable par RPC dès qu'on a
        l'accès au modèle : la vue n'est pas une barrière, et un résident n'a
        pas besoin d'un bouton à l'écran pour appeler la méthode. Les
        `UserError` de ces transitions sont des gardes d'ÉTAT (« n'est plus
        active »), pas des gardes de DROIT.

        Constat rapporté le 2026-08-22 par une autre session, vérifié par sonde
        avant correction : un résident pouvait appeler `action_confirm` sur SA
        réservation et s'auto-approuver, en restant dans son périmètre de
        lecture, tout en court-circuitant le réglage « confirmation du syndicat
        requise ». L'art. 1070 C.c.Q. impose au syndicat de tenir un registre
        fidèle ; un flux d'approbation que le demandeur clôt lui-même ne l'est
        pas.
        """
        if self.env.su or self.env.user.has_group(
            "bf_property_core.group_bf_property_manager"
        ):
            return
        raise AccessError(
            _(
                "%s relève du syndicat, pas du demandeur. Vous pouvez déposer "
                "et annuler ce qui vous appartient."
            )
            % what
        )

    def action_confirm(self):
        self._ensure_syndicat_decides(_("Confirmer une réservation"))
        for booking in self:
            if booking.state not in ("requested",):
                raise UserError(
                    _("« %s » n'est pas en attente de confirmation.") % booking.name
                )
            booking.state = "confirmed"
            booking.message_post(body=_("Réservation confirmée."))
        return True

    def action_refuse(self):
        self._ensure_syndicat_decides(_("Refuser une réservation"))
        for booking in self:
            if not booking.decision_reason:
                raise UserError(
                    _("Dites pourquoi « %s » est refusée.") % booking.name
                )
            booking.state = "refused"
            booking.message_post(
                body=_("Réservation refusée : %s") % booking.decision_reason
            )
        return True

    def action_cancel(self):
        """Annuler reste ouvert : la règle borne déjà chacun à ses réservations,
        et renoncer à son propre créneau n'est pas une décision du syndicat."""
        for booking in self:
            if booking.state in ("refused", "cancelled"):
                raise UserError(_("« %s » n'est plus active.") % booking.name)
            booking.state = "cancelled"
            booking.message_post(body=_("Réservation annulée."))
        return True

    # ── Disponibilité, sans nommer personne ──

    @api.model
    def _busy_slots(self, area, date_from, date_to):
        """Les créneaux occupés d'un espace, SANS le nom de qui les occupe.

        ⚠️ C'est tout l'intérêt de la méthode. Rendre les enregistrements
        laisserait un gabarit afficher `partner_id` par distraction, et
        l'agenda social de l'immeuble avec.
        """
        bookings = self.sudo().search(
            [
                ("common_area_id", "=", area.id),
                ("state", "in", BLOCKING_STATES),
                ("date_start", "<", date_to),
                ("date_stop", ">", date_from),
            ],
            order="date_start",
        )
        return [
            {"date_start": b.date_start, "date_stop": b.date_stop}
            for b in bookings
        ]


class BfPropertyCommonArea(models.Model):
    _inherit = "bf.property.common.area"

    booking_requires_approval = fields.Boolean(
        string="Confirmation du syndicat requise",
        help="Sans cela, une demande devient une réservation dès qu'elle est "
             "déposée. Avec, elle attend une décision.",
    )
    booking_max_minutes = fields.Integer(
        string="Durée maximale (minutes)",
        default=0,
        help="⚠️ Aucune durée n'a de source légale : c'est le règlement de "
             "l'immeuble qui fixe les conditions d'usage des parties communes. "
             "À zéro, aucune limite.",
    )
    booking_horizon_days = fields.Integer(
        string="Réservable à l'avance (jours)",
        default=0,
        help="À zéro, aucune limite.",
    )
    booking_rules = fields.Text(
        string="Conditions d'usage",
        help="Ce que le règlement de l'immeuble prévoit pour cet espace, tel "
             "que l'occupant doit le lire. Le module n'invente aucune "
             "condition. Modifier le règlement relève de la majorité de "
             "l'art. 1096 C.c.Q.",
    )
    booking_ids = fields.One2many(
        "bf.property.booking", "common_area_id", string="Réservations"
    )
