import logging

_logger = logging.getLogger(__name__)

#: Display name of the contextual list-view Action created on every thread model.
ACTION_NAME = "Ajouter une note (en lot)"

#: Python executed by the server action. Opens the wizard, forwarding the
#: current selection (``model._name`` / ``records.ids``) through the context.
SERVER_ACTION_CODE = """\
action = {
    'type': 'ir.actions.act_window',
    'name': 'Ajouter une note en lot',
    'res_model': 'bf.mass.note.wizard',
    'view_mode': 'form',
    'target': 'new',
    'context': dict(env.context, active_model=model._name, active_ids=records.ids),
}
"""


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


def post_init_hook(env):
    """Create one contextual list Action per thread model so a note can be
    posted to several chatters at once. Idempotent (safe to re-run on upgrade)."""
    IrModel = env["ir.model"]
    ServerAction = env["ir.actions.server"]
    created = 0
    for model_name in _thread_models(env):
        ir_model = IrModel._get(model_name)
        if not ir_model:
            continue
        existing = ServerAction.search([
            ("name", "=", ACTION_NAME),
            ("binding_model_id", "=", ir_model.id),
            ("state", "=", "code"),
        ], limit=1)
        if existing:
            continue
        ServerAction.create({
            "name": ACTION_NAME,
            "model_id": ir_model.id,
            "binding_model_id": ir_model.id,
            "binding_view_types": "list",
            "state": "code",
            "code": SERVER_ACTION_CODE,
        })
        created += 1
    _logger.info("bf_mass_notes: created %s mass-note list bindings", created)


def uninstall_hook(env):
    """Remove the server actions created by post_init_hook (created imperatively,
    so the ORM does not clean them up on uninstall)."""
    actions = env["ir.actions.server"].search([
        ("name", "=", ACTION_NAME),
        ("state", "=", "code"),
        ("code", "like", "bf.mass.note.wizard"),
    ])
    count = len(actions)
    actions.unlink()
    _logger.info("bf_mass_notes: removed %s mass-note list bindings", count)
