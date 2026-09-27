"""Colis reçus et visiteurs annoncés : le journal, et sa date de péremption.

C'est la fonction la plus aimée des produits concurrents, et c'est aussi la
plus sensible des quatre du portail. Un journal de colis dit qui reçoit quoi et
quand ; un journal de visiteurs dit qui reçoit **qui**. Tenu sans limite, ce
n'est plus un service, c'est une surveillance de l'immeuble par lui-même.

## Ce que le texte donne

**Art. 1070 al. 1 C.c.Q.** — le registre contient le nom et l'adresse de chaque
copropriétaire et de chaque occupant, et les **autres** renseignements
personnels seulement **si la personne y consent expressément**. Un journal de
visiteurs va bien au-delà du nom et de l'adresse, et il porte en plus des
renseignements sur des **tiers** qui n'ont rien consenti du tout : le visiteur
n'est ni copropriétaire, ni occupant, ni membre de quoi que ce soit.

D'où trois décisions de conception, et elles sont le module :

1. **Chacun ne voit que le sien.** Un occupant voit ses colis et ses visiteurs.
   Jamais ceux du voisin, et il n'existe aucune vue « qui est passé dans
   l'immeuble aujourd'hui » côté portail.
2. **Le journal a une date de péremption.** Le syndicat fixe une durée de
   conservation, et une tâche planifiée **supprime** ce qui l'a dépassée. Elle
   supprime, elle n'archive pas : archiver garde la donnée en la cachant, ce
   qui est le contraire de ce qu'on cherche.
3. **Rien n'est retenu au-delà de ce qui sert.** Le nom du visiteur suffit ; sa
   plaque n'est demandée que si le syndicat gère un stationnement, et le champ
   dit ce qu'il est.

⚠️ **Aucun article n'est cité pour la conservation.** Le cahier de règles du
projet porte le Code civil et ses règlements, pas la loi sur la protection des
renseignements personnels. Le module met donc en place la durée et la purge
parce que c'est la bonne façon de faire, sans prétendre à une disposition qu'il
n'a pas lue. À porter à la relecture juridique au même titre que l'accès aux
parties privatives.

⚠️ **Le module ne prétend rien sur la garde du colis.** Il note qu'un colis est
arrivé et qu'il a été remis. Il ne dit pas qui en répond s'il disparaît, parce
que ça ne se lit pas dans le Code civil de la copropriété.

⚠️ **Aucun matériel d'accès.** Ni serrure, ni interphone, ni ouverture de
porte : le plafond de la tâche, et il tient. Annoncer un visiteur ne lui ouvre
rien, cela prévient le concierge.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.bf_property_core.models.mail_template import bf_mail_layout

PARCEL_STATES = [
    ("held", "En dépôt"),
    ("collected", "Remis"),
    ("returned", "Retourné à l'expéditeur"),
]
VISIT_STATES = [
    ("expected", "Annoncé"),
    ("arrived", "Arrivé"),
    ("departed", "Reparti"),
    ("cancelled", "Annulé"),
]


class BfPropertyPortalActor(models.AbstractModel):
    """Ce que les deux journaux partagent : le lien à la personne et la garde.

    Les deux modèles ci-dessous ont la même question de droit à trancher, et
    la même façon d'y répondre. La mettre à un seul endroit évite qu'un des
    deux dérive.
    """

    _name = "bf.property.portal.actor"
    _inherit = ["bf.property.organisation.authority"]
    _description = "Journal rattaché à un occupant"

    def _check_person_is_here(self, partner, syndicat, unit=None):
        """Vérifie l'appartenance à la copropriété, et à la fraction nommée.

        ⚠️ Les deux, pas seulement la première. Sans la seconde, déposer
        une fiche au nom d'autrui était bien refusé, mais rien ne vérifiait la
        fraction inscrite — un occupant annonçait son visiteur à la porte du
        voisin. Le registre de l'art. 1070 disait alors qu'on attendait
        quelqu'un chez une personne qui n'en savait rien.

        La fraction n'est contrôlée que pour les occupants : le syndicat
        consigne pour tout l'immeuble, c'est son travail.
        """
        units = self.env["bf.property.unit"].sudo()
        if syndicat.id not in units._portal_audiences_for(partner):
            raise ValidationError(
                _("%(who)s n'a aucune fraction dans %(syndicat)s.")
                % {
                    "who": partner.display_name,
                    "syndicat": syndicat.display_name,
                }
            )
        if unit and not self.env.user._is_internal():
            owned, occupied = units._portal_units_for(partner)
            if unit not in (owned | occupied):
                raise ValidationError(
                    _("La fraction %(unit)s n'est ni possédée ni occupée par "
                      "%(who)s.")
                    # La règle cache cette fraction à qui ne l'a pas : la
                    # nommer sans élévation lèverait un refus de lecture au
                    # lieu de dire ce qui cloche.
                    % {"unit": unit.sudo().display_name,
                       "who": partner.display_name}
                )


class BfPropertyParcel(models.Model):
    _name = "bf.property.parcel"
    _description = "Colis reçu pour un occupant"
    _inherit = ["mail.thread", "bf.property.portal.actor"]
    _order = "date_received desc, id desc"

    name = fields.Char(string="Numéro", required=True, copy=False, default="Nouveau")
    organisation_id = fields.Many2one(
        "bf.property.organisation", string="Syndicat", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        related="organisation_id.company_id", store=True, string="Société"
    )
    building_id = fields.Many2one(
        "bf.property.building", string="Immeuble", index=True,
        domain="[('organisation_id', '=', organisation_id)]",
    )
    unit_id = fields.Many2one(
        "bf.property.unit", string="Fraction",
        domain="[('organisation_id', '=', organisation_id)]",
    )
    partner_id = fields.Many2one(
        "res.partner", string="Destinataire", required=True, index=True
    )
    carrier = fields.Char(string="Transporteur")
    reference = fields.Char(
        string="Référence",
        help="Le numéro que porte l'étiquette, s'il aide à retrouver le colis. "
             "Rien n'oblige à le saisir.",
    )
    parcel_type = fields.Selection(
        [
            ("parcel", "Colis"),
            ("letter", "Courrier recommandé"),
            ("bulky", "Encombrant"),
            ("perishable", "Périssable"),
        ],
        string="Nature", default="parcel", required=True,
    )
    storage_location = fields.Char(
        string="Où il est rangé", help="Local à colis, bureau, casier."
    )
    date_received = fields.Datetime(
        string="Reçu le", default=fields.Datetime.now, required=True
    )
    state = fields.Selection(
        PARCEL_STATES, string="État", default="held", required=True, tracking=True
    )
    date_closed = fields.Datetime(string="Remis ou retourné le", readonly=True)
    collected_by = fields.Char(
        string="Remis à",
        help="Le nom de qui repart avec, quand ce n'est pas le destinataire.",
    )
    note = fields.Text(string="Note")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "Nouveau") == "Nouveau":
                # ⚠️ `sudo` sur la séquence : le portail n'a aucun droit sur
                # `ir.sequence`.
                vals["name"] = self.env["ir.sequence"].sudo().with_context(
                    ir_sequence_date=False).next_by_code(
                    "bf.property.parcel"
                ) or _("Nouveau")
        parcels = super().create(vals_list)
        for parcel in parcels:
            parcel._check_person_is_here(parcel.partner_id, parcel.organisation_id)
        return parcels

    def action_collected(self):
        self._ensure_organisation_decides(_("Consigner la remise d'un colis"))
        for parcel in self:
            if parcel.state != "held":
                raise UserError(_("« %s » n'est plus en dépôt.") % parcel.name)
            parcel.write(
                {"state": "collected", "date_closed": fields.Datetime.now()}
            )
            parcel.message_post(body=_("Colis remis."))
        return True

    def action_returned(self):
        self._ensure_organisation_decides(_("Consigner le retour d'un colis"))
        for parcel in self:
            if parcel.state != "held":
                raise UserError(_("« %s » n'est plus en dépôt.") % parcel.name)
            parcel.write(
                {"state": "returned", "date_closed": fields.Datetime.now()}
            )
            parcel.message_post(body=_("Colis retourné à l'expéditeur."))
        return True


class BfPropertyVisit(models.Model):
    _name = "bf.property.visit"
    _description = "Visiteur annoncé"
    _inherit = ["mail.thread", "bf.property.portal.actor"]

    # ⚠️ Ce que l'hôte touche AVANT que la visite commence : ce qu'il annonce,
    # et le renoncement. Rien de ce qui CONSTATE (l'état, les heures d'arrivée
    # et de départ) : c'est le concierge qui constate, et c'est ce que le
    # registre de l'art. 1070 doit dire fidèlement.
    _portal_writable_fields = (
        "visitor_name", "visitor_kind", "vehicle_plate",
        "date_expected", "date_expected_end", "note", "unit_id", "state",
    )
    _portal_state_allowed = {
        "expected": ("cancelled",),
        "arrived": ("cancelled",),
    }
    # Ce que l'hôte annonce. L'arrivée et le départ se constatent au comptoir.
    _portal_creatable_fields = (
        "organisation_id", "building_id", "unit_id", "host_partner_id",
        "visitor_name", "visitor_kind", "vehicle_plate",
        "date_expected", "date_expected_end", "note",
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

    def write(self, vals):
        """Ferme le registre à l'hôte dès que la visite a commencé.

        🔴 Sans cette garde, l'hôte marquait son visiteur « arrivé »,
        antidatait l'heure d'arrivée et changeait le nom du visiteur une fois la
        visite passée. `date_arrived` est déclaré `readonly=True`, ce qui ne
        vaut que pour l'écran : un appel direct l'écrivait sans obstacle. C'est
        le document qu'on consultera après un incident.
        """
        self._ensure_portal_write_scope(vals)
        if not (self.env.su or self.env.user.has_group(
            "bf_property_core.group_bf_property_manager"
        )):
            # Annoncer se corrige, constater ne se corrige pas.
            declaration = set(vals) - {"state"}
            started = self.filtered(lambda v: v.state != "expected")
            if declaration and started:
                raise AccessError(
                    _(
                        "« %s » a déjà commencé : ce qui a été constaté ne se "
                        "réécrit pas. Vous pouvez encore l'annuler."
                    )
                    % started[0].name
                )
        return super().write(vals)
    _order = "date_expected desc, id desc"

    name = fields.Char(string="Numéro", required=True, copy=False, default="Nouveau")
    organisation_id = fields.Many2one(
        "bf.property.organisation", string="Syndicat", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        related="organisation_id.company_id", store=True, string="Société"
    )
    building_id = fields.Many2one(
        "bf.property.building", string="Immeuble", index=True,
        domain="[('organisation_id', '=', organisation_id)]",
    )
    unit_id = fields.Many2one(
        "bf.property.unit", string="Fraction visitée",
        domain="[('organisation_id', '=', organisation_id)]",
    )
    host_partner_id = fields.Many2one(
        "res.partner", string="Reçoit", required=True, index=True
    )
    visitor_name = fields.Char(
        string="Nom du visiteur",
        required=True,
        help="⚠️ Renseignement personnel d'un tiers qui n'a rien consenti. Le "
             "nom suffit ; le journal se purge automatiquement.",
    )
    visitor_kind = fields.Selection(
        [
            ("guest", "Invité"),
            ("family", "Famille"),
            ("trade", "Corps de métier"),
            ("delivery", "Livraison"),
            ("care", "Soins à domicile"),
            ("other", "Autre"),
        ],
        string="Nature de la visite", default="guest", required=True,
    )
    vehicle_plate = fields.Char(
        string="Plaque du véhicule",
        help="À ne remplir que si le syndicat gère un stationnement visiteur. "
             "C'est un renseignement personnel de plus, et il se purge avec le "
             "reste.",
    )
    date_expected = fields.Datetime(string="Attendu le", required=True)
    date_expected_end = fields.Datetime(string="Jusqu'à")
    state = fields.Selection(
        VISIT_STATES, string="État", default="expected", required=True, tracking=True
    )
    date_arrived = fields.Datetime(string="Arrivé le", readonly=True)
    date_departed = fields.Datetime(string="Reparti le", readonly=True)
    note = fields.Text(string="Note")

    @api.constrains("building_id", "unit_id", "organisation_id")
    def _check_building_belongs(self):
        """L'immeuble d'une visite est celui du syndicat, et celui de la fraction.

        Rien ne liait l'immeuble au reste : un dépôt pouvait citer l'immeuble
        d'un autre syndicat, et le registre de l'art. 1070 annonçait alors une
        visite à une porte qui n'est pas la sienne. Lu en élévation : la règle
        du portail cache ce qui n'est pas à l'occupant, et il faut pouvoir dire
        ce qui cloche au lieu de lever un refus de lecture.
        """
        for visit in self.sudo():
            if visit.unit_id and visit.unit_id.organisation_id != visit.organisation_id:
                raise ValidationError(
                    _("La fraction visée appartient à un autre syndicat."))
            building = visit.building_id
            if not building:
                continue
            if building.organisation_id != visit.organisation_id:
                raise ValidationError(
                    _("L'immeuble visé appartient à un autre syndicat."))
            if visit.unit_id and visit.unit_id.building_id != building:
                raise ValidationError(
                    _("La fraction visée est dans un autre immeuble."))

    @api.constrains("date_expected", "date_expected_end")
    def _check_window(self):
        for visit in self:
            if visit.date_expected_end and (
                visit.date_expected_end < visit.date_expected
            ):
                raise ValidationError(
                    _("« %s » repartirait avant d'arriver.") % visit.name
                )

    @api.model_create_multi
    def create(self, vals_list):
        self._ensure_portal_create_scope(vals_list)
        for vals in vals_list:
            if vals.get("name", "Nouveau") == "Nouveau":
                vals["name"] = self.env["ir.sequence"].sudo().with_context(
                    ir_sequence_date=False).next_by_code(
                    "bf.property.visit"
                ) or _("Nouveau")
        visits = super().create(vals_list)
        for visit in visits:
            visit._check_person_is_here(
                visit.host_partner_id, visit.organisation_id, visit.unit_id
            )
        return visits

    def action_arrived(self):
        self._ensure_organisation_decides(_("Consigner l'arrivée d'un visiteur"))
        for visit in self:
            if visit.state != "expected":
                raise UserError(_("« %s » n'est plus attendu.") % visit.name)
            visit.write({"state": "arrived", "date_arrived": fields.Datetime.now()})
            visit.message_post(body=_("Visiteur arrivé."))
        return True

    def action_departed(self):
        self._ensure_organisation_decides(_("Consigner le départ d'un visiteur"))
        for visit in self:
            if visit.state != "arrived":
                raise UserError(_("« %s » n'est pas arrivé.") % visit.name)
            visit.write({"state": "departed", "date_departed": fields.Datetime.now()})
            visit.message_post(body=_("Visiteur reparti."))
        return True

    def action_cancel(self):
        """L'hôte annule son propre visiteur : ce n'est pas une décision du
        syndicat, et la règle d'accès le borne déjà au sien."""
        for visit in self:
            if visit.state in ("departed", "cancelled"):
                raise UserError(_("« %s » n'est plus en cours.") % visit.name)
            visit.write({"state": "cancelled"})
            visit.message_post(body=_("Visite annulée."))
        return True


