"""L'invitation du cœur nomme l'ordre du jour de la rencontre.

Les gabarits surchargés ne sont écrits que par `post_init_hook` : leurs
originaux sont en `noupdate`, le chargeur de données d'Odoo ne les touche
jamais. On ne rejoue PAS le crochet entier, qui réécrirait les quinze autres
gabarits et l'« Avis de retard », et ferait disparaître sans un mot une
retouche faite à la main chez un locataire (même raison que la 18.0.3.25.1 et
la 18.0.3.26.0). Seule l'invitation du calendrier est reprise.

Le crochet garde de lui-même un gabarit `bf_garde_retouche="1"` retouché à la
main sur la base (`_retouched_by_tenant`), et il écrit dans toutes les langues
actives : le fichier français pour les langues françaises, l'anglais pour les
autres.

La ligne ajoutée ne paraît que si la rencontre porte un ordre du jour ET que
l'invité est un collègue interne : le corps est rendu une fois par invité
(`_send_mail_to_attendees`), et `calendar.event._bf_agenda_link(partner)` rend
False pour un invité du dehors.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env, only={'calendar.calendar_template_meeting_invitation'})
