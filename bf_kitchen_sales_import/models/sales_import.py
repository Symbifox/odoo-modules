import base64
import csv
import hashlib
import io
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime

from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

from odoo.addons.bf_kitchen.models.kitchen_consumption import SALE_TYPES

MANAGER = "bf_kitchen.group_kitchen_manager"

# Column names of the registers' exports, normalised (lower case, no accents, letters and digits).
COLUMNS = {
    "date": {"date", "day", "jour", "businessdate", "saledate", "salesdate", "datedevente", "datevente",
             "orderdate", "datedecommande", "transactiondate", "datetime"},
    "code": {"code", "sku", "itemcode", "plu", "codearticle", "articlecode", "itemid", "productcode",
             "codeproduit", "reference"},
    "name": {"name", "item", "itemname", "product", "productname", "article", "description", "nom",
             "produit", "menuitem", "nomarticle", "nomdelarticle", "nomduproduit", "nomdeproduit", "libelle",
             "plat", "nomduplat", "itemdescription", "articlename"},
    "qty": {"qty", "quantity", "quantite", "qte", "itemssold", "sold", "count", "nombre", "vendus",
            "quantitevendue", "qtysold", "unitssold", "nbvendus"},
}


def _norm(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def _key(code, name):
    """How an item of a register is recognised: its code, else its name, ignoring case and spaces."""
    code = (code or "").strip()
    return ("c:" + code.lower()) if code else ("n:" + " ".join((name or "").lower().split()))


class KitchenSalesSource(models.Model):
    _name = "bf.kitchen.sales.source"
    _description = "Register that is not Odoo's"
    _order = "name"
    _check_company_auto = True

    name = fields.Char(required=True, help="The register, as the kitchen calls it: « Square, dining room ».")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    warehouse_id = fields.Many2one(
        "stock.warehouse", "Warehouse", required=True, check_company=True,
        default=lambda self: self.env.company._bf_kitchen_warehouse(),
        help="The stock its sales take from.")
    date_order = fields.Selection(
        [("dmy", "Day / month / year"), ("mdy", "Month / day / year")], "Dates written", default="dmy",
        required=True,
        help="How the export writes a date like 03/04/2026. A date written 2026-04-03 is read in any case.")
    mapping_ids = fields.One2many("bf.kitchen.sales.mapping", "source_id", "Items")
    last_import_date = fields.Datetime("Last import", readonly=True, copy=False)
    import_ids = fields.One2many("bf.kitchen.sales.import", "source_id", "Imports")
    active = fields.Boolean(default=True)


class KitchenSalesMapping(models.Model):
    _name = "bf.kitchen.sales.mapping"
    _description = "Item of another register, matched to a dish"
    _order = "source_id, name, code"
    _check_company_auto = True

    source_id = fields.Many2one("bf.kitchen.sales.source", "Register", required=True, ondelete="cascade",
                                index=True)
    company_id = fields.Many2one(related="source_id.company_id", store=True)
    code = fields.Char("Code at the register")
    name = fields.Char("Name at the register")
    key = fields.Char(compute="_compute_key", store=True, index=True)
    product_id = fields.Many2one(
        "product.product", "Dish", check_company=True,
        domain="[('bf_kitchen_type', 'in', ('dish', 'preparation', 'ingredient'))]",
        help="What this item takes out of the stock: a dish (its ingredients), or an item sold as it is.")
    ignored = fields.Boolean("Not food", help="A gift card, a delivery fee: nothing leaves the stock.")

    _sql_constraints = [("one_per_item", "UNIQUE(source_id, key)", "This item of the register is already matched.")]

    @api.depends("code", "name")
    def _compute_key(self):
        for mapping in self:
            mapping.key = _key(mapping.code, mapping.name)

    @api.constrains("product_id", "ignored")
    def _check_product(self):
        for mapping in self:
            if mapping.product_id and mapping.product_id.product_tmpl_id.bf_kitchen_type not in SALE_TYPES:
                raise ValidationError(_("An item of the register is matched to a kitchen dish or item."))
            if mapping.product_id and mapping.ignored:
                raise ValidationError(_("An item is either matched to a dish or not food, not both."))


class KitchenSalesImport(models.Model):
    """One export file of another register: read, matched, then taken out of the stock, one
    Kitchen consumption transfer per day."""
    _name = "bf.kitchen.sales.import"
    _description = "Sales imported from another register"
    _inherit = ["mail.thread"]
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char("Reference", readonly=True, copy=False, default=lambda self: _("New"))
    source_id = fields.Many2one("bf.kitchen.sales.source", "Register", required=True, check_company=True,
                                tracking=True)
    company_id = fields.Many2one(related="source_id.company_id", store=True)
    file = fields.Binary("Export file", attachment=True, copy=False)
    filename = fields.Char("File name")
    checksum = fields.Char(readonly=True, copy=False, index=True)
    state = fields.Selection([("draft", "To read"), ("read", "To import"), ("done", "Imported"),
                              ("cancel", "Cancelled")], default="draft", required=True, readonly=True,
                             copy=False, tracking=True)
    line_ids = fields.One2many("bf.kitchen.sales.import.line", "import_id", "Sales")
    picking_ids = fields.One2many("stock.picking", "bf_kitchen_import_id", "Kitchen consumptions", readonly=True)
    date_from = fields.Date("First day", compute="_compute_summary", store=True)
    date_to = fields.Date("Last day", compute="_compute_summary", store=True)
    unmatched_count = fields.Integer("Items to match", compute="_compute_summary", store=True)
    picking_count = fields.Integer(compute="_compute_picking_count")

    @api.depends("line_ids.date", "line_ids.status")
    def _compute_summary(self):
        for imp in self:
            dates = imp.line_ids.mapped("date")
            imp.date_from = min(dates) if dates else False
            imp.date_to = max(dates) if dates else False
            imp.unmatched_count = len(imp.line_ids.filtered(lambda l: l.status == "unmatched"))

    def _compute_picking_count(self):
        for imp in self:
            imp.picking_count = len(imp.sudo().picking_ids)

    def _check_manager(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Importing sales is the kitchen manager's."))

    def write(self, vals):
        if not self.env.su and {"state", "checksum"} & set(vals):
            raise AccessError(_("An import moves through its own buttons."))
        if "file" in vals and any(imp.state != "draft" for imp in self) and not self.env.su:
            raise UserError(_("The file of an import already read is not replaced: make a new import."))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            # A state or a checksum given by RPC, or as a default of the context, would skip the reading.
            self = self.with_context({k: v for k, v in self.env.context.items()
                                      if k not in ("default_state", "default_checksum", "default_name")})
            for vals in vals_list:
                for field in ("state", "checksum", "line_ids", "name"):
                    vals.pop(field, None)
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].sudo().next_by_code("bf.kitchen.sales.import") or _("New")
        return super().create(vals_list)

    def unlink(self):
        if any(imp.state == "done" for imp in self):
            raise UserError(_("An imported file took food out of the stock: cancel it first."))
        return super().unlink()

    # ── reading ───────────────────────────────────────────────────────────────────────

    def action_read(self):
        self.ensure_one()
        self._check_manager()
        if self.state != "draft":
            raise UserError(_("This import is already read."))
        if not self.file:
            raise UserError(_("Add the export file of the register first."))
        content = base64.b64decode(self.file)
        rows = self._bf_rows(content, self.filename or "")
        sales = self._bf_parse(rows)
        Line = self.env["bf.kitchen.sales.import.line"].sudo()
        self.sudo().line_ids.unlink()
        Line.create([dict(sale, import_id=self.id) for sale in sales])
        self.sudo().write({"state": "read", "checksum": hashlib.sha256(content).hexdigest()})
        return True

    @api.model
    def _bf_rows(self, content, filename):
        """The rows of an export, CSV (any of , ; tab, UTF-8 or Windows) or Excel (first sheet)."""
        if content[:4] == b"PK\x03\x04" or filename.lower().endswith((".xlsx", ".xlsm")):
            try:
                import openpyxl
            except ImportError as error:  # pragma: no cover
                raise UserError(_("Reading Excel files needs openpyxl on the server.")) from error
            try:
                book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            except Exception as error:
                raise UserError(_("This file cannot be read as an Excel workbook.")) from error
            sheet = book.worksheets[0]
            return [list(row) for row in sheet.iter_rows(values_only=True)]
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","
        return list(csv.reader(io.StringIO(text), delimiter=delimiter))

    def _bf_parse(self, rows):
        """[{date, code, name, qty}] from the rows: the header is the first row with a date, a
        quantity and a code or a name column."""
        header, start = None, 0
        for index, row in enumerate(rows[:20]):
            names = [_norm(cell) for cell in row]
            found = {}
            for col, key in enumerate(names):
                for field, synonyms in COLUMNS.items():
                    if key in synonyms and field not in found:
                        found[field] = col
            if "date" in found and "qty" in found and ("code" in found or "name" in found):
                header, start = found, index + 1
                break
        if not header:
            raise UserError(_("No date, quantity and item columns were found in the first rows of the file. "
                              "Expected columns: Date, Item (or Code), Quantity."))
        sales = []
        for number, row in enumerate(rows[start:], start=start + 1):
            if not any(cell not in (None, "") for cell in row):
                continue
            cell = lambda field: row[header[field]] if field in header and header[field] < len(row) else None
            code = str(cell("code") or "").strip()
            if code.endswith(".0") and code[:-2].isdigit():
                code = code[:-2]  # a numeric code read from Excel
            name = str(cell("name") or "").strip()
            if not code and not name:
                continue
            day = self._bf_date(cell("date"), number)
            qty = self._bf_qty(cell("qty"), number)
            sales.append({"date": day, "code": code, "name": name, "qty": qty, "row": number})
        if not sales:
            raise UserError(_("The file has no sales."))
        return sales

    def _bf_date(self, value, number):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or "").strip()
        match = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
        if match:
            year, month, day = map(int, match.groups())
        else:
            match = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", text)
            if not match:
                raise UserError(_("Row %(row)s: « %(value)s » is not a date.", row=number, value=text))
            first, second, year = map(int, match.groups())
            year += 2000 if year < 100 else 0
            day, month = (first, second) if self.source_id.date_order == "dmy" else (second, first)
        try:
            return date(year, month, day)
        except ValueError as error:
            raise UserError(_("Row %(row)s: « %(value)s » is not a date.", row=number, value=text)) from error

    def _bf_qty(self, value, number):
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip().replace(" ", "").replace(" ", "")
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
        try:
            return float(text)
        except ValueError as error:
            raise UserError(_("Row %(row)s: « %(value)s » is not a quantity.", row=number, value=value)) from error

    # ── importing ─────────────────────────────────────────────────────────────────────

    def _bf_imported_days(self):
        """Days of these sales already imported for the same register."""
        self.ensure_one()
        done = self.search([("source_id", "=", self.source_id.id), ("state", "=", "done"), ("id", "!=", self.id)])
        return sorted(set(self.line_ids.mapped("date")) & set(done.line_ids.mapped("date")))

    def action_import(self):
        self.ensure_one()
        self._check_manager()
        if self.state != "read":
            raise UserError(_("Read the file before importing it."))
        # Two imports of the same register at the same moment would both find the days free. A lock
        # alone is not enough under REPEATABLE READ (the second one would read the days in its older
        # snapshot): writing the register makes the second transaction fail to serialize, and Odoo
        # replays the request with a fresh snapshot.
        self.source_id.sudo().write({"last_import_date": fields.Datetime.now()})
        twin = self.search([("source_id", "=", self.source_id.id), ("state", "=", "done"),
                            ("checksum", "=", self.checksum), ("id", "!=", self.id)], limit=1)
        if twin:
            raise UserError(_("This very file was already imported for this register (%s).", twin.name))
        days = self._bf_imported_days()
        if days:
            raise UserError(_("These days are already imported for this register: %s. Cancel that import "
                              "first, or remove these days from the file.",
                              ", ".join(fields.Date.to_string(d) for d in days)))
        self.line_ids._bf_match()
        unmatched = self.line_ids.filtered(lambda l: l.status == "unmatched")
        if unmatched:
            raise UserError(_("%s item(s) of the register are not matched to a dish yet (or marked not "
                              "food).", len(set(unmatched.mapped("key")))))
        by_day = defaultdict(lambda: defaultdict(float))
        for line in self.line_ids.filtered(lambda l: l.status == "matched"):
            by_day[line.date][line.product_id] += line.qty
        Picking = self.env["stock.picking"].sudo()
        for day in sorted(by_day):
            lines = [(product, qty, product.uom_id) for product, qty in by_day[day].items()
                     if float_compare(qty, 0.0, precision_rounding=product.uom_id.rounding) > 0]
            picking = Picking._bf_kitchen_consume(
                self.source_id.warehouse_id, lines, "import",
                "%s %s %s" % (self.source_id.name, self.name, fields.Date.to_string(day)), event_date=day)
            if picking:
                picking.bf_kitchen_import_id = self.id
        self.sudo().state = "done"
        self._message_log(body=_("%(count)s day(s) of sales taken out of the kitchen stock.",
                                 count=len(by_day)))
        return True

    def action_cancel(self):
        """Put the food back and free the days, to import a corrected file."""
        self.ensure_one()
        self._check_manager()
        if self.state not in ("read", "done"):
            raise UserError(_("Only a read or imported file is cancelled."))
        returns = self.sudo().picking_ids.filtered(lambda p: p.location_dest_id.usage == "customer")._bf_kitchen_return()
        for ret in returns:
            ret.bf_kitchen_import_id = self.id
        self.sudo().state = "cancel"
        if returns:
            self._message_log(body=_("Food put back in the kitchen stock: %s.",
                                     Markup(", ").join(r._get_html_link() for r in returns)))
        return True

    def action_view_pickings(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Kitchen consumptions"), "res_model": "stock.picking",
                "view_mode": "list,form", "domain": [("bf_kitchen_import_id", "=", self.id)],
                "context": {"create": False}}


