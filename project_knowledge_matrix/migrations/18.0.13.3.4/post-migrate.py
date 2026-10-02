"""Figer le texte actuel des versions publiées qui n'ont pas de copie.

La 18.0.13.3.4 fait imprimer au PDF d'un document l'instantané figé de sa
version publiée, et non plus le corps vivant. Une version publiée quand le
corps vivait encore sur Nextcloud, puis rédigé dans Odoo, n'a pas d'instantané :
son PDF imprimait le corps vivant sous son numéro, avec la mention « non figé ».

Décision retenue : figer le texte actuel comme copie de
ces versions, par le même geste qu'une publication
(``project.document.version._freeze_body``). Seules les versions à l'état
« publiée » sans aucune section figée, d'un document au corps rédigé dans Odoo.
Ni l'état, ni la date de publication, ni les distributions ne bougent ; rien ne
part par courriel. Une ligne de journal par document figé. Rejouée, la passe ne
trouve plus rien à figer.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['project.document.version']._freeze_unfrozen_released()
