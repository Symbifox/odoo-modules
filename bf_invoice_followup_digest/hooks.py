import logging

_logger = logging.getLogger(__name__)

# The standalone digest cron of bf_invoice_followup. Its content now rides in
# the daily digest, so the bridge switches it off — and switches it back on if
# the bridge is ever uninstalled. The record is noupdate="1", so an upgrade of
# bf_invoice_followup will not silently turn it back on behind us.
STANDALONE_DIGEST_CRON = "bf_invoice_followup.cron_weekly_followup_digest"

# The bridge re-labels bf_invoice_followup's opt-in field (models/res_users.py).
# Uninstalling the bridge drops its Python, but `ir_model_fields.field_description`
# keeps whatever was written last: nothing re-runs the owning module's field
# definition until it is upgraded. So the hook puts the original strings back
# itself, and only when the stored value is still exactly the bridge's own — if
# bf_invoice_followup has since changed its wording, we leave it alone.
RELABELLED_FIELD = ("res.users", "bf_followup_digest")
BRIDGE_STRING = "Voir les recevables en retard dans le digest quotidien"
BRIDGE_HELP = (
    "Si coché, la section « Factures en retard » apparaît dans votre digest "
    "quotidien, les jours où il y a des factures échues et impayées. Aucun "
    "courriel séparé n'est envoyé."
)
ORIGINAL_STRING = "Recevoir le récap des recevables en retard"
ORIGINAL_HELP = (
    "Si coché, vous recevez par courriel le récapitulatif des factures "
    "clients en retard, selon la fréquence choisie."
)


def _toggle_standalone_digest(env, active):
    cron = env.ref(STANDALONE_DIGEST_CRON, raise_if_not_found=False)
    if not cron:
        _logger.warning(
            "bf_invoice_followup_digest: cron %s introuvable, rien à basculer",
            STANDALONE_DIGEST_CRON)
        return
    if cron.active == active:
        return
    cron.sudo().write({"active": active})
    _logger.info(
        "bf_invoice_followup_digest: cron %s -> active=%s",
        STANDALONE_DIGEST_CRON, active)


def _restore_field_label(env):
    model, name = RELABELLED_FIELD
    field = env["ir.model.fields"].sudo().search(
        [("model", "=", model), ("name", "=", name)], limit=1)
    if not field:
        return
    if field.field_description != BRIDGE_STRING:
        _logger.info(
            "bf_invoice_followup_digest: libellé de %s.%s modifié depuis, laissé tel quel",
            model, name)
        return
    vals = {"field_description": ORIGINAL_STRING}
    if (field.help or "") == BRIDGE_HELP:
        vals["help"] = ORIGINAL_HELP
    field.write(vals)
    _logger.info(
        "bf_invoice_followup_digest: libellé de %s.%s rendu à son texte d'origine",
        model, name)


def post_init_hook(env):
    """Retire le courriel séparé : la section du digest prend le relais."""
    _toggle_standalone_digest(env, False)


def uninstall_hook(env):
    """Rend le courriel séparé, et son libellé, quand le pont s'en va."""
    _toggle_standalone_digest(env, True)
    _restore_field_label(env)
