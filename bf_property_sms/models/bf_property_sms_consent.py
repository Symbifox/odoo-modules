"""Le consentement à être joint par texto, et pourquoi il est un modèle.

⚠️ **Le numéro de téléphone n'est pas au registre de droit.** L'art. 1070 al. 1
C.c.Q. met au registre « le nom et l'adresse postale de chaque copropriétaire »
et de chaque occupant, et il ajoute que le registre « peut aussi contenir
d'autres renseignements personnels concernant un copropriétaire ou un autre
occupant de l'immeuble, **si celui-ci y consent expressément** ». Un numéro de
téléphone est exactement un de ces autres renseignements.

D'où un modèle plutôt qu'une case à cocher sur le partenaire. Trois raisons, et
chacune est une chose qu'une case ne sait pas faire :

1. **Le consentement se donne à un syndicat**, pas au monde. Quelqu'un qui
   possède une fraction ici et en loue une ailleurs peut vouloir être joint
   d'un côté et pas de l'autre.
2. **Il se date, et il se retire.** Un consentement dont on ne sait pas quand
   il a été donné ne vaut pas grand-chose, et un retrait qui efface la trace du
   consentement empêche de dire ce qui était permis au moment de l'envoi.
3. **Le numéro vit avec lui.** Le mettre sur le partenaire le rendrait visible
   à tout ce qui lit un partenaire ; ici il ne sert qu'à ce pour quoi il a été
   donné.

⚠️ **Aucun texto ne part sans lui.** Le réglage du syndicat ouvre le canal ; le
consentement ouvre la porte de la personne. Le premier sans le second n'envoie
rien, et c'est vérifié par un test.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

CHANNELS = [
    ("parcel", "Arrivée d'un colis"),
    ("urgent", "Avis urgent de l'immeuble"),
]


class BfPropertySmsConsent(models.Model):
    _name = "bf.property.sms.consent"
    _description = "Consentement à être joint par texto"
    _inherit = ["mail.thread"]
    _order = "organisation_id, partner_id"
    _rec_name = "partner_id"

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
    partner_id = fields.Many2one(
        "res.partner", string="Personne", required=True, index=True
    )
    phone = fields.Char(
        string="Numéro pour les avis",
        required=True,
        tracking=True,
        help="Art. 1070 al. 1 C.c.Q. : ce renseignement n'est au registre que "
             "parce que la personne y a consenti. Il ne sert qu'aux avis "
             "qu'elle a acceptés.",
    )
    channel_parcel = fields.Boolean(
        string="Colis", default=True, tracking=True,
        help="Prévenir cette personne quand un colis arrive à son nom.",
    )
    channel_urgent = fields.Boolean(
        string="Avis urgents", default=True, tracking=True,
        help="Prévenir cette personne d'un avis urgent de l'immeuble.",
    )
    given_date = fields.Date(
        string="Consenti le",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    withdrawn_date = fields.Date(
        string="Retiré le",
        tracking=True,
        help="Un retrait n'efface pas la ligne : il la ferme. Effacer "
             "empêcherait de dire ce qui était permis au moment d'un envoi "
             "déjà fait.",
    )
    active_consent = fields.Boolean(
        string="En vigueur",
        compute="_compute_active_consent",
        store=True,
    )
    note = fields.Text(string="Comment le consentement a été recueilli")

    _sql_constraints = [
        (
            "unique_partner_per_syndicat",
            "UNIQUE(organisation_id, partner_id)",
            "Cette personne a déjà une ligne de consentement pour ce syndicat.",
        ),
    ]

    @api.depends("given_date", "withdrawn_date")
    def _compute_active_consent(self):
        """⚠️ Stocké, et il ne dépend PAS de la date du jour.

        Un consentement est en vigueur tant qu'il n'a pas été retiré. Le faire
        dépendre d'aujourd'hui en ferait un champ à rafraîchir par cron, pour
        rien : ce qui le ferme est une écriture, pas le passage du temps.
        """
        for consent in self:
            consent.active_consent = bool(
                consent.given_date and not consent.withdrawn_date
            )

    @api.constrains("given_date", "withdrawn_date")
    def _check_dates(self):
        for consent in self:
            if consent.withdrawn_date and consent.withdrawn_date < consent.given_date:
                raise ValidationError(
                    _("Un consentement ne se retire pas avant d'être donné.")
                )

    @api.constrains("partner_id", "organisation_id")
    def _check_person_is_here(self):
        """On ne recueille pas le numéro de quelqu'un qui n'habite pas là."""
        units = self.env["bf.property.unit"].sudo()
        for consent in self:
            if consent.organisation_id.id not in units._portal_audiences_for(
                consent.partner_id
            ):
                raise ValidationError(
                    _("%(who)s n'a aucune fraction dans %(syndicat)s.")
                    % {
                        "who": consent.partner_id.display_name,
                        "syndicat": consent.organisation_id.display_name,
                    }
                )

    def action_withdraw(self):
        for consent in self:
            if consent.withdrawn_date:
                raise ValidationError(
                    _("Le consentement de %s est déjà retiré.")
                    % consent.partner_id.display_name
                )
            consent.withdrawn_date = fields.Date.context_today(consent)
            consent.message_post(body=_("Consentement retiré."))
        return True

    @api.model
    def _for(self, partner, syndicat, channel):
        """La ligne en vigueur, ou rien. C'est la seule porte d'entrée.

        ⚠️ Aucun envoi ne doit chercher un numéro ailleurs qu'ici. Prendre
        `partner.mobile` parce qu'il est là serait exactement se passer du
        consentement que l'art. 1070 al. 1 exige.
        """
        if not partner or not syndicat:
            return self.browse()
        field = "channel_%s" % channel
        if field not in self._fields:
            raise ValueError(_("Canal inconnu : %s") % channel)
        return self.sudo().search(
            [
                ("partner_id", "=", partner.id),
                ("organisation_id", "=", syndicat.id),
                ("active_consent", "=", True),
                (field, "=", True),
            ],
            limit=1,
        )
