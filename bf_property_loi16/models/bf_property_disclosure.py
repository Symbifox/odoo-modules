"""Documents permettant un consentement éclairé (art. 1068.2 C.c.Q.).

  « Celui qui promet d'acheter une fraction peut demander au syndicat qu'il lui
  fournisse les documents ou renseignements concernant l'immeuble et le syndicat
  qui sont de nature à lui permettre de donner un consentement éclairé. Le
  syndicat est tenu, sous réserve des dispositions relatives à la protection de
  la vie privée, de les fournir avec diligence au promettant acheteur, aux frais
  de celui-ci.
  Le syndicat doit transmettre au propriétaire de la fraction ou à ses ayants
  cause les documents ou renseignements qu'il a fournis au promettant acheteur. »

  (2019, c. 28, a. 35 — le même article que celui qui a créé l'attestation de
  l'art. 1068.1. C'est pourquoi les deux vivent ici.)

Troisième et dernier des régimes de documents à l'acquéreur. Les trois se
distinguent sur trois axes, et la doctrine les aplatit régulièrement :

| Article | Qui demande | Délai | Avis au propriétaire |
|---|---|---|---|
| 1068.1 | le copropriétaire vendeur | 15 jours | aucun |
| 1069 al. 2 | le proposant acquéreur | 15 jours, **au préalable** | avant de fournir |
| 1068.2 | le promettant acheteur | « avec diligence » | **après** avoir fourni |

⚠️ **Aucun délai chiffré, et le module n'en invente pas.** Le texte dit « avec
diligence » et s'arrête là. Poser trente ou quinze jours ici serait fabriquer une
règle que la loi ne pose pas, sur le modèle de ce que fait la doctrine ailleurs.
Le module compte les jours ouverts et les montre ; il ne déclare personne en
retard.

⚠️ **L'avis au propriétaire vient APRÈS, pas avant.** C'est l'inverse de
l'art. 1069 al. 2, où le préavis conditionne l'autorisation de fournir. Ici
l'alinéa 2 oblige à transmettre au propriétaire « les documents ou renseignements
qu'il A FOURNIS » : l'obligation naît de la remise et porte sur son contenu
exact. Un module qui traiterait les deux avis pareil se tromperait dans les deux
sens à la fois.

⚠️ **La réserve de vie privée est dans l'article même.** « Sous réserve des
dispositions relatives à la protection de la vie privée » : l'art. 1068.2 est une
autorisation de la loi au sens de l'art. 37, mais elle ne couvre pas les
renseignements personnels des autres copropriétaires. Le registre de l'art. 1070
en contient (noms, adresses, et davantage sur consentement), et les impayés en
sont. Le module exige donc que la revue soit consignée avant toute remise, et
garde le motif de ce qui a été retranché.

⚠️ **Aux frais du demandeur.** Le texte le dit, et le module porte le montant.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class BfPropertyDisclosure(models.Model):
    _name = "bf.property.disclosure"
    _description = "Demande de documents (art. 1068.2)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "request_date desc, id desc"

    name = fields.Char(
        string="Demande", required=True, default=lambda s: _("Nouvelle"),
        tracking=True,
    )
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
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Devise"
    )
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction promise",
        required=True,
        ondelete="cascade",
        domain="[('syndicat_id', '=', syndicat_id)]",
        tracking=True,
    )
    requester_partner_id = fields.Many2one(
        "res.partner",
        string="Promettant acheteur",
        required=True,
        tracking=True,
        help="Art. 1068.2 : « celui qui promet d'acheter une fraction ». Ce "
             "n'est ni le copropriétaire vendeur, qui demande l'attestation de "
             "l'art. 1068.1, ni le proposant acquéreur, qui demande l'état des "
             "charges de l'art. 1069 al. 2.",
    )
    owner_partner_ids = fields.Many2many(
        "res.partner",
        string="Propriétaires à qui transmettre",
        compute="_compute_owners",
        help="Art. 1068.2 al. 2 : le syndicat doit transmettre au propriétaire "
             "de la fraction ou à ses ayants cause ce qu'il a fourni.",
    )

    request_date = fields.Date(
        string="Demandée le",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    provided_date = fields.Date(string="Fournie le", tracking=True)
    owner_transmission_date = fields.Date(
        string="Transmise au propriétaire le", tracking=True
    )
    days_open = fields.Integer(
        string="Jours écoulés",
        compute="_compute_state",
        help="Art. 1068.2 : le syndicat fournit « avec diligence ». Le texte ne "
             "pose aucun nombre de jours et le module n'en invente pas : ce "
             "compteur informe, il ne déclare aucun retard.",
    )
    state = fields.Selection(
        [
            ("requested", "Demandée"),
            ("provided", "Fournie, à transmettre au propriétaire"),
            ("complete", "Fournie et transmise"),
            ("refused", "Refusée"),
            ("cancelled", "Annulée"),
        ],
        string="État",
        compute="_compute_state",
        store=True,
        tracking=True,
    )
    cancelled = fields.Boolean(string="Annulée", tracking=True)
    refusal_reason = fields.Text(
        string="Motif du refus",
        tracking=True,
        help="Un refus total se motive. La réserve de vie privée de "
             "l'art. 1068.2 justifie de retrancher des renseignements, "
             "rarement de tout refuser.",
    )

    privacy_reviewed = fields.Boolean(
        string="Revue de vie privée faite",
        tracking=True,
        help="Art. 1068.2 : « sous réserve des dispositions relatives à la "
             "protection de la vie privée ». L'article autorise la "
             "communication au sens de l'art. 37 C.c.Q., mais cette "
             "autorisation ne couvre pas les renseignements personnels des "
             "autres copropriétaires. Le registre de l'art. 1070 en contient, "
             "et l'état des impayés en est.",
    )
    privacy_note = fields.Text(
        string="Ce qui a été retranché ou caviardé",
        help="Garder le motif rend la décision relisible, et c'est elle qui "
             "sera discutée si le promettant acheteur estime avoir reçu trop "
             "peu.",
    )

    cost_amount = fields.Monetary(
        string="Frais réclamés",
        currency_field="currency_id",
        tracking=True,
        help="Art. 1068.2 : les documents sont fournis « aux frais » du "
             "promettant acheteur.",
    )
    cost_paid = fields.Boolean(string="Frais acquittés", tracking=True)

    line_ids = fields.One2many(
        "bf.property.disclosure.line", "disclosure_id", string="Documents fournis"
    )
    line_count = fields.Integer(compute="_compute_state")
    note = fields.Char(string="Note")

    @api.depends("unit_id.owner_ids")
    def _compute_owners(self):
        for disclosure in self:
            disclosure.owner_partner_ids = disclosure.unit_id.owner_ids

    @api.depends(
        "request_date",
        "provided_date",
        "owner_transmission_date",
        "cancelled",
        "refusal_reason",
        "line_ids",
    )
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for disclosure in self:
            disclosure.line_count = len(disclosure.line_ids)
            end = disclosure.provided_date or today
            disclosure.days_open = (
                (end - disclosure.request_date).days
                if disclosure.request_date
                else 0
            )
            if disclosure.cancelled:
                disclosure.state = "cancelled"
            elif disclosure.provided_date and disclosure.owner_transmission_date:
                disclosure.state = "complete"
            elif disclosure.provided_date:
                disclosure.state = "provided"
            elif disclosure.refusal_reason:
                disclosure.state = "refused"
            else:
                disclosure.state = "requested"

    @api.constrains("unit_id", "syndicat_id")
    def _check_unit_syndicat(self):
        for disclosure in self:
            if disclosure.unit_id.syndicat_id != disclosure.syndicat_id:
                raise ValidationError(
                    _("La fraction appartient à un autre syndicat.")
                )

    @api.constrains("provided_date", "owner_transmission_date")
    def _check_transmission_after(self):
        """Art. 1068.2 al. 2 : on transmet ce qu'on A FOURNI.

        ⚠️ C'est l'inverse du préavis de l'art. 1069 al. 2, qui doit précéder.
        Ici l'obligation naît de la remise et porte sur son contenu : la
        transmission ne peut pas la devancer.
        """
        for disclosure in self:
            if not disclosure.owner_transmission_date:
                continue
            if not disclosure.provided_date:
                raise ValidationError(
                    _(
                        "Art. 1068.2 al. 2 C.c.Q. : le syndicat transmet au "
                        "propriétaire « les documents ou renseignements qu'il "
                        "A FOURNIS ». Rien n'a encore été fourni."
                    )
                )
            if disclosure.owner_transmission_date < disclosure.provided_date:
                raise ValidationError(
                    _(
                        "La transmission au propriétaire ne peut pas précéder "
                        "la remise au promettant acheteur : l'art. 1068.2 "
                        "al. 2 porte sur ce qui a été fourni."
                    )
                )

    # ── Actions ──

    def action_provide(self):
        for disclosure in self:
            if disclosure.cancelled:
                raise UserError(_("Une demande annulée ne se fournit pas."))
            if not disclosure.line_ids:
                raise UserError(
                    _(
                        "Aucun document n'est inscrit. L'art. 1068.2 al. 2 "
                        "oblige à transmettre au propriétaire ce qui a été "
                        "fourni : il faut donc savoir ce que c'était."
                    )
                )
            if not disclosure.privacy_reviewed:
                raise UserError(
                    _(
                        "Art. 1068.2 C.c.Q. : le syndicat fournit « sous "
                        "réserve des dispositions relatives à la protection de "
                        "la vie privée ». L'autorisation de la loi au sens de "
                        "l'art. 37 ne couvre pas les renseignements personnels "
                        "des autres copropriétaires. Consignez la revue avant "
                        "de fournir."
                    )
                )
            disclosure.provided_date = fields.Date.context_today(disclosure)
            disclosure.message_post(
                body=_(
                    "%(count)d document(s) fourni(s) au promettant acheteur le "
                    "%(date)s, %(days)d jour(s) après la demande. "
                    "⚠️ Art. 1068.2 al. 2 : le syndicat doit maintenant "
                    "transmettre au propriétaire ce qu'il a fourni."
                )
                % {
                    "count": len(disclosure.line_ids),
                    "date": disclosure.provided_date,
                    "days": disclosure.days_open,
                }
            )
        return True

    def action_transmit_to_owner(self):
        """Art. 1068.2 al. 2, et il porte sur le contenu exact de la remise."""
        for disclosure in self:
            if not disclosure.provided_date:
                raise UserError(
                    _(
                        "Rien n'a été fourni au promettant acheteur : "
                        "l'art. 1068.2 al. 2 n'a pas d'objet."
                    )
                )
            disclosure.owner_transmission_date = fields.Date.context_today(
                disclosure
            )
            disclosure.message_post(
                body=_(
                    "<p>Transmis au propriétaire le %(date)s : %(owners)s. "
                    "Art. 1068.2 al. 2 C.c.Q.</p><ul>%(list)s</ul>"
                )
                % {
                    "date": disclosure.owner_transmission_date,
                    "owners": ", ".join(
                        disclosure.owner_partner_ids.mapped("name")
                    )
                    or _("aucun propriétaire au registre"),
                    "list": "".join(
                        "<li>%s</li>" % line.name for line in disclosure.line_ids
                    ),
                }
            )
        return True

    def action_cancel(self):
        self.write({"cancelled": True})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name") in (None, "", _("Nouvelle")):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "bf.property.disclosure"
                ) or _("Demande de documents")
        return super().create(vals_list)


class BfPropertyDisclosureLine(models.Model):
    _name = "bf.property.disclosure.line"
    _description = "Document fourni au promettant acheteur"
    _order = "disclosure_id, id"

    disclosure_id = fields.Many2one(
        "bf.property.disclosure",
        string="Demande",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="disclosure_id.company_id", store=True, string="Société"
    )
    name = fields.Char(string="Document ou renseignement", required=True)
    redacted = fields.Boolean(
        string="Caviardé",
        help="Le document a été fourni, mais des renseignements personnels en "
             "ont été retranchés au titre de la réserve de l'art. 1068.2.",
    )
    note = fields.Char(string="Précision")
