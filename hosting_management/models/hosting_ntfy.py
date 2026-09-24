# License LGPL-3.0 or later - see README.md for full license text.

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class HostingNtfy(models.AbstractModel):
    _name = "hosting.ntfy"
    _description = "Publication de notifications push via ntfy"

    @api.model
    def _get_config(self):
        ICP = self.env["ir.config_parameter"].sudo()
        return {
            "url": ICP.get_param("hosting.ntfy_url", "").strip().rstrip("/"),
            "token": ICP.get_param("hosting.ntfy_token", "").strip(),
            "topic": ICP.get_param("hosting.ntfy_topic", "").strip(),
        }

    @api.model
    def send(self, title, body, priority="default", tags=None, click=None):
        """Publier un message sur le sujet ntfy configuré.

        Retourne True si envoyé, False si non configuré ou erreur.
        """
        try:
            import requests  # noqa: PLC0415
        except ImportError:
            return False

        cfg = self._get_config()
        if not all([cfg["url"], cfg["token"], cfg["topic"]]):
            _logger.debug("ntfy non configuré, notification push ignorée")
            return False

        headers = {
            "Authorization": f"Bearer {cfg['token']}",
            "Title": title,
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = tags if isinstance(tags, str) else ",".join(tags)
        if click:
            headers["Click"] = click

        try:
            rep = requests.post(
                f"{cfg['url']}/{cfg['topic']}",
                data=(body or "").encode("utf-8"),
                headers=headers,
                timeout=10,
            )
        except Exception:
            _logger.exception("Erreur lors de l'envoi de la notification ntfy")
            return False
        # ⚠️ On rendait True dès que la requête n'avait pas LEVÉ. Un jeton
        # périmé ou une ACL manquante rend un 403 bien poli, et l'appelant
        # lisait « envoyé » sur une alerte que personne n'a reçue. Un contrôle
        # qui ment coûte plus cher qu'un contrôle absent : au moins l'absence
        # se voit. Relevé en écrivant la sonde du réveil, qui s'est fait
        # refuser exactement comme ça.
        if not rep.ok:
            _logger.warning(
                "ntfy a REFUSÉ la publication sur « %s » (HTTP %s) : "
                "l'alerte n'est pas partie.", cfg["topic"], rep.status_code)
            return False
        return True

    @api.model
    def record_url(self, record):
        """Construire un lien web Odoo vers un enregistrement."""
        if not record:
            return None
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        if not base:
            return None
        return f"{base.rstrip('/')}/web#id={record.id}&model={record._name}&view_type=form"
