# -*- coding: utf-8 -*-
"""Chaque réaction déjà posée devient un « J'aime », et la colonne se verrouille.

⚠️ Pourquoi ici et pas dans le pré-script : la réaction « J'aime » n'existe pas
encore avant le chargement des données du module. Conséquence, Odoo crée la
colonne `reaction_id` pendant le chargement, échoue à y poser NOT NULL parce
qu'elle est vide, et le dit dans une ligne de journal qu'on peut lire comme une
alerte. Ce script est la deuxième moitié : il remplit, puis il verrouille.

🔴 Le verrou est posé explicitement, et VÉRIFIÉ. Laissé à la prochaine mise à
jour, il aurait manqué en base pendant un temps que personne ne mesure, et la
seule garde aurait été celle de l'ORM.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    cr.execute("SELECT count(*) FROM bf_babillard_geste WHERE reaction_id IS NULL")
    orphelins = cr.fetchone()[0]

    if orphelins:
        defaut = env.ref("bf_babillard.reaction_jaime", raise_if_not_found=False)
        if not defaut:
            # Ceinture : le jeu semé a été touché avant la montée.
            defaut = env["bf.babillard.reaction"].search(
                [("active", "=", True)], order="sequence, id", limit=1)
        if not defaut:
            defaut = env["bf.babillard.reaction"].create({
                "name": "J'aime", "symbole": "👍", "sequence": 10,
                "predefinie": True,
            })
        cr.execute("UPDATE bf_babillard_geste SET reaction_id = %s "
                   "WHERE reaction_id IS NULL", (defaut.id,))
        _logger.info("bf_babillard : %s réaction(s) rattachée(s) à « %s »",
                     orphelins, defaut.name)

    cr.execute("ALTER TABLE bf_babillard_geste "
               "ALTER COLUMN reaction_id SET NOT NULL")

    # 🔴 On MESURE le verrou plutôt que de le supposer : un `ALTER` avalé par un
    # point de reprise laisserait la colonne nullable sans rien dire.
    cr.execute("""
        SELECT is_nullable FROM information_schema.columns
         WHERE table_name = 'bf_babillard_geste' AND column_name = 'reaction_id'
    """)
    nullable = cr.fetchone()
    if not nullable or nullable[0] != "NO":
        raise AssertionError(
            "bf_babillard : reaction_id est resté nullable après la montée")

    cr.execute("""
        SELECT conname FROM pg_constraint
         WHERE conrelid = 'bf_babillard_geste'::regclass AND contype = 'u'
    """)
    _logger.info("bf_babillard : unicité en place %s",
                 [ligne[0] for ligne in cr.fetchall()])
