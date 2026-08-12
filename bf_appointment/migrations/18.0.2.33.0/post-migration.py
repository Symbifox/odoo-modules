"""Ventilation du « Modifications Deadline » OCA en deux réglages.

Avant 2.33.0, `modifications_deadline` gouvernait à la fois le plancher de
disponibilité (préavis min avant réservation) ET le verrou de modification.
On a scindé le second dans le nouveau champ `modification_lock_hours`.

Pour préserver le comportement existant, on initialise le verrou de chaque type
à la valeur combinée qu'il portait jusqu'ici (= `modifications_deadline`),
plutôt qu'au défaut du champ (2 h). Idempotent, ne s'exécute qu'à cette montée
de version.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE resource_booking_type
        SET modification_lock_hours = modifications_deadline
        WHERE modifications_deadline IS NOT NULL
        """
    )