class KitchenSalesImportLine(models.Model):
    _name = "bf.kitchen.sales.import.line"
    _description = "Sale imported from another register"
    _order = "date, row, id"

    import_id = fields.Many2one("bf.kitchen.sales.import", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="import_id.company_id", store=True)
    source_id = fields.Many2one(related="import_id.source_id", store=True)
    row = fields.Integer("Row", readonly=True)
    date = fields.Date(required=True, readonly=True)
    code = fields.Char("Code", readonly=True)
    name = fields.Char("Item", readonly=True)
    qty = fields.Float("Quantity", digits="Product Unit of Measure", readonly=True)
    key = fields.Char(compute="_compute_key", store=True)
    mapping_id = fields.Many2one("bf.kitchen.sales.mapping", compute="_compute_mapping", store=True)
    product_id = fields.Many2one(
        "product.product", "Dish", compute="_compute_mapping", inverse="_inverse_match", store=True,
        readonly=False, domain="[('bf_kitchen_type', 'in', ('dish', 'preparation', 'ingredient'))]")
    ignored = fields.Boolean("Not food", compute="_compute_mapping", inverse="_inverse_match", store=True,
                             readonly=False)
    status = fields.Selection([("matched", "Matched"), ("ignored", "Not food"), ("unmatched", "To match")],
                              compute="_compute_mapping", store=True)

    @api.depends("code", "name")
    def _compute_key(self):
        for line in self:
            line.key = _key(line.code, line.name)

    @api.depends("key", "source_id.mapping_ids.product_id", "source_id.mapping_ids.ignored",
                 "source_id.mapping_ids.key")
    def _compute_mapping(self):
        mappings = {}
        for mapping in self.source_id.mapping_ids:
            mappings[(mapping.source_id.id, mapping.key)] = mapping
        for line in self:
            mapping = mappings.get((line.source_id.id, line.key), self.env["bf.kitchen.sales.mapping"])
            line.mapping_id = mapping
            line.product_id = mapping.product_id
            line.ignored = mapping.ignored
            line.status = "matched" if mapping.product_id else "ignored" if mapping.ignored else "unmatched"

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("The sales of an import come from its file."))
        return super().create(vals_list)

    def write(self, vals):
        # A cook or a manager matches an item; the sale itself comes from the file.
        if not self.env.su and set(vals) - {"product_id", "ignored"}:
            raise AccessError(_("The sales of an import come from its file."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise AccessError(_("The sales of an import come from its file."))
        return super().unlink()

    def _bf_match(self):
        """Match again from the register's items (an item matched since the file was read)."""
        for fname in ("mapping_id", "product_id", "ignored", "status"):
            self.env.add_to_compute(self._fields[fname], self)
        self.flush_recordset()

    def _inverse_match(self):
        """Matching a line matches the item of the register, for this file and the next ones."""
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Matching the register's items is the kitchen manager's."))
        Mapping = self.env["bf.kitchen.sales.mapping"]
        for line in self:
            if line.import_id.state not in ("draft", "read"):
                raise UserError(_("The items of an imported file are not matched again."))
            mapping = Mapping.search([("source_id", "=", line.source_id.id), ("key", "=", line.key)], limit=1)
            vals = {"product_id": line.product_id.id if not line.ignored else False, "ignored": line.ignored}
            if mapping:
                mapping.write(vals)
            else:
                Mapping.create(dict(vals, source_id=line.source_id.id, code=line.code or False,
                                    name=line.name or False))
        self.search([("import_id", "in", self.import_id.ids)])._bf_match()
