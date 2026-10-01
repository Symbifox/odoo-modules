from odoo import http
from odoo.http import request

from odoo.addons.helpdesk_mgmt.controllers.myaccount import CustomerPortalHelpdesk


class CustomerPortalHelpdeskBf(CustomerPortalHelpdesk):
    """Le filtre par étape du portail affiche le libellé client de l'étape.

    helpdesk_mgmt construit ce filtre dans la route même, avec le nom interne
    des étapes : on relit les libellés dans le contexte rendu, avant le rendu.
    """

    @http.route()
    def portal_my_tickets(self, *args, **kw):
        response = super().portal_my_tickets(*args, **kw)
        filters = getattr(response, "qcontext", {}).get("searchbar_filters")
        if filters:
            stage_ids = [int(key) for key in filters if key.isdigit()]
            stages = request.env["helpdesk.ticket.stage"].browse(stage_ids)
            for stage in stages.exists():
                if stage.portal_label:
                    filters[str(stage.id)]["label"] = stage.portal_label
        return response
