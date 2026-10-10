import base64
import csv
import difflib
import io
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.translate import LazyGettext

from . import ofx_parser
from ..models.book import REFUS_BUDGET

_logger = logging.getLogger(__name__)

# Legacy-reconciliation tolerances.
_RECONCILE_DATE_WINDOW = 3  # days
_AMOUNT_EPS = 0.005


class PersonalBudgetImportWizard(models.TransientModel):
    _name = 'personal.budget.import.wizard'
    _description = "CSV import wizard"

    import_type = fields.Selection([
        ('transaction', 'Transactions'),
        ('ofx', 'OFX / QFX statement'),
        ('share', 'Sharing ledger'),
        ('cheque', 'Cheques'),
        ('loan_line', 'Loan lines'),
        ('invoice', 'Invoices'),
        ('plan', 'Budget plans'),
    ], string="Import type", required=True, default='transaction')
    book_id = fields.Many2one(
        'personal.budget.book', string="Budget", required=True,
        default=lambda self: self.env['personal.budget.book']._default_book(),
        help="Budget receiving the import. Categories are looked up and created in this budget only.",
    )
    csv_file = fields.Binary(string="File", required=True)
    csv_filename = fields.Char(string="File name")
    date_floor = fields.Date(
        string="Ignore before",
        help="OFX transactions before this date are ignored (optional).",
    )
    delimiter = fields.Selection([
        (',', 'Comma (,)'),
        (';', 'Semicolon (;)'),
        ('\t', 'Tab'),
    ], string="Delimiter", default=',')
    skip_header = fields.Boolean(string="Skip header", default=True)
    loan_id = fields.Many2one(
        'personal.budget.loan', string="Target loan",
        domain="[('book_id', '=', book_id)]",
    )
    preview = fields.Text(string="Preview", readonly=True)
    result = fields.Text(string="Result", readonly=True)

    @api.onchange('csv_file', 'delimiter', 'skip_header', 'import_type')
    def _onchange_preview(self):
        if not self.csv_file:
            self.preview = ''
            return
        if self.import_type == 'ofx':
            self.preview = self._preview_ofx()
            return
        try:
            content = base64.b64decode(self.csv_file).decode('utf-8-sig')
            reader = csv.reader(io.StringIO(content), delimiter=self.delimiter or ',')
            lines = []
            for i, row in enumerate(reader):
                if i >= 10:
                    lines.append('...')
                    break
                lines.append(' | '.join(row))
            self.preview = '\n'.join(lines)
        except Exception as e:
            self.preview = _("Read error: %s", e)

    def _lazy_str(self, value):
        """Rend un message du lecteur OFX (traduction paresseuse) dans la langue
        de la personne : sa résolution par la pile ne trouve pas la langue."""
        if isinstance(value, LazyGettext):
            return value._translate(self.env.lang or 'en_US')
        return str(value)

    @staticmethod
    def _mask_acctid(acctid):
        if acctid and len(acctid) > 7:
            return f"{acctid[:3]}...{acctid[-4:]}"
        return acctid or '?'

    def _preview_ofx(self):
        try:
            statements = ofx_parser.parse_ofx(base64.b64decode(self.csv_file))
        except Exception as e:
            return _("OFX read error: %s", self._lazy_str(e.args[0] if e.args else e))
        lines = []
        for stmt in statements:
            txns = stmt['transactions']
            dates = [t['date'] for t in txns if t['date']]
            span = (_("from %(start)s to %(end)s", start=min(dates), end=max(dates))
                    if dates else _("(no date)"))
            lines.append(_(
                "Account %(account)s (%(kind)s, %(currency)s) — %(count)s transaction(s) %(span)s",
                account=self._mask_acctid(stmt['acctid']),
                kind=stmt['accttype'] or stmt['kind'], currency=stmt['currency'] or '?',
                count=len(txns), span=span,
            ))
            for t in txns[:5]:
                lines.append(
                    f"  {t['date']}  {t['amount']:>9.2f}  {t['name'][:30]}"
                )
            if len(txns) > 5:
                lines.append(_("  ... and %s more", len(txns) - 5))
            if stmt['warnings']:
                lines.append(_("  ⚠ %s warning(s)", len(stmt['warnings'])))
        return '\n'.join(lines)

    @api.constrains('book_id', 'loan_id')
    def _check_targets_readable(self):
        for wizard in self:
            wizard._check_targets()

    def _check_targets(self):
        """Le budget (et le prêt) visés doivent être lisibles par la personne, et le
        prêt, de ce budget. Même refus pour une cible d'autrui et une cible absente."""
        self.ensure_one()
        if self.env.su:
            return
        book, loan = self.book_id, self.loan_id
        if (book and not book.has_access('read')) or (
                loan and (not loan.has_access('read') or loan.sudo().book_id != book)):
            raise AccessError(_(REFUS_BUDGET))

    def action_import(self):
        self.ensure_one()
        self._check_targets()
        raw = base64.b64decode(self.csv_file)

        if self.import_type == 'ofx':
            self.result = self._import_ofx(raw)
            return self._reopen()

        content = raw.decode('utf-8-sig')
        reader = csv.reader(io.StringIO(content), delimiter=self.delimiter or ',')
        rows = list(reader)
        if self.skip_header and rows:
            rows = rows[1:]

        method = getattr(self, f'_import_{self.import_type}', None)
        if not method:
            self.result = _("Unsupported import type: %s", self.import_type)
            return self._reopen()

        created, errors = method(rows)
        parts = [_("%s record(s) created.", created)]
        if errors:
            parts.append(_("%s error(s):", len(errors)))
            for err in errors[:20]:
                parts.append(f"  - {err}")
            if len(errors) > 20:
                parts.append(_("  ... and %s more errors", len(errors) - 20))
        self.result = '\n'.join(parts)
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _("CSV import"),
            'res_model': self._name,
            'res_id': self.id,
            'views': [[False, 'form']],
            'target': 'new',
        }

    def _get_or_create_category(self, name, cat_type, sequence=None):
        if not name:
            return False
        Category = self.env['personal.budget.category']
        cat = Category.search([
            ('book_id', '=', self.book_id.id),
            ('name', '=ilike', name.strip()),
            ('category_type', '=', cat_type),
        ], limit=1)
        if not cat:
            vals = {'name': name.strip(), 'category_type': cat_type, 'book_id': self.book_id.id}
            if sequence is not None:
                vals['sequence'] = sequence
            cat = Category.create(vals)
        return cat.id

    # --- OFX import ---

    @staticmethod
    def _norm(s):
        return ' '.join((s or '').lower().split())

    @staticmethod
    def _matches(patterns, *texts):
        blob = ' '.join(t for t in texts if t).upper()
        return any(p in blob for p in patterns)

    def _build_details(self, name, memo):
        name = (name or '').strip()
        memo = (memo or '').strip()
        if memo and memo.lower() not in name.lower():
            return f"{name} — {memo}" if name else memo
        return name

    def _find_legacy_match(self, legacy, consumed, gross, posted, cat_type, details_norm):
        """Return (record_or_False, ambiguous_bool) for a legacy fitid-less row."""
        candidates = []
        for rec in legacy:
            if rec.id in consumed:
                continue
            if rec.category_type != cat_type:
                continue
            if abs(rec.gross_amount - gross) > _AMOUNT_EPS:
                continue
            dist = abs((rec.date - posted).days)
            if dist > _RECONCILE_DATE_WINDOW:
                continue
            sim = difflib.SequenceMatcher(
                None, details_norm, self._norm(rec.details)).ratio()
            candidates.append((dist, -sim, rec.id, rec))
        if not candidates:
            return False, False
        candidates.sort(key=lambda c: (c[0], c[1], c[2]))
        best = candidates[0]
        ambiguous = (
            len(candidates) > 1
            and candidates[1][0] == best[0]
            and abs(candidates[1][1] - best[1]) < 1e-9
        )
        return best[3], ambiguous

    def _import_ofx(self, raw):
        try:
            statements = ofx_parser.parse_ofx(raw)
        except Exception as e:
            return _("OFX read error: %s", self._lazy_str(e.args[0] if e.args else e))

        Transaction = self.env['personal.budget.transaction']
        # Catégories créées d'office : leur NOM est une donnée, écrite dans la
        # langue de la personne qui importe (« À attribuer » en fr_CA, ce qui
        # retrouve les catégories des bases existantes).
        cat_expense = self._get_or_create_category(_("To assign"), 'expense', sequence=0)
        cat_revenue = self._get_or_create_category(_("Other income"), 'revenue')

        consumed = set()  # legacy ids matched this run (across all statements)
        totals = dict(created=0, reconciled=0, dup_fitid=0, skipped_transfer=0,
                      skipped_payment=0, skipped_floor=0)
        refunds = []
        ambiguities = []
        errors = []
        report = []

        for stmt in statements:
            acctid = stmt['acctid']
            txns = sorted(
                (t for t in stmt['transactions'] if t['date']),
                key=lambda t: t['date'],
            )
            label = _("Account %(account)s (%(kind)s)", account=self._mask_acctid(acctid),
                      kind=stmt['accttype'] or stmt['kind'])

            existing_fitids = set(
                r['fitid'] for r in Transaction.search_read(
                    [('book_id', '=', self.book_id.id), ('account_ref', '=', acctid),
                     ('fitid', '!=', False)], ['fitid'])
            )

            dates = [t['date'] for t in txns]
            legacy = Transaction.browse()
            if dates:
                lo = min(dates) - timedelta(days=_RECONCILE_DATE_WINDOW)
                hi = max(dates) + timedelta(days=_RECONCILE_DATE_WINDOW)
                legacy = Transaction.search([
                    ('book_id', '=', self.book_id.id),
                    ('fitid', '=', False),
                    ('date', '>=', fields.Date.to_string(lo)),
                    ('date', '<=', fields.Date.to_string(hi)),
                ])

            s_created = s_reconciled = 0
            for t in txns:
                fitid, amount, posted = t['fitid'], t['amount'], t['date']
                if self.date_floor and posted < self.date_floor:
                    totals['skipped_floor'] += 1
                    continue
                if fitid in existing_fitids:
                    totals['dup_fitid'] += 1
                    continue

                # Classify.
                if t['trntype'] == 'XFER':
                    totals['skipped_transfer'] += 1
                    report.append(_("  transfer skipped: %(date)s %(name)s", date=posted, name=t['name']))
                    existing_fitids.add(fitid)
                    continue
                if stmt['kind'] == 'creditcard' and amount > 0:
                    if self._matches(ofx_parser.SKIP_PAYMENT_PATTERNS, t['name'], t['memo']):
                        totals['skipped_payment'] += 1
                        existing_fitids.add(fitid)
                        continue
                    cat_type = 'revenue'  # CC refund
                    refunds.append(f"{posted} {t['name']} {amount:.2f}$")
                else:
                    cat_type = 'expense' if amount < 0 else 'revenue'

                gross = abs(amount)
                details = self._build_details(t['name'], t['memo'])
                default_cat = cat_revenue if cat_type == 'revenue' else cat_expense

                match, ambiguous = self._find_legacy_match(
                    legacy, consumed, gross, posted, cat_type, self._norm(details))
                if match:
                    match.write({'fitid': fitid, 'account_ref': acctid, 'date': posted})
                    consumed.add(match.id)
                    existing_fitids.add(fitid)
                    totals['reconciled'] += 1
                    s_reconciled += 1
                    if ambiguous:
                        ambiguities.append(
                            _("FITID %(fitid)s: several tied candidates, chose id=%(id)s (%(details)s)",
                              fitid=fitid, id=match.id, details=match.details))
                    continue

                try:
                    with self.env.cr.savepoint():
                        Transaction.create({
                            'date': posted,
                            'category_id': default_cat,
                            'gross_amount': gross,
                            'details': details,
                            'fitid': fitid,
                            'account_ref': acctid,
                        })
                    existing_fitids.add(fitid)
                    totals['created'] += 1
                    s_created += 1
                except AccessError:
                    raise
                except Exception as e:
                    errors.append(_("FITID %(fitid)s (%(date)s): %(error)s", fitid=fitid, date=posted, error=e))

            report.append(_(
                "%(label)s: %(count)s transaction(s) — %(created)s created, %(reconciled)s reconciled",
                label=label, count=len(txns), created=s_created, reconciled=s_reconciled))
            for w in stmt['warnings']:
                report.append("  ⚠ " + self._lazy_str(w))

        # Summary.
        summary = [
            _("=== Summary ==="),
            _("%(created)s created, %(reconciled)s reconciled (legacy), %(dup)s duplicate FITID(s) skipped,",
              created=totals['created'], reconciled=totals['reconciled'], dup=totals['dup_fitid']),
            _("%(payment)s CC payment(s) skipped, %(transfer)s transfer(s) skipped, %(floor)s before the floor date.",
              payment=totals['skipped_payment'], transfer=totals['skipped_transfer'], floor=totals['skipped_floor']),
        ]
        if refunds:
            summary.append(_("CC refunds created as income (%s): ", len(refunds))
                           + "; ".join(refunds))
        if ambiguities:
            summary.append(_("Reconciliation ambiguities:"))
            summary.extend(f"  - {a}" for a in ambiguities)
        if errors:
            summary.append(_("%s error(s):", len(errors)))
            summary.extend(f"  - {e}" for e in errors[:20])
            if len(errors) > 20:
                summary.append(_("  ... and %s more", len(errors) - 20))

        return '\n'.join(summary + ['', _("=== Detail ===")] + report)

    def _parse_date(self, val):
        if not val or not val.strip():
            return False
        val = val.strip()
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%Y/%m/%d'):
            try:
                return fields.Date.to_string(
                    fields.Date.from_string(
                        __import__('datetime').datetime.strptime(val, fmt).strftime('%Y-%m-%d')
                    )
                )
            except (ValueError, TypeError):
                continue
        return False

    def _parse_float(self, val):
        if not val or not val.strip():
            return 0.0
        val = val.strip().replace(',', '.').replace('\xa0', '').replace(' ', '')
        try:
            return float(val)
        except ValueError:
            return 0.0

    # --- Import methods ---

    def _import_transaction(self, rows):
        """CSV columns: date, category, type(R/E), gross_amount, details, self_percent, contributor"""
        created = 0
        errors = []
        Transaction = self.env['personal.budget.transaction']

        for i, row in enumerate(rows, start=2 if self.skip_header else 1):
            try:
                with self.env.cr.savepoint():
                    if len(row) < 4:
                        errors.append(_("Line %(line)s: not enough columns (%(count)s)", line=i, count=len(row)))
                        continue

                    date = self._parse_date(row[0])
                    cat_name = row[1].strip() if len(row) > 1 else ''
                    cat_type_raw = row[2].strip().upper() if len(row) > 2 else 'E'
                    cat_type = 'revenue' if cat_type_raw in ('R', 'REVENU', 'REVENUE') else 'expense'
                    gross = self._parse_float(row[3])
                    details = row[4].strip() if len(row) > 4 else ''
                    self_pct = self._parse_float(row[5]) if len(row) > 5 and row[5].strip() else 100.0

                    cat_id = self._get_or_create_category(cat_name, cat_type)
                    if not cat_id:
                        errors.append(_("Line %s: missing category", i))
                        continue

                    vals = {
                        'date': date,
                        'category_id': cat_id,
                        'gross_amount': gross,
                        'details': details,
                        'self_percent': self_pct,
                    }

                    if len(row) > 6 and row[6].strip():
                        contributor = self.env['personal.budget.contributor'].search(
                            [('book_id', '=', self.book_id.id),
                             ('name', '=ilike', row[6].strip())], limit=1
                        )
                        if contributor:
                            vals['contributor_id'] = contributor.id

                    Transaction.create(vals)
                    created += 1
            except AccessError:
                raise
            except Exception as e:
                errors.append(_("Line %(line)s: %(error)s", line=i, error=e))

        return created, errors

    def _import_share(self, rows):
        """CSV columns: sequence, date, description, amount"""
        created = 0
        errors = []
        ShareLine = self.env['personal.budget.share.line']

        for i, row in enumerate(rows, start=2 if self.skip_header else 1):
            try:
                with self.env.cr.savepoint():
                    if len(row) < 4:
                        errors.append(_("Line %s: not enough columns", i))
                        continue

                    vals = {
                        'sequence': int(row[0]) if row[0].strip() else 10,
                        'date': self._parse_date(row[1]),
                        'description': row[2].strip(),
                        'amount': self._parse_float(row[3]),
                        'book_id': self.book_id.id,
                    }
                    ShareLine.create(vals)
                    created += 1
            except AccessError:
                raise
            except Exception as e:
                errors.append(_("Line %(line)s: %(error)s", line=i, error=e))

        return created, errors

    def _import_cheque(self, rows):
        """CSV columns: number, date, payee, amount, memo, is_cashed, comments, bank_label"""
        created = 0
        errors = []
        Cheque = self.env['personal.budget.cheque']

        for i, row in enumerate(rows, start=2 if self.skip_header else 1):
            try:
                with self.env.cr.savepoint():
                    if len(row) < 4:
                        errors.append(_("Line %s: not enough columns", i))
                        continue

                    vals = {
                        'number': row[0].strip(),
                        'date': self._parse_date(row[1]),
                        'payee': row[2].strip() if len(row) > 2 else '',
                        'amount': self._parse_float(row[3]),
                        'memo': row[4].strip() if len(row) > 4 else '',
                        'is_cashed': row[5].strip().lower() in ('1', 'true', 'oui', 'yes', 'x') if len(row) > 5 else False,
                        'comments': row[6].strip() if len(row) > 6 else '',
                        'bank_label': row[7].strip() if len(row) > 7 else '',
                        'book_id': self.book_id.id,
                    }
                    Cheque.create(vals)
                    created += 1
            except AccessError:
                raise
            except Exception as e:
                errors.append(_("Line %(line)s: %(error)s", line=i, error=e))

        return created, errors

    def _import_loan_line(self, rows):
        """CSV columns: date, description, amount"""
        if not self.loan_id:
            return 0, [_("Please select a target loan.")]
        if self.loan_id.book_id != self.book_id:
            return 0, [_("The target loan belongs to a different budget than the import.")]

        created = 0
        errors = []
        LoanLine = self.env['personal.budget.loan.line']

        for i, row in enumerate(rows, start=2 if self.skip_header else 1):
            try:
                with self.env.cr.savepoint():
                    if len(row) < 3:
                        errors.append(_("Line %s: not enough columns", i))
                        continue

                    vals = {
                        'loan_id': self.loan_id.id,
                        'date': self._parse_date(row[0]),
                        'description': row[1].strip(),
                        'amount': self._parse_float(row[2]),
                    }
                    LoanLine.create(vals)
                    created += 1
            except AccessError:
                raise
            except Exception as e:
                errors.append(_("Line %(line)s: %(error)s", line=i, error=e))

        return created, errors

    def _import_invoice(self, rows):
        """CSV columns: invoice_number, partner_name, invoice_date, due_date, pretax_amount, description, company_label, is_duplicate"""
        created = 0
        errors = []
        Invoice = self.env['personal.budget.invoice']

        for i, row in enumerate(rows, start=2 if self.skip_header else 1):
            try:
                with self.env.cr.savepoint():
                    if len(row) < 5:
                        errors.append(_("Line %s: not enough columns", i))
                        continue

                    vals = {
                        'invoice_number': row[0].strip(),
                        'partner_name': row[1].strip() if len(row) > 1 else '',
                        'invoice_date': self._parse_date(row[2]),
                        'due_date': self._parse_date(row[3]) if len(row) > 3 else False,
                        'pretax_amount': self._parse_float(row[4]),
                        'description': row[5].strip() if len(row) > 5 else '',
                        'company_label': row[6].strip() if len(row) > 6 else '',
                        'is_duplicate': row[7].strip().lower() in ('1', 'true', 'oui', 'yes', 'x') if len(row) > 7 else False,
                        'book_id': self.book_id.id,
                    }
                    Invoice.create(vals)
                    created += 1
            except AccessError:
                raise
            except Exception as e:
                errors.append(_("Line %(line)s: %(error)s", line=i, error=e))

        return created, errors

    def _import_plan(self, rows):
        """CSV columns: year, category, type(R/E), planned_amount"""
        created = 0
        errors = []
        Plan = self.env['personal.budget.plan']

        for i, row in enumerate(rows, start=2 if self.skip_header else 1):
            try:
                with self.env.cr.savepoint():
                    if len(row) < 4:
                        errors.append(_("Line %s: not enough columns", i))
                        continue

                    year = int(row[0].strip())
                    cat_name = row[1].strip()
                    cat_type_raw = row[2].strip().upper()
                    cat_type = 'revenue' if cat_type_raw in ('R', 'REVENU', 'REVENUE') else 'expense'
                    amount = self._parse_float(row[3])

                    cat_id = self._get_or_create_category(cat_name, cat_type)
                    if not cat_id:
                        errors.append(_("Line %s: missing category", i))
                        continue

                    existing = Plan.search([
                        ('year', '=', year), ('category_id', '=', cat_id),
                    ], limit=1)
                    if existing:
                        existing.write({'planned_amount': amount})
                    else:
                        Plan.create({
                            'year': year,
                            'category_id': cat_id,
                            'planned_amount': amount,
                        })
                    created += 1
            except AccessError:
                raise
            except Exception as e:
                errors.append(_("Line %(line)s: %(error)s", line=i, error=e))

        return created, errors
