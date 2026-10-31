from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """The kitchen stock on a database that already has the kitchen: its locations and loss reasons, as a
    fresh install would make them."""
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["stock.warehouse"]._bf_kitchen_create_all_locations()
    env["stock.scrap.reason.tag"]._bf_kitchen_create_reasons()
