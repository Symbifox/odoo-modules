from odoo import api, models


class BfDashboard(models.AbstractModel):
    _inherit = "bf.dashboard"
    # `AbstractModel` obligatoire : `bf.dashboard` est abstrait (aucune table),
    # et `BaseModel._build_model_check_base` lève `TypeError` dès qu'une classe
    # concrète l'étend — le registre ne charge plus du tout (BF #24867).

    @api.model
    def _get_helpdesk_summary(self):
        """KPI tile: open tickets, unattended, high-priority, P0 critical, by team."""
        Ticket = self.env["helpdesk.ticket"]
        Team = self.env["helpdesk.ticket.team"]

        teams = Team.search([("active", "=", True)])
        rows = []
        for team in teams:
            base_domain = [("team_id", "=", team.id), ("closed", "=", False)]
            rows.append({
                "team_id": team.id,
                "team_name": team.name,
                "open": Ticket.search_count(base_domain),
                "unattended": Ticket.search_count(base_domain + [("unattended", "=", True)]),
                "high": Ticket.search_count(base_domain + [("priority", "in", ["2", "3"])]),
                "critical": Ticket.search_count(base_domain + [("priority", "=", "3")]),
                "waiting_client": Ticket.search_count(base_domain + [("waiting_state", "=", "client")]),
                "waiting_external": Ticket.search_count(base_domain + [("waiting_state", "=", "external")]),
            })
        return {
            "teams": rows,
            "total_open": sum(r["open"] for r in rows),
            "total_critical": sum(r["critical"] for r in rows),
            "total_unattended": sum(r["unattended"] for r in rows),
        }

    # ⚠️ @api.model OBLIGATOIRE, et il manquait. Cette surcharge est la DERNIÈRE
    # de la chaîne d'héritage : c'est elle que `call_kw` inspecte. Sans le
    # décorateur, l'appel est routé vers `_call_kw_multi`, qui consomme le
    # premier argument comme une liste d'identifiants — avec zéro argument,
    # c'est `IndexError: tuple index out of range`, que le composant OWL
    # affiche sous la forme « Impossible de charger les données du tableau
    # de bord ». Les trois autres extensions de bf.dashboard le portent.
    @api.model
    def get_dashboard_data(self):
        data = super().get_dashboard_data()
        data["helpdesk"] = self._get_helpdesk_summary()
        return data
