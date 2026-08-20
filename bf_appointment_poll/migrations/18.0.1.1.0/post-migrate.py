# -*- coding: utf-8 -*-
"""Rafraîchit les deux gabarits de courriel, une fois, volontairement.

Les gabarits sont déclarés `noupdate="1"` pour qu'une retouche faite dans
l'interface survive aux montées de version. La 1.0.0 avait pourtant livré des
porteurs minimaux, sans marque, qui ne doivent pas atteindre un client : il
faut donc les remplacer.

⚠️ Le réflexe habituel — remettre `ir_model_data.noupdate` à faux dans un
pre-migrate — NE MARCHE PAS ici. `tools/convert.py` teste l'attribut
`noupdate` du FICHIER et saute l'enregistrement avant même de regarder le
drapeau en base :

    if self.noupdate and self.mode != 'init':
        ...
        return None   # la seconde vérification, celle qui lit la base,
                      # n'est jamais atteinte

Vérifié sur banc : avec le pre-migrate, la migration tournait, la version
passait, et le gabarit restait l'ancien — un succès apparent, silencieux.

La seule voie qui traverse le garde-fou est de recharger le fichier en mode
`init`. On vise nommément ce seul fichier ; `noupdate=True` repose le drapeau
derrière nous, donc les retouches ultérieures restent protégées.
"""

import logging

from odoo import SUPERUSER_ID, api
from odoo.tools.convert import convert_file

_logger = logging.getLogger(__name__)

FICHIER = "data/poll_mail_templates.xml"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    convert_file(
        env,
        "bf_appointment_poll",
        FICHIER,
        idref=None,
        mode="init",
        noupdate=True,
    )
    _logger.info("Gabarits du sondage rafraîchis depuis %s", FICHIER)
