"""Give the poke's reworded sentence its own translation, not its predecessor's.

6.3.0 rewords "Just checking that we are still meeting for" into "… at", the
meeting's name having left the sentence. On upgrade, Odoo carries each old term's
translation over to the new term that resembles it most, and the module's
`fr_CA.po` is then loaded without overwriting: measured on the bench, the French
body read "… nous nous rencontrons toujours pour 17 h 30?" after the upgrade,
with the corrected `.po` in place.

So this one term is set here, in every language the view carries other than its
source. Nothing else in the view is touched: a translation reworded on the
database itself stays.
"""

from odoo import SUPERUSER_ID, api

SOURCE = "Just checking that we are still meeting at"
FRENCH = "Je voulais simplement m'assurer que nous nous rencontrons toujours à"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    view = env.ref("bf_calendar_invite.mail_body_calendar_poke", raise_if_not_found=False)
    if not view:
        return
    # ⚠️ Read before asking the ORM anything: `get_installed()` queries on this
    # same cursor when its cache is cold, and the keys would be lost under it.
    cr.execute("SELECT jsonb_object_keys(arch_db) FROM ir_ui_view WHERE id = %s", [view.id])
    keys = {lang for (lang,) in cr.fetchall()}
    installed = {code for code, _name in env["res.lang"].get_installed()}
    langs = keys & installed - {"en_US"}
    if not langs:
        return
    # A falsy value drops the translation, and the term falls back to its source:
    # what any language without a French translation should read.
    view.update_field_translations("arch_db", {
        lang: {SOURCE: FRENCH if lang.startswith("fr") else False} for lang in langs
    })
