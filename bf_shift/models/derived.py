from odoo import api, models


class BfShiftDerived(models.AbstractModel):
    """Derived fields are never written by a caller.

    A stored related field, or a stored computed field without an inverse, is
    readonly in the forms only: over RPC, writing it would desynchronise what
    the record rules read (a draft shift marked published shows to the
    employees, a change given another employee_user_id shows to the wrong
    person) or what the pay reads (a tax verdict, the hours). Its value always
    comes from what it derives from: what a caller sends for it is dropped.
    The module's own writes run as superuser and keep it.
    """
    _name = "bf.shift.derived"
    _description = "Shifts: derived fields are never written"

    def _drop_derived(self, vals):
        if self.env.su:
            return vals
        fields_ = self._fields
        return {name: value for name, value in vals.items()
                if not (name in fields_ and fields_[name].store and fields_[name].readonly
                        and (fields_[name].related or fields_[name].compute)
                        and not fields_[name].inverse)}

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._drop_derived(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._drop_derived(vals))
