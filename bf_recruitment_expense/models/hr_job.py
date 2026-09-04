# Part of bf_recruitment_expense. Voir LICENSE.
from odoo import _, api, fields, models

from .hr_expense import EXPENSE_EXCLUDED_STATES

# Seules les séances TENUES ont coûté du temps. Une séance planifiée n'a encore
# rien consommé ; une séance annulée ou manquée par le candidat non plus.
INTERVIEW_BILLABLE_STATE = "tenue"

# 🔴 `hourly_cost` est réservé par le coeur à `hr.group_hr_user`. Tout champ qui
# en dérive porte la même restriction, sinon le module rend par arithmétique une
# donnée salariale que le coeur protège.
SALARY_DERIVED_GROUPS = "hr.group_hr_user"


class HrJob(models.Model):
    """Le poste à pourvoir devient le porteur du coût du recrutement.

    ⚠️ `analytic.mixin` s'ajoute ici à un modèle qui appartient à un autre
    module : c'est le patron exact de `mrp_account` sur `mrp.workcenter`. Le
    `_compute_analytic_distribution` du mixin ne fait rien, et c'est voulu : le
    champ reste saisissable à la main.
    """

    _name = "hr.job"
    _inherit = ["hr.job", "analytic.mixin"]

    # ⚠️ Le mixin nomme son champ « Analytic Distribution », et la traduction
    # française du coeur ne couvre que les modèles où le coeur pose lui-même le
    # mixin (`hr.expense`, par exemple). Sur `hr.job`, qui le reçoit de NOUS,
    # l'étiquette restait en anglais au milieu d'un écran français. On la pose
    # donc ici : redéclarer un champ hérité en ne donnant qu'un attribut fusionne
    # avec la définition amont, le calcul et le stockage sont conservés.
    analytic_distribution = fields.Json(string="Répartition analytique")

    # `hr.job` ne porte aucune devise. Les champs monétaires en exigent une.
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Devise", readonly=True,
    )

    expense_ids = fields.One2many(
        "hr.expense", "job_id", string="Débours de recrutement",
    )
    expense_count = fields.Integer(
        string="Nombre de débours", compute="_compute_expense_figures",
    )
    recruitment_expense_total = fields.Monetary(
        string="Débours", compute="_compute_expense_figures",
        currency_field="currency_id",
        help="Somme des notes de frais rattachées à ce poste, les refusées "
             "exclues. Une dépense refusée n'est pas un débours.",
    )

    interview_ids = fields.One2many(
        "bf.interview", "job_id", string="Séances d'entrevue",
    )
    panel_hours = fields.Float(
        string="Heures-personnes de panel", compute="_compute_panel_figures",
        digits=(8, 2),
        help="La durée de chaque séance TENUE, multipliée par le nombre de "
             "personnes qui y siégeaient. Aucune saisie : le cahier "
             "d'entrevues porte déjà les deux.",
    )
    panel_hours_unpriced = fields.Float(
        string="Heures non valorisées", compute="_compute_panel_figures",
        digits=(8, 2),
        help="Les heures-personnes qu'aucun taux ne couvre : ni coût horaire "
             "d'employé, ni taux de repli de la société.",
    )
    panel_cost = fields.Monetary(
        string="Coût du panel", compute="_compute_panel_figures",
        currency_field="currency_id", groups=SALARY_DERIVED_GROUPS,
    )
    recruitment_cost_total = fields.Monetary(
        string="Coût total du recrutement", compute="_compute_cost_per_hire",
        currency_field="currency_id", groups=SALARY_DERIVED_GROUPS,
    )
    cost_per_hire = fields.Monetary(
        string="Coût par embauche", compute="_compute_cost_per_hire",
        currency_field="currency_id", groups=SALARY_DERIVED_GROUPS,
        help="Débours plus temps de panel, divisé par le nombre d'embauches "
             "du poste. Sans embauche, il n'y a pas de coût par embauche : le "
             "champ reste à zéro et l'avertissement le dit.",
    )
    cost_is_partial = fields.Boolean(
        string="Chiffre incomplet", compute="_compute_cost_per_hire",
    )
    cost_warning = fields.Text(
        string="Ce qui manque au chiffre", compute="_compute_cost_per_hire",
        help="Écrit en heures, jamais en argent : cet avertissement est "
             "visible au recrutement, alors que les montants ne le sont pas.",
    )

    # ------------------------------------------------------------------
    # Les débours
    # ------------------------------------------------------------------

    @api.depends("expense_ids.total_amount", "expense_ids.state")
    def _compute_expense_figures(self):
        for job in self:
            # ⚠️ `sudo` : les notes de frais d'autrui ne sont pas lisibles par
            # un recruteur. Sans ça, le total serait partiel et silencieux,
            # exactement le défaut que ce module existe pour ne pas commettre.
            expenses = self.env["hr.expense"].sudo().search([
                ("job_id", "=", job.id),
                ("state", "not in", EXPENSE_EXCLUDED_STATES),
            ])
            job.expense_count = len(expenses)
            job.recruitment_expense_total = sum(expenses.mapped("total_amount"))

    # ------------------------------------------------------------------
    # Le temps de panel
    # ------------------------------------------------------------------

    def _panel_rates(self, users, company):
        """Le taux de chaque membre de panel : l'employé d'abord, puis le repli.

        ⚠️ `sudo` est obligatoire, pas commode : `hourly_cost` est réservé à
        `hr.group_hr_user` par le coeur. Un recruteur qui lit un poste ne peut
        pas lire le champ, et sans `sudo` le taux tomberait à zéro pour lui
        seul : le même poste afficherait deux coûts selon qui le regarde.

        ⚠️ Le dossier d'employé est cherché DANS LA SOCIÉTÉ DU POSTE. Une
        personne employée ailleurs dans le groupe n'a pas de taux ici, et
        retombe sur le repli : son coût horaire appartient à son employeur, pas
        à la société qui recrute.
        """
        rates = dict.fromkeys(users.ids, 0.0)
        if users and company:
            employees = self.env["hr.employee"].sudo().search([
                ("user_id", "in", users.ids),
                ("company_id", "=", company.id),
            ])
            for employee in employees:
                user_id = employee.user_id.id
                if rates.get(user_id):
                    continue
                if employee.hourly_cost > 0:
                    rates[user_id] = employee.hourly_cost
        fallback = company.recruitment_panel_hourly_cost if company else 0.0
        if fallback > 0:
            rates = {user_id: rate or fallback for user_id, rate in rates.items()}
        return rates

    @api.depends(
        "interview_ids.state", "interview_ids.duration",
        "interview_ids.interviewer_ids", "company_id",
        "company_id.recruitment_panel_hourly_cost",
    )
    def _compute_panel_figures(self):
        for job in self:
            # ⚠️ `sudo` de nouveau, et pour la même raison : la règle
            # `rule_interview_interviewer` ne montre à un membre de panel que
            # les séances où il siège. Lu sans `sudo`, le total des heures d'un
            # poste dépendrait de qui le regarde.
            interviews = self.env["bf.interview"].sudo().search([
                ("job_id", "=", job.id),
                ("state", "=", INTERVIEW_BILLABLE_STATE),
            ])
            panelists = interviews.mapped("interviewer_ids")
            rates = job._panel_rates(panelists, job.company_id)

            hours = 0.0
            unpriced = 0.0
            cost = 0.0
            for interview in interviews:
                for panelist in interview.interviewer_ids:
                    hours += interview.duration
                    rate = rates.get(panelist.id, 0.0)
                    if rate > 0:
                        cost += interview.duration * rate
                    else:
                        unpriced += interview.duration
            job.panel_hours = hours
            job.panel_hours_unpriced = unpriced
            job.panel_cost = cost

    # ------------------------------------------------------------------
    # Le coût par embauche, et ce qu'il avoue
    # ------------------------------------------------------------------

    @api.depends(
        "recruitment_expense_total", "panel_cost", "panel_hours",
        "panel_hours_unpriced", "no_of_hired_employee",
    )
    def _compute_cost_per_hire(self):
        for job in self:
            # `panel_cost` porte `groups`, donc il est illisible pour qui n'est
            # pas gestionnaire RH. On le relit en `sudo` plutôt que de laisser
            # le total tomber à zéro sans le dire.
            panel_cost = job.sudo().panel_cost
            total = job.recruitment_expense_total + panel_cost
            hires = job.no_of_hired_employee
            job.recruitment_cost_total = total
            job.cost_per_hire = (total / hires) if hires else 0.0
            job.cost_is_partial = bool(job.panel_hours_unpriced) or not hires
            job.cost_warning = job._cost_warning_text()

    def _cost_warning_text(self):
        """La phrase qui dit ce qui manque. En heures, jamais en argent."""
        self.ensure_one()
        messages = []
        if self.panel_hours_unpriced:
            messages.append(_(
                "%(unpriced).2f h de panel sur %(total).2f ne sont pas "
                "valorisées : ces personnes n'ont pas de coût horaire dans "
                "cette société, et aucun taux de repli n'y est posé. Le coût "
                "est donc plus bas que la réalité.",
                unpriced=self.panel_hours_unpriced, total=self.panel_hours,
            ))
        if not self.no_of_hired_employee:
            messages.append(_(
                "Aucune embauche à ce poste : il n'y a pas encore de coût par "
                "embauche, seulement une dépense engagée qui court."
            ))
        return "\n".join(messages)

    # ------------------------------------------------------------------
    # Les boutons
    # ------------------------------------------------------------------

    def action_view_recruitment_expenses(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Débours de recrutement"),
            "res_model": "hr.expense",
            "view_mode": "list,form",
            "domain": [("job_id", "=", self.id)],
            "context": {"default_job_id": self.id},
        }
