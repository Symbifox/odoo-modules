"""Install hook: apply per-language email/survey content."""
from .i18n_content import apply_cx_i18n

# Content a team reads every day, in a field Odoo does not translate: named in
# the company's language at install (the data files are English since 18.0.1.13.2).
# (The default programs' names are translatable: apply_cx_i18n writes them.)
NOMS_FR = {
    "seq_cx_complaint": "Plaintes clients",
}


def post_init_hook(env):
    apply_cx_i18n(env)
    lang = env.company.partner_id.lang or env.lang or ""
    if lang.startswith("fr"):
        for xmlid, name in NOMS_FR.items():
            record = env.ref("bf_cx.%s" % xmlid, raise_if_not_found=False)
            if record:
                record.name = name
