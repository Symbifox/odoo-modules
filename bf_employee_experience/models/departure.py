from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

FIRED_CODE = 342  # hr.departure.reason « Licencié », seul motif involontaire d'Odoo
WORK_DAYS = 260.0
MIN_DEPARTURES = 3

METHODS = [
    ("none", "Non calculé"),
    ("itemized", "Composantes réelles"),
    ("salary_pct", "Pourcentage du salaire annuel"),
    ("hybrid", "Mixte : direct réel, indirect en pourcentage"),
]


class DeparturePolicy(models.Model):
    """La méthode du coût d'un départ, une par société.

    Trois méthodes, jamais additionnées. Le pourcentage du salaire (CAP 2012 :
    médianes de 16 à 20 %, surtout du direct) résume le calcul détaillé, il ne
    s'y ajoute pas. Le mixte prend le direct réel et n'estime que l'indirect ;
    il écarte alors l'intérim et la montée en compétence, qui sont de
    l'indirect. Sources : CAP 2012, Work Institute, CCRHA.
    """

    _name = "bf.ex.departure.policy"
    _description = "Méthode du coût d'un départ"
    _rec_name = "company_id"

    company_id = fields.Many2one(
        "res.company", string="Société", required=True, default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id")
    method = fields.Selection(METHODS, string="Méthode", default="none", required=True)

    tier1_max = fields.Monetary(
        string="Tranche 1 : salaire annuel jusqu'à", default=40000.0,
        currency_field="currency_id",
        help="Environ 30 000 $ US, la tranche basse du CAP 2012.",
    )
    tier1_pct = fields.Float(string="Tranche 1 (%)", default=16.0)
    tier2_max = fields.Monetary(
        string="Tranche 2 : salaire annuel jusqu'à", default=100000.0,
        currency_field="currency_id",
        help="Environ 75 000 $ US, la tranche médiane du CAP 2012.",
    )
    tier2_pct = fields.Float(string="Tranche 2 (%)", default=20.0)
    tier3_pct = fields.Float(
        string="Au-delà (%)", default=33.3,
        help="Le CAP observe jusqu'à 213 % pour la haute direction. 33,3 % est "
             "l'estimation globale du Work Institute, prudente.",
    )
    total_pct = fields.Float(
        string="Estimation haute, direct et indirect (%)", default=33.3,
        help="Work Institute : environ 11 % de direct et 22 % d'indirect. Borne "
             "haute de la fourchette en mode pourcentage.",
    )
    indirect_pct = fields.Float(
        string="Indirect seulement (%)", default=22.0,
        help="Mode mixte. Jamais les 16-20 % du CAP ici : ils mesurent surtout "
             "du direct, déjà compté au réel.",
    )
    ramp_days = fields.Integer(string="Montée en compétence (jours ouvrés)", default=60)
    productivity_loss_pct = fields.Float(string="Productivité perdue pendant ce temps (%)", default=50.0)
    benefits_load_pct = fields.Float(
        string="Charge des avantages sur le salaire (%)", default=15.0,
        help="Valeur par défaut du calculateur du CCRHA.",
    )
    min_departures = fields.Integer(
        string="Départs minimum pour publier", default=MIN_DEPARTURES,
        help="Sous ce nombre de départs sur douze mois, aucun montant ne "
             "s'affiche : un seul départ redonnerait un salaire.",
    )

    _sql_constraints = [
        ("company_uniq", "unique(company_id)", "Une seule méthode par société."),
    ]

    @api.constrains("min_departures")
    def _check_min_departures(self):
        if any(p.min_departures < MIN_DEPARTURES for p in self):
            raise ValidationError(_(
                "Jamais moins de %s départs : en dessous, le montant laisse "
                "deviner le salaire d'une personne.",
            ) % MIN_DEPARTURES)

    @api.model
    def _for_company(self, company):
        policy = self.sudo().search([("company_id", "=", company.id)], limit=1)
        return policy or self.sudo().create({"company_id": company.id})

    def _salary_pct(self, annual):
        self.ensure_one()
        if annual <= self.tier1_max:
            return self.tier1_pct
        if annual <= self.tier2_max:
            return self.tier2_pct
        return self.tier3_pct

    def _range(self, departures):
        """(bas, haut) pour un lot de départs. Le bas est ce qui se mesure ou le
        direct ; le haut y ajoute l'indirect estimé. Lu en sudo : le salaire
        n'est jamais rendu, seulement une somme."""
        self.ensure_one()
        low = high = 0.0
        for departure in departures.sudo():
            annual = departure._annual_salary()
            measured = departure.measured_direct
            if self.method == "itemized":
                daily = annual * (1 + self.benefits_load_pct / 100.0) / WORK_DAYS
                ramp = self.ramp_days * daily * self.productivity_loss_pct / 100.0
                low += measured + departure.interim_cost
                high += measured + departure.interim_cost + ramp
            elif self.method == "salary_pct":
                pct = self._salary_pct(annual)
                low += annual * pct / 100.0
                high += annual * max(pct, self.total_pct) / 100.0
            elif self.method == "hybrid":
                low += measured
                high += measured + annual * self.indirect_pct / 100.0
        return low, high


class Departure(models.Model):
    """Un départ, et ce qu'il a réellement coûté.

    Le montant estimé d'un départ isolé n'est jamais affiché : il se déduit du
    salaire. Seules les composantes saisies à la main se lisent ici.
    """

    _name = "bf.ex.departure"
    _description = "Départ"
    _order = "departure_date desc, id desc"
    _rec_name = "employee_id"

    employee_id = fields.Many2one(
        "hr.employee", string="Employé", required=True, ondelete="cascade", index=True,
        context={"active_test": False},
    )
    company_id = fields.Many2one(
        "res.company", related="employee_id.company_id", store=True, readonly=True,
    )
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id")
    departure_date = fields.Date(
        related="employee_id.departure_date", store=True, readonly=True,
    )
    kind = fields.Selection(
        [("voluntary", "Volontaire"), ("involuntary", "Involontaire")],
        string="Nature", required=True, default="voluntary",
    )
    regrettable = fields.Boolean(
        string="Départ regretté",
        help="Au sens d'ISO/TS 30421 : une personne qu'on aurait voulu garder.",
    )
    separation_cost = fields.Monetary(
        string="Préavis et indemnités", currency_field="currency_id",
        help="Départ involontaire seulement (LNT art. 82-83, contrat). "
             "L'indemnité de vacances est déjà acquise : ne pas l'inscrire.",
    )
    recruitment_cost = fields.Monetary(
        string="Recrutement du remplaçant", currency_field="currency_id",
        help="Affichage, agence, temps d'entrevue, vérifications.",
    )
    training_cost = fields.Monetary(string="Formation du remplaçant", currency_field="currency_id")
    supervision_cost = fields.Monetary(
        string="Encadrement et mentorat", currency_field="currency_id",
        help="Heures du superviseur et du mentor, au coût horaire.",
    )
    interim_cost = fields.Monetary(
        string="Poste vacant : intérim et heures supplémentaires",
        currency_field="currency_id",
        help="Compté en mode « Composantes réelles » seulement : en mode mixte, "
             "le pourcentage indirect le couvre déjà.",
    )
    measured_direct = fields.Monetary(
        string="Direct mesuré", compute="_compute_measured_direct", store=True,
        currency_field="currency_id",
    )

    _sql_constraints = [
        ("employee_date_uniq", "unique(employee_id, departure_date)", "Un départ par personne et par date."),
    ]

    @api.depends("kind", "separation_cost", "recruitment_cost", "training_cost", "supervision_cost")
    def _compute_measured_direct(self):
        for departure in self:
            separation = departure.separation_cost if departure.kind == "involuntary" else 0.0
            departure.measured_direct = (
                separation + departure.recruitment_cost
                + departure.training_cost + departure.supervision_cost
            )

    def _annual_salary(self):
        """Salaire annuel du dernier contrat. À n'appeler qu'en sudo."""
        self.ensure_one()
        contract = self.env["hr.contract"].sudo().search(
            [("employee_id", "=", self.employee_id.id)], order="date_start desc", limit=1,
        )
        if not contract:
            return 0.0
        # wage_type n'existe qu'avec la paie (Enterprise) ; en CE, wage est mensuel.
        if "wage_type" in contract._fields and contract.wage_type == "hourly":
            calendar = contract.resource_calendar_id or self.employee_id.resource_calendar_id
            hours = (calendar.hours_per_day or 8.0) * WORK_DAYS
            return contract.hourly_wage * hours
        return contract.wage * 12.0

    @api.model
    def _ensure_for(self, employees):
        """Une fiche de départ par personne partie, avec la nature déduite du motif."""
        existing = self.sudo().search([("employee_id", "in", employees.ids)])
        known = {(d.employee_id.id, d.departure_date) for d in existing}
        vals = [
            {
                "employee_id": e.id,
                "kind": "involuntary" if e.departure_reason_id.reason_code == FIRED_CODE else "voluntary",
            }
            for e in employees
            if e.departure_date and (e.id, e.departure_date) not in known
        ]
        return existing | self.sudo().create(vals)


class DeparturePolicyAction(models.Model):
    _inherit = "bf.ex.departure.policy"

    @api.model
    def action_open_policy(self):
        policy = self._for_company(self.env.company)
        return {
            "type": "ir.actions.act_window",
            "name": _("Coût d'un départ : méthode"),
            "res_model": self._name,
            "res_id": policy.id,
            "view_mode": "form",
            "target": "current",
        }
