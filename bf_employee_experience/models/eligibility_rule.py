from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class EligibilityRule(models.Model):
    """Le droit exprimé en critères, jamais en domaine brut.

    Une règle est un ET de ses critères remplis ; les règles d'un même
    avantage sont OU. Un critère laissé vide ne contraint rien.

    Le choix des critères cochés plutôt que d'un domaine Odoo est délibéré :
    une personne qui conteste son droit doit pouvoir s'entendre expliquer
    pourquoi elle ne l'a pas. Un domaine ne s'explique pas.
    """

    _name = "bf.ex.eligibility.rule"
    _description = "Règle d'admissibilité"
    _order = "benefit_id, sequence, id"

    name = fields.Char(string="Nom", required=True)
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(string="Active", default=True)
    benefit_id = fields.Many2one(
        "bf.ex.benefit", string="Avantage", required=True, ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="benefit_id.company_id",
        store=True, readonly=True,
    )

    # ---- Les huit critères ----
    department_ids = fields.Many2many("hr.department", string="Départements")
    job_ids = fields.Many2many("hr.job", string="Postes")
    work_location_ids = fields.Many2many("hr.work.location", string="Lieux de travail")
    resource_calendar_ids = fields.Many2many(
        "resource.calendar", string="Horaires",
        help="Sert à distinguer le temps plein du temps partiel.",
    )
    manager_ids = fields.Many2many(
        "hr.employee", string="Gestionnaires",
        help="Les personnes qui relèvent d'un de ces gestionnaires.",
    )
    employee_type = fields.Selection(
        [
            ("employee", "Employé"),
            ("student", "Stagiaire étudiant"),
            ("trainee", "Stagiaire"),
            ("contractor", "Contractuel"),
            ("freelance", "Pigiste"),
        ],
        string="Type d'emploi",
        help="Une règle vise un seul type. Pour en couvrir plusieurs, "
             "ajouter une règle par type : les règles d'un avantage sont cumulatives.",
    )
    seniority_months_min = fields.Integer(
        string="Ancienneté minimale (mois)",
        help="Comptée depuis la date du premier contrat. Une personne sans contrat "
             "n'a aucune ancienneté connue et ne passe donc pas ce critère.",
    )

    criteria_summary = fields.Char(
        string="Résumé", compute="_compute_criteria_summary",
        help="La règle dite en une phrase, telle qu'on l'expliquerait à quelqu'un.",
    )
    employee_count = fields.Integer(string="Personnes visées", compute="_compute_employee_count")

    @api.constrains("seniority_months_min")
    def _check_seniority(self):
        for rule in self:
            if rule.seniority_months_min < 0:
                raise ValidationError(_("L'ancienneté minimale ne peut pas être négative."))

    @api.depends("department_ids", "job_ids", "work_location_ids", "resource_calendar_ids",
                 "manager_ids", "employee_type", "seniority_months_min")
    def _compute_criteria_summary(self):
        labels = dict(self._fields["employee_type"].selection)
        for rule in self:
            bits = []
            if rule.department_ids:
                bits.append(_("département : %s", ", ".join(rule.department_ids.mapped("name"))))
            if rule.job_ids:
                bits.append(_("poste : %s", ", ".join(rule.job_ids.mapped("name"))))
            if rule.employee_type:
                bits.append(_("type d'emploi : %s", labels[rule.employee_type]))
            if rule.resource_calendar_ids:
                bits.append(_("horaire : %s", ", ".join(rule.resource_calendar_ids.mapped("name"))))
            if rule.work_location_ids:
                bits.append(_("lieu : %s", ", ".join(rule.work_location_ids.mapped("name"))))
            if rule.manager_ids:
                bits.append(_("relève de : %s", ", ".join(rule.manager_ids.mapped("name"))))
            if rule.seniority_months_min:
                bits.append(_("au moins %s mois d'ancienneté", rule.seniority_months_min))
            rule.criteria_summary = " ; ".join(bits) if bits else _("tout le personnel")

    def _compute_employee_count(self):
        for rule in self:
            rule.employee_count = len(rule._matching_employees())

    # ------------------------------------------------------------------

    def _matching_employees(self):
        """Les employés que cette règle vise, aujourd'hui.

        Renvoie un recordset `hr.employee`. Un critère vide ne filtre rien.
        """
        self.ensure_one()
        domain = [("company_id", "=", self.company_id.id)]
        if self.department_ids:
            domain.append(("department_id", "in", self.department_ids.ids))
        if self.job_ids:
            domain.append(("job_id", "in", self.job_ids.ids))
        if self.work_location_ids:
            domain.append(("work_location_id", "in", self.work_location_ids.ids))
        if self.resource_calendar_ids:
            domain.append(("resource_calendar_id", "in", self.resource_calendar_ids.ids))
        if self.manager_ids:
            domain.append(("parent_id", "in", self.manager_ids.ids))
        if self.employee_type:
            domain.append(("employee_type", "=", self.employee_type))
        if self.seniority_months_min:
            # `first_contract_date` vient de `hr_contract`. Une personne sans
            # contrat le porte à False, et l'opérateur `<=` écarte les nuls en
            # SQL : c'est exactement le comportement voulu, une ancienneté
            # inconnue n'ouvre aucun droit.
            cutoff = fields.Date.context_today(self) - relativedelta(
                months=self.seniority_months_min
            )
            domain.append(("first_contract_date", "<=", cutoff))
        # Une personne partie ne reçoit plus de droit neuf.
        domain.append("|")
        domain.append(("departure_date", "=", False))
        domain.append(("departure_date", ">", fields.Date.context_today(self)))
        return self.env["hr.employee"].sudo().search(domain)
