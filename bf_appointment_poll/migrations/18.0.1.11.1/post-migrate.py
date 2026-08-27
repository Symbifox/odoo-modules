# -*- coding: utf-8 -*-
"""Recharge les gabarits de courriel, protégés par `noupdate="1"`.

🔴 Sans ça, le correctif du fuseau ne serait jamais arrivé jusqu'aux gens. Le
gabarit de confirmation appelait `scheduled_display()` sans destinataire, donc
tout le monde lisait la même heure. La version corrigée passe le participant —
mais un `-u` ne réécrit PAS un enregistrement `noupdate`, et l'échec est
silencieux : la version du module monte, aucune erreur, et la base garde
l'ancien texte. Constaté deux fois sur ce module.

⚠️ Remettre `ir_model_data.noupdate` à faux en pre-migrate NE MARCHE PAS :
`convert.py` teste l'attribut du FICHIER et sort avant de lire la base. Seul
`mode="init"` traverse le garde-fou. `noupdate=True` repose le drapeau derrière
soi, pour que les retouches faites ensuite dans l'interface restent protégées.
"""

from odoo import SUPERUSER_ID, api
from odoo.tools.convert import convert_file


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    convert_file(
        env,
        "bf_appointment_poll",
        "data/poll_mail_templates.xml",
        idref=None,
        mode="init",
        noupdate=True,
    )
