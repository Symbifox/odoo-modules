def _with_pronouns(env):
    return env["res.partner"].sudo().with_context(active_test=False).search([
        ("is_company", "=", False), ("bf_dav_href", "!=", False),
        "|", ("bf_pronouns", "!=", False), ("bf_grammatical_gender", "!=", False),
    ])


def post_init_hook(env):
    """Signale aux téléphones les cartes qui portent désormais des pronoms.

    Sans ce journal, un client en synchronisation ne redemanderait pas une
    carte qu'aucune écriture n'a touchée.
    """
    _with_pronouns(env)._bf_dav_journal()


def uninstall_hook(env):
    """À la désinstallation, l'ETag de ces cartes redevient celui de
    `bf_dav_server` : on les signale, sinon les téléphones gardent des pronoms
    périmés."""
    _with_pronouns(env)._bf_dav_journal()
