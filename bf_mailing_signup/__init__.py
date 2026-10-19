from . import controllers
from . import models


def uninstall_hook(env):
    """Effacer les relevés `mail_tracking` notés : leur purge part avec le module.

    La table des relevés à effacer (`bf.mailing.signup.tracking`) et la tâche qui la
    purge disparaissent à la désinstallation ; sans ce crochet, les relevés déjà
    notés (adresse du destinataire, ouvertures) restaient à vie. Tous, quel que soit leur âge : plus personne ne les purgera.
    """
    if "mail.tracking.email" not in env:
        return
    env.cr.execute("SELECT DISTINCT tracking_email_id FROM bf_mailing_signup_tracking")
    ids = [r[0] for r in env.cr.fetchall()]
    if ids:
        env["mail.tracking.email"].sudo().browse(ids).exists().unlink()
