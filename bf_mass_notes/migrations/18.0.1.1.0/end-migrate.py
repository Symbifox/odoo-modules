"""18.0.1.1.0: the mass-note Actions read in the user's language.

Odoo never translates into en_US, the source language: the Action created on
every thread model was named in French for everyone, and its window title was
French text inside the server action code, which nothing translates.

The Actions are created by the install hook, so no catalogue reaches them. For
each one still carrying the delivered name, every installed language without
its own key receives its name from the catalogue, then en_US becomes English.
A name edited by hand is left as is. The server action code still equal to the
delivered one is replaced by a call that lets the wizard title its window.
"""

import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.bf_mass_notes.hooks import (
    ACTION_CODE_MARKER,
    ACTION_NAME,
    SERVER_ACTION_CODE,
    _name_translations,
)

_logger = logging.getLogger(__name__)

OLD_NAME = "Ajouter une note (en lot)"
OLD_CODE = """\
action = {
    'type': 'ir.actions.act_window',
    'name': 'Ajouter une note en lot',
    'res_model': 'bf.mass.note.wizard',
    'view_mode': 'form',
    'target': 'new',
    'context': dict(env.context, active_model=model._name, active_ids=records.ids),
}
"""


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    actions = env["ir.actions.server"].search([
        ("state", "=", "code"),
        ("code", "like", ACTION_CODE_MARKER),
    ])
    english = env(context={"lang": "en_US"})._(ACTION_NAME)
    translations = _name_translations(env)
    renamed = rewired = 0
    for action in actions:
        if action.code == OLD_CODE:
            action.code = SERVER_ACTION_CODE
            rewired += 1
        cr.execute("SELECT name FROM ir_act_server WHERE id = %s", [action.id])
        raw = cr.fetchone()[0] or {}
        if (raw.get("en_US") or "").strip() != OLD_NAME:
            continue
        for code, name in translations.items():
            if code not in raw:
                action.update_field_translations("name", {code: name})
        action.with_context(lang="en_US").name = english
        renamed += 1
    _logger.info(
        "bf_mass_notes: %s of %s Actions renamed, %s opener(s) rewired",
        renamed, len(actions), rewired,
    )
