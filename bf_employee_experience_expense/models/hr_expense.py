from odoo import api, fields, models
from odoo.tools import format_amount

#: Les états d'une dépense dont le coût est engagé : approuvée, puis payée.
EX_APPROVED_STATES = ("approved", "done")

#: Ce qui, écrit sur la dépense elle-même, peut la faire entrer dans l'état
#: approuvé ou en sortir, ou changer ce que la ligne d'usage doit porter.
EX_EXPENSE_SYNC_KEYS = frozenset({"state", "sheet_id", "ex_benefit_id", "employee_id"})

#: Ce qui, écrit sur la note de frais, peut faire passer ses dépenses dans
#: l'état approuvé ou les en sortir. `approval_state` porte l'approbation
#: (`_do_approve`, l'assistant des doublons), le refus (`_do_refuse`) et la
#: remise en brouillon (`_do_reset_approval`) ; `state` porte les crochets
#: `set_to_paid` / `set_to_posted` ; `expense_line_ids` l'ajout ou le retrait
#: d'une dépense d'une note déjà approuvée.
EX_SHEET_SYNC_KEYS = frozenset({
    "approval_state", "state", "expense_line_ids", "account_move_ids",
})


class ProductTemplate(models.Model):
    _inherit = "product.template"

    ex_benefit_id = fields.Many2one(
        "bf.ex.benefit", string="Benefit",
        help="Expenses using this product feed this benefit's usage register.",
    )


