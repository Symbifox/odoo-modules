from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PersonalBudgetPlan(models.Model):
    _name = 'personal.budget.plan'
    _inherit = ['personal.budget.book.mixin']
    _description = 'Budget plan'
    _order = 'year desc, category_type, category_id, month'

    year = fields.Integer(string="Year", required=True)
    month = fields.Integer(
        string="Month", default=0,
        help="0 = lump-sum annual amount (spread /12). 1-12 = amount for that specific month.",
    )
    month_name = fields.Char(
        string="Month (name)", compute='_compute_month_name',
    )
    category_id = fields.Many2one(
        'personal.budget.category', string="Category", required=True,
        ondelete='restrict',
    )
    category_type = fields.Selection(
        related='category_id.category_type', store=True, string="Type",
    )
    # Le budget d'un plan est celui de sa catégorie.
    book_id = fields.Many2one(
        related='category_id.book_id', store=True, readonly=True,
        default=None, required=False,
    )
    _book_moving_fields = ('book_id', 'category_id')
    planned_amount = fields.Float(
        string="Planned amount", digits=(12, 2), required=True,
    )
    planned_monthly = fields.Float(
        string="Planned monthly", digits=(12, 2),
        compute='_compute_planned_monthly', store=True,
    )

    _sql_constraints = [
        ('unique_year_month_category',
         'UNIQUE(year, month, category_id)',
         'A plan for this category, year and month already exists.'),
        ('month_range', 'CHECK(month >= 0 AND month <= 12)',
         "The month must be 0 (annual) or between 1 and 12."),
    ]

    # Non stocké depuis 18.0.2.1.0 : le nom du mois suit la langue de la
    # personne qui lit (il était rangé en français dans la table).
    @api.depends_context('lang')
    @api.depends('month')
    def _compute_month_name(self):
        names = {
            0: _("Annual"),
            1: _("January"), 2: _("February"), 3: _("March"), 4: _("April"),
            5: _("May"), 6: _("June"), 7: _("July"), 8: _("August"),
            9: _("September"), 10: _("October"), 11: _("November"), 12: _("December"),
        }
        for rec in self:
            rec.month_name = names.get(rec.month or 0, names[0])

    @api.depends('planned_amount', 'month')
    def _compute_planned_monthly(self):
        for rec in self:
            if rec.month and rec.month > 0:
                rec.planned_monthly = rec.planned_amount or 0.0
            else:
                rec.planned_monthly = (rec.planned_amount or 0.0) / 12.0

    def action_init_monthly_rows(self):
        """For each selected annual plan (month=0), create 12 monthly rows
        pre-filled with planned_amount / 12. Skips months that already exist.
        Leaves the annual row in place (it acts as a fallback for any month
        the user later deletes)."""
        created = 0
        Plan = self.env['personal.budget.plan']
        for rec in self:
            if rec.month and rec.month > 0:
                # Already a monthly row — derive its (year, category) and seed siblings
                year = rec.year
                category_id = rec.category_id.id
                annual = Plan.search([
                    ('year', '=', year),
                    ('month', '=', 0),
                    ('category_id', '=', category_id),
                ], limit=1)
                seed_monthly = annual.planned_amount / 12.0 if annual else rec.planned_amount
            else:
                year = rec.year
                category_id = rec.category_id.id
                seed_monthly = (rec.planned_amount or 0.0) / 12.0

            existing_months = set(Plan.search([
                ('year', '=', year),
                ('category_id', '=', category_id),
                ('month', '>', 0),
            ]).mapped('month'))

            for m in range(1, 13):
                if m in existing_months:
                    continue
                Plan.create({
                    'year': year,
                    'month': m,
                    'category_id': category_id,
                    'planned_amount': round(seed_monthly, 2),
                })
                created += 1

        if not created:
            raise UserError(_("No monthly line created: all 12 months already existed."))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Monthly lines created"),
                'message': _("%d monthly lines added.") % created,
                'type': 'success',
                'sticky': False,
            },
        }
