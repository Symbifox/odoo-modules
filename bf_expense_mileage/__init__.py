# -*- coding: utf-8 -*-
from . import models
from . import wizard

# 2026 rates of the CRA (read on canada.ca, « Automobile allowance rates », 2026-11-24).
FULL_RATE_2026 = 0.73
REDUCED_RATE_2026 = 0.67


def post_init_hook(env):
    """The reduced-rate product, then both products and the threshold on every company.

    ⚠️ When Work routes' mileage (``bf_route_expense``) is already installed, its reduced
    product and its settings are taken as they are: two reduced products would split the
    yearly count in two, and each module would let an employee start over at the full rate.
    """
    full = env.ref("hr_expense.expense_product_mileage", raise_if_not_found=False)
    if not full:
        return
    route_reduced = env.ref("bf_route_expense.product_mileage_reduced", raise_if_not_found=False)
    if route_reduced:
        reduced = route_reduced
    else:
        reduced = full.copy({"default_code": "MIL2"})
        # ⚠️ Each language by name: a plain write in the installing user's language (French at
        # Blue Fox) also lands in the English source when the record is new.
        names = {"en_US": "Mileage, reduced rate"}
        for code in ("fr_CA", "fr_FR"):
            if env["res.lang"]._lang_get(code):
                names[code] = "Kilométrage, taux réduit"
        reduced.product_tmpl_id.update_field_translations("name", names)
    env["ir.model.data"].create({
        "module": "bf_expense_mileage", "name": "product_mileage_reduced",
        "model": "product.product", "res_id": reduced.id, "noupdate": True})

    cad = env.ref("base.CAD", raise_if_not_found=False)
    company_fields = env["res.company"]._fields
    for company in env["res.company"].search([]):
        vals = {"bf_mileage_product_id": full.id, "bf_mileage_reduced_product_id": reduced.id}
        if "bf_route_mileage_threshold" in company_fields:
            # The settings already chosen for route days stay the company's settings.
            vals["bf_mileage_product_id"] = company.bf_route_mileage_product_id.id or full.id
            vals["bf_mileage_reduced_product_id"] = (
                company.bf_route_mileage_reduced_product_id.id or reduced.id)
            vals["bf_mileage_threshold"] = company.bf_route_mileage_threshold
        company.write(vals)
        if route_reduced:
            continue
        if not cad or company.currency_id != cad:
            # Rates in dollars per km mean nothing in another currency: the office sets them.
            reduced.with_company(company).standard_price = 0.0
            continue
        # The full rate replaces Odoo's placeholder (1.00, or nothing) only, never a rate set here.
        if full.with_company(company).standard_price in (0.0, 1.0):
            full.with_company(company).standard_price = FULL_RATE_2026
        reduced.with_company(company).standard_price = REDUCED_RATE_2026
