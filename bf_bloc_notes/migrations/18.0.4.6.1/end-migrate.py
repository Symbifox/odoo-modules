"""18.0.4.6.1 : le francais du libelle et de l'aide de `is_shared`.

🔴 Un `-u` n'ecrase jamais une traduction existante. La source anglaise de
`bf.note.is_shared` passe de « Shared » a « Shared with all internal users »,
et le francais garde « Partagee » : la case ne dirait toujours pas, en
francais, qu'elle ouvre la note a TOUS les internes, ce qui est tout l'objet
de cette version. Le rechargement des catalogues de la pose
(`overwrite=False`) ne la remplace pas non plus.

`end` et non `post` : Odoo charge le catalogue du module APRES le `post`, donc
c'est ici seulement qu'on voit ce que le chargement a laisse. On ne remplace
une valeur que si elle porte ENCORE le texte d'avant, mot pour mot : une
retouche d'operateur reste intacte.

⚠️ Ne leve jamais : une migration qui leve ne se rejoue pas.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

PERIMES = {
    "field_description": (
        "Partagée",
        "Partagée avec tous les internes",
    ),
    "help": (
        "Si cochée, les autres utilisateurs internes peuvent voir cette note.",
        "Si cochée, tous les internes peuvent lire cette note. Pour la partager avec "
        "certaines personnes seulement, utilise « Partager… » ou l'onglet Partage.",
    ),
}


def migrate(cr, version):
    if not version:
        return
    try:
        env = api.Environment(cr, SUPERUSER_ID, {})
        champ = env["ir.model.fields"]._get("bf.note", "is_shared")
        if not champ:
            return
        for nom, (ancien, neuf) in PERIMES.items():
            # Le creneau BRUT : lire par l'ORM en fr_CA retomberait sur
            # l'anglais quand le creneau manque, et la comparaison mentirait.
            cr.execute(f"SELECT {nom}->>'fr_CA' FROM ir_model_fields WHERE id = %s",
                       (champ.id,))
            actuel = cr.fetchone()[0]
            if actuel == ancien:
                champ.update_field_translations(nom, {"fr_CA": neuf})
                _logger.info("bf_bloc_notes 4.6.1 : %s de is_shared remis a jour en fr_CA", nom)
    except Exception:  # noqa: BLE001 - une migration qui leve ne se rejoue pas
        _logger.exception("bf_bloc_notes 4.6.1 : libelle francais de is_shared non remis a jour")
