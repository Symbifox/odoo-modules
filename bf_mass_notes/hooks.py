import logging

from odoo.tools.translate import LazyTranslate

_logger = logging.getLogger(__name__)
_lt = LazyTranslate(__name__)

#: Display name of the contextual list-view Action created on every thread model.
#: The actions are created by this hook, after Odoo has loaded the catalogues:
#: the hook writes each installed language itself.
ACTION_NAME = _lt("Add a note (in bulk)")

#: Python executed by the server action. Opens the wizard on the current
#: selection; the wizard titles its window in the user's language.
SERVER_ACTION_CODE = """\
action = env['bf.mass.note.wizard'].action_open_for(model._name, records.ids)
"""

#: Marker shared by every version of the server action code (search key).
ACTION_CODE_MARKER = "bf.mass.note.wizard"


def _thread_models(env):
    """Yield the names of concrete models that carry a chatter (inherit mail.thread)."""
    for model_name in env.registry.models:
        model = env[model_name]
        if model._abstract or model._transient:
            continue
        # mail.thread defines both message_post and _mail_post_access; concrete
        # subclasses inherit them while plain models do not.
        if hasattr(model, "message_post") and getattr(model, "_mail_post_access", None) is not None:
            yield model_name


def _name_translations(env):
    """{lang: name} for every installed language other than English (the source)."""
    return {
        code: env(context=dict(env.context, lang=code))._(ACTION_NAME)
        for code, _name in env["res.lang"].get_installed()
        if code != "en_US"
    }


def post_init_hook(env):
    """Create one contextual list Action per thread model so a note can be
    posted to several chatters at once. Idempotent (safe to re-run on upgrade)."""
    IrModel = env["ir.model"]
    ServerAction = env["ir.actions.server"]
    english = env(context=dict(env.context, lang="en_US"))._(ACTION_NAME)
    translations = _name_translations(env)
    created = 0
    for model_name in _thread_models(env):
        ir_model = IrModel._get(model_name)
        if not ir_model:
            continue
        existing = ServerAction.search([
            ("binding_model_id", "=", ir_model.id),
            ("state", "=", "code"),
            ("code", "like", ACTION_CODE_MARKER),
        ], limit=1)
        if existing:
            continue
        action = ServerAction.with_context(lang="en_US").create({
            "name": english,
            "model_id": ir_model.id,
            "binding_model_id": ir_model.id,
            "binding_view_types": "list",
            "state": "code",
            "code": SERVER_ACTION_CODE,
        })
        for code, name in translations.items():
            action.update_field_translations("name", {code: name})
        created += 1
    _logger.info("bf_mass_notes: created %s mass-note list bindings", created)


def uninstall_hook(env):
    """Remove the server actions created by post_init_hook (created imperatively,
    so the ORM does not clean them up on uninstall)."""
    actions = env["ir.actions.server"].search([
        ("state", "=", "code"),
        ("code", "like", ACTION_CODE_MARKER),
    ])
    count = len(actions)
    actions.unlink()
    _logger.info("bf_mass_notes: removed %s mass-note list bindings", count)
