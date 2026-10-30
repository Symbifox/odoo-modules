from odoo import _, models
from odoo.exceptions import AccessError

KITCHEN_GROUPS = ("bf_kitchen.group_kitchen_cook", "bf_kitchen.group_kitchen_manager")


class KitchenScopeMixin(models.AbstractModel):
    """Keep the kitchen manager's write access to kitchen products and recipes.

    The kitchen groups get write access to products and bills of materials through the
    module's own access lines. When that is the user's only source of write access on the
    model, the records written must belong to the kitchen, and the fields that drive the
    accounting and the stock valuation (``_bf_kitchen_protected_fields``) stay out of reach. A
    user who can already write the model through another group (product, stock, purchase,
    sales, accounting, manufacturing manager, any app) keeps that access whole: this is checked
    against the access lines of the database, so an app installed later is covered without a
    new rule.
    """

    _name = "bf.kitchen.scope.mixin"
    _description = "Kitchen write scope"

    def _bf_kitchen_is_kitchen(self):
        """Return the records of ``self`` that belong to the kitchen."""
        raise NotImplementedError

    def _bf_kitchen_protected_fields(self):
        """Fields the kitchen scope never writes: changed on write, given on create."""
        return set()

    def _bf_kitchen_is_protected(self, fname):
        return fname in self._bf_kitchen_protected_fields() or (
            fname.startswith("property_") and fname in self._fields)

    def _bf_kitchen_scope_applies(self, mode):
        if self.env.su:
            return False
        user = self.env.user
        if not user.has_group("bf_kitchen.group_kitchen_manager"):
            return False
        kitchen = [self.env.ref(x).id for x in KITCHEN_GROUPS]
        groups = [g for g in user.groups_id.ids if g not in kitchen]
        self.env.cr.execute(f"""
            SELECT 1 FROM ir_model_access a JOIN ir_model m ON m.id = a.model_id
             WHERE m.model = %s AND a.active AND a.perm_{mode}
               AND (a.group_id IS NULL OR a.group_id = ANY(%s))
             LIMIT 1""", (self._name, groups))
        return not self.env.cr.fetchone()

    def _bf_kitchen_check_records(self):
        outside = self - self._bf_kitchen_is_kitchen()
        if outside:
            raise AccessError(_(
                "As a kitchen manager, you can only change kitchen products and recipes "
                "(%(records)s is not one).", records=", ".join(outside[:3].mapped("display_name"))))

    def _bf_kitchen_check_scope(self, mode):
        if not self or not self._bf_kitchen_scope_applies(mode):
            return
        self._bf_kitchen_check_records()

    def _bf_kitchen_refuse_fields(self, fnames):
        labels = [self._fields[f].get_description(self.env)["string"] for f in fnames if f in self._fields]
        raise AccessError(_(
            "As a kitchen manager, you cannot change %(fields)s: these settle the accounting and "
            "the stock valuation of the product. Ask a product, inventory or accounting manager.",
            fields=", ".join(labels or fnames)))

    def _bf_kitchen_snapshot(self, fnames):
        values = []
        for fname in fnames:
            value = self[fname]
            if isinstance(value, models.BaseModel):
                value = tuple(sorted(value.ids))
            elif isinstance(value, float):
                value = round(value, 9)
            values.append(value)
        return tuple(values)

    def write(self, vals):
        guard = bool(self) and self._bf_kitchen_scope_applies("write")
        before = {}
        if guard:
            self._bf_kitchen_check_records()
            protected = [f for f in vals if self._bf_kitchen_is_protected(f)]
            if protected:
                before = {rec: rec._bf_kitchen_snapshot(protected) for rec in self.sudo()}
        res = super().write(vals)
        if guard:
            self._bf_kitchen_check_records()
            changed = [f for f in protected if any(
                rec._bf_kitchen_snapshot([f]) != (old[protected.index(f)],) for rec, old in before.items())]
            if changed:
                self._bf_kitchen_refuse_fields(changed)
        return res

    def unlink(self):
        self._bf_kitchen_check_scope("unlink")
        return super().unlink()

    def _bf_kitchen_check_created(self, vals_list=None):
        if not self or not self._bf_kitchen_scope_applies("create"):
            return self
        self._bf_kitchen_check_records()
        given = {f for vals in vals_list or [] for f, v in vals.items() if v and self._bf_kitchen_is_protected(f)}
        # On create, the form sends every field with its default value: only a value given for
        # the cost or an accounting / stock property is refused (taxes, category and type of a
        # new product are the defaults or the kitchen's choice).
        given = {f for f in given if f == "standard_price" or f.startswith("property_")}
        if given:
            self._bf_kitchen_refuse_fields(sorted(given))
        return self
