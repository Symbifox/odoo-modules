from odoo import api, fields, models


class PersonalBudgetShareLine(models.Model):
    """Registre de partage : qui doit quoi à qui, avec un solde courant.

    Le solde courant se calcule PAR BUDGET. Calculé sur toute la table, il
    mêlait les registres de deux personnes du ménage dès qu'un chemin en
    superutilisateur le recalculait.
    """
    _name = 'personal.budget.share.line'
    _inherit = ['personal.budget.book.mixin']
    _description = 'Sharing ledger line'
    _order = 'sequence, id'

    sequence = fields.Integer(string="Sequence", default=10)
    date = fields.Date(string="Date")
    description = fields.Char(string="Description")
    amount = fields.Float(
        string="Amount", digits=(12, 2),
        help="Positive = the person owes, Negative = the person paid",
    )
    running_balance = fields.Float(
        string="Running balance", digits=(12, 2), readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self._recompute_running_balances(records.book_id)
        return records

    def write(self, vals):
        books_before = self.book_id
        res = super().write(vals)
        if {'amount', 'sequence', 'book_id'} & set(vals):
            self._recompute_running_balances(books_before | self.book_id)
        return res

    def unlink(self):
        books = self.book_id
        res = super().unlink()
        self._recompute_running_balances(books)
        return res

    @api.model
    def _recompute_running_balances(self, books):
        for book in books:
            lines = self.sudo().search([('book_id', '=', book.id)], order='sequence, id')
            balance = 0.0
            for line in lines:
                balance += line.amount
                if line.running_balance != balance:
                    line.write({'running_balance': balance})
