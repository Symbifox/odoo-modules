# -*- coding: utf-8 -*-
"""Les libellés d'écart déjà semés se lisent comme ceux du calcul neuf.

Deux défauts du plan de transformation imprimé, réglés dans le calcul en
18.0.5.0.6 et réécrits ici pour ce qui est déjà en base, sans attendre le
prochain semis :

* Lexend n'a pas de glyphe pour « → » (U+2192) : chaque changement sortait
  avec un carré blanc. Le calcul écrit désormais « vers ».
* Un changement de ton montrait les clés techniques (« risk vers ai ») au lieu
  des mots (« fragilité vers piste d'amélioration »).

Les écarts saisis à la main ne sont pas touchés : leur libellé est le texte
d'une personne.
"""
import logging
import re

_logger = logging.getLogger(__name__)

TONS = {"ai": "piste d'amélioration", "risk": "fragilité", "aucun": "aucun"}
TON = re.compile(r"… » : (risk|ai|aucun) (?:→|vers) (risk|ai|aucun) dans « ")


def migrate(cr, version):
    cr.execute("""
        UPDATE bf_process_ecart
           SET libelle = replace(libelle, %s, %s)
         WHERE source = 'seme'
           AND libelle LIKE %s
    """, (" → ", " vers ", "% → %"))
    _logger.info("bf_process : %s libellé(s) d'écart sans flèche", cr.rowcount)

    cr.execute("""
        SELECT id, libelle FROM bf_process_ecart
         WHERE source = 'seme' AND genre = 'ton'
    """)
    tons = 0
    for ecart_id, libelle in cr.fetchall():
        neuf = TON.sub(lambda m: "… » : %s vers %s dans « " % (
            TONS[m[1]], TONS[m[2]]), libelle, count=1)
        if neuf != libelle:
            cr.execute("UPDATE bf_process_ecart SET libelle = %s WHERE id = %s",
                       (neuf, ecart_id))
            tons += 1
    _logger.info("bf_process : %s changement(s) de ton dits en mots", tons)
