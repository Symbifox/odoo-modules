# -*- coding: utf-8 -*-
"""Le portail du client connecté, celui de `/my`.

Distinct de `portail.py`, qui sert une adresse à token à quelqu'un qui n'a pas
de compte. Ici, le client **est** connecté : il a un compte portail, il voit
déjà ses projets dans `/my/projects`, et il faut simplement qu'il trouve
l'échéancier au lieu d'attendre qu'on lui envoie un lien.

Deux ajouts, pas un de plus :

* un bouton sur la page du projet, quand l'échéancier y est publié ;
* une carte `/my` et une liste `/my/echeanciers` pour les plans autonomes, qui
  n'ont pas de page d'accueil à eux.

⚠️ Le filtrage se fait sur le partenaire **commercial** de l'usager, jamais sur
son contact seul : un client dont trois personnes ont un compte portail doit
voir le même échéancier depuis les trois.
"""
from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class EcheancierPortailClient(CustomerPortal):

    # ------------------------------------------------------------------
    # Ce que le client a le droit de voir
    # ------------------------------------------------------------------

    def _bf_gantt_domaine_plans(self):
        """Les plans autonomes qu'un usager du portail peut lire.

        Publiés, et rattachés à son partenaire commercial. Sans l'un ou l'autre,
        rien : c'est la même règle que celle posée dans `ir.rule`, répétée ici
        pour que la liste ne dépende pas seulement du filtre implicite.
        """
        commercial = request.env.user.partner_id.commercial_partner_id
        return [
            ("portal_published", "=", True),
            ("partner_id", "child_of", commercial.id),
        ]

    def _prepare_home_portal_values(self, counters):
        valeurs = super()._prepare_home_portal_values(counters)
        if "bf_gantt_plan_count" in counters:
            # ⚠️ Le module n'accorde ses groupes à personne à l'installation :
            # un usager interne ordinaire n'a donc AUCUN droit sur ce modèle, et
            # un `search_count` sec faisait tomber tout `/my/counters` en 500
            # pour lui. Une carte à zéro vaut mieux qu'une page cassée.
            try:
                valeurs["bf_gantt_plan_count"] = request.env[
                    "bf.gantt.plan"].search_count(self._bf_gantt_domaine_plans())
            except AccessError:
                valeurs["bf_gantt_plan_count"] = 0
        return valeurs

    # ------------------------------------------------------------------
    # La liste des échéanciers autonomes
    # ------------------------------------------------------------------

    @http.route(["/my/echeanciers", "/my/echeanciers/page/<int:page>"],
                type="http", auth="user", website=True)
    def bf_gantt_mes_echeanciers(self, page=1, **kw):
        Plan = request.env["bf.gantt.plan"]
        domaine = self._bf_gantt_domaine_plans()
        total = Plan.search_count(domaine)
        pagineur = portal_pager(
            url="/my/echeanciers", total=total, page=page, step=self._items_per_page)
        plans = Plan.search(domaine, order="date_start desc, name",
                            limit=self._items_per_page, offset=pagineur["offset"])
        return request.render("bf_gantt.portail_mes_echeanciers", {
            "plans": plans,
            "pager": pagineur,
            "page_name": "bf_gantt_plan",
            "default_url": "/my/echeanciers",
        })
