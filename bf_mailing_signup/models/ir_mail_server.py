# -*- coding: utf-8 -*-
"""Aucun pixel ni en-tête de suivi sur un courriel marqué `X-BF-Sans-Suivi`.

Le module OCA `mail_tracking`, quand il est installé, colle une image de suivi à
la fin de TOUT courriel sortant (`data-odoo-tracking-email`) et pose ses en-têtes
(`X-Odoo-MailTracking-ID`, `X-Odoo-Database`). Une confirmation d'inscription part
vers une adresse qui n'a peut-être rien demandé, et l'avis interne nomme cette
adresse : pour ces courriels, l'image est retirée du corps, et ni l'en-tête marqueur
ni ceux de `mail_tracking` ne sortent. Le travail tient quel que soit l'ordre de
chargement : appelé AVANT `mail_tracking`, le pixel est retiré et `mail_tracking` ne
trouve plus rien à relever ; appelé APRÈS, ses en-têtes sont retirés ici.

🔴 `build_email` est une méthode PUBLIQUE : tout usager connecté, portail compris,
l'appelle par `/web/dataset/call_kw/ir.mail_server/build_email`. Une version antérieure y notait en `sudo`, pour la purge, tout identifiant de relevé lu
dans le corps ou l'en-tête reçus : un usager portail faisait effacer n'importe quel
relevé de l'instance à sept jours, et un appel de 2 Mo écrivait 50 000 lignes. Elle ne fait
donc plus que RETIRER, et n'écrit rien. Les relevés se notent dans un chemin privé de
l'envoi, par le `mail_id` des `mail.mail` marqués (models/mail_mail.py).
"""

import re

from odoo import models

from ..controllers.main import ENTETE_SANS_SUIVI

_PIXEL_RE = re.compile(r'<img[^>]*data-odoo-tracking-email=["\'][0-9]*["\'][^>]*>')
# Posés par `mail_tracking` quand son `build_email` passe avant le nôtre.
_ENTETES_RETIRES = frozenset((ENTETE_SANS_SUIVI, "X-Odoo-MailTracking-ID", "X-Odoo-Database"))


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    def build_email(self, email_from, email_to, subject, body, email_cc=None,
                    email_bcc=None, reply_to=False, attachments=None, message_id=None,
                    references=None, object_id=False, subtype="plain", headers=None,
                    body_alternative=None, subtype_alternative="plain"):
        # ⚠️ Rien d'écrit ici, jamais (voir l'en-tête du fichier).
        if headers and ENTETE_SANS_SUIVI in headers:
            headers = {k: v for k, v in headers.items() if k not in _ENTETES_RETIRES}
            body = _PIXEL_RE.sub("", body or "")
            if body_alternative:
                body_alternative = _PIXEL_RE.sub("", body_alternative)
        return super().build_email(
            email_from, email_to, subject, body, email_cc=email_cc, email_bcc=email_bcc,
            reply_to=reply_to, attachments=attachments, message_id=message_id,
            references=references, object_id=object_id, subtype=subtype, headers=headers,
            body_alternative=body_alternative, subtype_alternative=subtype_alternative)
