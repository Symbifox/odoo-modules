# -*- coding: utf-8 -*-
"""Le pourboire d'un reçu, retiré de l'assiette de taxes de la dépense.

Le total d'une `hr.expense` est réputé taxe incluse (`special_mode` vaut
`total_included` dans `_prepare_base_line_for_taxes_computation`). Un
pourboire laissé dans ce total y est donc traité comme une fourniture
taxable, ce qu'il n'est pas : le restaurateur n'a perçu de TPS et de TVQ
que sur le repas.

Le parti pris du module : `total_amount_currency` garde son sens d'origine,
le montant réellement payé, parce que c'est lui qui est remboursé et qui
totalise le rapport. Seule l'assiette bouge, et elle devient
`total − pourboire`.
"""

from odoo import Command, _, api, fields, models
from odoo.exceptions import ValidationError


class HrExpense(models.Model):
    _inherit = "hr.expense"

    tip_amount_currency = fields.Monetary(
        string="Pourboire",
        currency_field="currency_id",
        default=0.0,
        tracking=True,
        help="Part du total qui ne porte aucune taxe. Elle est retirée de "
             "l'assiette de TPS et de TVQ, et comptabilisée sur sa propre "
             "ligne d'écriture.",
    )
    tip_amount = fields.Monetary(
        string="Pourboire (devise de la société)",
        currency_field="company_currency_id",
        compute="_compute_tip_amount", precompute=True, store=True,
    )

    # ------------------------------------------------------------------
    # Le pourboire dans la devise de la société
    # ------------------------------------------------------------------

    @api.depends("tip_amount_currency", "currency_rate", "is_multiple_currency")
    def _compute_tip_amount(self):
        for expense in self:
            if expense.is_multiple_currency:
                expense.tip_amount = expense.company_currency_id.round(
                    expense.tip_amount_currency * expense.currency_rate)
            else:
                expense.tip_amount = expense.tip_amount_currency

    # ------------------------------------------------------------------
    # L'assiette de taxes
    # ------------------------------------------------------------------

    @api.depends("tip_amount_currency")
    def _compute_tax_amount_currency(self):
        """Les taxes portent sur `total − pourboire`, jamais sur le pourboire.

        ⚠️ `untaxed_amount_currency` reste « le total hors taxes » et vaut donc
        `total − taxes`, pourboire compris. L'identité `hors taxes + taxes ==
        total` tient — `hr.expense.sheet._compute_amount` la suppose déjà — et
        le pourboire s'y range du bon côté, celui qui ne porte pas de taxe.
        """
        avec_pourboire = self.filtered("tip_amount_currency")
        super(HrExpense, self - avec_pourboire)._compute_tax_amount_currency()

        AccountTax = self.env["account.tax"]
        for expense in avec_pourboire:
            base_line = expense._prepare_base_line_for_taxes_computation(
                price_unit=expense.total_amount_currency - expense.tip_amount_currency,
                quantity=1.0,
            )
            AccountTax._add_tax_details_in_base_line(base_line, expense.company_id)
            AccountTax._round_base_lines_tax_details([base_line], expense.company_id)
            details = base_line["tax_details"]
            expense.tax_amount_currency = (
                details["total_included_currency"] - details["total_excluded_currency"])
            expense.untaxed_amount_currency = (
                expense.total_amount_currency - expense.tax_amount_currency)

    @api.depends("tip_amount")
    def _compute_tax_amount(self):
        avec_pourboire = self.filtered("tip_amount")
        super(HrExpense, self - avec_pourboire)._compute_tax_amount()

        AccountTax = self.env["account.tax"]
        for expense in avec_pourboire:
            if not expense.is_multiple_currency:
                # Raccourci mono-devise, comme le noyau.
                expense.tax_amount = expense.tax_amount_currency
                continue
            base_line = expense._prepare_base_line_for_taxes_computation(
                price_unit=expense.total_amount - expense.tip_amount,
                quantity=1.0,
                currency=expense.company_currency_id,
            )
            AccountTax._add_tax_details_in_base_line(base_line, expense.company_id)
            AccountTax._round_base_lines_tax_details([base_line], expense.company_id)
            details = base_line["tax_details"]
            expense.tax_amount = (
                details["total_included_currency"] - details["total_excluded_currency"])

    # ------------------------------------------------------------------
    # Garde-fous
    # ------------------------------------------------------------------

    @api.constrains("tip_amount_currency", "total_amount_currency", "product_id")
    def _check_tip_amount(self):
        for expense in self:
            if not expense.tip_amount_currency:
                continue
            if expense.tip_amount_currency < 0:
                raise ValidationError(_("Un pourboire ne peut pas être négatif."))
            if expense.currency_id.compare_amounts(
                    expense.tip_amount_currency, expense.total_amount_currency) > 0:
                raise ValidationError(_(
                    "Le pourboire (%(pourboire)s) dépasse le total de la dépense "
                    "(%(total)s). Le total est ce qui a été payé, pourboire compris.",
                    pourboire=expense.tip_amount_currency,
                    total=expense.total_amount_currency,
                ))
            if expense.product_has_cost:
                raise ValidationError(_(
                    "La catégorie « %(categorie)s » calcule son montant à partir "
                    "d'un prix fixe : un pourboire ne peut pas s'y ajouter.",
                    categorie=expense.product_id.display_name,
                ))

    # ------------------------------------------------------------------
    # La comptabilisation
    # ------------------------------------------------------------------

    def _get_tip_account(self):
        """Le compte du pourboire, celui du repas par défaut.

        Le pourboire fait partie des frais de représentation ; le ventiler
        ailleurs est une décision comptable, pas un défaut à corriger. D'où
        le réglage facultatif, et le repli sur le compte de la dépense.
        """
        self.ensure_one()
        return self.company_id.expense_tip_account_id or self._get_base_account()

    def _prepare_move_lines_vals(self):
        """La ligne du repas : le total moins le pourboire."""
        vals = super()._prepare_move_lines_vals()
        if self.tip_amount and self.quantity:
            vals["price_unit"] = self.company_currency_id.round(
                (self.total_amount - self.tip_amount) / self.quantity)
        return vals

    def _prepare_tip_move_line_vals(self):
        """La ligne du pourboire : aucune taxe, mais le lien vers la dépense.

        `expense_id` est posé pour la traçabilité — le comptable qui ouvre la
        ligne de pourboire arrive sur le reçu, et la pièce jointe suit
        (`hr_expense.account_move_line._get_attachment_domains`).

        ⚠️ On pourrait craindre que ce lien fasse passer le pourboire pour une
        base de TPS et de TVQ dans l'audit de taxes, puisque `hr_expense`
        surcharge `_get_extra_query_base_tax_line_mapping` avec `expense_id`.
        Vérifié dans `account_move_line_tax_details` : l'appariement passe
        d'abord par `account_move_line_account_tax_rel`, donc par les taxes de
        la ligne. Une ligne sans taxe ne peut être la base de rien, et le
        critère `expense_id` ne fait que RESTREINDRE cette jointure. Le
        contrôle qui compte reste `tax_tag_ids`, vide sur cette ligne, et
        c'est lui qui alimente la déclaration.
        """
        self.ensure_one()
        return {
            "name": _("%(nom)s (pourboire)", nom=self._get_move_line_name()),
            "account_id": self._get_tip_account().id,
            "quantity": 1,
            "price_unit": self.tip_amount,
            "product_id": self.product_id.id,
            "product_uom_id": self.product_uom_id.id,
            "analytic_distribution": self.analytic_distribution,
            "partner_id": (
                False if self.payment_mode == "company_account"
                else self.employee_id.sudo().work_contact_id.id
            ),
            "tax_ids": [Command.set([])],
            "expense_id": self.id,
        }

    def _prepare_base_line_for_taxes_computation(self, **kwargs):
        """Retire le pourboire de l'assiette, mais seulement sur demande.

        ⚠️ Ce point d'entrée est appelé par cinq endroits du noyau, et ils ne
        passent pas tous la même chose : `_compute_total_amount` s'en sert
        pour RECALCULER le total à partir de lui-même, et y soustraire le
        pourboire corromprait le total. D'où le drapeau de contexte : la
        soustraction n'a lieu que là où l'appelant l'a demandée.
        """
        if self.env.context.get("bf_tip_hors_assiette") and self.tip_amount_currency:
            prix = kwargs.get("price_unit")
            if prix is not None:
                devise = kwargs.get("currency") or self.currency_id
                retrait = (
                    self.tip_amount if devise == self.company_currency_id
                    else self.tip_amount_currency
                )
                kwargs["price_unit"] = prix - retrait
        return super()._prepare_base_line_for_taxes_computation(**kwargs)

    def _prepare_payments_vals(self):
        """Mode « carte de la compagnie » : deux lignes de charge.

        Le noyau construit UNE ligne de base, laisse la mécanique de taxes
        produire les lignes de taxe, puis **réécrit le solde de la ligne de
        base** (`total_amount - total_tax_line_balance`) pour que l'écriture
        balance. Ce réécrit part du total complet : il faut lui retirer le
        pourboire et donner à celui-ci sa propre ligne.

        ⚠️ `amount_currency`, lui, sort déjà juste — la mécanique de taxes a
        travaillé sur `total − pourboire` grâce au contexte. Le corriger une
        deuxième fois retirerait le pourboire en double.
        """
        if not self.tip_amount_currency:
            return super()._prepare_payments_vals()

        move_vals, payment_vals = super(
            HrExpense, self.with_context(bf_tip_hors_assiette=True)
        )._prepare_payments_vals()

        lignes = move_vals["line_ids"]
        charges = [c for c in lignes if c[2].get("expense_id") == self.id]
        if not charges:
            return move_vals, payment_vals

        commande_repas = charges[-1]
        commande_repas[2]["balance"] = self.company_currency_id.round(
            commande_repas[2]["balance"] - self.tip_amount)

        pourboire = self._prepare_tip_move_line_vals()
        pourboire.pop("price_unit", None)
        pourboire.pop("quantity", None)
        pourboire.update({
            "balance": self.tip_amount,
            "amount_currency": self.tip_amount_currency,
            "currency_id": self.currency_id.id,
            "partner_id": self.vendor_id.id,
        })
        lignes.insert(lignes.index(commande_repas) + 1, Command.create(pourboire))
        return move_vals, payment_vals
