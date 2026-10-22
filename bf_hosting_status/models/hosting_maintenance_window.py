# -*- coding: utf-8 -*-
from odoo import models


class HostingMaintenanceWindow(models.Model):
    _inherit = "hosting.maintenance.window"

    def _hosting_notice_context(self, partner):
        """L'avis de maintenance porte le lien de la page d'état, seulement quand la page
        du client est publiée (un lien vers une page fermée répondrait « introuvable ») ET
        que `partner` est un destinataire NOMMÉ du client, par la même règle que le portail
        (`_hosting_status_client_du_compte`). Le repli de `_hosting_destinataires` (courriel
        de la société, premier contact) reçoit l'avis sans le lien : le jeton ouvre la page
        à quiconque le détient, il ne part qu'aux personnes choisies."""
        ctx = super()._hosting_notice_context(partner)
        client = self.env["res.partner"]._hosting_status_client_du_compte(partner)
        if client and client.hosting_status_published and client.hosting_status_token:
            ctx["status_url"] = client._hosting_status_url()
        return ctx
