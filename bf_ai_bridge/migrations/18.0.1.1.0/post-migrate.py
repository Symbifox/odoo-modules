"""Reprend le locataire sous son nouveau nom sur un système DÉJÀ installé.

Le ``post_init_hook`` ne tourne qu'à l'installation : sans cette migration, les
locataires qui portent déjà ce module garderaient leur locataire déclaré
uniquement sous l'ancienne clé. Rien ne casserait (``tenant()`` la relit), mais
les Paramètres montreraient une case vide sur une valeur pourtant posée.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["bf.ai.bridge"]._adopt_legacy_tenant()
