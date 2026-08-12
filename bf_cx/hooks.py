"""Install hook: apply per-language email/survey content."""
from .i18n_content import apply_cx_i18n


def post_init_hook(env):
    apply_cx_i18n(env)
