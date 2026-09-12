"""Les libellés du module sont écrits en français à la source ; le français DOIT les suivre.

La 18.0.1.0.0 livrait un catalogue fr_CA d'identité, construit sur des libellés
que les versions suivantes ont changés (un champ sans `string=` s'appelait « Link Count »,
une action a été renommée). Or une mise à jour **n'écrase pas** une traduction
existante : sans ce script, le menu « Tâches fédérées » resterait « Link Count »
en français.

La règle est simple, parce que la source est déjà française : pour tout ce que ce
module possède, la valeur fr_CA est la valeur source.
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
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    if not env["res.lang"].search_count([("code", "=", "fr_CA"), ("active", "=", True)]):
        return

    def aligner(record, champs):
        for champ in champs:
            source = record.with_context(lang="en_US")[champ]
            if source and record.with_context(lang="fr_CA")[champ] != source:
                record.with_context(lang="fr_CA")[champ] = source

    # 1. tout ce que le module possède par ses données
    for data in env["ir.model.data"].search([("module", "=", "bf_federation")]):
        champs = TRADUITS.get(data.model)
        if not champs or data.model not in env:
            continue
        record = env[data.model].browse(data.res_id).exists()
        if record:
            aligner(record, champs)

    # 2. les champs des modèles du module, et ceux qu'il ajoute ailleurs
    fields = env["ir.model.fields"].search(["|", ("model", "in", MODELES),
                                            "&", ("model", "in", ["project.task", "project.project"]),
                                            ("name", "like", "federation%")])
    for field in fields:
        aligner(field, ("field_description", "help"))
        for sel in field.selection_ids:
            aligner(sel, ("name",))
