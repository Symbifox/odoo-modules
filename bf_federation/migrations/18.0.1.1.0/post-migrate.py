"""Les libellés des champs sont posés en français à la source depuis 18.0.1.1.0.

La 18.0.1.0.0 livrait un catalogue fr_CA dont les traductions reprenaient les
libellés dérivés des noms de champs (« Peer », « Origin », « Kind »…). Une mise à
jour **n'écrase pas** une traduction existante : sans ce script, `peer_id`
continuerait d'afficher « Peer » en français. On recharge donc le catalogue du
module en écrasant, puis on réaligne les libellés restants sur leur source, qui
est déjà en français.
"""


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    if not env["res.lang"].search_count([("code", "=", "fr_CA"), ("active", "=", True)]):
        return
    module = env["ir.module.module"].search([("name", "=", "bf_federation")], limit=1)
    if module:
        try:
            module._update_translations(overwrite=True)
        except Exception:  # noqa: BLE001 — le réalignement ci-dessous suffit alors
            pass
    models = ["federation.peer", "federation.peer.identity", "federation.link", "federation.link.message",
              "federation.outbox", "federation.nonce", "federation.accept.wizard", "federation.share.wizard"]
    fields = env["ir.model.fields"].search(["|", ("model", "in", models),
                                            "&", ("model", "in", ["project.task", "project.project"]),
                                            ("name", "like", "federation%")])
    for field in fields:
        source = field.with_context(lang="en_US").field_description
        if source and field.with_context(lang="fr_CA").field_description != source:
            field.with_context(lang="fr_CA").field_description = source
        for sel in field.selection_ids:
            src = sel.with_context(lang="en_US").name
            if src and sel.with_context(lang="fr_CA").name != src:
                sel.with_context(lang="fr_CA").name = src
