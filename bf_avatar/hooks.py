from .models.bf_avatar import DEFAULT_STYLE, PARAM_CONTACTS, PARAM_PALETTE, PARAM_STYLE


def post_init_hook(env):
    params = env["ir.config_parameter"].sudo()
    if not params.get_param(PARAM_STYLE):
        params.set_param(PARAM_STYLE, DEFAULT_STYLE)
    env["bf.avatar"]._regenerate_generated()


def uninstall_hook(env):
    """Give the generated avatars back to Odoo's own style before leaving."""
    params = env["ir.config_parameter"].sudo()
    params.set_param(PARAM_STYLE, "odoo")
    env["bf.avatar"]._regenerate_generated()
    for key in (PARAM_STYLE, PARAM_PALETTE, PARAM_CONTACTS):
        params.search([("key", "=", key)]).unlink()
