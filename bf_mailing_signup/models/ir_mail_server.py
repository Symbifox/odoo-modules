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

import email.policy
import re

from odoo import models

from ..controllers.main import ENTETE_SANS_SUIVI

# 🔴 Linéaire, et seulement ainsi. Une expression qui cherche l'attribut DANS la balise
# (`<img[^<>]*data-odoo-tracking-email=…[^<>]*>`) relit tout le reste de la balise à chaque
# occurrence de l'attribut : un corps de `<img ` + `data-odoo-tracking-email="1" ` répétés sans
# `>` coûtait 20 secondes pour 464 Ko, et `build_email` est appelable par tout usager connecté
# (portail compris, par `call_kw`). On isole d'abord chaque balise `<img …>` (une balise ne contient
# jamais de `<` : chaque départ s'arrête au `<` suivant), puis on cherche l'attribut par une
# recherche de sous-chaîne.
_IMG_RE = re.compile(r'<img[^<>]*>')
ATTRIBUT_PIXEL = "data-odoo-tracking-email="


def _retirer_pixel(corps):
    return _IMG_RE.sub(lambda m: "" if ATTRIBUT_PIXEL in m.group(0) else m.group(0), corps or "")


# Posés par `mail_tracking` quand son `build_email` passe avant le nôtre.
_ENTETES_RETIRES = frozenset((ENTETE_SANS_SUIVI, "X-Odoo-MailTracking-ID", "X-Odoo-Database"))


# 🔴 Les en-têtes de désabonnement en un clic (RFC 2369 et 8058) ne se plient JAMAIS. La politique
# SMTP d'Odoo plie tout en-tête non structuré de plus de 78 caractères, et comme une URL n'a pas
# d'espace, Python la coupe en mots encodés RFC 2047 (`=?utf-8?q?=3Chttp=3A//…`) : un client de
# messagerie ne reconnaît alors plus le lien, et le bouton « se désabonner » n'apparaît pas. Le
# cœur fait déjà la même chose pour Message-Id ; on étend la liste à ces deux-là.
ENTETES_SANS_PLIAGE = frozenset(("list-unsubscribe", "list-unsubscribe-post"))
_SANS_PLIAGE = email.policy.SMTP.clone(max_line_length=None)


class _PolitiqueSansPliageDesabonnement(type(email.policy.SMTP)):
    def _fold(self, name, value, *args, **kwargs):
        if name.lower() in ENTETES_SANS_PLIAGE:
            return _SANS_PLIAGE._fold(name, value, *args, **kwargs)
        return super()._fold(name, value, *args, **kwargs)


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    def build_email(self, email_from, email_to, subject, body, email_cc=None,
                    email_bcc=None, reply_to=False, attachments=None, message_id=None,
                    references=None, object_id=False, subtype="plain", headers=None,
                    body_alternative=None, subtype_alternative="plain"):
        # ⚠️ Rien d'écrit en base ici, jamais (voir l'en-tête du fichier).
        desabonnement = any(k.lower() in ENTETES_SANS_PLIAGE for k in (headers or {}))
        if headers and ENTETE_SANS_SUIVI in headers:
            headers = {k: v for k, v in headers.items() if k not in _ENTETES_RETIRES}
            body = _retirer_pixel(body)
            if body_alternative:
                body_alternative = _retirer_pixel(body_alternative)
        message = super().build_email(
            email_from, email_to, subject, body, email_cc=email_cc, email_bcc=email_bcc,
            reply_to=reply_to, attachments=attachments, message_id=message_id,
            references=references, object_id=object_id, subtype=subtype, headers=headers,
            body_alternative=body_alternative, subtype_alternative=subtype_alternative)
        if desabonnement:
            message.policy = _PolitiqueSansPliageDesabonnement(linesep=message.policy.linesep)
        return message
