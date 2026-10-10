"""Selling at the stop: the truck's stock, the invoice, the deposits and the payment.

⚠️ A mark from the phone may arrive hours after it was made (no network in the
basement). Once the stop is marked on the phone, the server must not lose it for a
business reason: no stock in the truck, a payment that does not match the invoice,
a refund with money handed over. Those are recorded and flagged for the office, never
refused. Only malformed data (unknown product, absurd quantity) is refused.

⚠️ The worker has no right on stock nor accounting. Every document is created with
sudo, in the company of the day, after bf_route checked the day is the worker's.

⚠️ No card data: the phone sends the amount, the method and the terminal's
authorization number, nothing else.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.addons.account.wizard.account_payment_register import \
    AccountPaymentRegister as BaseRegister
from odoo.addons.bf_route.models.bf_route_day import clean_env
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_round

PAY_MODES = [
    ("cash", "Cash"),
    ("cheque", "Cheque"),
    ("card", "Card (terminal)"),
    ("account", "On account"),
]
JOURNAL_FIELDS = {"cash": "bf_route_cash_journal_id", "cheque": "bf_route_cheque_journal_id",
                  "card": "bf_route_card_journal_id"}
MAX_QTY = 10_000
MAX_LINES = 50
# Below this, a payment and an invoice are equal.
CENT = 0.005
# Cash is rounded to 5 cents in Canada: a gap this small is written off to the cash
# journal's difference accounts, not reported (one alert per problem, not per sale).
CASH_ROUNDING = 0.025


def _quantity(env, value):
    """⚠️ env._ and not _: at module level, _ finds no language and stays in English."""
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        raise UserError(env._("A quantity must be a number."))
    if value != value or not 0 <= value <= MAX_QTY:
        raise UserError(env._("A quantity must be between 0 and %s.", MAX_QTY))
    return value


class ProductTemplate(models.Model):
    _inherit = "product.template"

    bf_route_sold = fields.Boolean(
        string="Sold on routes", help="Can be sold at a stop even if it is not in the customer's "
                                      "usual products.")
    bf_route_deposit_product_id = fields.Many2one(
        "product.product", string="Container deposit",
        help="Charged for each full container delivered, credited for each empty taken back.")
    bf_route_empty_product_id = fields.Many2one(
        "product.product", string="Empty container",
        help="Stocked product the empties become when they come back in the truck. Optional.")


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    bf_route_location_id = fields.Many2one(
        "stock.location", string="Truck stock", domain=[("usage", "=", "internal")],
        help="Where the stock of this vehicle is counted between loading and unloading.")

    def action_bf_route_create_location(self):
        for vehicle in self.filtered(lambda v: not v.bf_route_location_id):
            company = vehicle.company_id or self.env.company
            warehouse = self.env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
            if not warehouse:
                raise UserError(_("Create a warehouse first."))
            vehicle.bf_route_location_id = self.env["stock.location"].create({
                "name": vehicle.license_plate or vehicle.name,
                "location_id": warehouse.view_location_id.id,
                "usage": "internal",
                "company_id": company.id,
            })


class ResCompany(models.Model):
    _inherit = "res.company"

    bf_route_cash_journal_id = fields.Many2one("account.journal", string="Cash journal",
                                               domain=[("type", "=", "cash")])
    bf_route_cheque_journal_id = fields.Many2one("account.journal", string="Cheque journal",
                                                 domain=[("type", "in", ("bank", "cash"))])
    bf_route_card_journal_id = fields.Many2one("account.journal", string="Card journal",
                                               domain=[("type", "=", "bank")])

    def _bf_route_journal(self, mode):
        self.ensure_one()
        journal = self[JOURNAL_FIELDS[mode]]
        if not journal:
            journal = self.env["account.journal"].search(
                [("company_id", "=", self.id), ("type", "=", "cash" if mode == "cash" else "bank")],
                limit=1)
        return journal


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_route_cash_journal_id = fields.Many2one(related="company_id.bf_route_cash_journal_id",
                                               readonly=False)
    bf_route_cheque_journal_id = fields.Many2one(related="company_id.bf_route_cheque_journal_id",
                                                 readonly=False)
    bf_route_card_journal_id = fields.Many2one(related="company_id.bf_route_card_journal_id",
                                               readonly=False)


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_route_deposit_ids = fields.One2many("bf.route.deposit", "partner_id",
                                           string="Containers")
    bf_route_container_count = fields.Float(string="Containers held",
                                            compute="_compute_bf_route_container_count",
                                            groups="bf_route.group_route_manager")

    @api.depends("bf_route_deposit_ids.quantity")
    def _compute_bf_route_container_count(self):
        groups = self.env["bf.route.deposit"].sudo()._read_group(
            [("partner_id", "in", self.ids)], ["partner_id"], ["quantity:sum"])
        held = {partner.id: total for partner, total in groups}
        for partner in self:
            partner.bf_route_container_count = held.get(partner.id, 0.0)


class BfRouteDeposit(models.Model):
    """The containers each customer holds: + when delivered full, - when taken back."""
    _name = "bf.route.deposit"
    _description = "Containers held by a customer"
    _order = "date desc, id desc"

    partner_id = fields.Many2one("res.partner", string="Customer", required=True, index=True,
                                 ondelete="cascade")
    product_id = fields.Many2one("product.product", string="Deposit", required=True)
    quantity = fields.Float(required=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    stop_id = fields.Many2one("bf.route.day.stop", string="Stop", ondelete="set null")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    note = fields.Char()


class BfRouteStopLine(models.Model):
    _name = "bf.route.stop.line"
    _description = "Usual product of a stop"
    _order = "stop_id, id"

    stop_id = fields.Many2one("bf.route.stop", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="stop_id.company_id", store=True)
    product_id = fields.Many2one("product.product", string="Product", required=True,
                                 domain=[("sale_ok", "=", True)])
    quantity = fields.Float(string="Usual quantity", default=1.0)


class BfRouteStop(models.Model):
    _inherit = "bf.route.stop"

    line_ids = fields.One2many("bf.route.stop.line", "stop_id", string="Usual products", copy=True)

    def _day_stop_vals(self):
        vals = super()._day_stop_vals()
        vals["line_ids"] = [(0, 0, {"product_id": line.product_id.id,
                                    "quantity_planned": line.quantity}) for line in self.line_ids]
        return vals


class BfRouteDayStopLine(models.Model):
    _name = "bf.route.day.stop.line"
    _description = "Product sold at a stop"
    _order = "stop_id, id"

    stop_id = fields.Many2one("bf.route.day.stop", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="stop_id.company_id", store=True)
    product_id = fields.Many2one("product.product", string="Product", required=True)
    quantity_planned = fields.Float(string="Usual")
    # A duplicated day starts with nothing delivered; a phone-added line stays one.
    quantity_delivered = fields.Float(string="Delivered", readonly=True, copy=False)
    empties_returned = fields.Float(string="Empties back", readonly=True, copy=False)
    price_unit = fields.Float(string="Unit price", readonly=True, digits="Product Price", copy=False)
    from_phone = fields.Boolean(
        readonly=True, copy=True,
        help="Added by a sale on the phone, not planned by the office: never a usual product, "
             "never carried to another day.")


class BfRouteDayStopLineEdits(models.Model):
    """A product line changed by the office edits its planned day (see bf_route)."""
    _inherit = "bf.route.day.stop.line"

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.stop_id._flag_edited(lines.stop_id.day_id)
        return lines

    def write(self, vals):
        result = super().write(vals)
        self.stop_id._flag_edited(self.stop_id.day_id)
        return result

    def unlink(self):
        stops = self.stop_id
        result = super().unlink()
        stops.exists()._flag_edited(stops.exists().day_id)
        return result


class BfRouteDayStop(models.Model):
    _inherit = "bf.route.day.stop"

    line_ids = fields.One2many("bf.route.day.stop.line", "stop_id", string="Products", copy=True)
    invoice_id = fields.Many2one("account.move", string="Invoice", readonly=True, copy=False)
    picking_ids = fields.Many2many("stock.picking", string="Transfers", readonly=True, copy=False)
    payment_id = fields.Many2one("account.payment", string="Payment", readonly=True, copy=False)
    payment_mode = fields.Selection(PAY_MODES, string="Paid by", readonly=True, copy=False)
    payment_amount = fields.Float(string="Amount received", readonly=True, copy=False)
    payment_reference = fields.Char(string="Authorization / cheque no.", readonly=True, copy=False)
    sale_error = fields.Text(string="Sale to finish", readonly=True, copy=False,
                             help="The sale was recorded on the phone but its documents could not be "
                                  "made. Fix the cause, then Retry the sale.")

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------
    def _postponed_copy_vals(self):
        vals = super()._postponed_copy_vals()
        vals["line_ids"] = [(0, 0, {"product_id": line.product_id.id,
                                    "quantity_planned": line.quantity_planned})
                            for line in self._bf_route_planned_lines()]
        return vals

    def _bf_route_planned_lines(self):
        """The products the office planned for this stop (not those a sale on the phone added)."""
        return self.line_ids.filtered(lambda l: not l.from_phone)

    def _merge_postponed_into(self, target):
        super()._merge_postponed_into(target)
        Line = self.env["bf.route.day.stop.line"].sudo().with_context(bf_route_auto=True)
        for line in self._bf_route_planned_lines():
            same = target.line_ids.filtered(lambda l: l.product_id == line.product_id and not l.from_phone)[:1]
            if same:
                same.quantity_planned += line.quantity_planned
            else:
                Line.create({"stop_id": target.id, "product_id": line.product_id.id,
                             "quantity_planned": line.quantity_planned})

    def _bf_route_pricelist(self):
        partner = self.partner_id.sudo().with_company(self.day_id.company_id)
        return partner.property_product_pricelist

    def _bf_route_currency(self):
        return self._bf_route_pricelist().currency_id or self.day_id.company_id.currency_id

    def _bf_route_price(self, product, quantity):
        """In the currency of the customer's price list (the invoice is made in it)."""
        pricelist = self._bf_route_pricelist()
        product = product.sudo().with_company(self.day_id.company_id)
        if pricelist:
            price = pricelist._get_product_price(product, quantity or 1.0, date=self.day_id.date)
        else:
            price = product.lst_price
        # As the invoice line will store it: the phone's estimate uses the same figure.
        return float_round(price, precision_digits=self.env["decimal.precision"].precision_get(
            "Product Price"))

    def _bf_route_unit_total(self, product, price):
        """Unit price with taxes, unrounded, for the estimate shown on the phone."""
        company = self.day_id.company_id
        partner = self.partner_id.sudo()
        taxes = product.sudo().taxes_id.filtered(lambda t: t.company_id == company)
        fpos = self.env["account.fiscal.position"].sudo().with_company(company)._get_fiscal_position(partner)
        taxes = fpos.map_tax(taxes)
        # Odoo rounds to the cent: computed on 1,000 units, the unit keeps 5 decimals.
        result = taxes.compute_all(price, self._bf_route_currency(), 1000.0, product=product,
                                   partner=partner)
        return round(result["total_included"] / 1000.0, 6)

    def _bf_route_price_breaks(self, product):
        """[[from quantity, unit price, unit price with taxes], ...] of the customer's price list
        for this product: the phone prices what is DELIVERED, as the invoice does, not what was
        planned. The thresholds are those of the list and of the lists it is based on."""
        quantities = {1.0}
        seen, todo = set(), [self._bf_route_pricelist()]
        while todo and len(seen) < 10:
            pricelist = todo.pop()
            if not pricelist or pricelist.id in seen:
                continue
            seen.add(pricelist.id)
            for item in pricelist.sudo().item_ids:
                if item.min_quantity > 1:
                    quantities.add(item.min_quantity)
                if item.base == "pricelist" and item.base_pricelist_id:
                    todo.append(item.base_pricelist_id)
        breaks = []
        for quantity in sorted(quantities):
            price = self._bf_route_price(product, quantity)
            breaks.append([quantity, price, self._bf_route_unit_total(product, price)])
        return breaks

    def _bf_route_tax_rates(self, product):
        """The percentages of the product's taxes, when the phone can redo the invoice's sum
        exactly (simple percentages added on top, rounded per line). Otherwise None, and the
        phone falls back on the unit price with taxes."""
        company = self.day_id.company_id
        if company.tax_calculation_rounding_method != "round_per_line":
            return None
        partner = self.partner_id.sudo()
        taxes = product.sudo().taxes_id.filtered(lambda t: t.company_id == company)
        fpos = self.env["account.fiscal.position"].sudo().with_company(company)._get_fiscal_position(partner)
        taxes = fpos.map_tax(taxes)
        # A group (the "GST+QST" of the Canadian chart) is its children, each rounded per line.
        flat = self.env["account.tax"]
        for tax in taxes:
            flat |= tax.children_tax_ids if tax.amount_type == "group" else tax
        if any(t.amount_type != "percent" or t.price_include or t.include_base_amount for t in flat):
            return None
        return [t.amount for t in flat]

    # ------------------------------------------------------------------
    # The phone
    # ------------------------------------------------------------------
    def _app_data(self, local):
        data = super()._app_data(local)
        if not self.line_ids and not self.invoice_id:
            data["sale"] = False
            return data
        lines = []
        for line in self.line_ids.sudo():
            product = line.product_id
            deposit = product.bf_route_deposit_product_id
            price = self._bf_route_price(product, line.quantity_planned)
            deposit_price = self._bf_route_price(deposit, 1.0) if deposit else 0.0
            lines.append({
                "product_id": product.id,
                "product": product.display_name,
                "planned": line.quantity_planned,
                "delivered": line.quantity_delivered,
                "empties": line.empties_returned,
                "price": price,
                "price_breaks": self._bf_route_price_breaks(product),
                "tax_rates": self._bf_route_tax_rates(product),
                "unit_total": self._bf_route_unit_total(product, price),
                "has_deposit": bool(deposit),
                "deposit_price": deposit_price,
                "deposit_tax_rates": self._bf_route_tax_rates(deposit) if deposit else [],
                "deposit_unit_total": self._bf_route_unit_total(deposit, deposit_price) if deposit else 0.0,
            })
        invoice = self.invoice_id.sudo()
        data["sale"] = {
            "lines": lines,
            "currency": self._bf_route_currency().symbol or "",
            "containers_held": self.partner_id.commercial_partner_id.sudo().bf_route_container_count,
            "invoice": invoice.name or False,
            "invoice_total": invoice.amount_total_signed if invoice else False,
            "payment_mode": self.payment_mode or False,
            "payment_amount": self.payment_amount or False,
            "to_finish": bool(self.sale_error),
        }
        return data

    def app_mark(self, state, note=None, position=None, client_time=None, app_key=None,
                 sale=None, **extra):
        self = clean_env(self)
        self.ensure_one()
        self.day_id._check_driver()
        clean = None
        if state == "done" and sale is not None and not self._is_resend(app_key):
            clean = self._bf_route_clean_sale(sale)
        result = super().app_mark(state, note=note, position=position, client_time=client_time,
                                  app_key=app_key, **extra)
        if clean:
            self._bf_route_sell(clean)
            result = self._app_result()
        return result

    def _bf_route_clean_sale(self, sale):
        """Check what the phone sent. Refuses malformed data only."""
        if not isinstance(sale, dict):
            raise UserError(_("The sale sent by the phone is unreadable."))
        items = sale.get("lines") or []
        if not isinstance(items, list) or len(items) > MAX_LINES:
            raise UserError(_("The sale sent by the phone is unreadable."))
        # Usual = planned by the office. A line a phone sale added earlier is not (it would
        # otherwise make any product usual once it has been sold here, then postponed).
        usual = {line.product_id.id: line for line in self._bf_route_planned_lines()}
        lines = []
        for item in items:
            if not isinstance(item, dict):
                raise UserError(_("The sale sent by the phone is unreadable."))
            try:
                product_id = int(item.get("product_id") or 0)
            except (TypeError, ValueError):
                product_id = 0
            product = self.env["product.product"].sudo().with_context(active_test=False).browse(
                product_id).exists()
            if not product:
                raise UserError(_("A product of the sale does not exist."))
            delivered = _quantity(self.env, item.get("delivered"))
            empties = _quantity(self.env, item.get("empties"))
            # Unusual: not in this stop's usual products (the office may have removed it while
            # the worker sold it), or archived, or not for sale. Kept, never invoiced by the
            # phone: the office confirms it (see _bf_route_sell).
            unusual = (product.id not in usual and not product.bf_route_sold) \
                or not product.active or not product.sale_ok
            if unusual and not (delivered or empties):
                continue
            lines.append({"product": product, "line": usual.get(product.id), "delivered": delivered,
                          "empties": empties, "unusual": unusual})
        payment = sale.get("payment") or {}
        if not isinstance(payment, dict):
            payment = {}
        mode = payment.get("mode") or "account"
        if mode not in dict(PAY_MODES):
            raise UserError(_("Unknown payment method."))
        try:
            amount = round(float(payment.get("amount") or 0.0), 2)
        except (TypeError, ValueError):
            raise UserError(_("The amount received must be a number."))
        if not 0 <= amount <= 1_000_000:
            raise UserError(_("The amount received must be between 0 and 1,000,000."))
        reference = str(payment.get("reference") or "").strip()[:64]
        return {"lines": lines, "mode": mode, "amount": 0.0 if mode == "account" else amount,
                "reference": reference}

    # ------------------------------------------------------------------
    # The sale: recorded first, documents after, never lost
    # ------------------------------------------------------------------
    def _bf_route_summary(self, clean):
        parts = ["%s × %s (%s)" % (item["delivered"], item["product"].display_name,
                                   _("empties back: %s", item["empties"]))
                 for item in clean["lines"]]
        parts.append("%s %s %s" % (dict(PAY_MODES)[clean["mode"]], clean["amount"],
                                   clean["reference"] or ""))
        return "; ".join(parts)

    def _bf_route_flag(self, lines):
        """Tell the office: a note on the day and one activity for the person responsible."""
        self.ensure_one()
        day = self.day_id.sudo()
        partner = self.partner_id.display_name
        day.message_post(body=Markup("<p><b>%s</b></p><ul>%s</ul>") % (
            partner, Markup("").join(Markup("<li>%s</li>") % line for line in lines)))
        day._alert(lambda e: e._("Sale to check: %s", partner), lambda e: " ".join(lines))

    def _bf_route_sell(self, clean):
        self.ensure_one()
        stop = self.sudo()
        # A stop already sold (invoiced, paid, or waiting for the office) is never sold over:
        # the first sale would be lost. The second one goes to the office.
        if stop._bf_route_sold():
            stop._bf_route_flag([
                _("A second sale arrived for a stop already sold (%s): it was not recorded.",
                  stop.invoice_id.name or dict(PAY_MODES).get(stop.payment_mode) or "-"),
                _("Recorded on the phone: %s", stop._bf_route_summary(clean))])
            return
        stop._bf_route_record(clean)
        unusual = [item["product"].display_name for item in clean["lines"] if item.get("unusual")]
        if unusual:
            # Never invoiced on the phone's word alone: the office confirms, then Retry the sale.
            message = _("Not in the usual products of this stop: %s. Confirm the sale, then use "
                        "Retry the sale.", ", ".join(unusual))
            stop.with_context(bf_route_auto=True).write({"sale_error": message})
            stop._bf_route_flag([message, _("Recorded on the phone: %s", stop._bf_route_summary(clean))])
            return
        try:
            with self.env.cr.savepoint():
                warnings = stop._bf_route_documents()
        except AccessError:
            raise  # A rights problem is a bug to see, never a "sale to finish".
        except (UserError, ValueError, KeyError, TypeError) as exc:
            # The mark and the quantities stay; the documents are for the office to finish.
            self.env.invalidate_all()
            stop = self.sudo()
            message = str(exc.args[0] if exc.args else exc)[:500]
            stop.with_context(bf_route_auto=True).write({"sale_error": message})
            stop._bf_route_flag([
                _("The documents of this sale could not be made: %s", message),
                _("Recorded on the phone: %s", stop._bf_route_summary(clean)),
                _("Fix the cause, then use Retry the sale on the stop.")])
            return
        if warnings:
            stop._bf_route_flag(warnings)

    def _bf_route_sold(self):
        """Something was sold at this stop already: a document, money, quantities, or a sale
        waiting for the office. A "Done" with nothing delivered and nothing paid is not a sale."""
        self.ensure_one()
        return bool(self.invoice_id or self.payment_id or self.sale_error or self.payment_amount
                    or self.line_ids.filtered(lambda l: l.quantity_delivered or l.empties_returned))

    @api.model
    def _refusal_details(self, extra, stop):
        sale = extra.get("sale")
        if not isinstance(sale, dict):
            return super()._refusal_details(extra, stop)
        # Only names the worker may see: products of this stop, or sold on the routes.
        known = stop.sudo().line_ids.product_id if stop else self.env["product.product"]
        parts = []
        for item in (sale.get("lines") or [])[:MAX_LINES]:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("product_id") or "0")
            product = self.env["product.product"].sudo().browse(int(raw) if raw.isdigit() else 0).exists()
            name = product.display_name if product and (product in known or product.bf_route_sold) else "?"
            parts.append("%s × %s, %s" % (str(item.get("delivered"))[:12], name,
                                          _("empties back: %s", str(item.get("empties"))[:12])))
        payment = sale.get("payment") if isinstance(sale.get("payment"), dict) else {}
        parts.append("%s %s %s" % (dict(PAY_MODES).get(payment.get("mode"), "-"),
                                   str(payment.get("amount"))[:16], str(payment.get("reference") or "")[:64]))
        return super()._refusal_details(extra, stop) + parts

    def _bf_route_record(self, clean):
        """What the phone sent, on the stop and its lines, before any document."""
        self.ensure_one()
        Line = self.env["bf.route.day.stop.line"].sudo().with_context(bf_route_auto=True)
        for item in clean["lines"]:
            vals = {"quantity_delivered": item["delivered"], "empties_returned": item["empties"],
                    "price_unit": self._bf_route_price(item["product"], item["delivered"])}
            if item["line"]:
                item["line"].sudo().with_context(bf_route_auto=True).write(vals)
            else:
                Line.create(dict(vals, stop_id=self.id, product_id=item["product"].id, from_phone=True))
        self.with_context(bf_route_auto=True).write({
            "payment_mode": clean["mode"],
            "payment_amount": clean["amount"],
            "payment_reference": clean["reference"] or False,
            "sale_error": False,
        })

    def _bf_route_documents(self):
        """Transfers, deposits, invoice and payment from what is recorded on the stop.
        Returns the warnings for the office. Raises when a document cannot be made."""
        self = clean_env(self)
        self.ensure_one()
        stop = self.sudo()
        day = stop.day_id
        company = day.company_id
        stop = stop.with_company(company)
        env = stop.env
        warnings = []
        lines = stop.line_ids.filtered(lambda l: l.quantity_delivered or l.empties_returned)
        customer = stop.partner_id.commercial_partner_id
        # 1. Stock: from the truck to the customer, empties back into the truck.
        warehouse = env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        pickings = env["stock.picking"]
        if warehouse:
            truck = day.vehicle_id.sudo().bf_route_location_id or warehouse.lot_stock_id
            customer_location = stop.partner_id.property_stock_customer
            out, back = {}, {}
            for line in lines:
                if line.quantity_delivered:
                    out[line.product_id] = out.get(line.product_id, 0.0) + line.quantity_delivered
                empty = line.product_id.bf_route_empty_product_id
                if empty and line.empties_returned:
                    back[empty] = back.get(empty, 0.0) + line.empties_returned
            # Products followed by lot or serial number travel apart: they wait for the office,
            # the others are delivered at once.
            moves = []
            for picking_type, source, destination, quantities in (
                    (warehouse.out_type_id, truck, customer_location, out),
                    (warehouse.in_type_id, customer_location, truck, back)):
                plain = {p: q for p, q in quantities.items() if p.tracking == "none"}
                tracked = {p: q for p, q in quantities.items() if p.tracking != "none"}
                moves += [(picking_type, source, destination, plain),
                          (picking_type, source, destination, tracked)]
            for picking_type, source, destination, quantities in moves:
                picking, warning = stop._bf_route_transfer(picking_type, source, destination, quantities)
                pickings |= picking
                if warning:
                    warnings.append(warning)
        # 2. Containers: + full delivered, - empties back, per deposit product.
        held_before = customer.bf_route_container_count
        net_total = 0.0
        for line in lines:
            deposit = line.product_id.bf_route_deposit_product_id
            net = line.quantity_delivered - line.empties_returned
            if deposit and net:
                env["bf.route.deposit"].sudo().create({
                    "partner_id": customer.id, "product_id": deposit.id, "quantity": net,
                    "date": day.date, "stop_id": stop.id, "company_id": company.id})
                net_total += net
        if held_before + net_total < 0:
            warnings.append(_("More empties taken back than the customer held (%(held)s before this "
                              "stop): check the count.", held=held_before))
        # 3. The invoice: products, deposits on the full containers, credit for the empties.
        invoice_lines = []
        for line in lines:
            if line.quantity_delivered:
                invoice_lines.append((line.product_id, line.quantity_delivered, line.price_unit))
            deposit = line.product_id.bf_route_deposit_product_id
            if deposit:
                price = stop._bf_route_price(deposit, 1.0)
                if line.quantity_delivered:
                    invoice_lines.append((deposit, line.quantity_delivered, price))
                if line.empties_returned:
                    invoice_lines.append((deposit, -line.empties_returned, price))
        invoice = env["account.move"]
        if invoice_lines:
            invoice = env["account.move"].sudo().create({
                "move_type": "out_invoice",
                "partner_id": stop.partner_id.id,
                "invoice_date": day.date,
                "invoice_origin": day.name,
                "company_id": company.id,
                "currency_id": stop._bf_route_currency().id,
                "invoice_line_ids": [(0, 0, {"product_id": product.id, "quantity": qty,
                                             "price_unit": price})
                                     for product, qty, price in invoice_lines],
            })
            currency = invoice.currency_id
            # ⚠️ The direction is decided on the total WITH taxes: deposits and products may
            # not carry the same taxes.
            if currency.compare_amounts(invoice.amount_total, 0) < 0:
                invoice.action_switch_move_type()
                warnings.append(_("Credit note %(amount)s left in draft: more empties than full "
                                  "containers. Check it, then confirm it.",
                                  amount=invoice.amount_total))
            elif currency.is_zero(invoice.amount_total):
                if any(qty > 0 and price for _p, qty, price in invoice_lines) or \
                        any(line.quantity_delivered for line in lines):
                    warnings.append(_("Goods delivered for an invoice of 0: check the prices."))
                invoice.unlink()
                invoice = env["account.move"]
            else:
                invoice.action_post()
        # 4. The payment, exactly as received.
        payment = env["account.payment"]
        mode, amount = stop.payment_mode, stop.payment_amount
        if mode and mode != "account" and amount:
            journal = company._bf_route_journal(mode)
            memo = " · ".join(p for p in (day.name, dict(PAY_MODES)[mode], stop.payment_reference) if p)
            if not journal:
                warnings.append(_("No journal for %(mode)s: the payment of %(amount)s was not "
                                  "recorded.", mode=dict(PAY_MODES)[mode], amount=amount))
            elif invoice and invoice.state == "posted" and invoice.move_type == "out_invoice":
                gap = invoice.amount_total - amount
                # Legal cash rounding: never reported. Written off when the payment makes a
                # journal entry; without one (Odoo 18, no outstanding account), the gap is
                # settled with the cash statement like any other.
                # Legal rounding only: the amount is the total rounded to the nearest 5 cents.
                legal = round(round(invoice.amount_total * 20) / 20, 2)
                rounding = (mode == "cash" and abs(gap) <= CASH_ROUNDING
                            and abs(amount - legal) < CENT
                            and invoice.currency_id == company.currency_id)
                method = journal.inbound_payment_method_line_ids[:1]
                writeoff = (rounding and not invoice.currency_id.is_zero(gap)
                            and method.payment_account_id
                            and journal.profit_account_id and journal.loss_account_id)
                wizard_vals = {"journal_id": journal.id, "amount": amount, "payment_date": day.date,
                               "communication": memo, "payment_difference_handling": "open",
                               "currency_id": invoice.currency_id.id}
                if method:
                    wizard_vals["payment_method_line_id"] = method.id
                if writeoff:
                    wizard_vals.update(
                        payment_difference_handling="reconcile",
                        writeoff_account_id=(journal.loss_account_id if gap > 0
                                             else journal.profit_account_id).id,
                        writeoff_label=_("Cash rounding"))
                wizard = env["account.payment.register"].sudo().with_context(
                    active_model="account.move", active_ids=invoice.ids).create(wizard_vals)
                # ⚠️ Odoo hands the payments back WITHOUT sudo (``sudo(flag=False)``), and
                # base_accounting_kit's override then rewrites them with the worker's rights,
                # who has none on accounting: AccessError. Its override only adds two bank
                # references, empty here, so Odoo's own step is called directly.
                payment = BaseRegister._create_payments(wizard).sudo()
                if not rounding and float_compare(amount, invoice.amount_total,
                                                  precision_rounding=CENT) != 0:
                    warnings.append(_("Received %(amount)s for an invoice of %(total)s.",
                                      amount=amount, total=invoice.amount_total))
                if invoice.currency_id != company.currency_id:
                    warnings.append(_("Paid in %s: hand it in apart from the cash of the day.",
                                      invoice.currency_id.name))
            else:
                payment = env["account.payment"].sudo().create({
                    "payment_type": "inbound", "partner_type": "customer",
                    "partner_id": customer.id, "amount": amount, "journal_id": journal.id,
                    "date": day.date, "memo": memo, "company_id": company.id,
                    "currency_id": stop._bf_route_currency().id})
                payment.action_post()
                warnings.append(_("%(amount)s received without an invoice to pay: left on the "
                                  "customer's account.", amount=amount))
        if mode == "card" and amount and not stop.payment_reference:
            warnings.append(_("Card payment without the terminal's authorization number."))
        stop.with_context(bf_route_auto=True).write({
            "invoice_id": invoice.id,
            "picking_ids": [(6, 0, pickings.ids)],
            "payment_id": payment[:1].id,
            "sale_error": False,
        })
        return warnings

    def _bf_route_transfer(self, picking_type, source, destination, quantities):
        """A done transfer for these {product: quantity}, and a warning or None.

        Stock may go below zero in the truck: the delivery happened, unloading corrects
        the count. A product followed by lot or serial number cannot be validated without
        one: its transfer stays ready for the office.
        """
        quantities = {p: q for p, q in quantities.items() if q > 0 and p.type == "consu"}
        Picking = self.env["stock.picking"].sudo()
        if not quantities or not picking_type:
            return Picking, None
        picking = Picking.create({
            "picking_type_id": picking_type.id,
            "location_id": source.id,
            "location_dest_id": destination.id,
            "partner_id": self.partner_id.id,
            "origin": self.day_id.name,
            "move_ids": [(0, 0, {"name": product.display_name, "product_id": product.id,
                                 "product_uom_qty": qty, "product_uom": product.uom_id.id,
                                 "location_id": source.id, "location_dest_id": destination.id})
                         for product, qty in quantities.items()],
        })
        picking.action_confirm()
        tracked = picking.move_ids.product_id.filtered(lambda p: p.tracking != "none")
        if tracked:
            return picking, _("Transfer %(name)s waits for the lot or serial numbers of %(products)s.",
                              name=picking.name, products=", ".join(tracked.mapped("display_name")))
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.with_context(skip_backorder=True, cancel_backorder=True, skip_sms=True,
                             skip_immediate=True).button_validate()
        return picking, None

    def action_bf_route_retry_sale(self):
        """The office fixed the cause: make the documents of the recorded sale."""
        self = clean_env(self)
        self.env["bf.route.day"]._check_manager()
        for stop in self.filtered(lambda s: s.sale_error and not s.invoice_id):
            with self.env.cr.savepoint():
                warnings = stop._bf_route_documents()
            if warnings:
                stop._bf_route_flag(warnings)
        return True


