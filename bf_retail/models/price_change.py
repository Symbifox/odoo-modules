# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_is_zero, formatLang

from .res_company import _bf_retail_dans_la_langue_du_commerce

MANAGER = "bf_retail.group_retail_manager"

REASONS = [
    ("cost", "The cost moved"),
    ("rounding", "Rounding of the category"),
    ("promo_end", "End of a promotion"),
    ("manual", "Decided by hand"),
]


class RetailPriceChange(models.Model):
    """A batch of shelf prices, proposed from the costs and applied in one go.

    A store does not move one price: it moves the prices a delivery just changed, prints the
    labels, and walks the aisle once. The batch is also the only trace of what the shelf price
    was before, which is what the cost against price index reads.
    """
    _name = "bf.retail.price.change"
    _description = "Retail price change"
    _order = "date desc, id desc"
    _inherit = ["mail.thread"]
    _check_company_auto = True

    name = fields.Char("Reference", required=True, copy=False, readonly=True, default="/")
    company_id = fields.Many2one(
        "res.company", "Company", required=True, readonly=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date = fields.Date("Day", required=True, default=fields.Date.context_today, tracking=True)
    state = fields.Selection(
        [("draft", "Draft"), ("applied", "Applied"), ("cancelled", "Cancelled")],
        default="draft", required=True, readonly=True, tracking=True)
    reason = fields.Selection(REASONS, "Reason", default="cost", required=True, tracking=True)
    note = fields.Text("Note")
    follow_down = fields.Boolean(
        "Also propose decreases", default=False,
        help="A cost that goes up has to be passed on to hold the margin, and the batch "
             "proposes it. A cost that goes down leaves a margin above target, and giving it "
             "back is a decision, not an arithmetic: those are left out unless this is set.")
    user_id = fields.Many2one("res.users", "Decided by", default=lambda self: self.env.user,
                              tracking=True)
    applied_at = fields.Datetime("Applied on", readonly=True, copy=False)
    line_ids = fields.One2many("bf.retail.price.change.line", "change_id", "Prices")
    line_count = fields.Integer("Prices in the batch", compute="_compute_counts")
    up_count = fields.Integer("Going up", compute="_compute_counts")
    down_count = fields.Integer("Going down", compute="_compute_counts")

    @api.depends("line_ids.price_old", "line_ids.price_new")
    def _compute_counts(self):
        for change in self:
            lines = change.line_ids
            change.line_count = len(lines)
            change.up_count = len(lines.filtered(lambda l: l.price_new > l.price_old))
            change.down_count = len(lines.filtered(lambda l: l.price_new < l.price_old))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                company = self.env["res.company"].browse(
                    vals.get("company_id") or self.env.company.id)
                vals["name"] = self.env["ir.sequence"].with_company(company).next_by_code(
                    "bf.retail.price.change") or "/"
        return super().create(vals_list)

    def _bf_check_manager(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Shelf prices are the retail manager's."))

    def _bf_check_draft(self):
        for change in self:
            if change.state != "draft":
                raise UserError(_("%s is not a draft any more.", change.name))

    def action_propose(self):
        """Fill the batch with every followed product whose price has drifted from its target.

        Read in the batch's own company: the cost series, the target and the rounding are all
        per company, and a batch of another company's prices would be nonsense.
        """
        self.ensure_one()
        self._bf_check_manager()
        self._bf_check_draft()
        company = self.company_id
        products = self.env["product.product"].with_company(company).search(
            [("bf_retail_managed", "=", True), ("active", "=", True),
             ("company_id", "in", (company.id, False))])
        floor = company.bf_retail_price_gap_min or 0.0
        rounding = company.currency_id.rounding or 0.01
        # 🔴 The two guards of the arbitrages of used to sit at the application only, and
        # the proposal knew nothing of them: it filled the batch with lines `action_apply`
        # refused EVERY time, and proposing again put the same ones back -- one tied variant
        # made a batch whose other lines were sound inapplicable. They are set aside here and
        # NAMED, which is the spirit of the third arbitrage of 
        # whole batch at the end.
        stores = self.env["res.company"]._bf_retail_stores() - company
        decided = self.line_ids.filtered("by_hand").product_id
        lines, shared, tied = [], [], []
        for product in products:
            cost = product.bf_retail_cost
            if float_is_zero(cost, precision_rounding=0.0001):
                continue
            suggested = product.bf_retail_price_suggested
            if float_is_zero(suggested, precision_rounding=rounding):
                continue
            price = product.lst_price
            gap = suggested - price
            # Only what has to go up, unless the batch was told to follow costs down too.
            if (abs(gap) if self.follow_down else gap) < floor - 1e-9:
                continue
            # A product the manager already decided on by hand stays as they left it: the batch
            # holds one line per product, and the proposal is not a second opinion on a price
            # someone chose.
            if product in decided:
                continue
            # Written the other way round than the same guard in `action_apply`, on purpose:
            # the mutation harness names its target by the TEXT of a line, and two identical
            # lines in one file would be mutated together and prove half as much.
            if not product.company_id and stores:
                shared.append(product.display_name)
                continue
            if not product._bf_retail_price_on_its_own():
                tied.append(product.display_name)
                continue
            untaxed_new = product._bf_retail_untaxed(suggested, company)
            lines.append(fields.Command.create({
                "product_id": product.id,
                "cost": cost,
                "price_old": price,
                "price_new": suggested,
                "margin_old": product.bf_retail_margin_pct,
                "margin_new": ((untaxed_new - cost) / untaxed_new * 100.0) if untaxed_new else 0.0,
                "target_pct": product.bf_retail_target_pct,
                "by_hand": False,
            }))
        # 🔴 Only what the proposal wrote itself: `line_ids.unlink` used to destroy the lines
        # a manager had decided, without a word and without a way back. The button asks before
        # it replaces its own lines (the view carries the confirmation).
        self.line_ids.filtered(lambda line: not line.by_hand).unlink()
        self.write({"line_ids": lines})
        self._bf_retail_name_what_is_left_out(shared, tied)
        return True

    def _bf_retail_name_what_is_left_out(self, shared, tied):
        """What the proposal set aside, said in the thread of the batch.

        A line counted and named is the third arbitrage of the manager sees the product
        the batch cannot price, and why, instead of a batch that refuses itself at the end.

        The whole method moves to the language of the store at its first line, and not only
        the `_message_log` at its end: the pieces of the body are built HERE with `_`, which
        reads the context off the `self` of its own frame.
        """
        self = _bf_retail_dans_la_langue_du_commerce(self, self.company_id)
        parts = []
        if shared:
            parts.append(_("Left out, more than one store would read their price: %s.",
                           ", ".join(shared[:10])))
        if tied:
            parts.append(_("Left out, their price cannot be moved without moving a sister "
                           "variant: %s.", ", ".join(tied[:10])))
        if parts:
            # 🔴 `_message_log` and not `message_post`: this is a report of what the module
            # just did, and it has nobody to notify. `message_post` REQUIRES an address for
            # its author (`_message_compute_author(..., raise_on_email=True)`), and a clerk
            # or a corner-store manager has no email: the body of the thread then raised and
            # took the whole gesture down with it. `_message_log` writes the same internal
            # note, under the same subtype, without notifying and without asking for an
            # address (`raise_on_email=False`). What is given up is the notification to the
            # followers, which a report of this kind never owed anyone.
            self._message_log(body=" ".join(parts))
        return True

    def action_apply(self):
        """Write the new prices, log them on the products, and queue their labels.

        🔴 A line whose product moved under the batch is refused, named: the new price was
        computed from a cost and from a price that both held at the time of the proposal, and
        applying it blind would overwrite someone else's decision, or hold a margin against a
        cost that is gone. Two more families are refused the same way, and for the same reason
        -- a price nobody here decided: a product no store owns while a second store follows
        the retail, and a variant whose price cannot move without moving its sisters.
        """
        self.ensure_one()
        self._bf_check_manager()
        self._bf_check_draft()
        if not self.line_ids:
            raise UserError(_("Nothing to apply: the batch is empty."))
        company = self.company_id
        rounding = company.currency_id.rounding or 0.01
        # 🔴 The guard below READS the price that the write underneath it WRITES: between the
        # two, another batch could apply its own. The rows the write will touch are locked
        # first, in the order of their ids so two batches cannot wait on each other, and the
        # products are read again after the lock. A second batch now waits here, then reads
        # the prices as this one left them -- and refuses them by name instead of overwriting
        # a decision taken a second ago.
        templates = self.line_ids.product_id.product_tmpl_id
        if templates:
            self.env.flush_all()
            self.env.cr.execute(
                "SELECT id FROM product_template WHERE id IN %s ORDER BY id FOR UPDATE",
                [tuple(templates.ids)])
            self.line_ids.product_id.invalidate_recordset()
        stores = self.env["res.company"]._bf_retail_stores() - company
        stale_price, stale_cost, shared, tied = [], [], [], []
        for line in self.line_ids:
            product = line.product_id.with_company(company)
            if float_compare(product.lst_price, line.price_old, precision_rounding=rounding) != 0:
                stale_price.append(product.display_name)
            elif float_compare(product.bf_retail_cost, line.cost, precision_digits=4) != 0:
                stale_cost.append(product.display_name)
            # 🔴 In Odoo 18 `list_price` is not company dependent, where `standard_price` is: a
            # shelf price written here is the price every other store reads. A product no store
            # owns, while another store follows the retail too, is refused and named (,
            # first arbitrage) rather than overwritten in silence.
            if stores and not product.company_id:
                shared.append(product.display_name)
            elif not product._bf_retail_price_on_its_own():
                tied.append(product.display_name)
        if stale_price or stale_cost or shared or tied:
            parts = []
            if stale_price:
                parts.append(_("Their price moved since the proposal: %s.",
                               ", ".join(stale_price[:10])))
            if stale_cost:
                parts.append(_("Their cost moved since the proposal: %s.",
                               ", ".join(stale_cost[:10])))
            if stale_price or stale_cost:
                parts.append(_("Propose the batch again to read them as they are now."))
            if shared:
                parts.append(_(
                    "More than one store would read these prices: %s. In Odoo the shelf price "
                    "is not kept per company, so applying the batch here would write it for "
                    "the other store too. Give this store its own product.",
                    ", ".join(shared[:10])))
            if tied:
                parts.append(_(
                    "Their price cannot be moved on its own: %s. They are variants of a model "
                    "whose attributes cross, and the price of one is carried by an attribute "
                    "value their sister variants share.", ", ".join(tied[:10])))
            raise UserError("\n".join(parts))
        Queue = self.env["bf.retail.label.queue"]

        def money(amount):
            return formatLang(self.env, amount, currency_obj=company.currency_id)

        for line in self.line_ids:
            product = line.product_id.with_company(company)
            # By the variant's own extra when the model has several, by the template's price
            # when it has one: `lst_price` writes `list_price` on the model, which the sister
            # variants share.
            # The journal of price moves is told WHICH line is writing and on WHAT day: a
            # batch carries its own date, and the index reads the journal (
            # arbitrage).
            product.with_context(
                bf_retail_price_date=self.date,
                bf_retail_price_line=line.id)._bf_retail_write_shelf_price(line.price_new)
            # In the language of the store, like every body the chatter keeps. Read through
            # the record itself (`env._`) and not through the `_` of the frame, which would
            # take the language of the batch, that is, of the context.
            dit = _bf_retail_dans_la_langue_du_commerce(product.product_tmpl_id, company)
            dit._message_log(body=dit.env._(
                "Shelf price of %(product)s: %(old)s to %(new)s (%(reference)s).",
                product=product.display_name, old=money(line.price_old),
                new=money(line.price_new), reference=self.name))
            Queue.sudo().create({
                "product_id": product.id,
                "company_id": company.id,
                "reason": "price",
                "price": line.price_new,
                "change_line_id": line.id,
            })
        self.write({"state": "applied", "applied_at": fields.Datetime.now()})
        dit = _bf_retail_dans_la_langue_du_commerce(self, company)
        # Logged and not posted, for the reason spelled out in `_bf_retail_name_what_is_left_out`:
        # the prices WERE written, and a manager with no email address saw them all rolled
        # back by the thread body that came after them.
        dit._message_log(body=dit.env._("%s price(s) applied.", len(self.line_ids)))
        return True

    def action_cancel(self):
        self._bf_check_manager()
        self._bf_check_draft()
        self.write({"state": "cancelled"})
        return True

    def action_back_to_draft(self):
        self._bf_check_manager()
        for change in self:
            if change.state != "cancelled":
                raise UserError(_("Only a cancelled batch goes back to draft."))
        self.write({"state": "draft"})
        return True


class RetailPriceChangeLine(models.Model):
    _name = "bf.retail.price.change.line"
    _description = "Retail price change line"
    _order = "id"
    _check_company_auto = True

    change_id = fields.Many2one(
        "bf.retail.price.change", "Batch", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="change_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="change_id.currency_id")
    date = fields.Date(related="change_id.date", store=True, index=True)
    state = fields.Selection(related="change_id.state", store=True, index=True)
    product_id = fields.Many2one(
        "product.product", "Product", required=True, ondelete="cascade", check_company=True)
    cost = fields.Float("Cost then", digits=(16, 4), readonly=True)
    price_old = fields.Monetary("Price before", currency_field="currency_id", readonly=True)
    price_new = fields.Monetary("New price", currency_field="currency_id", required=True)
    margin_old = fields.Float("Margin before (%)", digits=(16, 1), readonly=True)
    margin_new = fields.Float("Margin after (%)", digits=(16, 1), readonly=True)
    target_pct = fields.Float("Target (%)", digits=(16, 1), readonly=True)
    by_hand = fields.Boolean(
        "Decided by hand", default=True, readonly=True,
        help="A line a manager added rather than one the proposal wrote: proposing from the "
             "costs again leaves it alone.")

    _sql_constraints = [
        ("price_positive", "CHECK (price_new >= 0)", "A price cannot be negative."),
        ("one_product_per_batch", "UNIQUE (change_id, product_id)",
         "This product is already in the batch."),
    ]

    @api.model
    def _bf_against(self, product, company):
        """What a line is applied against, read from the product in the batch's company."""
        product = product.with_company(company)
        return {
            "cost": product.bf_retail_cost,
            "price_old": product.lst_price,
            "margin_old": product.bf_retail_margin_pct,
            "target_pct": product.bf_retail_target_pct,
            "price_new": product.bf_retail_price_suggested or product.lst_price,
        }

    @api.model_create_multi
    def create(self, vals_list):
        """A line typed by hand carries the price and the cost it is applied against.

        🔴 The screen offers the manager a line of their own (a product the proposal left out,
        a price decided rather than computed), and the four readonly columns are not sent back
        by the client. Without them the guard of `action_apply` read a price before of 0.00,
        found the product's real price, and refused the WHOLE batch by naming that product --
        as if someone else had moved it.
        """
        for vals in vals_list:
            if not vals.get("product_id") or "price_old" in vals:
                continue
            change = self.env["bf.retail.price.change"].browse(vals.get("change_id") or [])
            product = self.env["product.product"].browse(vals["product_id"])
            against = self._bf_against(product, change.company_id or self.env.company)
            for field, value in against.items():
                # 🔴 `not vals.get(field)` does not tell an ABSENT field from a zero: a new
                # price of 0.00, typed by a manager who gives a line away, was replaced in
                # silence by the suggested price, while the SQL constraint allows 0.
                if field not in vals:
                    vals[field] = value
        return super().create(vals_list)

    @api.onchange("product_id")
    def _onchange_product_id(self):
        """The same four numbers, shown as soon as the product is picked."""
        for line in self:
            if not line.product_id:
                continue
            against = self._bf_against(
                line.product_id, line.change_id.company_id or self.env.company)
            for field, value in against.items():
                if field != "price_new" or not line.price_new:
                    line[field] = value

    @api.onchange("price_new")
    def _onchange_price_new(self):
        """A manager who types another price sees the margin it leaves, before applying."""
        for line in self:
            product = line.product_id
            if not product or not line.change_id.company_id:
                continue
            untaxed = product._bf_retail_untaxed(line.price_new, line.change_id.company_id)
            line.margin_new = ((untaxed - line.cost) / untaxed * 100.0) if untaxed else 0.0
