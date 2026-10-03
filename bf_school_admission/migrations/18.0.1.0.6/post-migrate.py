from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """In fr_CA, the second parent's address was labelled « E-mail » instead of « Courriel ».

    An upgrade does not replace a translation already in the database: this one term is
    rewritten, and nothing else (a school's own wording of the other terms is kept). The new
    texts of this version (« Ouvrir la campagne », the tabs' titles) are loaded by the upgrade.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    if "fr_CA" not in dict(env["res.lang"].get_installed()):
        return
    env.ref("bf_school_admission.admission_form").update_field_translations(
        "arch_db", {"fr_CA": {"Email": "Courriel"}})
