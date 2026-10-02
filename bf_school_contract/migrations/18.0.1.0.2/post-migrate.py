from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """"Contract for educational services" was left in English in fr_CA: the model's name (the
    history of every new contract read "Contract for educational services créé") and the Print
    menu. Messages already written keep their text: only what is shown from now on changes.

    An upgrade does not replace a translation already in the database, even one equal to the
    English text: the module's terms are reloaded from its files, overwriting (once, a school's
    own fr_CA wording of this module's screens or printed contract would be replaced).
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, __ in env["res.lang"].get_installed() if code != "en_US"]
    if langs:
        env["ir.module.module"]._load_module_terms(["bf_school_contract"], langs, overwrite=True)
