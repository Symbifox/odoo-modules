from odoo import fields, models


class PersonalBudgetCategory(models.Model):
    _name = 'personal.budget.category'
    _inherit = ['personal.budget.book.mixin']
    _description = 'Budget category'
    _order = 'category_type, sequence, name'

    name = fields.Char(string="Name", required=True)
    category_type = fields.Selection([
        ('revenue', 'Revenue'),
        ('expense', 'Expense'),
    ], string="Type", required=True)
    active = fields.Boolean(string="Active", default=True)
    sequence = fields.Integer(string="Sequence", default=10)
    is_recurring = fields.Boolean(string="Recurring")
    notes = fields.Text(string="Notes")

    # L'unicité vaut PAR BUDGET : deux personnes du ménage ont chacune droit à
    # leur « Épicerie ». Globale, elle bloquait la deuxième et lui apprenait du
    # même coup ce que la première avait créé.
    _sql_constraints = [
        ('unique_name_type', 'UNIQUE(book_id, name, category_type)',
         'A category with this name and type already exists in this budget.'),
    ]
