from odoo import fields, models, tools


class PersonalBudgetAnalysis(models.Model):
    _name = 'personal.budget.analysis'
    _description = 'Budget analysis'
    _auto = False
    _order = 'year desc, month desc'
    # Vue SQL : ses lignes n'ont pas de propriétaire à elles. L'isolation passe
    # par `book_id` (règle `budget_analysis_rule`), comme pour les transactions.

    # Tables lues par la vue : l'ORM les écrit en base avant chaque lecture.
    _depends = {
        'personal.budget.transaction': [
            'book_id', 'category_id', 'category_type', 'year', 'month',
            'month_number', 'gross_amount', 'self_amount',
        ],
    }

    book_id = fields.Many2one(
        'personal.budget.book', string="Budget", readonly=True,
    )
    category_id = fields.Many2one(
        'personal.budget.category', string="Category", readonly=True,
    )
    category_type = fields.Selection([
        ('revenue', 'Revenue'),
        ('expense', 'Expense'),
    ], string="Type", readonly=True)
    year = fields.Integer(string="Year", readonly=True)
    month = fields.Integer(string="Month", readonly=True)
    month_number = fields.Integer(string="# month", readonly=True)
    gross_total = fields.Float(string="Gross total", readonly=True)
    self_total = fields.Float(string="Personal total", readonly=True)
    transaction_count = fields.Integer(string="Transaction count", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                SELECT
                    ROW_NUMBER() OVER () AS id,
                    t.book_id,
                    t.category_id,
                    t.category_type,
                    t.year,
                    t.month,
                    t.month_number,
                    SUM(t.gross_amount) AS gross_total,
                    SUM(t.self_amount) AS self_total,
                    COUNT(*) AS transaction_count
                FROM personal_budget_transaction t
                GROUP BY
                    t.book_id,
                    t.category_id,
                    t.category_type,
                    t.year,
                    t.month,
                    t.month_number
            )
        """ % self._table)
