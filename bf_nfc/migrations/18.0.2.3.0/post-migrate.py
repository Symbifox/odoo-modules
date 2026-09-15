"""18.0.2.3.0 : les clés des puces sortent des paramètres système.

``bf_nfc.sdm_meta_key`` / ``bf_nfc.sdm_file_key`` (en clair) deviennent une paire
chiffrée par société dans ``bf.nfc.sdm.key``, et les paramètres sont effacés.

⚠️ La liste des types de fiche, elle, se sème dans ``end-migrate`` : ici, pendant la
montée de ``bf_nfc``, les modules qui n'en dépendent pas (Projet) ne sont pas encore
chargés, et ``project.task`` serait écarté comme « absent de cette base » alors qu'il
y est. Vécu à l'essai de montée : seul ``res.partner`` avait été semé, et l'ancien
paramètre effacé quand même.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["bf.nfc.sdm.key"]._migrer_les_parametres()
