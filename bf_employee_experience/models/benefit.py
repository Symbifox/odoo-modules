from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class Benefit(models.Model):
    """Un avantage offert au personnel.

    Le catalogue, et rien de plus : ce à quoi une personne a droit vit dans
    `bf.ex.entitlement`, ce qu'elle en a fait dans `bf.ex.usage`.
    """

    _name = "bf.ex.benefit"
    _description = "Avantage"
    _inherit = ["mail.thread"]
    _order = "sequence, name"

    name = fields.Char(string="Nom", required=True, translate=True, tracking=True)
    code = fields.Char(string="Code", help="Repère court, libre. Sert aux imports.")
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(string="Actif", default=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        "res.currency", string="Devise", related="company_id.currency_id", readonly=True,
    )
    category = fields.Selection(
        [
            ("health", "Santé et assurance"),
            ("retirement", "Retraite et épargne"),
            ("time_off", "Congés"),
            ("wellness", "Mieux-être"),
            ("learning", "Formation et développement"),
            ("equipment", "Équipement et espace de travail"),
            ("transport", "Transport"),
            ("family", "Famille"),
            ("other", "Autre"),
        ],
        string="Catégorie", required=True, default="other", tracking=True,
    )
    description = fields.Html(string="Description", translate=True)
    provider_id = fields.Many2one(
        "res.partner", string="Fournisseur",
        help="Assureur, clinique, fournisseur de service. Facultatif.",
    )
    responsible_id = fields.Many2one(
        "res.users", string="Responsable", default=lambda self: self.env.user,
        help="La personne qui administre cet avantage. Approbateur par défaut.",
    )
    date_start = fields.Date(string="Offert à partir du")
    date_end = fields.Date(string="Offert jusqu'au")

    # ------------------------------------------------------------------
    # Coût
    # ------------------------------------------------------------------
    cost_model = fields.Selection(
        [
            ("per_employee_year", "Par personne et par année"),
            ("per_use", "À l'usage"),
            ("flat_year", "Forfaitaire annuel"),
            ("none", "Sans coût suivi"),
        ],
        string="Modèle de coût", required=True, default="per_employee_year", tracking=True,
    )
    cost_amount = fields.Monetary(
        string="Montant de référence", currency_field="currency_id",
        help="Selon le modèle : par personne et par année, par utilisation, "
             "ou pour l'année entière.",
    )

    # ------------------------------------------------------------------
    # Approbation
    # ------------------------------------------------------------------
    approval_required = fields.Boolean(
        string="Approbation requise", default=False, tracking=True,
        help="Une assurance collective ne se demande pas, elle s'applique. "
             "Un remboursement de formation, oui.",
    )
    approver_mode = fields.Selection(
        [
            ("responsible", "Le responsable de l'avantage"),
            ("manager", "Le gestionnaire direct de la personne"),
            ("both", "Les deux, en séquence"),
        ],
        string="Qui approuve", default="responsible",
        help="N'a d'effet que si l'approbation est requise.",
    )

    rule_ids = fields.One2many(
        "bf.ex.eligibility.rule", "benefit_id", string="Règles d'admissibilité",
    )
    entitlement_ids = fields.One2many("bf.ex.entitlement", "benefit_id", string="Droits")
    usage_ids = fields.One2many("bf.ex.usage", "benefit_id", string="Usages")

    # ------------------------------------------------------------------
    # Indicateurs
    # ------------------------------------------------------------------
    rule_count = fields.Integer(string="Règles", compute="_compute_counts")
    entitled_count = fields.Integer(
        string="Personnes ayant droit", compute="_compute_counts",
        help="Droits ouverts aujourd'hui.",
    )
    user_count = fields.Integer(
        string="Personnes qui s'en servent", compute="_compute_counts",
        help="Personnes distinctes ayant au moins un usage confirmé sur les douze derniers mois.",
    )
    uptake_rate = fields.Float(
        string="Taux d'adhésion (%)", compute="_compute_counts",
        help="Personnes qui s'en servent, sur personnes qui y ont droit.",
    )
    unused = fields.Boolean(
        string="Personne ne l'utilise", compute="_compute_counts",
        search="_search_unused",
        help="Des personnes y ont droit, aucune ne s'en est servie depuis un an.",
    )
    annual_cost = fields.Monetary(
        string="Coût annuel", currency_field="currency_id", compute="_compute_cost",
        help="Selon le modèle de coût, sur les douze derniers mois.",
    )
    cost_per_entitled = fields.Monetary(
        string="Coût par personne ayant droit", currency_field="currency_id",
        compute="_compute_cost",
    )

    _sql_constraints = [
        (
            "code_company_uniq",
            "unique(code, company_id)",
            "Le code d'un avantage doit être unique dans la société.",
        ),
    ]

    # ------------------------------------------------------------------

    @api.depends("rule_ids", "entitlement_ids.date_end", "usage_ids.state", "usage_ids.date")
    def _compute_counts(self):
        today = fields.Date.context_today(self)
        horizon = fields.Date.subtract(today, years=1)
        for benefit in self:
            benefit.rule_count = len(benefit.rule_ids)
            entitled = benefit.entitlement_ids.filtered(
                lambda e: e.date_start <= today and (not e.date_end or e.date_end >= today)
            )
            benefit.entitled_count = len(entitled.employee_id)
            users = benefit.usage_ids.filtered(
                lambda u: u.state == "confirmed" and u.date >= horizon
            ).employee_id
            benefit.user_count = len(users)
            benefit.uptake_rate = (
                100.0 * benefit.user_count / benefit.entitled_count
                if benefit.entitled_count else 0.0
            )
            benefit.unused = bool(benefit.entitled_count) and not benefit.user_count

    @api.depends("cost_model", "cost_amount", "usage_ids.amount", "usage_ids.state",
                 "usage_ids.date", "entitlement_ids.date_end")
    def _compute_cost(self):
        today = fields.Date.context_today(self)
        horizon = fields.Date.subtract(today, years=1)
        for benefit in self:
            if benefit.cost_model == "flat_year":
                cost = benefit.cost_amount
            elif benefit.cost_model == "per_employee_year":
                cost = benefit.cost_amount * benefit.entitled_count
            elif benefit.cost_model == "per_use":
                # Le coût réel des lignes prime toujours sur le montant de
                # référence : c'est le seul chiffre qui vient du terrain.
                lines = benefit.usage_ids.filtered(
                    lambda u: u.state == "confirmed" and u.date >= horizon
                )
                cost = sum(
                    line.amount if line.amount else benefit.cost_amount * line.quantity
                    for line in lines
                )
            else:
                cost = 0.0
            benefit.annual_cost = cost
            benefit.cost_per_entitled = (
                cost / benefit.entitled_count if benefit.entitled_count else 0.0
            )

    def _search_unused(self, operator, value):
        """« Personne ne l'utilise » doit se filtrer, pas seulement s'afficher.

        Le champ est calculé et non stocké : sans cette méthode, le filtre de la
        vue de recherche fait échouer le chargement de la vue à l'installation.
        """
        if operator not in ("=", "!="):
            raise ValueError(_("Opérateur non pris en charge : %s", operator))
        wanted = bool(value) if operator == "=" else not value
        matching = self.search([]).filtered(lambda b: b.unused == wanted)
        return [("id", "in", matching.ids)]

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for benefit in self:
            if benefit.date_start and benefit.date_end and benefit.date_end < benefit.date_start:
                raise ValidationError(
                    _("« %s » : la fin de l'offre précède son début.", benefit.name)
                )

    # ------------------------------------------------------------------

    def action_recompute_entitlements(self):
        """Bouton : rejouer les règles de ces avantages tout de suite."""
        self.env["bf.ex.entitlement"]._sync_from_rules(benefits=self)
        return True

    def action_view_entitlements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Droits"),
            "res_model": "bf.ex.entitlement",
            "view_mode": "list,form",
            "domain": [("benefit_id", "=", self.id)],
            "context": {"default_benefit_id": self.id},
        }

    def action_view_usage(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Usages"),
            "res_model": "bf.ex.usage",
            "view_mode": "list,form",
            "domain": [("benefit_id", "=", self.id)],
            "context": {"default_benefit_id": self.id},
        }
