from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class Membership(models.Model):
    """Le lien d'une personne à une unité de négociation.

    🔴 Deux états, pas un. `covered` dit que la personne fait partie de l'unité
    de négociation ; `is_member` dit qu'elle a adhéré au syndicat. L'article 47
    du Code du travail impose la retenue de la cotisation à TOUT salarié de
    l'unité, qu'il soit membre ou non. Un seul booléen pour les deux serait
    faux dès la première paie, et le corriger après coup coûterait une
    migration de données.

    🔴 L'ancienneté vit ici, jamais sur le contrat. `hr_contract` porte
    `first_contract_date`, que `bf_employee_experience` lit déjà pour ouvrir des
    avantages. Ce n'est pas la même ancienneté : l'une donne droit à une
    assurance, l'autre décide d'une mise à pied. Elles divergent dès qu'il y a
    eu une interruption, un transfert ou une reconnaissance négociée, et c'est
    la convention qui dit laquelle compte.
    """

    _name = "bf.labour.membership"
    _description = "Appartenance à une unité"
    _inherit = ["mail.thread"]
    _order = "unit_id, seniority_date, id"

    employee_id = fields.Many2one(
        "hr.employee", string="Personne", required=True,
        ondelete="cascade", index=True, tracking=True,
    )
    unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité de négociation", required=True,
        ondelete="cascade", index=True, tracking=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="unit_id.company_id",
        store=True, readonly=True, index=True,
    )
    union_id = fields.Many2one(
        "bf.labour.union", string="Syndicat", related="unit_id.union_id",
        store=True, readonly=True,
    )

    date_start = fields.Date(
        string="Entrée dans l'unité", required=True,
        default=fields.Date.context_today, tracking=True,
    )
    date_end = fields.Date(
        string="Sortie de l'unité", tracking=True,
        help="Laissée vide, la personne est toujours rattachée. Une sortie ne "
             "supprime pas la ligne : l'ancienneté d'un rappel se lit dedans.",
    )

    seniority_date = fields.Date(
        string="Date d'ancienneté", required=True, tracking=True,
        help="La date que la convention reconnaît, qui n'est pas forcément "
             "celle de l'entrée dans l'unité.",
    )
    seniority_hours = fields.Float(
        string="Heures d'ancienneté", tracking=True,
        help="Pour les conventions qui comptent l'ancienneté en heures plutôt "
             "qu'en date. Les deux coexistent : c'est la convention qui dit "
             "laquelle fait foi.",
    )
    seniority_reason = fields.Text(
        string="Motif de la date retenue",
        help="Obligatoire dès que la date d'ancienneté diffère de l'entrée dans "
             "l'unité. Une personne qui conteste son rang doit pouvoir "
             "s'entendre expliquer pourquoi.",
    )

    covered = fields.Boolean(
        string="Couverte par l'unité", default=True, tracking=True,
        help="Fait partie du groupe visé par l'accréditation. C'est cet état, "
             "et non l'adhésion, qui déclenche la retenue de la cotisation.",
    )
    is_member = fields.Boolean(
        string="Membre du syndicat", default=True, tracking=True,
        help="A adhéré au syndicat. C'est cet état qui ouvre le droit de vote. "
             "Une personne couverte qui n'a pas adhéré cotise quand même.",
    )
    card_date = fields.Date(string="Date d'adhésion")

    active = fields.Boolean(string="Actif", default=True)
    note = fields.Text(string="Note")

    # ⚠️ NON stocké : « en cours » se juge contre aujourd'hui.
    is_current = fields.Boolean(string="En cours", compute="_compute_is_current")
    seniority_years = fields.Float(
        string="Années d'ancienneté", compute="_compute_seniority_years",
        help="Calculée à la date du jour, pour lecture seulement.",
    )

    _sql_constraints = [
        (
            "employee_unit_unique",
            "unique(employee_id, unit_id, date_start)",
            "Cette personne est déjà rattachée à cette unité à cette date.",
        ),
    ]

    @api.depends("date_end")
    def _compute_is_current(self):
        today = fields.Date.context_today(self)
        for line in self:
            line.is_current = not line.date_end or line.date_end >= today

    @api.depends("seniority_date")
    def _compute_seniority_years(self):
        today = fields.Date.context_today(self)
        for line in self:
            if not line.seniority_date:
                line.seniority_years = 0.0
                continue
            line.seniority_years = (today - line.seniority_date).days / 365.25

    @api.onchange("date_start")
    def _onchange_date_start(self):
        # Le cas courant est que les deux dates coïncident. On la propose, on
        # ne l'impose pas : la corriger est justement ce qui exige un motif.
        if self.date_start and not self.seniority_date:
            self.seniority_date = self.date_start

    @api.constrains("seniority_date", "date_start", "seniority_reason")
    def _check_seniority_reason(self):
        for line in self:
            if not line.seniority_date or not line.date_start:
                continue
            if line.seniority_date != line.date_start and not (line.seniority_reason or "").strip():
                raise ValidationError(_(
                    "La date d'ancienneté de %(nom)s diffère de son entrée dans "
                    "l'unité. Écrivez le motif : c'est ce texte qu'on relira le "
                    "jour où le rang est contesté.",
                    nom=line.employee_id.display_name or _("cette personne"),
                ))

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for line in self:
            if line.date_end and line.date_start and line.date_end < line.date_start:
                raise ValidationError(_(
                    "La sortie de l'unité ne peut pas précéder l'entrée."
                ))

    @api.constrains("employee_id", "unit_id")
    def _check_company_consistency(self):
        """La personne et l'unité vivent dans la même société.

        Sans cette garde, un transfert entre établissements crée en silence une
        appartenance qui traverse les sociétés, et la liste d'ancienneté d'un
        établissement se met à contenir le personnel d'un autre.
        """
        for line in self:
            employee_company = line.employee_id.company_id
            if employee_company and line.unit_id.company_id != employee_company:
                raise ValidationError(_(
                    "%(nom)s appartient à %(sienne)s, mais cette unité est celle "
                    "de %(autre)s. Une appartenance ne traverse pas les sociétés : "
                    "un transfert se termine d'un côté et s'ouvre de l'autre, et "
                    "c'est la convention qui dit si l'ancienneté suit.",
                    nom=line.employee_id.display_name,
                    sienne=employee_company.display_name,
                    autre=line.unit_id.company_id.display_name,
                ))
