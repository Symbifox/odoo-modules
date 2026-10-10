from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class PersonalBudgetDashboard(models.AbstractModel):
    _name = 'personal.budget.dashboard'
    _description = 'Budget dashboard'
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve.

    # --- Budget affiché ---------------------------------------------------
    #
    # 🔴 Toutes les requêtes de ce modèle sont en SQL brut : les règles
    # d'enregistrement ne s'y appliquent PAS. Chacune doit donc filtrer sur un
    # budget dont l'accès a été vérifié par `_resolve_book()`. Sans ce filtre,
    # le tableau de bord additionnait les données de tout le ménage.

    @api.model
    def _resolve_book(self, book_id=None):
        Book = self.env['personal.budget.book']
        if not book_id:
            book = Book._default_book()
            if not book:
                raise UserError(_("No budget to display."))
            return book
        book = Book.browse(int(book_id)).exists()
        # Même refus pour un budget absent et pour celui d'autrui.
        if not book or not book.has_access('read'):
            raise AccessError(_("This budget does not exist or is not shared with you."))
        return book

    @api.model
    def get_available_books(self):
        Book = self.env['personal.budget.book']
        default = Book._default_book()
        return [{
            'id': book.id,
            'name': book.name,
            'is_shared': book.is_shared,
            'is_owner': book.user_id == self.env.user,
            'is_default': book == default,
        } for book in Book.search([])]

    @api.model
    def get_available_years(self, book_id=None):
        book = self._resolve_book(book_id)
        self.env.flush_all()
        self.env.cr.execute("""
            SELECT DISTINCT year FROM (
                SELECT year FROM personal_budget_transaction WHERE book_id = %(book)s
                UNION
                SELECT year FROM personal_budget_plan WHERE book_id = %(book)s
            ) sub
            WHERE year IS NOT NULL AND year > 0
        """, {'book': book.id})
        years = {row[0] for row in self.env.cr.fetchall()}
        years.add(fields.Date.context_today(self).year)
        return sorted(years, reverse=True)

    @api.model
    def get_dashboard_data(self, year=None, book_id=None):
        book = self._resolve_book(book_id)
        today = fields.Date.context_today(self)
        return self._get_dashboard_data(book, int(year) if year else today.year, today)

    @api.model
    def _get_dashboard_data(self, book, year, today):
        # SQL brut plus bas : écrire d'abord ce que l'ORM garde en attente.
        self.env.flush_all()
        is_current_year = (today.year == year)
        days_in_year = 366 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 365

        if is_current_year:
            cur_month = today.month
            day_of_year = today.timetuple().tm_yday
            days_remaining = days_in_year - day_of_year
            ytd_fraction = day_of_year / days_in_year
        else:
            cur_month = 12
            days_remaining = 0
            ytd_fraction = 1.0

        recurring_forecast = self._get_recurring_forecast(book, year)
        income_rows = self._get_budget_rows(book, year, 'revenue', ytd_fraction, cur_month, {})
        expense_rows = self._get_budget_rows(
            book, year, 'expense', ytd_fraction, cur_month, recurring_forecast)

        # Per-month totals across all categories — for column footers
        def column_totals(rows):
            t = {m: 0.0 for m in range(1, 13)}
            for r in rows:
                for m, v in r['by_month'].items():
                    t[m] += v
            return {m: round(v, 2) for m, v in t.items()}

        monthly_totals_income = column_totals(income_rows)
        monthly_totals_expense = column_totals(expense_rows)

        total_income_planned = sum(r['ytd_planned'] for r in income_rows)
        total_income_real = sum(r['ytd_real'] for r in income_rows)
        total_expense_planned = sum(r['ytd_planned'] for r in expense_rows)
        total_expense_real = sum(r['ytd_real'] for r in expense_rows)

        net_planned = total_income_planned - total_expense_planned
        net_real = total_income_real - total_expense_real

        overall_offset = 0
        if total_expense_planned:
            overall_offset = round(
                (total_expense_real - total_expense_planned)
                / abs(total_expense_planned) * 100, 1
            )

        # KPI: vs. previous year, same date
        prev_year_net = self._get_prev_year_net_ytd(book, year, today)
        net_vs_prev_pct = None
        if prev_year_net is not None and prev_year_net != 0:
            net_vs_prev_pct = round((net_real - prev_year_net) / abs(prev_year_net) * 100, 1)

        recurring = self._get_recurring_summary(book, year, today)

        return {
            'year': year,
            'cur_month': cur_month,
            'book': {'id': book.id, 'name': book.name, 'is_shared': book.is_shared},
            'income_rows': income_rows,
            'expense_rows': expense_rows,
            'monthly_totals_income': monthly_totals_income,
            'monthly_totals_expense': monthly_totals_expense,
            'summary': {
                'total_income_planned': round(total_income_planned, 2),
                'total_income_real': round(total_income_real, 2),
                'total_expense_planned': round(total_expense_planned, 2),
                'total_expense_real': round(total_expense_real, 2),
                'net_planned': round(net_planned, 2),
                'net_real': round(net_real, 2),
                'days_remaining': days_remaining,
                'overall_offset_pct': overall_offset,
                'prev_year_net_real': round(prev_year_net, 2) if prev_year_net is not None else None,
                'net_vs_prev_pct': net_vs_prev_pct,
                'expense_projected': round(total_expense_real + recurring['remaining'], 2),
            },
            'recurring': recurring,
            'loan_balances': self._get_loan_balances(book),
            'invoice_summary': self._get_invoice_summary(book),
            'recent_transactions': self._get_recent_transactions(book),
        }

    # --- Dépenses récurrentes -------------------------------------------

    @api.model
    def _recurring_in_book(self, book):
        return self.env['personal.budget.recurring'].sudo().search([
            ('book_id', '=', book.id),
        ])

    @api.model
    def _get_recurring_forecast(self, book, year):
        """{category_id: {mois: montant}} : les échéances de l'année, part « moi »."""
        start, end = date(year, 1, 1), date(year, 12, 31)
        forecast = {}
        for rec in self._recurring_in_book(book):
            share = rec._self_share()
            for day in rec._occurrences(start, end):
                months = forecast.setdefault(rec.category_id.id, {m: 0.0 for m in range(1, 13)})
                months[day.month] += share
        return forecast

    @api.model
    def _get_recurring_summary(self, book, year, today, horizon_days=30):
        """Engagements récurrents : équivalent mensuel, reste à payer dans
        l'année, prochaines échéances (en retard comprises)."""
        start, end = date(year, 1, 1), date(year, 12, 31)
        horizon = today + timedelta(days=horizon_days)
        monthly_total = 0.0
        remaining = 0.0
        upcoming = []
        records = self._recurring_in_book(book)
        for rec in records:
            share = rec._self_share()
            monthly_total += rec.monthly_amount * (rec.self_percent or 0.0) / 100.0
            if not rec.next_date or year < today.year:
                continue
            for day in rec._occurrences(max(rec.next_date, start), end):
                remaining += share
            for day in rec._occurrences(rec.next_date, horizon):
                upcoming.append({
                    'id': rec.id,
                    'name': rec.name,
                    'date': day.isoformat(),
                    'amount': round(rec.amount, 2),
                    'is_overdue': day < today,
                })
        upcoming.sort(key=lambda u: (u['date'], u['name']))
        return {
            'count': len(records),
            'monthly_total': round(monthly_total, 2),
            'remaining': round(remaining, 2),
            'upcoming': upcoming[:10],
        }

    @api.model
    def _get_budget_rows(self, book, year, category_type, ytd_fraction, cur_month, recurring_forecast):
        """Return one row per category with monthly real + monthly plan dicts
        keyed 1..12, plus YTD totals. The OWL component slices to the chosen
        month range for display.

        Une catégorie SANS plan pour l'année prend pour plan les échéances de
        ses dépenses récurrentes (`recurring_forecast`) : un abonnement compte
        au budget même quand personne n'a saisi de plan."""
        self.env.cr.execute("""
            WITH annual_plan AS (
                SELECT category_id, planned_amount AS annual_amount
                FROM personal_budget_plan
                WHERE year = %(year)s AND month = 0 AND book_id = %(book)s
            ),
            monthly_plan AS (
                SELECT category_id, month, planned_amount
                FROM personal_budget_plan
                WHERE year = %(year)s AND month BETWEEN 1 AND 12 AND book_id = %(book)s
            ),
            has_plan AS (
                SELECT DISTINCT category_id FROM personal_budget_plan
                WHERE year = %(year)s AND book_id = %(book)s
            ),
            effective_month_plan AS (
                SELECT c.id AS category_id, m.month_num AS month,
                       COALESCE(mp.planned_amount,
                                ap.annual_amount / 12.0, 0) AS amount
                FROM personal_budget_category c
                CROSS JOIN (SELECT generate_series(1, 12) AS month_num) m
                LEFT JOIN annual_plan ap ON ap.category_id = c.id
                LEFT JOIN monthly_plan mp
                       ON mp.category_id = c.id AND mp.month = m.month_num
                WHERE c.category_type = %(category_type)s AND c.active = true
                  AND c.book_id = %(book)s
            ),
            monthly_real AS (
                SELECT category_id, month, SUM(self_amount) AS total
                FROM personal_budget_transaction
                WHERE category_type = %(category_type)s AND year = %(year)s
                  AND book_id = %(book)s
                GROUP BY category_id, month
            )
            SELECT
                c.id AS category_id,
                c.name AS category_name,
                c.sequence AS seq,
                ep.month AS month,
                ep.amount AS plan_amount,
                (hp.category_id IS NOT NULL) AS has_plan,
                COALESCE(mr.total, 0) AS real_amount
            FROM personal_budget_category c
            JOIN effective_month_plan ep ON ep.category_id = c.id
            LEFT JOIN has_plan hp ON hp.category_id = c.id
            LEFT JOIN monthly_real mr
                   ON mr.category_id = c.id AND mr.month = ep.month
            WHERE c.category_type = %(category_type)s
              AND c.active = true
              AND c.book_id = %(book)s
            ORDER BY c.sequence, c.name, ep.month
        """, {'year': year, 'category_type': category_type, 'book': book.id})

        # Build per-category aggregates
        rows_by_cat = {}
        for r in self.env.cr.dictfetchall():
            cid = r['category_id']
            forecast = recurring_forecast.get(cid)
            use_forecast = bool(forecast) and not r['has_plan']
            row = rows_by_cat.setdefault(cid, {
                'category_id': cid,
                'category_name': r['category_name'],
                'seq': r['seq'],
                'by_month': {m: 0.0 for m in range(1, 13)},
                'plan_by_month': {m: 0.0 for m in range(1, 13)},
                'recurring_by_month': {m: round(v, 2) for m, v in (forecast or {}).items()}
                                      or {m: 0.0 for m in range(1, 13)},
                'plan_source': 'recurring' if use_forecast else ('plan' if r['has_plan'] else 'none'),
                'year_planned': 0.0,
                'ytd_real': 0.0,
            })
            plan_amount = forecast[r['month']] if use_forecast else r['plan_amount']
            row['by_month'][r['month']] = round(r['real_amount'], 2)
            row['plan_by_month'][r['month']] = round(plan_amount, 2)
            row['year_planned'] += plan_amount

        # Filter: keep categories with any real OR any plan
        rows = [r for r in rows_by_cat.values()
                if any(v != 0 for v in r['by_month'].values()) or r['year_planned'] != 0]

        # YTD real = sum across all months
        for r in rows:
            r['ytd_real'] = round(sum(r['by_month'].values()), 2)

        # YTD planned = months 1..cur_month-1 in full + cur_month prorated
        month_frac = max(0.0, min(1.0, ytd_fraction * 12.0 - (cur_month - 1)))
        for r in rows:
            completed = sum(r['plan_by_month'][m] for m in range(1, cur_month))
            cur_plan = r['plan_by_month'].get(cur_month, 0.0)
            r['ytd_planned'] = round(completed + cur_plan * month_frac, 2)
            r['year_planned'] = round(r['year_planned'], 2)
            if r['ytd_planned']:
                r['ytd_gap_pct'] = round(
                    (r['ytd_real'] - r['ytd_planned']) / abs(r['ytd_planned']) * 100, 1
                )
            else:
                r['ytd_gap_pct'] = 0.0 if not r['ytd_real'] else 100.0

        rows.sort(key=lambda x: (x['seq'] or 0, x['category_name']))
        return rows

    @api.model
    def _get_prev_year_net_ytd(self, book, year, today):
        """Net réel (revenus - dépenses) de l'année précédente, jusqu'au même
        jour de l'année qu'aujourd'hui. None s'il n'y a rien."""
        prev_year = year - 1
        if today.month == 2 and today.day == 29:
            cutoff = today.replace(year=prev_year, day=28)
        else:
            cutoff = today.replace(year=prev_year)
        self.env.cr.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN category_type = 'revenue' THEN self_amount END), 0) AS rev,
                COALESCE(SUM(CASE WHEN category_type = 'expense' THEN self_amount END), 0) AS exp
            FROM personal_budget_transaction
            WHERE year = %s AND date <= %s AND book_id = %s
        """, (prev_year, cutoff.isoformat(), book.id))
        row = self.env.cr.dictfetchone()
        if not row or (row['rev'] == 0 and row['exp'] == 0):
            return None
        return float(row['rev']) - float(row['exp'])

    @api.model
    def _get_loan_balances(self, book):
        loans = self.env['personal.budget.loan'].search([('book_id', '=', book.id)])
        return [{
            'id': loan.id,
            'name': loan.name,
            'current_balance': round(loan.current_balance, 2),
            'is_overdue': loan.is_overdue,
        } for loan in loans]

    @api.model
    def _get_invoice_summary(self, book):
        self.env.cr.execute("""
            SELECT
                COUNT(*) AS total_invoices,
                COALESCE(SUM(pretax_amount), 0) AS total_pretax,
                COALESCE(SUM(pretax_amount * 0.40), 0) AS total_reserve,
                COALESCE(SUM(pretax_amount * 0.60), 0) AS total_operating
            FROM personal_budget_invoice
            WHERE is_duplicate IS NOT TRUE AND book_id = %s
        """, (book.id,))
        row = self.env.cr.dictfetchone()
        return {
            'total_invoices': row['total_invoices'],
            'total_pretax': round(row['total_pretax'], 2),
            'total_reserve': round(row['total_reserve'], 2),
            'total_operating': round(row['total_operating'], 2),
        }

    @api.model
    def _get_recent_transactions(self, book, limit=10):
        """Les N dernières transactions saisies du budget (carte « Récentes »)."""
        self.env.cr.execute("""
            SELECT t.id, t.date, t.gross_amount, t.self_amount, t.details,
                   t.category_type, c.name AS category_name
            FROM personal_budget_transaction t
            JOIN personal_budget_category c ON c.id = t.category_id
            WHERE t.book_id = %s
            ORDER BY t.create_date DESC, t.id DESC
            LIMIT %s
        """, (book.id, limit))
        return [{
            'id': r['id'],
            'date': r['date'].isoformat() if r['date'] else None,
            'gross_amount': round(r['gross_amount'], 2),
            'self_amount': round(r['self_amount'], 2) if r['self_amount'] else 0.0,
            'details': r['details'] or '',
            'category_name': r['category_name'],
            'category_type': r['category_type'],
        } for r in self.env.cr.dictfetchall()]

    # --- Navigation methods ---

    @api.model
    def action_view_transactions(self, category_id=None, year=None, category_type=None, book_id=None):
        domain = []
        if book_id:
            domain.append(('book_id', '=', book_id))
        if category_id:
            domain.append(('category_id', '=', category_id))
        if year:
            domain.append(('year', '=', year))
        if category_type:
            domain.append(('category_type', '=', category_type))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Transactions"),
            'res_model': 'personal.budget.transaction',
            'views': [[False, 'list'], [False, 'form']],
            'domain': domain,
        }

    @api.model
    def action_view_transaction_form(self, transaction_id):
        return {
            'type': 'ir.actions.act_window',
            'name': _("Transaction"),
            'res_model': 'personal.budget.transaction',
            'res_id': transaction_id,
            'views': [[False, 'form']],
            'target': 'current',
        }

    @api.model
    def action_view_loans(self, book_id=None):
        return {
            'type': 'ir.actions.act_window',
            'name': _("Loans"),
            'res_model': 'personal.budget.loan',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('book_id', '=', book_id)] if book_id else [],
        }

    @api.model
    def action_view_invoices(self, book_id=None):
        return {
            'type': 'ir.actions.act_window',
            'name': _("Invoices"),
            'res_model': 'personal.budget.invoice',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('book_id', '=', book_id)] if book_id else [],
        }

    @api.model
    def action_view_recurring(self, book_id=None):
        return {
            'type': 'ir.actions.act_window',
            'name': _("Recurring expenses"),
            'res_model': 'personal.budget.recurring',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('book_id', '=', book_id)] if book_id else [],
        }
