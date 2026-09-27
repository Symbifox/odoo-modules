"""L'organisation responsable d'un immeuble.

Un immeuble est tenu par quelqu'un, et ce quelqu'un n'est pas toujours de la
même nature. Un syndicat de copropriété est une personne morale créée de plein
droit par la publication de la déclaration (art. 1039 C.c.Q.) ; un bailleur est
propriétaire de son immeuble et le loue ; une coopérative et un OBNL
d'habitation sont l'un et l'autre à la fois. Ce modèle porte ce qu'ils ont en
commun : un nom, une personne morale, des immeubles, des logements, et des gens
qui y habitent.

⚠️ **Neutralisé avant toute publication, et le moment comptait.** Le modèle
s'appelait `bf.property.syndicat` tant que le produit ne servait que la
copropriété divise. Le locatif multifamilial est entré au périmètre, et le
portail de l'occupant — annonces, documents, demandes d'entretien, réservation
d'espaces communs, colis, visiteurs — vaut identiquement pour un immeuble
locatif. La seule alternative était d'en tenir deux copies, alors qu'un audit de
sécurité venait de trouver quatre défauts d'autorisation dans
exactement ces modèles-là : une garde recopiée est une garde qui manquera
quelque part. Rien n'était publié ni installé chez un client, donc le
renommage n'a coûté aucune migration. Il en aurait coûté une le lendemain.

⚠️ **« Organisation », et pas « gestionnaire ».** La clause d'usage additionnel
de la licence exclut l'administration d'immeubles pour le compte d'autrui. Un
nom qui suggérerait la gestion pour compte de tiers décrirait un usage que la
licence ne permet pas.

⚠️ **Ce qui reste ici et qui ne concerne que la copropriété.** La base des
quotes-parts et leur vérification ne veulent rien dire chez un bailleur. Elles
n'ont pas été déplacées dans cette étape : elle s'arrête à la neutralisation, et
sortir la mécanique des quotes-parts du socle est un deuxième chantier, à faire
quand le volet locatif aura besoin d'un socle plus petit. En attendant, ces
champs sont masqués hors du régime syndicat plutôt que retirés.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare

QUOTE_PART_DIGITS = 4


class BfPropertyOrganisation(models.Model):
    _name = "bf.property.organisation"
    _description = "Organisation responsable d'un immeuble"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(string="Nom", required=True, tracking=True)
    active = fields.Boolean(default=True)
    kind = fields.Selection(
        [
            ("syndicat", "Syndicat de copropriété"),
            ("landlord", "Bailleur"),
            ("cooperative", "Coopérative d'habitation"),
            ("nonprofit", "OBNL d'habitation"),
        ],
        string="Nature",
        default="syndicat",
        required=True,
        tracking=True,
        help="Ce qui décide des obligations qui s'appliquent. Un syndicat de "
             "copropriété tient une assemblée, répartit des charges communes et "
             "doit une attestation à chaque vente ; un bailleur signe des baux "
             "et donne des avis d'augmentation. Rien de ce qui suit n'est "
             "commun aux deux, et le module ne les mélange pas.",
    )
    is_syndicat = fields.Boolean(
        string="Régime de la copropriété divise",
        compute="_compute_is_syndicat",
        store=True,
        help="⚠️ Stocké, parce que les modules de copropriété cherchent dessus. "
             "Il ne dépend que de la nature, donc d'une écriture, jamais de la "
             "date du jour : aucun cron ne lui est nécessaire.",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Personne morale",
        tracking=True,
        help="Fiche du syndicat comme personne morale distincte des "
             "copropriétaires. Sert de tiers pour les contrats et la facturation.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        default=lambda self: self.env.company,
        required=True,
    )
    neq = fields.Char(
        string="NEQ",
        tracking=True,
        help="Numéro d'entreprise du Québec du organisation.",
    )
    declaration_date = fields.Date(
        string="Date de la déclaration",
        tracking=True,
        help="Date de publication de la déclaration de copropriété au registre "
             "foncier.",
    )
    fraction_base = fields.Integer(
        string="Base des quotes-parts",
        default=1000,
        required=True,
        tracking=True,
        help="Total que doivent atteindre les quotes-parts de toutes les "
             "fractions. 1000 pour des millièmes, 10000 pour des dix-millièmes.",
    )

    building_ids = fields.One2many(
        "bf.property.building", "organisation_id", string="Immeubles"
    )
    unit_ids = fields.One2many(
        "bf.property.unit", "organisation_id", string="Fractions"
    )
    building_count = fields.Integer(compute="_compute_counts")
    unit_count = fields.Integer(compute="_compute_counts")

    quote_part_total = fields.Float(
        string="Total des quotes-parts",
        compute="_compute_quote_part",
        store=True,
        digits=(16, 4),
    )
    quote_part_gap = fields.Float(
        string="Écart",
        compute="_compute_quote_part",
        store=True,
        digits=(16, 4),
        help="Total des quotes-parts moins la base déclarée. Zéro quand la "
             "répartition est complète.",
    )
    quote_part_state = fields.Selection(
        [
            ("empty", "Aucune fraction"),
            ("under", "Incomplet"),
            ("balanced", "Équilibré"),
            ("over", "Dépassement"),
        ],
        string="État des quotes-parts",
        compute="_compute_quote_part",
        store=True,
    )

    _sql_constraints = [
        (
            "fraction_base_positive",
            "CHECK(fraction_base > 0)",
            "La base des quotes-parts doit être supérieure à zéro.",
        ),
    ]

    @api.depends("kind")
    def _compute_is_syndicat(self):
        for organisation in self:
            organisation.is_syndicat = organisation.kind == "syndicat"

    @api.depends("building_ids", "building_ids.active", "unit_ids", "unit_ids.active")
    def _compute_counts(self):
        for organisation in self:
            organisation.building_count = len(organisation.building_ids.filtered("active"))
            organisation.unit_count = len(organisation.unit_ids.filtered("active"))

    @api.depends("unit_ids.quote_part", "unit_ids.active", "fraction_base")
    def _compute_quote_part(self):
        # `active` doit figurer aux dépendances ET au filtre : sans la
        # dépendance le total ne se recalcule pas quand on archive une
        # fraction, et sans le filtre un appel fait avec active_test=False
        # recompterait les fractions archivées.
        for organisation in self:
            total = sum(organisation.unit_ids.filtered("active").mapped("quote_part"))
            organisation.quote_part_total = total
            organisation.quote_part_gap = total - organisation.fraction_base
            comparison = float_compare(
                total, organisation.fraction_base, precision_digits=QUOTE_PART_DIGITS
            )
            if not organisation.unit_ids.filtered("active"):
                organisation.quote_part_state = "empty"
            elif comparison == 0:
                organisation.quote_part_state = "balanced"
            elif comparison < 0:
                organisation.quote_part_state = "under"
            else:
                organisation.quote_part_state = "over"

    @api.constrains("fraction_base")
    def _check_fraction_base(self):
        for organisation in self:
            if organisation.fraction_base <= 0:
                raise ValidationError(
                    _("La base des quotes-parts doit être supérieure à zéro.")
                )

    def action_view_units(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Fractions"),
            "res_model": "bf.property.unit",
            "view_mode": "list,form",
            "domain": [("organisation_id", "=", self.id)],
            "context": {"default_organisation_id": self.id},
        }

    def action_view_buildings(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Immeubles"),
            "res_model": "bf.property.building",
            "view_mode": "list,form",
            "domain": [("organisation_id", "=", self.id)],
            "context": {"default_organisation_id": self.id},
        }
