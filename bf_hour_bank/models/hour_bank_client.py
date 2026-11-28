import base64
import io
import logging
from collections import defaultdict
from datetime import datetime

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import formatLang, get_lang

_logger = logging.getLogger(__name__)

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    _logger.warning("openpyxl not installed — Excel generation disabled")
    openpyxl = None


class HourBankClient(models.Model):
    _name = 'hour.bank.client'
    _description = "Client hour bank configuration"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    name = fields.Char(
        compute='_compute_name', store=True, string="Name",
    )
    partner_id = fields.Many2one(
        'res.partner', required=True, string="Client",
        tracking=True,
    )
    project_ids = fields.Many2many(
        'project.project', string="Included projects",
    )
    product_filter_mode = fields.Selection([
        ('all', "All invoice lines"),
        ('include', "Include only these products"),
        ('exclude', "Exclude these products"),
    ], string="Product filter", default='all', required=True)
    filter_product_ids = fields.Many2many(
        'product.product', string="Products",
    )
    company_filter_mode = fields.Selection([
        ('all', "All companies"),
        ('include', "Include only these companies"),
        ('exclude', "Exclude these companies"),
    ], string="Company filter", default='all', required=True)
    filter_company_ids = fields.Many2many(
        'res.company', string="Companies",
    )
    invoice_partner_ids = fields.Many2many(
        'res.partner', 'hour_bank_invoice_partner_rel',
        string="Billing partners",
        help="If set, only the invoices issued to these partners are "
             "counted. Otherwise every invoice of the commercial partner "
             "is included.",
    )
    extra_invoice_ids = fields.Many2many(
        'account.move', string="Extra invoices",
        domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted')]",
    )
    adjustment_ids = fields.One2many(
        'hour.bank.adjustment', 'hour_bank_id',
        string="Manual adjustments",
    )
    recipient_ids = fields.Many2many(
        'res.partner', 'hour_bank_recipient_rel',
        string="Report recipients",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company,
    )
    current_balance = fields.Float(
        compute='_compute_balance', string="Current balance",
    )
    last_report_date = fields.Datetime(
        string="Last report", tracking=True,
    )
    auto_send = fields.Boolean(
        string="Automatic sending", default=False, tracking=True,
    )
    send_frequency = fields.Selection([
        ('weekly', "Weekly (Monday)"),
        ('biweekly', "Fortnightly (Monday)"),
        ('monthly', "Monthly (1st of the month)"),
    ], string="Sending frequency", default='weekly')

    # ---- Threshold notifications ----
    threshold_mode = fields.Selection([
        ('disabled', "Disabled"),
        ('unbilled', "Unbilled hours"),
        ('budget_pct', "% of allocated budget"),
        ('balance_floor', "Remaining balance below a floor"),
    ], string="Threshold mode", default='disabled', required=True, tracking=True)
    threshold_budget_hours = fields.Float(
        string="Allocated budget (h)", tracking=True,
        help="Reference budget used in '%% of allocated budget' mode. "
             "Changing this value rearms every threshold.",
    )
    threshold_line_ids = fields.One2many(
        'hour.bank.threshold.line', 'bank_id',
        string="Thresholds to watch",
    )
    threshold_event_ids = fields.One2many(
        'hour.bank.threshold.event', 'bank_id',
        string="Alert history",
    )
    threshold_event_count = fields.Integer(
        compute='_compute_threshold_event_count', string="Alerts sent",
    )
    alert_recipient_ids = fields.Many2many(
        'res.partner', 'hour_bank_alert_recipient_rel',
        string="Alert recipients",
        help="Recipients of the threshold alerts. Leave empty to reuse "
             "the periodic report's recipients.",
    )
    notify_internal_followers = fields.Boolean(
        string="Notify internal followers", default=True,
        # Le module tourne chez plusieurs locataires : l'aide ne nomme la maison
        # d'aucun d'eux.
        help="If checked, also posts a message in the chatter to alert the "
             "record's internal followers.",
    )

    # ---- Facture générée depuis la banque ----
    invoice_mode = fields.Selection([
        ('postpaid', "Postpaid: bill the hours consumed"),
        ('prepaid', "Prepaid: bill a block of hours"),
    ], string="Billing mode", default='postpaid', required=True, tracking=True)
    invoice_block_hours = fields.Float(
        string="Block (h)", tracking=True,
        help="Number of hours billed in advance at each renewal, in "
             "prepaid mode.",
    )
    invoice_product_id = fields.Many2one(
        'product.product', string="Invoiced product", tracking=True,
        help="Product of the generated invoice lines. It must be a "
             "product the bank credits (product filter), otherwise the "
             "posted invoice would not reduce the balance.",
    )
    invoice_hourly_rate = fields.Float(
        string="Hourly rate", digits='Product Price', tracking=True,
        help="Unit price of the generated lines. Prefilled from the price "
             "on the last invoice counted by the bank, never from the "
             "catalogue price.",
    )
    invoice_split_by_project = fields.Boolean(
        string="Split per project",
        help="By default, one line per project. Otherwise a single line "
             "for the period, the detail being in the attached report. "
             "The wizard lets you change your mind for one invoice.",
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('partner_id')
    def _compute_name(self):
        for rec in self:
            rec.name = rec.partner_id.name or _("New")

    def _compute_balance(self):
        for rec in self:
            data = rec._get_report_data()
            rec.current_balance = data['balance']

    # ------------------------------------------------------------------
    # Core: build report data
    # ------------------------------------------------------------------

    def _bf_lang(self, partner=None):
        """La langue de qui lira : le destinataire s'il est donné, sinon le
        client de la banque, puis sa société mère, puis la maison.

        Un envoi automatique tourne sans langue au contexte : sans ce repli, le
        rapport partait dans la langue SOURCE (l'anglais) à un
        client francophone. Seules les langues actives comptent."""
        self.ensure_one()
        actives = {code for code, _name in self.env['res.lang'].get_installed()}
        candidats = [
            partner and partner.lang,
            self.partner_id.lang,
            self.partner_id.commercial_partner_id.lang,
            self.company_id.partner_id.lang,
            self.env.lang,
        ]
        for code in candidats:
            if code and code in actives:
                return code
        return next(iter(sorted(actives)), 'en_US')

    def _bf_by_lang(self, partners):
        """{langue: destinataires}, chacun dans sa langue (voir _bf_lang)."""
        self.ensure_one()
        groupes = {}
        for partner in partners:
            groupes.setdefault(self._bf_lang(partner), self.env['res.partner'])
            groupes[self._bf_lang(partner)] |= partner
        return groupes

    def _bf_file_stem(self, date_str=None):
        """Nom de fichier du rapport (sans extension), dans la langue du
        contexte : « Banque_heures_<client>_<date> » en français."""
        self.ensure_one()
        return "%s_%s_%s" % (
            self.env._("Hour_bank"),
            (self.partner_id.name or '').replace(' ', '_'),
            date_str or fields.Date.today().isoformat(),
        )

    def _bf_tz_lecteur(self):
        """Le fuseau du client à qui part le PDF, jamais celui de qui l'envoie.

        L'envoi automatique tourne sous l'usager système (sans fuseau, donc en
        UTC) et un envoi manuel d'Auckland datait le PDF d'un client du Québec en
        heure de Nouvelle-Zélande. Repli : le fuseau par défaut de bf_timezone s'il
        est installé, sinon celui de la société."""
        self.ensure_one()
        if self.partner_id.tz:
            return self.partner_id.tz
        if 'bf.timezone' in self.env:
            return self.env['bf.timezone'].default_tz()
        return self.company_id.partner_id.tz or self.env.user.tz or 'UTC'

    def _get_report_data(self, date_from=None, date_to=None):
        """Build the full hour-bank dataset (debits + credits) for self.

        Returns dict with keys:
            entries: list of dicts sorted date ASC
            balance: final cumulative balance
            summary_by_project: {project_name: total_hours}
            summary_by_month: {(year, month): {project_name: hours}}
            last_update: datetime
        """
        self.ensure_one()
        # La liste des projets se lit en sudo : c'est la configuration de la
        # banque qui décide de ce qui compte, pas ce que voit la personne. Lue
        # avec ses droits, un projet privé (visibilité « abonnés ») disparaissait
        # et le solde, le PDF et la facture générée en perdaient les
        # heures. Le portail et les envois automatiques lisaient déjà en sudo.
        projects = self.sudo().project_ids
        # Même règle pour les filtres : lus avec les droits de la personne, une
        # société décochée ou un produit d'une autre société vidait le filtre,
        # qui ne filtrait alors plus rien.
        cfg = self.sudo()

        if not projects:
            return {
                'entries': [],
                'balance': 0.0,
                'summary_by_project': {},
                'summary_by_month': {},
                'last_update': fields.Datetime.now(),
            }

        project_ids = tuple(projects.ids)
        partner_id = cfg.partner_id.commercial_partner_id.id or cfg.partner_id.id

        # ---- Debits: timesheets ----
        ts_clauses = ["aal.project_id IN %s"]
        ts_params = [project_ids]
        if date_from:
            ts_clauses.append("aal.date >= %s")
            ts_params.append(date_from)
        if date_to:
            ts_clauses.append("aal.date <= %s")
            ts_params.append(date_to)

        ts_sql = """
            SELECT
                aal.date AS entry_date,
                -aal.unit_amount AS hours,
                aal.name AS description,
                pp.name->>'en_US' AS project_name,
                pt.name AS task_name,
                'debit' AS entry_type,
                'timesheet' AS source_kind,
                aal.id AS source_id,
                aal.project_id AS project_id
            FROM account_analytic_line aal
            JOIN project_project pp ON pp.id = aal.project_id
            LEFT JOIN project_task pt ON pt.id = aal.task_id
            WHERE """ + " AND ".join(ts_clauses)

        # ---- Credits: posted customer invoices ----
        inv_clauses = [
            "am.move_type = 'out_invoice'",
            "am.state = 'posted'",
        ]
        inv_params = []

        # Company filter
        if cfg.filter_company_ids and cfg.company_filter_mode == 'include':
            inv_clauses.append("am.company_id IN %s")
            inv_params.append(tuple(cfg.filter_company_ids.ids))
        elif cfg.filter_company_ids and cfg.company_filter_mode == 'exclude':
            inv_clauses.append("am.company_id NOT IN %s")
            inv_params.append(tuple(cfg.filter_company_ids.ids))

        # Partner filter: specific partners or commercial partner fallback
        extra_ids = tuple(cfg.extra_invoice_ids.ids) if cfg.extra_invoice_ids else ()
        if cfg.invoice_partner_ids:
            partner_filter_ids = tuple(cfg.invoice_partner_ids.ids)
            if extra_ids:
                inv_clauses.append("(am.partner_id IN %s OR am.id IN %s)")
                inv_params += [partner_filter_ids, extra_ids]
            else:
                inv_clauses.append("am.partner_id IN %s")
                inv_params.append(partner_filter_ids)
        else:
            if extra_ids:
                inv_clauses.append("(am.commercial_partner_id = %s OR am.id IN %s)")
                inv_params += [partner_id, extra_ids]
            else:
                inv_clauses.append("am.commercial_partner_id = %s")
                inv_params.append(partner_id)

        if cfg.filter_product_ids and cfg.product_filter_mode == 'include':
            inv_clauses.append("aml.product_id IN %s")
            inv_params.append(tuple(cfg.filter_product_ids.ids))
        elif cfg.filter_product_ids and cfg.product_filter_mode == 'exclude':
            inv_clauses.append("(aml.product_id IS NULL OR aml.product_id NOT IN %s)")
            inv_params.append(tuple(cfg.filter_product_ids.ids))

        if date_from:
            inv_clauses.append("am.invoice_date >= %s")
            inv_params.append(date_from)
        if date_to:
            inv_clauses.append("am.invoice_date <= %s")
            inv_params.append(date_to)

        inv_clauses += ["aml.quantity > 0", "aml.display_type = 'product'"]

        inv_sql = """
            SELECT
                am.invoice_date AS entry_date,
                aml.quantity AS hours,
                am.name AS description,
                NULL AS project_name,
                NULL AS task_name,
                'credit' AS entry_type,
                'invoice' AS source_kind,
                aml.id AS source_id,
                NULL AS project_id
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            WHERE """ + " AND ".join(inv_clauses)

        # ---- Adjustments: manual entries ----
        adj_clauses = ["hba.hour_bank_id = %s"]
        adj_params = [self.id]
        if date_from:
            adj_clauses.append("hba.date >= %s")
            adj_params.append(date_from)
        if date_to:
            adj_clauses.append("hba.date <= %s")
            adj_params.append(date_to)

        adj_sql = """
            SELECT
                hba.date AS entry_date,
                hba.hours AS hours,
                hba.description AS description,
                NULL AS project_name,
                NULL AS task_name,
                CASE WHEN hba.hours >= 0 THEN 'credit' ELSE 'debit' END AS entry_type,
                'adjustment' AS source_kind,
                hba.id + 1000000000 AS source_id,
                NULL AS project_id
            FROM hour_bank_adjustment hba
            WHERE """ + " AND ".join(adj_clauses)

        # ---- Union + order ----
        sql = """
            SELECT * FROM (
                (%s)
                UNION ALL
                (%s)
                UNION ALL
                (%s)
            ) combined
            ORDER BY entry_date ASC, entry_type DESC, source_id ASC
        """ % (ts_sql, inv_sql, adj_sql)
        params = ts_params + inv_params + adj_params
        self.env.cr.execute(sql, params)
        rows = self.env.cr.dictfetchall()

        # ---- Build entries with cumulative balance ----
        entries = []
        balance = 0.0
        summary_by_project = defaultdict(float)
        # summary_by_month: {(year, month): {project: hours}}
        summary_by_month = defaultdict(lambda: defaultdict(float))

        # Les deux libellés de regroupement se rendent dans la langue du
        # contexte : écrits en dur dans le SQL, ils restaient français dans le
        # PDF, le classeur et le portail d'un client anglophone.
        kind_labels = {
            'invoice': self.env._("Billed hours"),
            'adjustment': self.env._("Adjustment"),
        }
        for row in rows:
            balance += row['hours']
            project_name = row['project_name'] or kind_labels.get(row['source_kind'], '')
            entry = {
                'date': row['entry_date'],
                'hours': row['hours'],
                'cumulative': balance,
                'description': row['description'] or '',
                'project': project_name or '',
                'task': row['task_name'] or '',
                'entry_type': row['entry_type'],
                'project_id': row['project_id'],
            }
            entries.append(entry)

            # Summaries
            summary_by_project[entry['project']] += row['hours']
            dt = row['entry_date']
            summary_by_month[(dt.year, dt.month)][entry['project']] += row['hours']

        # Reverse for display (most recent first)
        entries.reverse()

        return {
            'entries': entries,
            'balance': balance,
            'summary_by_project': dict(summary_by_project),
            'summary_by_month': {k: dict(v) for k, v in summary_by_month.items()},
            'last_update': fields.Datetime.now(),
        }

    # ------------------------------------------------------------------
    # PDF generation
    # ------------------------------------------------------------------

    def action_generate_pdf(self):
        """Generate the PDF report and return download action."""
        self.ensure_one()
        return self.env.ref(
            'bf_hour_bank.action_report_hour_bank'
        ).report_action(self)

    def _get_pdf_binary(self, lang=None):
        """Generate PDF as binary for attachment purposes.

        `lang` : la langue du lecteur. Sans elle, le PDF suit le contexte, et
        un envoi planifié (sans langue) le rendait dans la langue source."""
        self.ensure_one()
        if lang:
            self = self.with_context(lang=lang)
        pdf_content, _content_type = self.env['ir.actions.report']._render_qweb_pdf(
            'bf_hour_bank.action_report_hour_bank', self.ids,
        )
        return pdf_content

    # ------------------------------------------------------------------
    # Excel generation
    # ------------------------------------------------------------------

    def action_generate_xlsx(self):
        """Generate Excel and return download action."""
        self.ensure_one()
        xlsx_data = self._generate_xlsx_binary()
        filename = "%s.xlsx" % self._bf_file_stem()
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(xlsx_data),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'res_model': self._name,
            'res_id': self.id,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%d?download=true' % attachment.id,
            'target': 'new',
        }

    def _generate_xlsx_binary(self, lang=None):
        """Build a 4-sheet .xlsx and return raw bytes.

        Onglets et en-têtes dans la langue du contexte (ou `lang`) : ils
        étaient écrits en dur, en français pour tout le monde."""
        self.ensure_one()
        if lang:
            self = self.with_context(lang=lang)
        env = self.env
        if not openpyxl:
            raise UserError(_("The openpyxl library is not installed."))

        data = self._get_report_data()
        wb = openpyxl.Workbook()

        # Styles
        header_font = Font(name='Calibri', bold=True, size=11, color='FFFFFF')
        header_hex = (self.env.company.report_brand_dark or '#212529').lstrip('#').upper()
        header_fill = PatternFill(start_color=header_hex, end_color=header_hex, fill_type='solid')
        credit_fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
        number_fmt = '#,##0.00'
        thin_border = Border(
            left=Side(style='thin', color='D9D9D9'),
            right=Side(style='thin', color='D9D9D9'),
            top=Side(style='thin', color='D9D9D9'),
            bottom=Side(style='thin', color='D9D9D9'),
        )

        def safe_cell_value(value):
            """Prevent Excel formula injection by prefixing dangerous characters."""
            if isinstance(value, str) and value and value[0] in ('=', '+', '-', '@'):
                return "'" + value
            return value

        def style_header(ws, cols):
            for idx, col in enumerate(cols, 1):
                cell = ws.cell(row=1, column=idx, value=col)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal='center')
                cell.border = thin_border

        # ─── Sheet 1: timesheets ───
        ws1 = wb.active
        ws1.title = env._("Timesheets")
        cols1 = [env._("Date"), env._("Number of hours"), env._("Running balance"),
                 env._("Description"), env._("Project"), env._("Task")]
        style_header(ws1, cols1)

        for i, entry in enumerate(data['entries'], 2):
            ws1.cell(row=i, column=1, value=entry['date']).number_format = 'YYYY-MM-DD'
            ws1.cell(row=i, column=2, value=entry['hours']).number_format = number_fmt
            ws1.cell(row=i, column=3, value=entry['cumulative']).number_format = number_fmt
            ws1.cell(row=i, column=4, value=safe_cell_value(entry['description']))
            ws1.cell(row=i, column=5, value=safe_cell_value(entry['project']))
            ws1.cell(row=i, column=6, value=safe_cell_value(entry['task']))
            for c in range(1, 7):
                ws1.cell(row=i, column=c).border = thin_border
            if entry['entry_type'] == 'credit':
                for c in range(1, 7):
                    ws1.cell(row=i, column=c).fill = credit_fill

        # Auto-width
        for col_idx in range(1, 7):
            ws1.column_dimensions[get_column_letter(col_idx)].width = (
                [14, 16, 16, 40, 25, 25][col_idx - 1]
            )

        # ─── Sheet 2: per-project summary ───
        ws2 = wb.create_sheet(env._("Per-project summary"))
        cols2 = [env._("Project"), env._("Total hours")]
        style_header(ws2, cols2)
        row = 2
        for project, hours in sorted(data['summary_by_project'].items()):
            ws2.cell(row=row, column=1, value=project).border = thin_border
            cell = ws2.cell(row=row, column=2, value=hours)
            cell.number_format = number_fmt
            cell.border = thin_border
            row += 1
        ws2.column_dimensions['A'].width = 40
        ws2.column_dimensions['B'].width = 18

        # ─── Sheet 3: monthly summary ───
        ws3 = wb.create_sheet(env._("Monthly summary"))
        # Collect all projects for column headers
        all_projects = sorted(set(
            p for month_data in data['summary_by_month'].values()
            for p in month_data
        ))
        cols3 = [env._("Month")] + all_projects + [env._("Total")]
        style_header(ws3, cols3)

        row = 2
        for (year, month), proj_hours in sorted(data['summary_by_month'].items()):
            ws3.cell(row=row, column=1, value=f"{year}-{month:02d}").border = thin_border
            month_total = 0.0
            for col_idx, proj in enumerate(all_projects, 2):
                val = proj_hours.get(proj, 0.0)
                cell = ws3.cell(row=row, column=col_idx, value=val)
                cell.number_format = number_fmt
                cell.border = thin_border
                month_total += val
            cell = ws3.cell(row=row, column=len(all_projects) + 2, value=month_total)
            cell.number_format = number_fmt
            cell.border = thin_border
            cell.font = Font(bold=True)
            row += 1

        ws3.column_dimensions['A'].width = 14
        for idx in range(2, len(all_projects) + 3):
            ws3.column_dimensions[get_column_letter(idx)].width = 20

        # ─── Sheet 4: to bill ───
        ws4 = wb.create_sheet(env._("To bill"))
        cols4 = [env._("Date"), env._("Hours"), env._("Description"), env._("Project"), env._("Task")]
        style_header(ws4, cols4)

        # Entries since last credit (invoice)
        unbilled = []
        for entry in reversed(data['entries']):
            if entry['entry_type'] == 'credit':
                break
            unbilled.insert(0, entry)

        row = 2
        total_unbilled = 0.0
        for entry in unbilled:
            ws4.cell(row=row, column=1, value=entry['date']).number_format = 'YYYY-MM-DD'
            ws4.cell(row=row, column=2, value=entry['hours']).number_format = number_fmt
            ws4.cell(row=row, column=3, value=safe_cell_value(entry['description']))
            ws4.cell(row=row, column=4, value=safe_cell_value(entry['project']))
            ws4.cell(row=row, column=5, value=safe_cell_value(entry['task']))
            for c in range(1, 6):
                ws4.cell(row=row, column=c).border = thin_border
            total_unbilled += entry['hours']
            row += 1

        # Total row
        ws4.cell(row=row, column=1, value=env._("TOTAL")).font = Font(bold=True)
        ws4.cell(row=row, column=1).border = thin_border
        total_cell = ws4.cell(row=row, column=2, value=total_unbilled)
        total_cell.number_format = number_fmt
        total_cell.font = Font(bold=True)
        total_cell.border = thin_border

        ws4.column_dimensions['A'].width = 14
        ws4.column_dimensions['B'].width = 14
        ws4.column_dimensions['C'].width = 40
        ws4.column_dimensions['D'].width = 25
        ws4.column_dimensions['E'].width = 25

        # Write to buffer
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Open send wizard
    # ------------------------------------------------------------------

    def action_open_send_wizard(self):
        """Open the email-send wizard with pre-generated attachments."""
        self.ensure_one()
        wizard = self.env['hour.bank.send.wizard'].create({
            'hour_bank_id': self.id,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _("Send the report"),
            'res_model': 'hour.bank.send.wizard',
            'res_id': wizard.id,
            'views': [[False, 'form']],
            'target': 'new',
        }

    # ------------------------------------------------------------------
    # Facture générée depuis la banque
    # ------------------------------------------------------------------

    @api.constrains('invoice_product_id', 'product_filter_mode', 'filter_product_ids')
    def _check_invoice_product(self):
        for rec in self:
            if rec.invoice_product_id and not rec._bf_product_credits(rec.invoice_product_id):
                raise ValidationError(_(
                    "The invoiced product \"%(product)s\" is not counted "
                    "by this bank's product filter: a posted invoice on "
                    "that product would not reduce the balance. Choose a "
                    "product from the filter, or add it to the filter.",
                    product=rec.invoice_product_id.display_name,
                ))

    @api.onchange('invoice_product_id')
    def _onchange_invoice_product_id(self):
        if self.invoice_product_id and not self.invoice_hourly_rate:
            line = self._bf_last_credit_line(self.invoice_product_id)
            if line:
                self.invoice_hourly_rate = line.price_unit

    def _bf_product_credits(self, product):
        """Vrai si une ligne sur ce produit serait comptée par _get_report_data.

        Même règle que le SQL : un filtre sans produits ne filtre rien."""
        self.ensure_one()
        cfg = self.sudo()
        if cfg.filter_product_ids and cfg.product_filter_mode == 'include':
            return product in cfg.filter_product_ids
        if cfg.filter_product_ids and cfg.product_filter_mode == 'exclude':
            return product not in cfg.filter_product_ids
        return True

    def _bf_partner_credits(self, partner):
        """Vrai si une facture adressée à ce partenaire serait comptée.

        Le crédit se lit sur commercial_partner_id quand invoice_partner_ids est
        vide : un contact enfant du client compte, un autre client jamais."""
        self.ensure_one()
        cfg = self.sudo()
        if cfg.invoice_partner_ids:
            return partner in cfg.invoice_partner_ids
        commercial = cfg.partner_id.commercial_partner_id or cfg.partner_id
        return partner.commercial_partner_id == commercial

    def _bf_company_credits(self, company):
        self.ensure_one()
        cfg = self.sudo()
        if cfg.filter_company_ids and cfg.company_filter_mode == 'include':
            return company in cfg.filter_company_ids
        if cfg.filter_company_ids and cfg.company_filter_mode == 'exclude':
            return company not in cfg.filter_company_ids
        return True

    def _bf_credit_line_domain(self, state='posted'):
        """Domaine des lignes de facture que la banque compte, miroir ORM du
        côté crédit de _get_report_data. `state='draft'` donne les lignes qui
        seront comptées une fois leur facture validée."""
        self.ensure_one()
        cfg = self.sudo()
        partner_id = cfg.partner_id.commercial_partner_id.id or cfg.partner_id.id
        domain = [
            ('move_id.move_type', '=', 'out_invoice'),
            ('move_id.state', '=', state),
            ('display_type', '=', 'product'),
            ('quantity', '>', 0),
        ]
        if cfg.filter_company_ids and cfg.company_filter_mode == 'include':
            domain.append(('move_id.company_id', 'in', cfg.filter_company_ids.ids))
        elif cfg.filter_company_ids and cfg.company_filter_mode == 'exclude':
            domain.append(('move_id.company_id', 'not in', cfg.filter_company_ids.ids))
        if cfg.invoice_partner_ids:
            partner_clause = [('move_id.partner_id', 'in', cfg.invoice_partner_ids.ids)]
        else:
            partner_clause = [('move_id.commercial_partner_id', '=', partner_id)]
        if cfg.extra_invoice_ids:
            partner_clause = ['|'] + partner_clause + [('move_id', 'in', cfg.extra_invoice_ids.ids)]
        domain += partner_clause
        if cfg.filter_product_ids and cfg.product_filter_mode == 'include':
            domain.append(('product_id', 'in', cfg.filter_product_ids.ids))
        elif cfg.filter_product_ids and cfg.product_filter_mode == 'exclude':
            domain += ['|', ('product_id', '=', False),
                       ('product_id', 'not in', cfg.filter_product_ids.ids)]
        return domain

    def _bf_last_credit_line(self, product=None):
        """La dernière ligne de facture validée que la banque compte (sur ce
        produit si donné) : elle porte le taux, les taxes et le contact facturé."""
        self.ensure_one()
        domain = self._bf_credit_line_domain()
        if product:
            domain.append(('product_id', '=', product.id))
        return self.env['account.move.line'].sudo().search(
            domain, order='date desc, move_id desc, id desc', limit=1,
        )

    def _bf_draft_credit_lines(self):
        """Lignes de brouillons qui créditeront la banque une fois validés."""
        self.ensure_one()
        return self.env['account.move.line'].sudo().search(
            self._bf_credit_line_domain(state='draft'),
        )

    def _bf_uncovered_debits(self, date_cutoff, extra_credit=0.0):
        """Débits non couverts portés jusqu'à `date_cutoff`, du plus ancien au
        plus récent : [{'date', 'hours', 'project_id', 'project'}].

        Les crédits (factures validées, ajustements positifs, plus
        `extra_credit`, les heures déjà en brouillon) couvrent les débits les
        plus anciens d'abord ; un débit peut n'être couvert qu'en partie."""
        self.ensure_one()
        entries = list(reversed(self._get_report_data()['entries']))
        # Une feuille de temps négative (correction) rend des heures au lot dès
        # le départ, comme un crédit : versée au fil de la boucle, elle arrivait
        # après les débits plus anciens qu'elle corrige, déjà déclarés non
        # couverts (5 h proposées pour un solde de −3 h).
        pool = extra_credit + sum(
            e['hours'] for e in entries
            if e['entry_type'] == 'credit' or e['hours'] > 0
        )
        uncovered = []
        for entry in entries:
            if entry['entry_type'] != 'debit':
                continue
            need = -entry['hours']
            if need <= 0:
                continue
            covered = min(max(pool, 0.0), need)
            pool -= covered
            rest = need - covered
            if rest > 1e-6 and entry['date'] <= date_cutoff:
                uncovered.append({
                    'date': entry['date'],
                    'hours': rest,
                    'project_id': entry['project_id'],
                    'project': entry['project'],
                })
        return uncovered

    def action_open_invoice_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Generate the invoice"),
            'res_model': 'hour.bank.invoice.wizard',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_bank_id': self.id},
        }

    # ------------------------------------------------------------------
    # Automated sending (cron)
    # ------------------------------------------------------------------

    def _send_report_to_recipients(self):
        """Generate and send branded report email to recipients.

        Used by both the cron and can be called programmatically.
        """
        self.ensure_one()
        if not self.recipient_ids:
            _logger.info("Hour bank %s: no recipients configured, skipping", self.name)
            return

        date_str = fields.Date.today().isoformat()
        sender = self.company_id.email or self.env.user.email_formatted
        destinataires = self.recipient_ids.filtered('email')
        attachments = self.env['ir.attachment']

        # Un envoi par langue de lecteur : courriel, PDF et classeur dans la
        # langue de chacun. Le travail planifié tourne sans langue au
        # contexte ; tout rendre une seule fois l'aurait écrit dans la langue
        # source pour tout le monde.
        for lang, partners in self._bf_by_lang(destinataires).items():
            bank = self.with_context(lang=lang)
            stem = bank._bf_file_stem(date_str)
            pdf_att = self.env['ir.attachment'].create({
                'name': "%s.pdf" % stem,
                'type': 'binary',
                'datas': base64.b64encode(bank._get_pdf_binary()),
                'mimetype': 'application/pdf',
                'res_model': self._name,
                'res_id': self.id,
            })
            xlsx_att = self.env['ir.attachment'].create({
                'name': "%s.xlsx" % stem,
                'type': 'binary',
                'datas': base64.b64encode(bank._generate_xlsx_binary()),
                'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'res_model': self._name,
                'res_id': self.id,
            })
            attachments |= pdf_att | xlsx_att
            body_html = self.env['hour.bank.send.wizard'].with_context(
                lang=lang)._wrap_branded_body(bank._bf_report_body(self.company_id.name or ''))
            subject = bank.env._("Hour bank statement — %(client)s",
                                 client=self.partner_id.name)
            for partner in partners:
                mail = self.env['mail.mail'].sudo().create({
                    'subject': subject,
                    'body_html': body_html,
                    'email_from': sender,
                    # recipient_ids ONLY — never also set email_to to the same
                    # person: Odoo's _prepare_outgoing_list builds one outgoing
                    # email for email_to AND one per recipient_ids partner (no
                    # dedup), so the recipient gets two identical SMTP deliveries.
                    'recipient_ids': [(4, partner.id)],
                    'attachment_ids': [(6, 0, [pdf_att.id, xlsx_att.id])],
                })
                mail.send()

        self.write({'last_report_date': fields.Datetime.now()})

        recipient_names = ', '.join(self.recipient_ids.mapped('name'))
        self.message_post(
            body=_("Automatic report sent to %s") % recipient_names,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
            attachment_ids=attachments.ids,
        )
        _logger.info("Hour bank %s: report sent to %s", self.name, recipient_names)

    def _bf_report_body(self, signature):
        """Contenu du courriel du rapport (sans l'habillage), dans la langue
        du contexte. L'assistant d'envoi le pré-remplit, l'envoi planifié
        l'habille directement.

        Le texte passe par le catalogue phrase par phrase ; le balisage reste
        ici, et les valeurs sont échappées par Markup."""
        self.ensure_one()
        env = self.env
        p = Markup('<p style="font-size:16px;line-height:26px;color:#374151;margin:%s;">%s</p>')
        inner = Markup('').join([
            p % ('0 0 16px 0', env._("Hello,")),
            p % ('0 0 20px 0', Markup(env._(
                "Please find attached the hour bank statement for "
                "%(client)s.")) % {
                    'client': Markup('<strong>%s</strong>') % (self.partner_id.name or '')}),
            p % ('0 0 20px 0', env._("The PDF and Excel files are attached.")),
            p % ('0', Markup('%s<br/>%s') % (env._("Kind regards,"), signature)),
        ])
        return inner

    @api.model
    def _cron_send_reports(self):
        """Cron job: send reports for all active auto-send hour banks."""
        # Sans langue au contexte, les notes du fil sortaient dans la langue
        # source : la maison écrit dans sa langue. Chaque courriel,
        # lui, prend la langue de son destinataire.
        if not self.env.lang:
            self = self.with_context(lang=get_lang(self.env).code)
        today = fields.Date.today()
        weekday = today.weekday()  # 0=Monday
        day = today.day

        banks = self.search([('auto_send', '=', True), ('active', '=', True)])
        for bank in banks:
            try:
                send = False
                if bank.send_frequency == 'weekly' and weekday == 0:
                    send = True
                elif bank.send_frequency == 'biweekly' and weekday == 0:
                    # Send on even ISO weeks
                    if today.isocalendar()[1] % 2 == 0:
                        send = True
                elif bank.send_frequency == 'monthly' and day == 1:
                    send = True

                if send:
                    bank._send_report_to_recipients()
            except Exception:
                _logger.exception(
                    "Hour bank cron: failed to send report for %s", bank.name
                )

    # ------------------------------------------------------------------
    # Threshold notifications
    # ------------------------------------------------------------------

    @api.depends('threshold_event_ids')
    def _compute_threshold_event_count(self):
        for rec in self:
            rec.threshold_event_count = len(rec.threshold_event_ids)

    def _get_alert_recipients(self):
        """Return partners to email for threshold alerts.

        Fallback: if alert_recipient_ids is empty, reuse recipient_ids
        (same audience as the periodic report). Drops partners without email.
        """
        self.ensure_one()
        partners = self.alert_recipient_ids or self.recipient_ids
        return partners.filtered(lambda p: p.email)

    def _last_posted_invoice_id(self):
        """Return id (int) of the most recently posted invoice that would
        be counted by this bank's filters, or 0 if none.

        Used as the period_key for the 'unbilled' mode: when a new invoice
        is posted, the key changes and thresholds re-arm.
        """
        self.ensure_one()
        partner_id = self.partner_id.commercial_partner_id.id or self.partner_id.id
        domain = [
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
        ]
        if self.invoice_partner_ids:
            domain.append(('partner_id', 'in', self.invoice_partner_ids.ids))
        else:
            domain.append(('commercial_partner_id', '=', partner_id))
        if self.filter_company_ids and self.company_filter_mode == 'include':
            domain.append(('company_id', 'in', self.filter_company_ids.ids))
        elif self.filter_company_ids and self.company_filter_mode == 'exclude':
            domain.append(('company_id', 'not in', self.filter_company_ids.ids))
        inv = self.env['account.move'].sudo().search(
            domain, order='invoice_date desc, id desc', limit=1,
        )
        # Also include extra_invoice_ids — if the most recent extra invoice
        # is newer than the domain match, use that one.
        if self.extra_invoice_ids:
            latest_extra = self.extra_invoice_ids.sorted(
                lambda m: (m.invoice_date or fields.Date.from_string('1900-01-01'), m.id),
                reverse=True,
            )[:1]
            if latest_extra and (
                not inv
                or (latest_extra.invoice_date or fields.Date.from_string('1900-01-01'))
                > (inv.invoice_date or fields.Date.from_string('1900-01-01'))
            ):
                inv = latest_extra
        return inv.id if inv else 0

    def _current_period_key(self):
        """Return the period_key string for the current mode.

        - unbilled    -> str(id of most-recent posted invoice), '0' if none
        - budget_pct  -> str(threshold_budget_hours)  (changes when budget changes)
        - balance_floor -> '' (handled via crossing logic in _check_thresholds)
        - disabled    -> ''
        """
        self.ensure_one()
        if self.threshold_mode == 'unbilled':
            return str(self._last_posted_invoice_id())
        if self.threshold_mode == 'budget_pct':
            return '%.4f' % (self.threshold_budget_hours or 0.0)
        return ''

    def _compute_threshold_measurements(self, report_data=None):
        """Return a dict {current_unbilled, current_balance, current_pct,
        current_period_key, last_invoice_date}.

        Computes once per call by reusing _get_report_data() output if provided.
        """
        self.ensure_one()
        data = report_data if report_data is not None else self._get_report_data()
        balance = data['balance']

        # current_unbilled: sum of debit hours since the last credit entry.
        # Entries are reversed (most recent first), so iterate until we hit a credit.
        unbilled = 0.0
        last_invoice_date = None
        for entry in data['entries']:
            if entry['entry_type'] == 'credit':
                last_invoice_date = entry['date']
                break
            # debit hours are negative; "unbilled hours consumed" is the absolute value
            unbilled += -entry['hours']

        budget = self.threshold_budget_hours or 0.0
        pct = (unbilled / budget * 100.0) if budget > 0 else 0.0

        return {
            'current_unbilled': unbilled,
            'current_balance': balance,
            'current_pct': pct,
            'current_period_key': self._current_period_key(),
            'last_invoice_date': last_invoice_date,
        }

    def _check_thresholds(self):
        """Evaluate every active threshold line on self and fire alerts as needed.

        Returns the number of alerts fired (useful for tests and UI feedback).
        """
        self.ensure_one()
        if self.threshold_mode == 'disabled' or not self.threshold_line_ids:
            return 0
        if not self.active:
            return 0

        data = self._get_report_data()
        m = self._compute_threshold_measurements(report_data=data)
        fired = 0

        for line in self.threshold_line_ids.filtered('active'):
            try:
                should_fire, new_period_key, measured = self._evaluate_threshold(line, m)
            except Exception:
                _logger.exception(
                    "Hour bank %s: threshold evaluation failed for line %s",
                    self.name, line.value,
                )
                continue
            if should_fire:
                try:
                    self._fire_threshold_alert(line, m, measured, new_period_key, report_data=data)
                    fired += 1
                except Exception:
                    _logger.exception(
                        "Hour bank %s: failed to send threshold alert for line %s",
                        self.name, line.value,
                    )
            elif new_period_key is not None and new_period_key != (line.last_fired_period_key or ''):
                # Silent re-arm: update period_key without firing
                # (used by balance_floor when balance rises back above hysteresis)
                line.write({'last_fired_period_key': new_period_key})
        return fired

    def _evaluate_threshold(self, line, measurement):
        """Decide whether `line` should fire given current `measurement`.

        Returns tuple (should_fire: bool, new_period_key: str|None, measured: float).
        new_period_key is the value to persist on line.last_fired_period_key:
        - on fire: the new key (consumed)
        - on silent re-arm (balance_floor): the new "armed" key
        - None: no change to persist
        """
        self.ensure_one()
        mode = self.threshold_mode

        if mode == 'unbilled':
            measured = measurement['current_unbilled']
            current_key = measurement['current_period_key']
            rearmed = (line.last_fired_period_key or '') != current_key
            if rearmed and measured >= line.value:
                return True, current_key, measured
            return False, None, measured

        if mode == 'budget_pct':
            measured = measurement['current_pct']
            current_key = measurement['current_period_key']
            rearmed = (line.last_fired_period_key or '') != current_key
            if rearmed and measured >= line.value:
                return True, current_key, measured
            return False, None, measured

        if mode == 'balance_floor':
            measured = measurement['current_balance']
            # Hysteresis: re-arm silently when balance rises above value + 0.5h
            hysteresis = 0.5
            currently_armed = not (line.last_fired_period_key or '')
            if not currently_armed and measured > (line.value + hysteresis):
                # Silent re-arm
                return False, '', measured
            if currently_armed and measured <= line.value:
                # Fire and disarm
                new_key = fields.Datetime.to_string(fields.Datetime.now())
                return True, new_key, measured
            return False, None, measured

        return False, None, 0.0

    def _fire_threshold_alert(self, line, measurement, measured, period_key, report_data=None):
        """Send the alert email + record the event + post on chatter."""
        self.ensure_one()
        partners = self._get_alert_recipients()

        if not partners:
            _logger.info(
                "Hour bank %s: threshold %s reached but no recipients with email",
                self.name, line.name,
            )
            # Still record the event and update line so we don't loop
            line.write({
                'last_fired_date': fields.Datetime.now(),
                'last_fired_period_key': period_key,
            })
            self.env['hour.bank.threshold.event'].create({
                'bank_id': self.id,
                'threshold_line_id': line.id,
                'mode_snapshot': self.threshold_mode,
                'value_snapshot': line.value,
                'measured_value': measured,
                'period_key': period_key,
                'recipient_ids': [(6, 0, [])],
            })
            return

        date_str = fields.Date.today().isoformat()
        sender = self.company_id.email or self.env.user.email_formatted
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        bank_url = "%s/odoo/action-base.action_client/?model=hour.bank.client&view_type=form&id=%d" % (
            base_url.rstrip('/'), self.id,
        ) if base_url else ''

        # Un courriel par langue de lecteur : objet, corps, palier et
        # classeur dans la langue de chacun. Les heures suivent la
        # langue (`formatLang`).
        xlsx_att = self.env['ir.attachment']
        for lang, groupe in self._bf_by_lang(partners).items():
            bank = self.with_context(lang=lang)
            att = self.env['ir.attachment'].create({
                'name': "%s.xlsx" % bank._bf_file_stem(date_str),
                'type': 'binary',
                'datas': base64.b64encode(bank._generate_xlsx_binary()),
                'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'res_model': self._name,
                'res_id': self.id,
            })
            xlsx_att |= att
            libelle = line.with_context(lang=lang)._bf_label()
            body_html = bank._bf_alert_body(
                libelle, bank._bf_measure_label(measured, measurement),
                measurement['current_balance'], bank_url,
            )
            subject = bank.env._(
                "Hour bank %(client)s — threshold \"%(threshold)s\" reached",
                client=self.partner_id.name, threshold=libelle,
            )
            for partner in groupe:
                mail = self.env['mail.mail'].sudo().create({
                    'subject': subject,
                    'body_html': body_html,
                    'email_from': sender,
                    # recipient_ids ONLY — see note in _send_report_to_recipients:
                    # setting email_to to the same partner duplicates the SMTP send.
                    'recipient_ids': [(4, partner.id)],
                    'attachment_ids': [(6, 0, [att.id])],
                })
                mail.send()

        # La trace du fil et l'avis aux abonnés internes : langue de la maison.
        measure_label = self._bf_measure_label(measured, measurement)
        subject = self.env._(
            "Hour bank %(client)s — threshold \"%(threshold)s\" reached",
            client=self.partner_id.name, threshold=line._bf_label(),
        )

        # Update the line state (consume the period)
        line.write({
            'last_fired_date': fields.Datetime.now(),
            'last_fired_period_key': period_key,
        })

        # Chatter note (audit) — captures the mail.message id for the event
        recipient_names = ', '.join(partners.mapped('name'))
        message = self.message_post(
            body=_("Threshold alert \"%(label)s\" sent to %(names)s — "
                   "%(measure)s") % {
                'label': line._bf_label(),
                'names': recipient_names,
                'measure': measure_label,
            },
            message_type='comment',
            subtype_xmlid='mail.mt_note',
            attachment_ids=xlsx_att.ids,
        )

        # Optional message_notify to internal followers
        if self.notify_internal_followers:
            internal_partners = self.message_partner_ids.filtered(
                lambda p: p.user_ids and not p.user_ids.share
            ) - partners
            if internal_partners:
                try:
                    self.message_notify(
                        partner_ids=internal_partners.ids,
                        subject=subject,
                        body=_(
                            "Threshold alert \"%(label)s\" on %(bank)s. "
                            "Measure: %(measure)s."
                        ) % {
                            'label': line._bf_label(),
                            'bank': self.name,
                            'measure': measure_label,
                        },
                    )
                except Exception:
                    _logger.exception(
                        "Hour bank %s: message_notify to internal followers failed",
                        self.name,
                    )

        # Append-only event record
        self.env['hour.bank.threshold.event'].create({
            'bank_id': self.id,
            'threshold_line_id': line.id,
            'mode_snapshot': self.threshold_mode,
            'value_snapshot': line.value,
            'measured_value': measured,
            'period_key': period_key,
            'recipient_ids': [(6, 0, partners.ids)],
            'attachment_id': xlsx_att[:1].id,
            'mail_message_id': message.id if message else False,
        })
        _logger.info(
            "Hour bank %s: threshold %s fired (measured=%.2f, recipients=%s)",
            self.name, line.name, measured, recipient_names,
        )

    def _bf_measure_label(self, measured, measurement):
        """La mesure du palier, en clair, dans la langue du contexte."""
        self.ensure_one()
        env = self.env

        def _h(valeur):
            return "%s h" % formatLang(env, valeur)

        if self.threshold_mode == 'budget_pct':
            return env._(
                "%(pct)s %% of the budget used (%(used)s of %(budget)s)",
                pct=formatLang(env, measured, digits=1),
                used=_h(measurement['current_unbilled']),
                budget=_h(self.threshold_budget_hours),
            )
        if self.threshold_mode == 'unbilled':
            return env._("%(hours)s unbilled", hours=_h(measured))
        return env._("Current balance: %(hours)s", hours=_h(measured))

    def _bf_alert_body(self, threshold_label, measure_label, balance, bank_url):
        """Corps du courriel d'alerte, dans la langue du contexte."""
        self.ensure_one()
        env = self.env
        p = Markup('<p style="font-size:16px;line-height:26px;color:#374151;margin:%s;">%s</p>')
        ligne = Markup('<tr><td style="color:#6B7280;">%s</td><td><strong>%s</strong></td></tr>')
        lien = Markup('')
        if bank_url:
            lien = Markup('<p style="font-size:14px;line-height:22px;margin:0 0 20px 0;">'
                          '<a href="%s" style="color:%s;">%s</a></p>') % (
                bank_url, self.company_id.report_brand_primary or '#714B67',
                env._("Open the bank in Odoo"))
        inner = Markup('').join([
            p % ('0 0 16px 0', env._("Hello,")),
            p % ('0 0 16px 0', Markup(env._(
                "Your hour bank for %(client)s has just reached the threshold "
                "%(threshold)s.")) % {
                    'client': Markup('<strong>%s</strong>') % (self.partner_id.name or ''),
                    'threshold': Markup('<strong>%s</strong>') % env._(
                        "\"%(label)s\"", label=threshold_label),
                }),
            Markup('<table cellpadding="6" cellspacing="0" border="0" '
                   'style="border-collapse:collapse;margin:0 0 20px 0;'
                   'font-size:14px;color:#374151;">%s%s</table>') % (
                ligne % (env._("Current measure:"), measure_label),
                ligne % (env._("Current balance:"),
                         "%s h" % formatLang(env, balance)),
            ),
            p % ('0 0 20px 0', env._("The details of the entries are attached in Excel format.")),
            lien,
            p % ('0', Markup('%s<br/>%s') % (env._("Kind regards,"), self.company_id.name or '')),
        ])
        return self.env['hour.bank.send.wizard'].with_context(lang=env.lang)._wrap_branded_body(inner)

    def action_check_thresholds_now(self):
        """Manual button: evaluate thresholds and show how many fired."""
        self.ensure_one()
        fired = self._check_thresholds()
        if fired:
            msg = _("%(n)d threshold alert(s) sent.", n=fired)
        else:
            msg = _("No threshold fired. (All already notified, or below "
                    "the thresholds.)")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Threshold check"),
                'message': msg,
                'sticky': False,
                'type': 'success' if fired else 'info',
            },
        }

    def action_view_threshold_events(self):
        """Smart button: open event history for this bank."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Alerts sent"),
            'res_model': 'hour.bank.threshold.event',
            'view_mode': 'list,form',
            'domain': [('bank_id', '=', self.id)],
            'context': {'default_bank_id': self.id},
        }

    @api.model
    def _cron_check_thresholds(self):
        """Cron: evaluate thresholds for all active banks with mode != disabled."""
        if not self.env.lang:
            self = self.with_context(lang=get_lang(self.env).code)
        banks = self.search([
            ('active', '=', True),
            ('threshold_mode', '!=', 'disabled'),
        ])
        for bank in banks:
            try:
                bank._check_thresholds()
            except Exception:
                _logger.exception(
                    "Hour bank cron: threshold check failed for %s", bank.name,
                )
