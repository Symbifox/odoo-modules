from odoo import fields

from . import models


def post_init_hook(env):
    """The meals served from today on leave the stock; the meals of the past are left as they were."""
    env["ir.config_parameter"].sudo().set_param("bf_kitchen_school_meal.since", fields.Date.to_string(fields.Date.today()))
