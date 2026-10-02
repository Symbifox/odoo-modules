from odoo.addons.bf_work_category.hooks import bf_work_category_initialise, bf_work_category_prepare_columns

from . import models


def pre_init_hook(env):
    bf_work_category_prepare_columns(env, "claude_chat_session", linked=True)


def post_init_hook(env):
    bf_work_category_initialise(env, "claude.chat.session")
