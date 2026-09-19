# Part of bf_oe2oc. See LICENSE file for full copyright and licensing details.
"""Rattrape l'étiquette des deux champs `name`, restée en anglais à l'écran.

La 18.0.1.0.0 déclarait `name = fields.Char(required=True)` sans `string=` :
Odoo prend alors le nom technique et l'affiche « Name », y compris en
français. La 18.0.1.0.1 pose `string="Nom"`.

🔴 Le `-u` ne suffit pas. `ir.model.fields.field_description` est un champ
traduit stocké en jsonb, et **Odoo n'écrase pas une traduction déjà en base**.
La montée met donc à jour la case `en_US` — qui devient « Nom », la nouvelle
source — et laisse `fr_CA` sur l'ancienne valeur « Name ». Résultat : l'écran
français continue d'afficher « Name » après la montée, et seule une
installation neuve est correcte.

Ce script vide les cases de traduction devenues fausses, pour que le chargement
des fichiers `.po` les repose. Il ne touche qu'aux deux champs concernés, et
qu'aux cases qui portent encore l'ancienne valeur : une traduction que
quelqu'un aurait corrigée à la main survit.
"""

import logging

_logger = logging.getLogger(__name__)

CHAMPS = (("bf.oe2oc.bundle", "name"), ("bf.oe2oc.check", "name"))
PERIME = "Name"


def migrate(cr, version):
    if not version:
        return
    for modele, champ in CHAMPS:
        cr.execute("""
            UPDATE ir_model_fields f
               SET field_description = f.field_description - 'fr_CA' - 'en_CA'
              FROM ir_model m
             WHERE m.id = f.model_id
               AND m.model = %s AND f.name = %s
               AND (f.field_description->>'fr_CA' = %s
                 OR f.field_description->>'en_CA' = %s)
        """, (modele, champ, PERIME, PERIME))
        if cr.rowcount:
            _logger.info("bf_oe2oc : étiquette « %s » retirée de %s.%s",
                         PERIME, modele, champ)
