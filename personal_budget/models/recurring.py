"""Dépenses récurrentes : les abonnements du ménage vivent DANS le budget.

Netflix, l'assurance habitation, le cellulaire : un montant, une fréquence et
une première échéance suffisent à connaître toutes les échéances. Le module
s'en sert de trois façons :

* **prévision** : faute de plan saisi pour une catégorie et une année, le
  tableau de bord prend les échéances à venir comme montant prévu ;
* **engagements** : ce qui reste à payer d'ici la fin de l'année s'affiche à
  part, avec les prochaines échéances ;
* **saisie** : une échéance se transforme en transaction, à la main
  (« Saisir l'échéance ») ou d'office si `auto_post` est coché (tâche
  planifiée quotidienne).

Les échéances se calculent depuis la première (`date_start`), jamais de proche
en proche : un abonnement du 31 revient le 28 ou le 29 février, puis de
nouveau le 31 mars.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

FREQUENCIES = [
    ('weekly', 'Weekly'),
    ('biweekly', 'Every two weeks'),
    ('monthly', 'Monthly'),
    ('quarterly', 'Quarterly'),
    ('semiannual', 'Every six months'),
    ('yearly', 'Yearly'),
]

STEPS = {
    'weekly': relativedelta(weeks=1),
    'biweekly': relativedelta(weeks=2),
    'monthly': relativedelta(months=1),
    'quarterly': relativedelta(months=3),
    'semiannual': relativedelta(months=6),
    'yearly': relativedelta(years=1),
}

# Nombre d'échéances par mois, en moyenne sur l'année.
MONTHLY_FACTORS = {
    'weekly': 52.0 / 12.0,
    'biweekly': 26.0 / 12.0,
    'monthly': 1.0,
    'quarterly': 1.0 / 3.0,
    'semiannual': 1.0 / 6.0,
    'yearly': 1.0 / 12.0,
}

# Garde-fou contre une boucle sans fin (une échéance hebdomadaire sur 100 ans).
_MAX_OCCURRENCES = 5300


class PersonalBudgetRecurring(models.Model):
    _name = 'personal.budget.recurring'
    _inherit = ['personal.budget.book.mixin']
    _description = 'Recurring expense'
    _order = 'next_date, name, id'

    name = fields.Char(string="Name", required=True, help="Netflix, home insurance, mobile phone…")
    provider = fields.Char(string="Provider")
    category_id = fields.Many2one(
        'personal.budget.category', string="Category", required=True,
        ondelete='restrict', domain="[('category_type', '=', 'expense')]",
    )
    # Le budget d'une dépense récurrente est celui de sa catégorie.
    book_id = fields.Many2one(
        related='category_id.book_id', store=True, readonly=True,
        default=None, required=False,
    )
    _book_moving_fields = ('book_id', 'category_id')

    amount = fields.Float(string="Amount", digits=(12, 2), required=True)
    frequency = fields.Selection(
        FREQUENCIES, string="Frequency", required=True, default='monthly',
    )
    date_start = fields.Date(
        string="First payment", required=True,
        default=fields.Date.context_today,
    )
    date_end = fields.Date(
        string="Last possible payment",
        help="Leave empty for a subscription with no planned end.",
    )
    last_posted_date = fields.Date(
        string="Last recorded payment", copy=False,
        help="Most recent payment already turned into a transaction.",
    )
    next_date = fields.Date(
        string="Next payment", compute='_compute_next_date', store=True,
    )
    self_percent = fields.Float(string="% me", default=100.0)
    auto_post = fields.Boolean(
        string="Record automatically",
        help="Every day, due payments are turned into transactions "
             "automatically.",
    )
    active = fields.Boolean(string="Active", default=True)
    notes = fields.Text(string="Notes")
    monthly_amount = fields.Float(
        string="Monthly equivalent", digits=(12, 2),
        compute='_compute_monthly_amount',
    )
    transaction_ids = fields.One2many(
        'personal.budget.transaction', 'recurring_id', string="Recorded transactions",
    )
    transaction_count = fields.Integer(
        string="Transaction count", compute='_compute_transaction_count',
    )

    _sql_constraints = [
        ('amount_positive', 'CHECK(amount >= 0)',
         "The amount of a recurring expense cannot be negative."),
    ]

    @api.constrains('category_id')
    def _check_category_expense(self):
        for rec in self:
            if rec.category_id.category_type != 'expense':
                raise ValidationError(_(
                    "A recurring expense belongs in an expense category."))

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(_(
                    "The last possible payment is before the first one."))

    @api.depends('amount', 'frequency')
    def _compute_monthly_amount(self):
        for rec in self:
            rec.monthly_amount = (rec.amount or 0.0) * MONTHLY_FACTORS.get(rec.frequency, 0.0)

    def _compute_transaction_count(self):
        for rec in self:
            rec.transaction_count = len(rec.transaction_ids)

    @api.depends('date_start', 'date_end', 'frequency', 'last_posted_date')
    def _compute_next_date(self):
        for rec in self:
            rec.next_date = rec._first_occurrence_after(rec.last_posted_date)

    # --- Calcul des échéances -------------------------------------------

    def _occurrence(self, index):
        self.ensure_one()
        step = STEPS[self.frequency]
        return self.date_start + step * index

    def _iter_occurrences(self):
        """Échéances dans l'ordre, de la première à `date_end` (ou sans fin)."""
        self.ensure_one()
        if not (self.date_start and self.frequency):
            return
        for index in range(_MAX_OCCURRENCES):
            day = self._occurrence(index)
            if self.date_end and day > self.date_end:
                return
            yield day

    def _first_occurrence_after(self, day):
        """Première échéance strictement après `day` (toutes si `day` est vide)."""
        self.ensure_one()
        for occurrence in self._iter_occurrences():
            if not day or occurrence > day:
                return occurrence
        return False

    def _occurrences(self, date_from, date_to):
        """Échéances comprises entre `date_from` et `date_to`, bornes incluses."""
        self.ensure_one()
        result = []
        for occurrence in self._iter_occurrences():
            if occurrence > date_to:
                break
            if occurrence >= date_from:
                result.append(occurrence)
        return result

    def _self_share(self):
        self.ensure_one()
        return (self.amount or 0.0) * (self.self_percent or 0.0) / 100.0

    # --- Saisie des échéances -------------------------------------------

    def _post_occurrences(self, days):
        """Transforme les échéances `days` en transactions, dans l'ordre."""
        self.ensure_one()
        Transaction = self.env['personal.budget.transaction']
        created = Transaction.browse()
        for day in sorted(days):
            created |= Transaction.create({
                'date': day,
                'category_id': self.category_id.id,
                'gross_amount': self.amount,
                'self_percent': self.self_percent,
                'details': self.name,
                'recurring_id': self.id,
            })
            self.last_posted_date = day
        return created

    def _post_due(self, up_to):
        """Saisit toutes les échéances non saisies jusqu'à `up_to` inclus."""
        created = self.env['personal.budget.transaction']
        for rec in self:
            if not rec.next_date or rec.next_date > up_to:
                continue
            created |= rec._post_occurrences(rec._occurrences(rec.next_date, up_to))
        return created

    def action_post_next(self):
        """Saisit la prochaine échéance, même si elle n'est pas encore arrivée."""
        for rec in self:
            if not rec.next_date:
                raise UserError(_("\"%s\" has no payment left to record.", rec.name))
            rec._post_occurrences([rec.next_date])
        return True

    def action_post_due(self):
        today = fields.Date.context_today(self)
        created = self._post_due(today)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Payments recorded"),
                'message': _("%s transaction(s) created.", len(created)),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_view_transactions(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'personal_budget.action_budget_transaction')
        action['domain'] = [('recurring_id', '=', self.id)]
        action['context'] = {}
        return action

    @api.model
    def _cron_post_due(self):
        """Tâche planifiée : saisit les échéances arrivées des abonnements
        « Saisir d'office ». Tourne en superutilisateur ; les transactions
        créées rejoignent le budget de leur catégorie, donc restent visibles
        des seules personnes de ce budget."""
        today = fields.Date.context_today(self)
        due = self.sudo().search([
            ('auto_post', '=', True),
            ('next_date', '!=', False),
            ('next_date', '<=', today),
        ])
        return due._post_due(today)
