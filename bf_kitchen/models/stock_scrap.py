from odoo import api, fields, models

MANAGER = "bf_kitchen.group_kitchen_manager"
LOSS_TYPES = ("ingredient", "preparation", "dish")

# The loss reasons a kitchen gives. Odoo 18 has the reason tags but ships none, and their name is
# unique in the database: they are made by code, reusing a tag of the same name if one exists.
LOSS_REASONS = [
    ("expired", "Expired", "Périmé", 10),
    ("cooking", "Cooking failure", "Raté en cuisson", 20),
    ("customer_return", "Customer return", "Retour client", 30),
    ("spilled", "Spilled or dropped", "Échappé", 40),
    ("staff_meal", "Staff meal", "Repas du personnel", 50),
    ("tasting", "Tasting", "Dégustation", 60),
]


class StockScrap(models.Model):
    _inherit = "stock.scrap"

    bf_is_kitchen = fields.Boolean(
        "Kitchen loss", compute="_compute_bf_is_kitchen", store=True)
    bf_kitchen_currency_id = fields.Many2one(related="company_id.currency_id")
    bf_kitchen_cost = fields.Monetary(
        "Kitchen cost", currency_field="bf_kitchen_currency_id", readonly=True, groups=MANAGER,
        help="The loss valued at the kitchen cost of the item on the day it was declared.")

    @api.depends("product_id.product_tmpl_id.bf_kitchen_type")
    def _compute_bf_is_kitchen(self):
        for scrap in self:
            scrap.bf_is_kitchen = scrap.product_id.product_tmpl_id.bf_kitchen_type in LOSS_TYPES

    def do_scrap(self):
        """The kitchen cost of the day, whichever way the loss was recorded (the kitchen form or
        Odoo's own scrap): a loss left at 0 would make the losses report too low."""
        for scrap in self.sudo().filtered(lambda s: s.bf_is_kitchen and not s.bf_kitchen_cost):
            product = scrap.product_id.with_company(scrap.company_id)
            qty = scrap.product_uom_id._compute_quantity(scrap.scrap_qty, product.uom_id, round=False)
            scrap.bf_kitchen_cost = qty * product.bf_cost_unit
        return super().do_scrap()


class StockScrapReasonTag(models.Model):
    _inherit = "stock.scrap.reason.tag"

    @api.model
    def _bf_kitchen_create_reasons(self):
        """The kitchen's loss reasons, with their French name. Idempotent."""
        Data = self.env["ir.model.data"].sudo()
        Tag = self.sudo().with_context(lang="en_US", active_test=False)
        fr = self.env["res.lang"]._lang_get("fr_CA")
        for key, name_en, name_fr, sequence in LOSS_REASONS:
            xmlid = f"bf_kitchen.scrap_reason_{key}"
            if self.env.ref(xmlid, raise_if_not_found=False):
                continue
            existing = Tag.search([("name", "=", name_en)], limit=1) or \
                Tag.with_context(lang="fr_CA").search([("name", "=", name_fr)], limit=1)
            if existing:
                # The database's own tag: reused as it is, its translation kept, and no xmlid of
                # ours on it (uninstalling the kitchen would delete it).
                continue
            tag = Tag.create({"name": name_en, "sequence": sequence})
            Data.create({"module": "bf_kitchen", "name": f"scrap_reason_{key}", "model": tag._name,
                         "res_id": tag.id, "noupdate": True})
            if fr and fr.active:
                tag.update_field_translations("name", {"fr_CA": name_fr})

    @api.model
    def _bf_kitchen_reason(self, key):
        """A kitchen loss reason: ours, or the database's tag of the same name."""
        tag = self.env.ref(f"bf_kitchen.scrap_reason_{key}", raise_if_not_found=False)
        if tag:
            return tag
        names = next((r[1:3] for r in LOSS_REASONS if r[0] == key), None)
        if not names:
            return self.browse()
        Tag = self.sudo().with_context(active_test=False)
        return self.browse((Tag.with_context(lang="en_US").search([("name", "=", names[0])], limit=1)
                            or Tag.with_context(lang="fr_CA").search([("name", "=", names[1])], limit=1)).ids)
