from .models.res_partner import _to_push


def _with_pronouns(env):
    return env["res.partner"].sudo().with_context(active_test=False).search([
        ("is_company", "=", False),
        ("x_nc_contact_uid", "!=", False), ("x_nc_contacts_config_id", "!=", False),
        "|", ("bf_pronouns", "!=", False), ("bf_grammatical_gender", "!=", False),
    ]).filtered(_to_push)


def post_init_hook(env):
    """Marque à repousser les cartes qui portent désormais des pronoms.

    Sans lui, une carte déjà dans Nextcloud attendrait qu'un autre de ses
    champs change. Seulement les fiches que la synchro
    pousse (sens « Odoo vers Nextcloud ») : ailleurs, marquer fait relire la
    carte de Nextcloud et écraser la fiche.
    """
    _with_pronouns(env).with_context(skip_nc_contact_sync=True).write({"x_carddav_etag": False})


def uninstall_hook(env):
    """À la désinstallation, les cartes qui portaient des pronoms sont
    repoussées sans eux."""
    _with_pronouns(env).with_context(skip_nc_contact_sync=True).write({"x_carddav_etag": False})
