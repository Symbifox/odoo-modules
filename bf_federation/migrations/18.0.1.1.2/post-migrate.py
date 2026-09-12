"""Le module n'a pas de catalogue français, et ce script retire celui de la 1.0.0.

Les chaînes du module sont écrites en français à la source. La 18.0.1.0.0
livrait malgré tout un catalogue fr_CA d'identité, construit sur des libellés que
les versions suivantes ont changés : un champ sans `string=` s'appelait « Link
Count », une action a été renommée, et l'architecture d'une vue porte ses propres
termes. Une mise à jour **n'écrase pas** une traduction existante, et le
catalogue la réinstallait à chaque passage. Or une mise à jour **n'écrase pas** une traduction
existante : sans ce script, le menu « Tâches fédérées » resterait « Link Count »
en français.

Le catalogue est donc retiré de la 18.0.1.1.2, et ce script efface ce qu'il avait
laissé : sans entrée fr_CA, Odoo sert la source, qui est déjà la bonne.
"""

# Les modèles du module dont un champ traduit peut avoir été figé, et les champs visés.
TRADUITS = {
    "ir.actions.act_window": ("name",),
    "ir.actions.server": ("name",),
    "ir.ui.menu": ("name",),
    "ir.ui.view": ("name",),
    "ir.cron": ("name",),
    "ir.filters": ("name",),
}
MODELES = ["federation.peer", "federation.peer.identity", "federation.link", "federation.link.message",
           "federation.outbox", "federation.nonce", "federation.accept.wizard", "federation.share.wizard"]


def migrate(cr, version):
    import logging

    from odoo import api, SUPERUSER_ID

    _logger = logging.getLogger(__name__)
    env = api.Environment(cr, SUPERUSER_ID, {})
    if not env["res.lang"].search_count([("code", "=", "fr_CA"), ("active", "=", True)]):
        return

    # 1. l'architecture des vues : on RETIRE la traduction plutôt que de la réécrire.
    # Un champ traduit par termes ne se réassigne pas en bloc ; sans entrée fr_CA,
    # Odoo sert la source. C'est le premier geste : il ne dépend de rien.
    vues = env["ir.model.data"].search([("module", "=", "bf_federation"), ("model", "=", "ir.ui.view")]).mapped("res_id")
    if vues:
        cr.execute("UPDATE ir_ui_view SET arch_db = arch_db - 'fr_CA' "
                   "WHERE id IN %s AND arch_db ? 'fr_CA'", (tuple(vues),))
        _logger.info("bf_federation : %s vue(s) rendues à leur source française", cr.rowcount)
        env["ir.ui.view"].invalidate_model(["arch_db"])

    def aligner(record, champs):
        for champ in champs:
            try:
                source = record.with_context(lang="en_US")[champ]
                if source and record.with_context(lang="fr_CA")[champ] != source:
                    record.with_context(lang="fr_CA")[champ] = source
            except Exception:  # noqa: BLE001 — un enregistrement verrouillé ne doit pas tout arrêter
                _logger.warning("bf_federation : %s.%s non réaligné", record._name, champ)

    # 2. tout ce que le module possède par ses données
    for data in env["ir.model.data"].search([("module", "=", "bf_federation")]):
        champs = TRADUITS.get(data.model)
        if not champs or data.model not in env:
            continue
        record = env[data.model].browse(data.res_id).exists()
        if record:
            aligner(record, champs)

    # 3. les champs des modèles du module, et ceux qu'il ajoute ailleurs
    fields = env["ir.model.fields"].search(["|", ("model", "in", MODELES),
                                            "&", ("model", "in", ["project.task", "project.project"]),
                                            ("name", "like", "federation%")])
    for field in fields:
        aligner(field, ("field_description", "help"))
        for sel in field.selection_ids:
            aligner(sel, ("name",))
