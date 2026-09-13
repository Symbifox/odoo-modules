"""Le désabonnement en un clic, là où le courriel est ouvert.

Gmail a sorti « Manage subscriptions » en juillet 2025 : tous les expéditeurs
d'infolettres, classés par fréquence, avec un désabonnement en un clic. Chez
nous la matière était déjà là, dans les en-têtes : mesuré sur une base réelle le
2026-09-13, **1 234 courriels reçus portent `List-Unsubscribe`, dont 1 197
portent aussi `List-Unsubscribe-Post`**, c'est-à-dire le clic unique de la
RFC 8058.

⚠️ Et ça ne réglera PAS l'encombrement de la boîte. Les lignes marquées en
masse en sont déjà toutes sorties par les règles ; ce qui reste est du courrier
transactionnel de machine, qui ne porte aucun de ces en-têtes et ne se
désabonne pas. Ce bouton nettoie l'archive et les infolettres, pas la boîte.

⚠️ Le POST de la RFC 8058 sort de notre serveur vers une URL écrite par
l'expéditeur : c'est un puits à SSRF aveugle si on ne le garde pas. On réutilise
`safe_push_endpoint`, la garde déjà écrite pour les points de poussée, plutôt
que d'en écrire une deuxième qui finirait par dire autre chose.
"""
import logging
import re

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .push_transport import safe_push_endpoint

_logger = logging.getLogger(__name__)

# `List-Unsubscribe: <https://…>, <mailto:…>` — la RFC 2369 veut les chevrons.
_LIEN = re.compile(r"<([^>]+)>")
TIMEOUT = 10


class BfEmailUnsubscribe(models.Model):
    _inherit = "bf.email"

    unsubscribe_url = fields.Char(
        string="Lien de désabonnement",
        compute="_compute_unsubscribe",
        store=True,
        help="Le premier lien https du `List-Unsubscribe`, s'il y en a un.",
    )
    unsubscribe_mailto = fields.Char(
        string="Adresse de désabonnement",
        compute="_compute_unsubscribe",
        store=True,
    )
    unsubscribe_one_click = fields.Boolean(
        string="Désabonnement en un clic",
        compute="_compute_unsubscribe",
        store=True,
        help="L'expéditeur annonce `List-Unsubscribe-Post` (RFC 8058) : un "
             "POST suffit, sans ouvrir de page ni confirmer.",
    )

    @api.depends("raw_headers")
    def _compute_unsubscribe(self):
        for rec in self:
            entete = rec._header_value("List-Unsubscribe")
            post = rec._header_value("List-Unsubscribe-Post")
            url = mailto = False
            for lien in _LIEN.findall(entete or ""):
                lien = lien.strip()
                if lien.lower().startswith("mailto:") and not mailto:
                    mailto = lien
                elif lien.lower().startswith("https://") and not url:
                    url = lien
            rec.unsubscribe_url = url or False
            rec.unsubscribe_mailto = mailto or False
            rec.unsubscribe_one_click = bool(
                url and "one-click" in (post or "").lower())

    def _header_value(self, name):
        """La valeur d'un en-tête dans `raw_headers`, lignes repliées comprises.

        ⚠️ Un en-tête peut se poursuivre sur la ligne suivante, qui commence
        alors par une espace ou une tabulation, et `List-Unsubscribe` en
        contient souvent deux. Lire ligne par ligne sans recoller rendrait un
        lien coupé en deux, donc inutilisable.
        """
        self.ensure_one()
        blob = self.raw_headers or ""
        if not blob:
            return ""
        voulu = name.strip().lower().rstrip(":")
        trouve = []
        courant = None
        for ligne in blob.splitlines():
            if ligne[:1] in (" ", "\t"):
                if courant is not None:
                    courant.append(ligne.strip())
                continue
            if courant is not None:
                trouve.append(" ".join(courant))
                courant = None
            if ":" in ligne and ligne.split(":", 1)[0].strip().lower() == voulu:
                courant = [ligne.split(":", 1)[1].strip()]
        if courant is not None:
            trouve.append(" ".join(courant))
        return " ".join(trouve)

    def action_unsubscribe(self):
        """Se désabonne de cet expéditeur, par le chemin le plus court.

        Trois chemins, dans l'ordre : le POST de la RFC 8058 quand
        l'expéditeur l'annonce, un courriel vide à l'adresse de désabonnement,
        ou l'ouverture de la page. Les deux premiers ne demandent rien au
        lecteur ; le troisième lui rend la main.
        """
        self.ensure_one()
        if self.unsubscribe_one_click and self.unsubscribe_url:
            return self._unsubscribe_one_click()
        if self.unsubscribe_mailto:
            return self._unsubscribe_by_mail()
        if self.unsubscribe_url:
            return {"type": "ir.actions.act_url",
                    "url": self.unsubscribe_url, "target": "new"}
        raise UserError(_(
            "Ce courriel ne porte aucun lien de désabonnement. Une règle sur "
            "l'expéditeur le sortira de la boîte, ce qui est souvent la seule "
            "chose possible avec du courrier de machine."))

    def _unsubscribe_one_click(self):
        if not safe_push_endpoint(self.unsubscribe_url):
            raise UserError(_(
                "Le lien de désabonnement ne pointe pas vers une adresse "
                "publique. Rien n'a été envoyé : un serveur qui poste vers une "
                "adresse interne est une porte ouverte, pas un service."))
        try:
            reponse = requests.post(
                self.unsubscribe_url,
                data={"List-Unsubscribe": "One-Click"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=TIMEOUT,
            )
        except requests.RequestException as err:
            raise UserError(_(
                "Le serveur de l'expéditeur n'a pas répondu : %s", err)) from err
        if reponse.status_code >= 400:
            raise UserError(_(
                "L'expéditeur a refusé le désabonnement (code %s). Le lien "
                "reste ouvrable à la main.", reponse.status_code))
        _logger.info("bf.email #%s : désabonnement en un clic accepté (%s)",
                     self.id, reponse.status_code)
        return self._unsubscribe_done(_(
            "Désabonnement envoyé. L'expéditeur peut mettre quelques jours à "
            "l'appliquer."))

    def _unsubscribe_by_mail(self):
        adresse = (self.unsubscribe_mailto or "")[len("mailto:"):]
        adresse = adresse.split("?", 1)[0].strip()
        if not adresse:
            raise UserError(_("L'adresse de désabonnement est illisible."))
        expediteur = (
            self.account_id.login
            or self.env.user.email
            or self.env.user.partner_id.email
        )
        self.env["mail.mail"].sudo().create({
            "subject": "unsubscribe",
            "body_html": "<p>unsubscribe</p>",
            "email_from": expediteur,
            "email_to": adresse,
            "auto_delete": True,
        }).send()
        return self._unsubscribe_done(_(
            "Demande de désabonnement envoyée à %s.", adresse))

    def _unsubscribe_done(self, message):
        """Sort la ligne de la boîte : on ne se désabonne pas pour la relire."""
        self.action_archive()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Désabonnement"),
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }
