# Part of bf_recruitment_mail. Voir LICENSE.
"""Rejouer le nettoyage des traductions après une mise à niveau.

Le `post_init_hook` ne tourne qu'à l'installation. Un locataire qui avait déjà
le module, ou chez qui une langue a été réinstallée depuis, garde des valeurs
traduites qui masquent le texte de ce module. Voir hooks.py.
"""

from odoo.addons.bf_recruitment_mail.hooks import _reduire_a_la_source


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    _reduire_a_la_source(env)
