"""Écrit les sept gabarits que la 18.0.3.26.0 change, et eux seuls.

Quatre gabarits d'accès rejoignent les surcharges (auth_signup, auth_totp_mail,
auth_totp_mail_enforce) ; trois du calendrier perdent leur coquille, parce
qu'ils partent par le gabarit light, désormais habillé (carte dans la carte).
Ils sont noupdate et leurs champs traduits sont en jsonb par langue : seul
post_init_hook les écrit dans toutes les langues actives.

On ne rejoue PAS le crochet entier : il réécrirait les quinze autres gabarits
surchargés et l'Avis de retard, et une retouche faite à la main chez un
locataire disparaîtrait sans un mot (même raison que la 18.0.3.25.1). Un
gabarit d'accès retouché à la main est gardé tel quel (journal). Mesuré avant
la pose : les trois du calendrier n'avaient pas bougé depuis le dernier passage
du crochet, chez aucun locataire.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env, only={
        'auth_signup.set_password_email',
        'auth_signup.mail_template_user_signup_account_created',
        'auth_totp_mail.mail_template_totp_invite',
        'auth_totp_mail_enforce.mail_template_totp_mail_code',
        'calendar.calendar_template_meeting_invitation',
        'calendar.calendar_template_meeting_changedate',
        'calendar.calendar_template_meeting_reminder',
    })
