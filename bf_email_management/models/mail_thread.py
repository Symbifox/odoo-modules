"""Rattraper au routage les réponses qui visent la boîte plutôt qu'un dossier.

``bf.email`` hérite de ``mail.thread`` : la passerelle sait donc déposer un
message sur une rangée de la boîte, et elle le fait dès qu'un correspondant
répond à un envoi parti de la boîte avant que la rangée soit classée — son
Message-ID porte alors ``openerp-<id>-bf.email``. Le fil quitte le dossier, et
il n'y revient pas tout seul : chaque réponse suivante cite le même en-tête.

``bf.email._composer_target`` empêche les nouveaux cas. Cette garde-ci sert les
fils DÉJÀ partis : le Message-ID est chez le correspondant, on ne peut pas le
reprendre, mais on peut lire où la rangée visée est classée et y rediriger le
message. Une réécriture de route, pas un second envoi.
"""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    @api.model
    def message_route(self, message, message_dict, model=None,
                      thread_id=None, custom_values=None):
        routes = super().message_route(
            message, message_dict, model=model, thread_id=thread_id,
            custom_values=custom_values,
        )
        return [self._bf_email_redirect_route(route) for route in routes]

    @api.model
    def _bf_email_redirect_route(self, route):
        """Réécrire une route qui vise la boîte vers le dossier du fil.

        ``route`` est le 5-uplet d'Odoo ``(modèle, id, valeurs, usager,
        alias)``. Seuls les deux premiers changent : l'alias et les valeurs
        par défaut que la passerelle a choisis restent ceux qu'elle a choisis.
        """
        if not isinstance(route, (list, tuple)) or len(route) < 2:
            return route
        if route[0] != "bf.email" or not route[1]:
            return route
        row = self.env["bf.email"].sudo().browse(route[1]).exists()
        if not row:
            return route
        target = row._filing_target()
        if not target:
            return route
        _logger.info(
            "bf.email : message routé sur la rangée #%s redirigé vers %s/%s",
            row.id, target[0], target[1],
        )
        return (target[0], target[1]) + tuple(route[2:])
