"""Le gabarit d'avis passe à l'habillage de la société.

Il vit dans un bloc `noupdate="1"`. 🔴 Lever le drapeau de `ir_model_data` ne
suffit PAS : `xml_import` saute un enregistrement existant dès que le FICHIER
déclare `noupdate`, quel que soit ce drapeau (mesuré : l'export de la base
rendait encore l'ancien corps après la montée). Seul le mode `init` réécrit un
enregistrement `noupdate` : on recharge donc ce fichier-là, en `init`, une fois.
"""
from odoo import SUPERUSER_ID, api
from odoo.tools import convert_file


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    convert_file(env, "bf_property_cx", "data/mail_template_data.xml", None,
                 mode="init", noupdate=True, kind="data")
