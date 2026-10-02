from odoo.addons.bf_work_category.hooks import bf_work_category_initialise, bf_work_category_prepare_columns

from . import models


def pre_init_hook(env):
    bf_work_category_prepare_columns(env, "bf_email", linked=True)


def post_init_hook(env):
    bf_work_category_initialise(env, "bf.email")
