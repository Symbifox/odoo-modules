"""18.0.4.6.2 : l'aide francaise de `is_shared` vouvoie.

La 4.6.1 l'avait ecrite au tutoiement (« utilise « Partager… » »), alors que
le reste du module vouvoie. Un `-u` n'ecrase jamais une traduction existante
de champ : sans cette migration, l'aide garderait le tutoiement chez qui a
monte en 4.6.1.

`end` et non `post` : le catalogue du module se charge APRES le `post`. On ne
remplace la valeur que si elle porte ENCORE le texte de 4.6.1, mot pour mot ;
une retouche d'operateur reste intacte. Ne leve jamais : une migration qui
leve ne se rejoue pas.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

ANCIEN = (
    "Si cochée, tous les internes peuvent lire cette note. Pour la partager avec "
    "certaines personnes seulement, utilise « Partager… » ou l'onglet Partage."
)
NEUF = (
    "Si cochée, tous les internes peuvent lire cette note. Pour la partager avec "
    "certaines personnes seulement, utilisez « Partager… » ou l'onglet Partage."
)


def migrate(cr, version):
    if not version:
        return
    try:
        env = api.Environment(cr, SUPERUSER_ID, {})
        champ = env["ir.model.fields"]._get("bf.note", "is_shared")
        if not champ:
            return
        # Le creneau BRUT : l'ORM retomberait sur l'anglais s'il manque.
        cr.execute("SELECT help->>'fr_CA' FROM ir_model_fields WHERE id = %s", (champ.id,))
        if cr.fetchone()[0] == ANCIEN:
            champ.update_field_translations("help", {"fr_CA": NEUF})
            _logger.info("bf_bloc_notes 4.6.2 : aide fr_CA de is_shared au vouvoiement")
    except Exception:  # noqa: BLE001 - une migration qui leve ne se rejoue pas
        _logger.exception("bf_bloc_notes 4.6.2 : aide fr_CA de is_shared non remise a jour")