class BfPropertyOrganisation(models.Model):
    _inherit = "bf.property.organisation"

    log_retention_days = fields.Integer(
        string="Conservation des journaux (jours)",
        default=0,
        help="⚠️ Le journal des colis dit qui reçoit quoi, celui des visiteurs "
             "dit qui reçoit qui. Tenu sans limite, ce n'est plus un service. "
             "Passé ce nombre de jours, une tâche planifiée SUPPRIME les "
             "entrées closes. À zéro, rien n'est supprimé, et le module "
             "l'affiche comme un choix plutôt que comme un défaut.",
    )
    parcel_ids = fields.One2many(
        "bf.property.parcel", "organisation_id", string="Colis"
    )
    visit_ids = fields.One2many("bf.property.visit", "organisation_id", string="Visites")

    @api.model
    def _cron_purge_portal_logs(self):
        """Supprime les entrées closes au-delà de la conservation choisie.

        ⚠️ **Supprime, n'archive pas.** Archiver garde la donnée en la cachant,
        ce qui est exactement le contraire du but. Et seules les entrées
        **closes** partent : un colis encore en dépôt et un visiteur encore
        attendu restent, quelle que soit leur date.
        """
        purged = {"parcels": 0, "visits": 0}
        for syndicat in self.sudo().search([("log_retention_days", ">", 0)]):
            cutoff = fields.Datetime.now() - timedelta(
                days=syndicat.log_retention_days
            )
            parcels = self.env["bf.property.parcel"].sudo().search(
                [
                    ("organisation_id", "=", syndicat.id),
                    ("state", "in", ("collected", "returned")),
                    ("date_closed", "<", cutoff),
                ]
            )
            visits = self.env["bf.property.visit"].sudo().search(
                [
                    ("organisation_id", "=", syndicat.id),
                    ("state", "in", ("departed", "cancelled")),
                    ("date_expected", "<", cutoff),
                ]
            )
            purged["parcels"] += len(parcels)
            purged["visits"] += len(visits)
            parcels.unlink()
            visits.unlink()
        return purged
