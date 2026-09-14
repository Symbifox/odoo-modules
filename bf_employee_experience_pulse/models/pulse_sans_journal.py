"""Le garde qui rend l'anonymat vrai en BASE, pas seulement en Python.

🔴 Retirer un champ d'un modèle ne retire pas sa colonne. Odoo ne fait jamais
tomber une colonne : il la laisse. Une version de ce module qui partirait un
jour avec `_log_access = True`, puis serait corrigée, laisserait derrière elle
`create_uid` et `create_date` remplies pour toutes les lignes écrites entre
les deux. La promesse d'anonymat serait fausse dans la base et juste dans le
code, ce qui est la pire des deux combinaisons.

Découvert à l'essai, et pas par une relecture : la passe de mutation avait
posé `_log_access = True` le temps d'une passe, et les colonnes sont restées
après la remise en état. L'essai qui lit `information_schema` a vu ce qu'aucun
essai de modèle n'aurait vu.

`init()` tourne après la création des tables, à CHAQUE mise à jour du module,
donc le ménage se refait tout seul.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)

COLONNES_DE_JOURNAL = ("create_uid", "create_date", "write_uid", "write_date")


class SansJournal(models.AbstractModel):
    """À mêler à tout modèle qui porte `_log_access = False`."""

    _name = "bf.ex.pulse.sans.journal"
    _description = "Modèle sans colonnes de journal"
    # 🔴 Indispensable ici aussi. Un mixin abstrait qui garde le défaut
    # `_log_access = True` porte les quatre champs magiques et les transmet à
    # qui l'hérite : le garde créait lui-même la colonne qu'il retire ensuite.
    # Vu sur une base NEUVE, où `create_uid` apparaissait à l'installation.
    _log_access = False

    def init(self):
        super().init()
        # Le contexte n'est jamais posé en production. Il existe pour qu'un
        # essai puisse observer ce que fait `init()` sur une table à lui, au
        # lieu de lire le source de la méthode, ce qu'un `if False:` trompe.
        self._bf_pulse_drop_log_columns(
            table=self.env.context.get("bf_pulse_table_d_essai"))

    def _bf_pulse_drop_log_columns(self, table=None):
        """Fait tomber les colonnes de journal de `table` (la sienne par défaut).

        Le paramètre existe pour que l'essai puisse éprouver le ménage sur une
        table à lui. ⚠️ Un essai qui pose une colonne sur la VRAIE table la
        laisse derrière lui : le DDL survit au démontage, et la passe suivante
        se met à parler d'un résidu qui n'est pas un défaut du module.
        """
        if self._log_access:
            raise ValueError(
                "%s mêle bf.ex.pulse.sans.journal tout en gardant "
                "_log_access = True." % self._name
            )
        table = table or self._table
        self.env.cr.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_name = %s AND column_name IN %s
            """,
            (table, COLONNES_DE_JOURNAL),
        )
        restantes = [row[0] for row in self.env.cr.fetchall()]
        for colonne in restantes:
            _logger.warning(
                "Pulse : la table %s portait encore %s, colonne retirée.",
                table, colonne,
            )
            self.env.cr.execute(
                'ALTER TABLE "%s" DROP COLUMN "%s"' % (table, colonne)
            )
        return restantes