class BfRouteDay(models.Model):
    _inherit = "bf.route.day"

    load_picking_id = fields.Many2one("stock.picking", string="Loading", readonly=True, copy=False)
    unload_picking_ids = fields.Many2many("stock.picking", "bf_route_day_unload_rel", "day_id",
                                          "picking_id", string="Unloading", readonly=True, copy=False)
    cash_total = fields.Float(string="Cash to hand in", compute="_compute_totals")
    cheque_total = fields.Float(string="Cheques to hand in", compute="_compute_totals")
    card_total = fields.Float(string="Card payments", compute="_compute_totals")
    sales_total = fields.Float(string="Invoiced", compute="_compute_totals")
    sales_to_finish = fields.Integer(string="Sales to finish", compute="_compute_totals")

    @api.depends("stop_ids.payment_amount", "stop_ids.payment_mode", "stop_ids.invoice_id",
                 "stop_ids.sale_error")
    def _compute_totals(self):
        for day in self:
            stops = day.stop_ids.sudo()

            company_currency = day.company_id.currency_id

            def total(mode, stops=stops):
                # In the company's currency only: a payment in another currency is handed in apart.
                return sum(stops.filtered(
                    lambda s: s.payment_mode == mode
                    and (s._bf_route_currency() == company_currency)).mapped("payment_amount"))

            day.cash_total = total("cash")
            day.cheque_total = total("cheque")
            day.card_total = total("card")
            day.sales_total = sum(stops.mapped("invoice_id.amount_total_signed"))
            day.sales_to_finish = len(stops.filtered("sale_error"))

    def _app_data(self):
        data = super()._app_data()
        data["cash_total"] = self.cash_total
        data["cheque_total"] = self.cheque_total
        return data

    def _keep_when_dropped(self):
        return super()._keep_when_dropped() or bool(self.load_picking_id)

    def _office_prepared(self):
        return super()._office_prepared() or bool(self.load_picking_id)

    def _bf_route_warehouse(self):
        self.ensure_one()
        warehouse = self.env["stock.warehouse"].sudo().search(
            [("company_id", "=", self.company_id.id)], limit=1)
        truck = self.vehicle_id.sudo().bf_route_location_id
        if not warehouse or not truck:
            raise UserError(_("Give the vehicle a truck stock location first (Vehicle, Truck stock)."))
        return warehouse, truck

    def action_load_truck(self):
        """A transfer from the warehouse to the truck for the usual quantities of the stops left."""
        self._check_manager()
        for day in self:
            warehouse, truck = day._bf_route_warehouse()
            wanted = {}
            for line in day.stop_ids.filtered(lambda s: s.state == "todo").line_ids:
                if line.product_id.type == "consu" and line.quantity_planned > 0:
                    wanted[line.product_id] = wanted.get(line.product_id, 0.0) + line.quantity_planned
            if not wanted:
                raise UserError(_("No product to load for this day."))
            day.with_context(bf_route_auto=True).load_picking_id = day._bf_route_internal(
                warehouse, warehouse.lot_stock_id, truck, wanted)
        return self._bf_route_open(self.load_picking_id)

    def action_unload_truck(self):
        """Bring the truck's count to zero: what is left goes back to the warehouse; what went
        below zero (more delivered than the loading said) is taken from the warehouse."""
        self._check_manager()
        for day in self:
            # Not twice from a stale screen; again once the first unloading is done (a sale the
            # office confirmed afterwards moved the truck's count).
            if day.unload_picking_ids.filtered(lambda p: p.state not in ("done", "cancel")):
                raise UserError(_("This day's truck is already being unloaded."))
            # The count of the truck is the vehicle's, not the day's: once the truck left again
            # for a later day (on the road, finished, or loaded for real), unloading this one
            # would take that day's load back. A cancelled day or loading does not count.
            later = self.search([("vehicle_id", "=", day.vehicle_id.id), ("date", ">", day.date),
                                 ("state", "!=", "canceled"), "|",
                                 ("state", "in", ("in_progress", "done")),
                                 ("load_picking_id.state", "=", "done")], order="date", limit=1)
            if later:
                raise UserError(_("The truck was loaded again for %s: unload that day instead.",
                                  later.name))
            warehouse, truck = day._bf_route_warehouse()
            quants = self.env["stock.quant"].sudo().search([("location_id", "=", truck.id)])
            left, missing = {}, {}
            for quant in quants:
                if quant.quantity > 0:
                    left[quant.product_id] = left.get(quant.product_id, 0.0) + quant.quantity
                elif quant.quantity < 0:
                    missing[quant.product_id] = missing.get(quant.product_id, 0.0) - quant.quantity
            if not left and not missing:
                raise UserError(_("The truck is empty."))
            pickings = self.env["stock.picking"]
            if left:
                pickings |= day._bf_route_internal(warehouse, truck, warehouse.lot_stock_id, left)
            if missing:
                correction = day._bf_route_internal(warehouse, warehouse.lot_stock_id, truck, missing)
                correction.sudo().message_post(body=_("Correction: more was delivered from the truck than "
                                               "the loading said."))
                pickings |= correction
            day.with_context(bf_route_auto=True).unload_picking_ids = [(4, p.id) for p in pickings]
        return self._bf_route_open(self.unload_picking_ids)

    def _bf_route_internal(self, warehouse, source, destination, quantities):
        picking = self.env["stock.picking"].create({
            "picking_type_id": warehouse.int_type_id.id,
            "location_id": source.id,
            "location_dest_id": destination.id,
            "origin": self.name,
            "move_ids": [(0, 0, {"name": product.display_name, "product_id": product.id,
                                 "product_uom_qty": qty, "product_uom": product.uom_id.id,
                                 "location_id": source.id, "location_dest_id": destination.id})
                         for product, qty in quantities.items()],
        })
        picking.action_confirm()
        return picking

    def _bf_route_open(self, pickings):
        if len(self) != 1 or not pickings:
            return True
        if len(pickings) == 1:
            return {"type": "ir.actions.act_window", "res_model": "stock.picking",
                    "res_id": pickings.id, "view_mode": "form", "target": "current"}
        return {"type": "ir.actions.act_window", "res_model": "stock.picking", "name": _("Unloading"),
                "domain": [("id", "in", pickings.ids)], "view_mode": "list,form"}

    def action_open_invoices(self):
        self.ensure_one()
        invoices = self.stop_ids.invoice_id
        return {"type": "ir.actions.act_window", "res_model": "account.move", "name": _("Invoices"),
                "domain": [("id", "in", invoices.ids)], "view_mode": "list,form"}