class HrExpense(models.Model):
    _inherit = "hr.expense"

    ex_benefit_id = fields.Many2one(
        "bf.ex.benefit", string="Benefit",
        compute="_compute_ex_benefit_id", store=True, readonly=False,
        help="Taken from the product, editable. On approval, a confirmed "
             "usage line is created for the amount actually spent.",
    )
    ex_usage_id = fields.Many2one(
        "bf.ex.usage", string="Generated usage line", readonly=True, copy=False,
    )

    @api.depends("product_id")
    def _compute_ex_benefit_id(self):
        for expense in self:
            if expense.ex_benefit_id:
                continue
            expense.ex_benefit_id = expense.product_id.product_tmpl_id.ex_benefit_id

    # ------------------------------------------------------------------
    # La ligne d'usage suit l'approbation
    # ------------------------------------------------------------------

    def _ex_sync_usage(self):
        """Aligner la ligne d'usage de chaque dépense sur son état. Idempotent.

        🔴 **Pourquoi ce n'est pas `write`.** En Odoo 18, `state`
        est un champ CALCULÉ sur `hr.expense`, à partir de la note de frais
        (`hr.expense.sheet`). Approuver passe par la note : le recalcul de
        l'état est versé en base par l'ORM, sans jamais appeler `write` sur la
        dépense. Le pont 18.0.1.0.0 n'écoutait que ce `write` : il ne se
        déclenchait que dans ses propres essais, qui écrivaient l'état à la main.
        Cette méthode est donc appelée par la note de frais (voir plus bas),
        et relit l'état APRÈS coup plutôt que de se fier à une transition.

        * Dépense approuvée ou payée, sans ligne : la ligne se crée, confirmée.
        * Dépense qui n'est plus approuvée (refusée, remise en brouillon,
          retirée de sa note) : la ligne qu'elle avait produite se retire. Une
          ligne confirmée ne revient pas au brouillon ; « si elle est fausse,
          elle se supprime et se ressaisit, ce qui laisse une trace » (socle,
          `bf.ex.usage.action_reset_draft`). La trace va au fil de la dépense.
        * Avantage ou personne changés sur une dépense approuvée : la ligne se
          retire et se refait, plutôt que de porter le mauvais avantage.

        Le garde contre les doublons reste `ex_usage_id` : approuver, remettre
        en brouillon puis réapprouver laisse UNE ligne, au montant du moment.

        ⚠️ La comparaison se lit en `sudo` : qui approuve ou refuse une note
        de frais n'a pas forcément le droit de lire le registre d'usage d'un
        collègue (il se lit par la personne et par l'administration des
        avantages seulement).
        """
        stale = self.sudo().filtered(lambda e: e.ex_usage_id and (
            e.state not in EX_APPROVED_STATES
            or e.ex_usage_id.benefit_id != e.ex_benefit_id
            or e.ex_usage_id.employee_id != e.employee_id
        ))
        stale._ex_remove_usage()
        self.filtered(
            lambda e: e.state in EX_APPROVED_STATES and not e.ex_usage_id
        )._ex_create_usage()
        return True

    def _ex_create_usage(self):
        """Créer la ligne d'usage d'une dépense, une seule fois.

        Le garde est `ex_usage_id` : `state` est un champ CALCULÉ sur
        `hr.expense`, donc il peut être recalculé plusieurs fois pour la même
        transition. Se fier à la transition seule créerait des doublons.

        Le lien s'écrit en `sudo` : la personne qui remet sa propre note en
        brouillon n'a plus le droit d'écrire sur une dépense approuvée.
        """
        Usage = self.env["bf.ex.usage"].sudo()
        for expense in self:
            if expense.ex_usage_id or not expense.ex_benefit_id:
                continue
            if not expense.employee_id:
                continue
            usage = Usage.create({
                "employee_id": expense.employee_id.id,
                "benefit_id": expense.ex_benefit_id.id,
                "date": expense.date or fields.Date.context_today(expense),
                "quantity": expense.quantity or 1.0,
                # `total_amount` est dans la devise de la société, comme le
                # registre ; `total_amount_currency` est dans celle de la dépense.
                "amount": expense.total_amount or expense.total_amount_currency,
                "source": "expense",
                # Texte STOCKÉ au registre de la personne : sa langue (voir
                # `_ex_reader_lang`), pas celle de qui approuve.
                "note": expense.with_context(
                    lang=expense._ex_reader_lang()
                ).env._("Expense: %s", expense.name or expense.display_name),
            })
            usage.action_confirm()
            expense.sudo().ex_usage_id = usage.id
        return True

    def _ex_reader_lang(self):
        """La langue de qui lit le registre d'usage : la personne elle-même.

        Son usager, sinon son contact de travail, sinon la société.
        Rend une langue ACTIVE, sinon celle de l'env (`env.lang` lève sur une
        langue inactive).
        """
        self.ensure_one()
        actives = {code for code, _nom in self.env["res.lang"].get_installed()}
        # En `sudo` : qui approuve n'a pas forcément le droit de lire la fiche
        # de l'employé ; on n'en lit que la langue.
        employee = self.sudo().employee_id
        for lang in (
            employee.user_id.lang,
            employee.work_contact_id.lang,
            (self.company_id or self.env.company).partner_id.lang,
        ):
            if lang and lang in actives:
                return lang
        return self.env.lang

    def _ex_remove_usage(self):
        """Retirer la ligne qu'une dépense avait produite, avec une trace."""
        for expense in self.sudo():
            usage = expense.ex_usage_id
            expense.ex_usage_id = False
            # Seule une ligne née d'une note de frais se retire ici : une ligne
            # saisie à la main et rattachée après coup n'appartient pas au pont.
            if not usage.exists() or usage.source != "expense":
                continue
            benefit = usage.benefit_id.display_name
            amount = usage.amount
            currency = usage.currency_id
            usage.unlink()
            expense._message_log(body=self.env._(
                "Usage line removed from the register (\"%(benefit)s\", "
                "%(amount)s): the expense is no longer approved. A new "
                "approval will create a new one, for the amount at that "
                "time.",
                benefit=benefit,
                amount=format_amount(self.env, amount, currency) if currency else amount,
            ))
        return True

    def write(self, vals):
        result = super().write(vals)
        # `state` est calculé et stocké : il n'apparaît dans `vals` que si on
        # l'écrit à la main. Le chemin normal passe par la note de frais.
        if EX_EXPENSE_SYNC_KEYS.intersection(vals):
            self._ex_sync_usage()
        return result


class HrExpenseSheet(models.Model):
    _inherit = "hr.expense.sheet"

    @api.model_create_multi
    def create(self, vals_list):
        sheets = super().create(vals_list)
        sheets.expense_line_ids._ex_sync_usage()
        return sheets

    def write(self, vals):
        touched = EX_SHEET_SYNC_KEYS.intersection(vals)
        # Une dépense retirée de la note n'y est plus après l'écriture : la
        # retenir avant, pour retirer aussi sa ligne d'usage.
        before = self.expense_line_ids if "expense_line_ids" in vals else self.env["hr.expense"]
        result = super().write(vals)
        if touched:
            (before | self.expense_line_ids)._ex_sync_usage()
        return result
