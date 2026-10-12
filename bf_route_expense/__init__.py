from . import models


def post_init_hook(env):
    """The reduced-rate product, made from Odoo's Mileage product so it keeps its unit (km),
    taxes and accounts; then both products set on every company."""
    full = env.ref("hr_expense.expense_product_mileage", raise_if_not_found=False)
    if not full:
        return
    reduced = full.copy({"default_code": "MIL2"})
    env["ir.model.data"].create({
        "module": "bf_route_expense", "name": "product_mileage_reduced", "model": "product.product",
        "res_id": reduced.id, "noupdate": True})
    # ⚠️ Each language by name: a plain write in the installing user's language (French,
    # for instance) also lands in the English source when the record is new.
    names = {"en_US": "Mileage, reduced rate"}
    if env["res.lang"]._lang_get("fr_CA"):
        names["fr_CA"] = "Kilométrage, taux réduit"
    if env["res.lang"]._lang_get("fr_FR"):
        names["fr_FR"] = "Kilométrage, taux réduit"
    reduced.product_tmpl_id.update_field_translations("name", names)
    cad = env.ref("base.CAD", raise_if_not_found=False)
    for company in env["res.company"].search([]):
        company.write({"bf_route_mileage_product_id": full.id,
                       "bf_route_mileage_reduced_product_id": reduced.id})
        if not cad or company.currency_id != cad:
            # Rates in dollars per km mean nothing in another currency: the office sets them.
            reduced.with_company(company).standard_price = 0.0
            continue
        # 2026 rates of the CRA and Revenu Québec: 0.73 for the first 5,000 km, 0.67 beyond.
        # The full rate replaces Odoo's placeholder (1.00, or nothing) only, never a rate set here.
        if full.with_company(company).standard_price in (0.0, 1.0):
            full.with_company(company).standard_price = 0.73
        reduced.with_company(company).standard_price = 0.67
