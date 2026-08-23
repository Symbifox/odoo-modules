"""Retire les colonnes des trois champs devenus calculés non stockés.

Rendre un champ non stocké empêche les écritures FUTURES. Ça ne retire ni la
colonne ni ce qu'elle contient déjà : Odoo ne supprime jamais une colonne de
lui-même. Or c'est précisément le contenu qui posait problème.

* `bf_appointment_onetime_wizard.url` porte des liens de réservation, qui
  valent jeton d'accès — qui les a peut réserver.
* `res_config_settings.bf_appointment_nc_talk_password` a porté le mot de
  passe d'application Nextcloud EN CLAIR, le temps que le ramasse-miettes des
  modèles transitoires passe (une heure par défaut).

Les deux tables sont transitoires, donc leurs lignes ont sans doute déjà été
balayées. « Sans doute » ne suffit pas quand il s'agit d'un secret, et une
sauvegarde prise entre l'enregistrement et le balayage, elle, l'a gardé.
On retire la colonne : c'est la seule façon de savoir.
"""

import logging

_logger = logging.getLogger(__name__)

COLONNES = [
    ("bf_appointment_onetime_wizard", "url"),
    ("bf_appointment_onetime_wizard", "expires_display"),
    ("res_config_settings", "bf_appointment_nc_talk_password"),
]


def migrate(cr, version):
    if not version:
        return
    for table, colonne in COLONNES:
        cr.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            """,
            (table, colonne),
        )
        if not cr.fetchone():
            continue
        # Guillemets : `url` n'est pas réservé, mais la liste est destinée à
        # grandir et un nom réservé casserait la requête sans prévenir.
        cr.execute('ALTER TABLE "%s" DROP COLUMN IF EXISTS "%s"' % (table, colonne))
        _logger.info(
            "bf_appointment 2.50.0 : colonne %s.%s retirée (champ désormais "
            "calculé non stocké)", table, colonne,
        )
