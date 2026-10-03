from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """In fr_CA, the « Open » state of a meeting session read « Ouvrir » (a verb): it reads
    « Ouverte ». Only this label is rewritten.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    if "fr_CA" not in dict(env["res.lang"].get_installed()):
        return
    env.ref("bf_school_meeting.selection__bf_school_meeting_session__state__open").update_field_translations(
        "name", {"fr_CA": "Ouverte"})
