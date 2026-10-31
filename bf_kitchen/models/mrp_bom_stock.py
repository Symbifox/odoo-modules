from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_is_zero

MANAGER = "bf_kitchen.group_kitchen_manager"
# Context value only server code can give (an RPC context is JSON): the type change comes from
# the module's own buttons, after their checks.
HOLD = object()


class MrpBom(models.Model):
    """A preparation made ahead and kept in stock (a stock, a sauce) is a normal bill of
    materials: making it is a manufacturing order that takes its ingredients out of stock and
    puts the preparation in. A preparation made on the fly stays a kit, exploded where it is
    used. Only a preparation can be held in stock: a dish is sold, and stays a kit."""
    _inherit = "mrp.bom"

    bf_kitchen_stocked = fields.Boolean(
        "Held in stock", compute="_compute_bf_kitchen_stocked",
        help="Made ahead and kept in stock: the dishes take it from the stock, and making it takes "
             "its ingredients out.")

    @api.depends("type")
    def _compute_bf_kitchen_stocked(self):
        for bom in self:
            bom.bf_kitchen_stocked = bom.type == "normal"

    def _bf_check_preparation_change(self):
        if not self.env.su and not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only a kitchen manager can change how a preparation is made."))
        for bom in self:
            if bom.bf_kitchen_type != "preparation":
                raise UserError(_("Only a preparation can be held in stock: %s is not one.",
                                  bom.product_tmpl_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        boms = super().create(vals_list)
        # A kitchen recipe is born a kit (the Manufacturing app makes a normal one by default): a
        # dish made as a normal bill of materials is never exploded at the sale, and its
        # ingredients never leave the stock. A preparation is held in stock only by its button.
        wrong = boms.filtered(lambda b: b.bf_kitchen_type in ("preparation", "dish") and b.type != "phantom"
                              and not (b.bf_kitchen_type == "preparation" and b.product_tmpl_id.is_storable
                                       and self.env.su))
        if wrong:
            wrong.sudo().with_context(bf_kitchen_stock_switch=HOLD).write({"type": "phantom"})
        return boms

    def write(self, vals):
        if "type" in vals and not self.env.su and not self.env.context.get("bf_kitchen_stock_switch") is HOLD \
                and any(bom.bf_kitchen_type in ("preparation", "dish") for bom in self):
            raise UserError(_("A kitchen recipe changes type through Hold in stock or Make it on the fly, "
                              "on a preparation only."))
        return super().write(vals)

    def _bf_kitchen_recipes_using(self):
        """These recipes and every active kitchen recipe that uses them, preparations on the way
        included: changing how a preparation is made changes how all of them explode."""
        Bom = self.env["mrp.bom"].sudo()
        found, frontier = Bom, self.sudo()
        while frontier:
            found |= frontier
            products = frontier.product_id | frontier.product_tmpl_id.product_variant_ids
            lines = self.env["mrp.bom.line"].sudo().search([
                ("product_id", "in", products.ids), ("bom_id.active", "=", True),
                ("bom_id.product_tmpl_id.bf_kitchen_type", "in", ("preparation", "dish"))])
            frontier = lines.bom_id - found
        return found

    def _bf_check_switch_free(self):
        """Refuse a switch while something already exploded with the old recipe is under way: Odoo
        reads the delivered quantity of a kit sold through the moves of its recipe lines, and a
        switched recipe would leave those sales at 0 delivered, never invoiced."""
        recipes = self._bf_kitchen_recipes_using()
        moves = self.env["stock.move"].sudo().search_count([
            ("bom_line_id.bom_id", "in", recipes.ids), ("state", "not in", ("done", "cancel"))])
        if moves:
            raise UserError(_(
                "%(prep)s cannot change how it is made yet: %(n)s stock move(s) of it, or of the dishes "
                "that use it, are still to be done (a delivery, a register order). Validate or cancel "
                "them first.", prep=", ".join(self.product_tmpl_id.mapped("display_name")), n=moves))
        kits = recipes.filtered(lambda b: b.type == "phantom")
        if kits and hasattr(kits, "_ensure_bom_is_free"):
            try:
                kits._ensure_bom_is_free()
            except UserError as error:
                raise UserError(_(
                    "%(prep)s cannot change how it is made yet: confirmed sales of it, or of the dishes that "
                    "use it, are not fully invoiced. Invoice or cancel them first.\n\n%(odoo)s",
                    prep=", ".join(self.product_tmpl_id.mapped("display_name")), odoo=error.args[0])) from error

    def action_bf_hold_in_stock(self):
        self._bf_check_preparation_change()
        for bom in self.filtered(lambda b: b.type == "phantom"):
            bom._bf_check_switch_free()
            template = bom.product_tmpl_id
            if not template.is_storable:
                # Created as a kit, the preparation was not kept in stock: it must be, or a batch
                # made would enter no stock. It has no quant, nothing to revalue.
                template.sudo().is_storable = True
            try:
                bom.with_context(bf_kitchen_stock_switch=HOLD).write({"type": "normal"})
            except ValidationError:
                raise   # the recipe's own checks (a dish's portion that would round to 0)
            except UserError as error:
                # sale_mrp refuses while a confirmed sale of it as a kit is not fully invoiced.
                raise UserError(_(
                    "%(prep)s cannot be held in stock yet: confirmed sales of it as a kit are not fully "
                    "invoiced. Invoice or cancel them first.\n\n%(odoo)s",
                    prep=bom.product_tmpl_id.display_name, odoo=error.args[0])) from error
        return True

    def action_bf_make_on_the_fly(self):
        self._bf_check_preparation_change()
        for bom in self.filtered(lambda b: b.type == "normal"):
            bom._bf_check_switch_free()
            products = bom.product_id | bom.product_tmpl_id.product_variant_ids
            company = bom.company_id or self.env.company
            # Made on the fly, its stock would never leave again (the dishes take its ingredients):
            # it must be used up, declared lost or counted to 0 first.
            quants = self.env["stock.quant"].sudo().search([
                ("product_id", "in", products.ids), ("location_id.usage", "=", "internal"),
                ("company_id", "=", company.id)])
            if any(not float_is_zero(q.quantity, precision_rounding=q.product_uom_id.rounding) for q in quants):
                raise UserError(_(
                    "%s is still in stock: use it up, declare it lost or count it to 0 before making it on the fly.",
                    bom.product_tmpl_id.display_name))
            if self.env["mrp.production"].sudo().search_count([
                    ("product_id", "in", products.ids), ("state", "not in", ("done", "cancel"))]):
                raise UserError(_("A batch of %s is still in progress: finish or cancel it first.",
                                  bom.product_tmpl_id.display_name))
            # It stays "tracked" (Odoo refuses to untrack a product already moved): as a kit, Odoo
            # reads its stock from its ingredients, and a kitchen count leaves it out.
            bom.with_context(bf_kitchen_stock_switch=HOLD).write({"type": "phantom"})
        return True

    def action_bf_prepared(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("I prepared"), "res_model": "bf.kitchen.prepared",
            "view_mode": "form", "target": "new",
            "context": {"default_bom_id": self.id},
        }
