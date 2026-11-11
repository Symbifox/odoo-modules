from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfBudgetLine(models.Model):
    """Déplier une ligne budgétaire dans les lignes analytiques qu'elle lit.

    🔴 LA SEULE EXPOSITION RÉELLE DE LA FAMILLE (plan, §11) : une ligne de
    main-d'œuvre se déplie en feuilles de temps, valorisées au taux horaire de
    l'employé — déplier un budget de main-d'œuvre, c'est lire des salaires.

    Le dépliage ne transmet donc qu'un DOMAINE, jamais des enregistrements ni
    des ids relevés en sudo : les ACL et les règles d'enregistrement s'évaluent
    au rendu, sous le compte de la personne. Les montants de la ligne, eux,
    restent des SOMMES calculées en sudo par le socle : le chiffre est stable
    par rôle, le détail ne l'est pas.

    🔴 CE QUE LE DÉPLIAGE MONTRE DÉPEND DU RÔLE, ET LA PROMESSE SE DIT PAR
    RÔLE — « chacun ses feuilles de temps » n'est pas une propriété du
    dépliage, c'est une propriété d'un groupe :

    * budget + feuilles de temps (`hr_timesheet.group_hr_timesheet_user`) : la
      règle d'enregistrement du cœur restreint à `user_id = l'usager`, donc ne
      déplie QUE ses propres heures;
    * budget + comptabilité analytique (`analytic.group_analytic_accounting`)
      SANS les feuilles de temps : aucune règle d'enregistrement n'est attachée
      à ce groupe sur `account.analytic.line`, et `_compute_domain` ne rend
      alors que les règles globales — la seule étant le filtre de société.
      Ce rôle déplie donc TOUTES les feuilles de temps valorisées de la
      société, salaires compris.

    Le lot n'élargit AUCUN droit du cœur : ce second rôle lit déjà la même
    chose par le menu des écritures analytiques d'Odoo. Mais la phrase publiée
    ne doit pas laisser croire que le dépliage cloisonne par lui-même : qui
    accorde la comptabilité analytique accorde la lecture des salaires.

    ⚠️ `check_access("read")` ouvre l'action : un usager interne nu n'a AUCUNE
    ACL de lecture sur `account.analytic.line` (elle vient des groupes feuilles
    de temps, comptabilité analytique ou achats).

    Sans ce garde, l'action rendrait une vue qui explose en AccessError brute
    au rendu — avec lui, le refus est propre et immédiat. Le lecteur refusé
    garde les SOMMES de la ligne, compteur et heures compris (calculés en sudo,
    comme les montants du socle) : c'est le DÉTAIL qui lui est fermé, pas le
    chiffre.
    """

    _inherit = "bf.budget.line"

    analytic_line_count = fields.Integer(
        string="Analytic lines",
        compute="_compute_analytic_measures",
        help="How many analytic lines the line reads: timesheets and manual "
             "entries for an internal cost line, distributed shares of posted "
             "journal items for a narrowed accounting line. A count, computed "
             "with elevated rights like the amounts: opening the detail shows "
             "each viewer only what their own rights carry.",
    )
    timesheet_hours = fields.Float(
        string="Timesheet hours",
        compute="_compute_analytic_measures",
        help="Hours logged on timesheets inside the budget window, beside the "
             "dollars. Hours stay visible even when the employee's hourly "
             "cost is missing and the dollars read zero.",
    )

    @api.depends(
        "source",
        "analytic_account_ids",
        "position_id.account_ids",
        "budget_id.date_start",
        "budget_id.date_end",
        "budget_id.company_id",
    )
    def _compute_analytic_measures(self):
        Analytic = self.env["account.analytic.line"].sudo()
        for line in self:
            line.analytic_line_count = 0
            line.timesheet_hours = 0.0
            model, _field, domain = line._actual_source()
            if model != "account.analytic.line":
                continue
            line.analytic_line_count = Analytic.search_count(domain)
            if line.source != "internal_cost":
                continue
            groups = Analytic._read_group(
                domain + [("project_id", "!=", False)],
                aggregates=["unit_amount:sum"],
            )
            line.timesheet_hours = (groups[0][0] or 0.0) if groups else 0.0

    def action_view_analytic_lines(self):
        """Ouvre EXACTEMENT ce que la ligne lit, sous les droits de la personne.

        Le domaine vient de `_actual_source()` : c'est le même que celui de la
        mesure. Ce que la mesure compte et ce que le dépliage montre ne peuvent
        pas diverger — il n'y a qu'un énoncé.
        """
        self.ensure_one()
        model, _field, domain = self._actual_source()
        if model != "account.analytic.line" or domain == [("id", "=", False)]:
            # La sentinelle du socle (« ne matche rien ») couvre aussi une
            # ligne desserrée par un greffon qui lit un AUTRE registre : le
            # message ne prétend pas savoir ce qu'elle lit.
            raise UserError(
                _("There are no analytic lines behind this line.")
            )
        self.env["account.analytic.line"].check_access("read")
        if self.source != "internal_cost":
            # Les parts d'une ligne comptable se filtrent À TRAVERS l'écriture
            # (`move_line_id.…`) : évaluer ce domaine exige la lecture du grand
            # livre, et une recherche sans ce droit lève une AccessError au
            # rendu, pas ici. Le dépliage du grand livre demande donc les
            # droits du grand livre — le refus est propre et immédiat.
            self.env["account.move.line"].check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Analytic lines — %s", self.display_name),
            "res_model": "account.analytic.line",
            "view_mode": "list,form",
            "domain": domain,
            "context": {"create": False, "edit": False, "delete": False},
        }
