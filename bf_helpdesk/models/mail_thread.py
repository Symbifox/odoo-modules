from odoo import models


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _message_route_process(self, message, message_dict, routes):
        """Marquer les réponses d'automates avant qu'elles touchent un billet.

        Un « absent du bureau » qui répond à une relance n'est pas une réponse
        du client : il ne doit ni lever l'attente ni rouvrir le billet. Les
        en-têtes ne sont plus lisibles une fois le message posté, d'où la
        détection ici, transmise par le contexte au billet.
        """
        if any(route[0] == "helpdesk.ticket" for route in routes):
            probe = {
                "auto-submitted": message.get("Auto-Submitted"),
                "subject": message_dict.get("subject"),
                "custom_headers": {
                    key: message.get(key)
                    for key in ("X-Auto-Response-Suppress", "X-AutoReply",
                                "Precedence", "Auto-Submitted")
                    if message.get(key)
                },
            }
            if self.env["helpdesk.ticket"]._is_autoresponder(probe):
                self = self.with_context(bf_helpdesk_autoreply=True)
        return super(MailThread, self)._message_route_process(
            message, message_dict, routes)

    def _notify_get_recipients(self, message, msg_vals, **kwargs):
        """Canal imposé par le répartiteur de l'assistance (bf_hd_force_notif).

        Une préférence « Odoo » doit arriver dans la boîte de réception même
        chez un usager réglé sur le courriel, et inversement : sans cela, le
        canal choisi dans la matrice ne voudrait rien dire.
        """
        recipients = super()._notify_get_recipients(message, msg_vals, **kwargs)
        forced = self.env.context.get("bf_hd_force_notif")
        # Le contexte d'une requête vient de l'appelant (/mail/message/post). Le
        # répartiteur pose un dictionnaire à clés ENTIÈRES, que ni JSON ni
        # XML-RPC ne peuvent produire (leurs clés sont des chaînes), et seulement
        # les canaux « email » ou « inbox » : rien d'autre n'est accepté.
        if isinstance(forced, dict):
            for rdata in recipients:
                notif = forced.get(rdata["id"])
                if notif in ("email", "inbox"):
                    rdata["notif"] = notif
        return recipients
