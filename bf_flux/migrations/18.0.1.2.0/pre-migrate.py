# -*- coding: utf-8 -*-
"""La cadence passe des heures aux minutes.

Sans cette reprise, chaque source existante prendrait le défaut de la nouvelle
colonne (120 min) au lieu de la cadence qu'on lui avait réglée.
"""


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'bf_flux_source' AND column_name = 'cadence_heures'
    """)
    if not cr.fetchone():
        return
    cr.execute("ALTER TABLE bf_flux_source ADD COLUMN IF NOT EXISTS cadence_minutes integer")
    cr.execute("""
        UPDATE bf_flux_source SET cadence_minutes = GREATEST(cadence_heures, 1) * 60
         WHERE cadence_minutes IS NULL
    """)
    # L'ancienne contrainte porte le même nom et vise l'ancienne colonne.
    cr.execute("ALTER TABLE bf_flux_source DROP CONSTRAINT IF EXISTS bf_flux_source_cadence_positive")
