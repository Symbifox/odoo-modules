from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """In fr_CA, a closed breach of the rules read « Fermée » next to « Ouvert » (un manquement):
    it reads « Fermé ». Only this label is rewritten.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    if "fr_CA" not in dict(env["res.lang"].get_installed()):
        return
    env.ref("bf_school_conduct.selection__bf_school_incident__state__closed").update_field_translations(
        "name", {"fr_CA": "Fermé"})
