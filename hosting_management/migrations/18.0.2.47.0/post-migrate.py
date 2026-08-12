# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Marqueur de version 18.0.2.47.0 — Gestion des DID + intégration VOIP.ms.

Aucune migration de données nécessaire : les nouveaux modèles
(hosting.voip.did / hosting.voip.cdr / hosting.voip.transaction) et leurs
champs sont créés automatiquement par l'ORM lors de la mise à jour. La
synchronisation initiale se déclenche via Configuration > Paramètres une fois
les identifiants VOIP.ms saisis et l'intégration activée.
"""


def migrate(cr, version):
    pass
