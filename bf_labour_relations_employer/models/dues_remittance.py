from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DuesRemittanceLine(models.Model):
    """L'assiette saisie, et la règle appliquée dessus.

    🔴 Le module ne devine pas l'assiette : il l'attend. Ce qu'il garantit,
    c'est que la règle de la convention est appliquée UNIFORMÉMENT, ce qui est
    exactement ce qu'une vérification syndicale contrôle. Calculer l'assiette
    demanderait une paie, qui n'existe pas dans le parc.
    """

    _inherit = "bf.labour.dues.remittance.line"

    base_amount = fields.Monetary(
        string="Assiette", currency_field="currency_id",
        help="Le salaire ou les heures de la période, repris du système de "
             "paie. C'est la seule donnée que le module ne peut pas produire.",
    )
    computed_amount = fields.Monetary(
        string="Montant selon la règle", currency_field="currency_id",
        compute="_compute_computed_amount",
        help="Ce que la règle de la convention prélèverait sur cette assiette.",
    )
    # ⚠️ NON stocké : il dépend de la règle en vigueur, qui peut changer.
    has_gap = fields.Boolean(string="Écart", compute="_compute_computed_amount")

    @api.depends("base_amount", "amount", "remittance_id.period_end",
                 "remittance_id.unit_id")
    def _compute_computed_amount(self):
        for line in self:
            rule = line._applicable_rule()
            if not rule or not line.base_amount:
                line.computed_amount = 0.0
                line.has_gap = False
                continue
            computed = rule.amount_for(line.base_amount)
            line.computed_amount = computed
            line.has_gap = abs(computed - (line.amount or 0.0)) > 0.005

    def _applicable_rule(self):
        """La règle en vigueur à la fin de la période, pas la dernière saisie.

        Un taux qui change en cours de convention laisse deux règles datées :
        prendre la plus récente donnerait le mauvais montant sur toute période
        antérieure au changement.
        """
        self.ensure_one()
        remittance = self.remittance_id
        reference = remittance.period_end
        if not reference:
            return self.env["bf.labour.dues.rule"]
        agreement = remittance.unit_id.agreement_ids.filtered(
            lambda a: a.date_start and a.date_start <= reference
        ).sorted("date_start", reverse=True)[:1]
        if not agreement:
            return self.env["bf.labour.dues.rule"]
        rules = agreement.dues_rule_ids.filtered(
            lambda r: r.date_start <= reference
            and (not r.date_end or r.date_end >= reference)
        ).sorted("date_start", reverse=True)
        return rules[:1]


class DuesRemittance(models.Model):
    _inherit = "bf.labour.dues.remittance"

    # ⚠️ AUCUN de ces quatre n'est stocké, et c'est une contrainte, pas un choix :
    # ils sortent du même calcul, et Odoo refuse d'y mélanger stocké et non
    # stocké (« accessing computed_total may recompute and update base_total »).
    # Les trois derniers dépendent de la règle en vigueur à la période, donc ils
    # ne peuvent pas être stockés ; le premier suit.
    base_total = fields.Monetary(
        string="Assiette totale", currency_field="currency_id",
        compute="_compute_gap",
    )
    computed_total = fields.Monetary(
        string="Total selon la règle", currency_field="currency_id",
        compute="_compute_gap",
    )
    gap_amount = fields.Monetary(
        string="Écart", currency_field="currency_id", compute="_compute_gap",
        help="Entre ce qui est saisi et ce que la règle donnerait. Un écart "
             "n'est pas une erreur : il se justifie, ou il se corrige.",
    )
    gap_line_count = fields.Integer(string="Lignes en écart", compute="_compute_gap")

    @api.depends("line_ids.base_amount", "line_ids.amount",
                 "line_ids.computed_amount", "line_ids.has_gap")
    def _compute_gap(self):
        for remittance in self:
            remittance.base_total = sum(remittance.line_ids.mapped("base_amount"))
            remittance.computed_total = sum(remittance.line_ids.mapped("computed_amount"))
            remittance.gap_amount = remittance.computed_total - remittance.amount_total
            remittance.gap_line_count = len(remittance.line_ids.filtered("has_gap"))

    def action_apply_rule(self):
        """Reporter le montant de la règle dans le montant retenu.

        Un geste explicite, jamais automatique : l'employeur assume ce qu'il
        déclare avoir retenu, et une écriture silencieuse effacerait justement
        l'écart qu'une vérification cherche.
        """
        for remittance in self:
            if remittance.state != "draft":
                raise UserError(_(
                    "Une remise déclarée ne se recalcule pas. Le montant "
                    "déclaré est ce que l'employeur affirme avoir retenu."
                ))
            for line in remittance.line_ids.filtered("base_amount"):
                line.amount = line.computed_amount
        return True
