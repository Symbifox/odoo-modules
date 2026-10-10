# -*- coding: utf-8 -*-
"""Rattachement d'une campagne à un compte analytique.

La campagne existe déjà dans Odoo et connaît sa recette. Ce qui lui manque est
sa dépense. Plutôt qu'un registre de dépenses parallèle, qui finirait par
diverger du grand livre, on lui attache un compte analytique : la dépense entre
alors par la facture fournisseur ou la note de frais, avec ses taxes, et remonte
toute seule.

🔴 La règle du socle est reprise telle quelle : **une lecture, une source**. Ici
les deux totaux sont disjoints par construction, la comptabilité d'un côté
(`move_line_id` posé) et le coût interne de l'autre (`move_line_id` absent).
Aucun dollar ne peut entrer dans les deux.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Le plan des campagnes est retenu ici, comme Odoo retient son plan projet dans
# `analytic.project_plan`. Chercher le plan par son nom serait fragile : il se
# renomme, et il se traduit.
PARAM_PLAN = "bf_budget_campaign.plan_id"


class UtmCampaign(models.Model):
    _inherit = "utm.campaign"

    # ⚠️ `utm.campaign` de base n'a NI `company_id` NI `currency_id` : les deux
    # arrivent avec `sale`. Un Monetary qui hérite du `currency_field` par défaut
    # fait échouer le MONTAGE DU REGISTRE, pas une lecture. On porte la nôtre.
    bf_currency_id = fields.Many2one(
        "res.currency", string="Budget currency", compute="_compute_bf_currency_id",
    )
    analytic_account_id = fields.Many2one(
        "account.analytic.account",
        string="Analytic account",
        copy=False,
        help="The account that carries this campaign's spend. An expense "
             "charged to this account shows up here with no double entry.",
    )

    bf_cost_accounting = fields.Monetary(
        string="Posted spend", compute="_compute_bf_cost",
        currency_field="bf_currency_id",
        help="Analytic lines backed by a journal entry. This is the money "
             "spent, taxes included, as the general ledger records it.",
    )
    bf_cost_internal = fields.Monetary(
        string="Internal cost", compute="_compute_bf_cost",
        currency_field="bf_currency_id",
        help="Analytic lines WITHOUT a journal entry: timesheets and "
             "manual entries. Separate from posted spend.",
    )
    bf_cost_total = fields.Monetary(
        string="Total spend", compute="_compute_bf_cost",
        currency_field="bf_currency_id",
    )
    bf_hours_internal = fields.Float(
        string="Internal hours", compute="_compute_bf_cost",
    )
    bf_unvalued_hours = fields.Float(
        string="Uncosted hours", compute="_compute_bf_cost",
        help="Logged hours whose cost is zero. Odoo costs time at the "
             "employee's hourly cost: when that rate is missing, the "
             "campaign reads zero and looks perfectly normal.",
    )
    bf_has_unvalued_time = fields.Boolean(compute="_compute_bf_cost")

    # --- estimation des heures qu'Odoo n'a pas pu valoriser ---------------
    # 🔴 Une estimation ne doit JAMAIS se faire passer pour un montant engagé.
    # Elle vit donc dans ses propres champs, à côté de la dépense réelle, et le
    # total réel reste intact.
    bf_estimate_rate = fields.Float(
        string="Estimation rate", compute="_compute_bf_cost",
        help="The default cost rate set for this instance. It only "
             "applies to hours with no hourly cost.",
    )
    bf_cost_internal_estimated = fields.Monetary(
        string="Estimated internal cost", compute="_compute_bf_cost",
        currency_field="bf_currency_id",
        help="Uncosted hours at the default rate. It is an estimate, "
             "never a posted amount.",
    )
    bf_cost_total_estimated = fields.Monetary(
        string="Estimated total spend", compute="_compute_bf_cost",
        currency_field="bf_currency_id",
        help="Actual spend plus the estimate of uncosted hours. Not to be "
             "confused with total spend, which carries actuals only.",
    )

    bf_budget_line_ids = fields.Many2many(
        "bf.budget.line", string="Budget lines",
        compute="_compute_bf_budget_lines",
        help="The budget lines whose analytic axis names this campaign's "
             "account.",
    )
    bf_amount_planned = fields.Monetary(
        string="Planned", compute="_compute_bf_budget_lines",
        currency_field="bf_currency_id",
    )
    bf_budget_count = fields.Integer(
        string="Budget lines", compute="_compute_bf_budget_lines",
    )

    _sql_constraints = [
        (
            "analytic_account_uniq",
            "unique(analytic_account_id)",
            "This analytic account is already used by another campaign. "
            "An account can carry only one campaign, otherwise both would "
            "show the same spend.",
        ),
    ]

    # --- garde ------------------------------------------------------------
    @api.constrains("analytic_account_id")
    def _check_colonne_du_plan(self):
        """Refuse un compte dont le plan racine n'est pas celui du projet.

        Odoo range les lignes analytiques dans une colonne par plan racine. Un
        compte rattaché à un autre plan racine ne serait lu ni par le socle ni
        ici : la campagne afficherait une dépense nulle, sans erreur. Mieux vaut
        refuser à la saisie que mentir à la lecture.
        """
        for campagne in self:
            compte = campagne.analytic_account_id
            if compte and compte.plan_id._column_name() != "account_id":
                raise ValidationError(_(
                    "The \"%(compte)s\" account belongs to the "
                    "\"%(plan)s\" plan, a root plan separate from the "
                    "project plan. Analytic lines of such a plan are "
                    "stored in another column, and the campaign's spend "
                    "would silently stay at zero. Move this plan under "
                    "the project plan, or choose an account from the "
                    "project plan.",
                    compte=compte.display_name, plan=compte.plan_id.display_name,
                ))

    # --- plan des campagnes ----------------------------------------------
    @api.model
    def _bf_campaign_plan(self):
        """Le plan « Campagnes », créé à la demande sous le plan projet.

        Créé paresseusement plutôt qu'à l'installation : un crochet
        post-installation qui échoue bloque une installation neuve, et ce plan
        n'a de raison d'être qu'au premier compte créé.
        """
        Params = self.env["ir.config_parameter"].sudo()
        Plan = self.env["account.analytic.plan"].sudo()
        plan = Plan.browse(int(Params.get_param(PARAM_PLAN, 0))).exists()
        if plan:
            return plan
        parent = Plan.browse(int(Params.get_param("analytic.project_plan", 0))).exists()
        if not parent:
            raise UserError(_(
                "The project analytic plan cannot be found. Odoo points "
                "to it with the \"analytic.project_plan\" system "
                "parameter; without it, there is no way to know which "
                "plan to put campaigns under."
            ))
        # Le plan est partagé : chaque langue installée reçoit son nom, plutôt
        # que la seule langue de qui clique le premier (un `create` en fr_CA
        # écrit le français aussi en en_US).
        plan = Plan.with_context(lang="en_US").create({"name": "Campaigns", "parent_id": parent.id})
        plan.update_field_translations("name", {
            code: self.with_context(lang=code).env._("Campaigns")
            for code, _nom in self.env["res.lang"].get_installed() if code != "en_US"
        })
        Params.set_param(PARAM_PLAN, str(plan.id))
        return plan

    def action_bf_create_analytic_account(self):
        """Crée le compte analytique de la campagne et le lui attache."""
        Compte = self.env["account.analytic.account"]
        for campagne in self:
            if campagne.analytic_account_id:
                continue
            campagne.analytic_account_id = Compte.create({
                "name": campagne.name,
                "plan_id": campagne._bf_campaign_plan().id,
                "company_id": self.env.company.id,
            })
        return True

    # --- lecture ----------------------------------------------------------
    @api.depends("analytic_account_id")
    def _compute_bf_currency_id(self):
        for campagne in self:
            societe = campagne.analytic_account_id.company_id or self.env.company
            campagne.bf_currency_id = societe.currency_id

    def _bf_analytic_domain(self):
        """Le domaine analytique de la campagne, sur la colonne de son plan."""
        self.ensure_one()
        if not self.analytic_account_id:
            return None
        colonne = self.analytic_account_id.plan_id._column_name()
        return [(colonne, "=", self.analytic_account_id.id)]

    @api.depends("analytic_account_id")
    def _compute_bf_cost(self):
        Ligne = self.env["account.analytic.line"]
        for campagne in self:
            comptable = interne = heures = non_valorisees = 0.0
            domaine = campagne._bf_analytic_domain()
            if domaine is not None:
                # Une dépense arrive en négatif dans l'analytique ; on la rend en
                # positif, un coût se lit mieux ainsi. Le groupement sur la
                # présence de la pièce comptable garantit la disjonction.
                for adossee, montant, quantite in Ligne._read_group(
                    domaine, ["move_line_id"], ["amount:sum", "unit_amount:sum"]
                ):
                    if adossee:
                        comptable -= montant
                    else:
                        interne -= montant
                        heures += quantite
                muettes = Ligne.search(
                    domaine + [("move_line_id", "=", False),
                               ("amount", "=", 0.0), ("unit_amount", ">", 0.0)]
                )
                non_valorisees = sum(muettes.mapped("unit_amount"))
            campagne.bf_cost_accounting = comptable
            campagne.bf_cost_internal = interne
            campagne.bf_cost_total = comptable + interne
            campagne.bf_hours_internal = heures
            campagne.bf_unvalued_hours = non_valorisees
            campagne.bf_has_unvalued_time = bool(non_valorisees)
            taux = self.env["res.config.settings"]._bf_campaign_hourly_cost()
            campagne.bf_estimate_rate = taux
            campagne.bf_cost_internal_estimated = non_valorisees * taux
            campagne.bf_cost_total_estimated = (
                comptable + interne + non_valorisees * taux
            )

    @api.depends("analytic_account_id")
    def _compute_bf_budget_lines(self):
        Budget = self.env["bf.budget.line"]
        for campagne in self:
            lignes = Budget.browse()
            if campagne.analytic_account_id:
                lignes = Budget.search([
                    ("analytic_account_ids", "in", campagne.analytic_account_id.id),
                ])
            campagne.bf_budget_line_ids = lignes
            campagne.bf_budget_count = len(lignes)
            campagne.bf_amount_planned = sum(lignes.mapped("amount_planned"))

    def action_bf_open_analytic_lines(self):
        """Le détail analytique derrière la dépense de la campagne."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Campaign entries"),
            "res_model": "account.analytic.line",
            "view_mode": "list,form",
            "domain": self._bf_analytic_domain() or [("id", "=", False)],
        }

    def action_bf_open_budget_lines(self):
        """Les lignes budgétaires qui nomment le compte de la campagne."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Campaign budget lines"),
            "res_model": "bf.budget.line",
            "view_mode": "list,form",
            "domain": [("id", "in", self.bf_budget_line_ids.ids)],
        }
