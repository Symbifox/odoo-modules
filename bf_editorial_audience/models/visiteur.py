# -*- coding: utf-8 -*-
"""L'agent utilisateur, relevé sur le visiteur.

Odoo ne le garde pas. Sans lui, aucune des deux séries de mesures n'existe :
on ne peut ni retirer les robots, ni dire combien il y en avait.

⚠️ Le point d'accroche. ``_upsert_visitor`` est une requête SQL brute qui
crée le visiteur et sa trace en un seul aller ; la doubler pour y glisser une
colonne serait recopier du SQL d'Odoo dans un module, et le voir se périmer au
prochain palier. On accroche donc ``_get_visitor_from_request``, juste après,
et on n'écrit que quand la chaîne manque ou a changé. Un visiteur porte un
témoin, donc son agent ne change presque jamais : en régime, c'est une
écriture par nouveau visiteur, zéro pour les autres.
"""

import logging

from odoo import api, fields, models
from odoo.http import request

from . import robots

_logger = logging.getLogger(__name__)

# Assez pour les agents réels, qui tiennent sous 300 caractères. Au-delà, c'est
# du remplissage ou une tentative : on tronque plutôt que d'engraisser la table.
LONGUEUR_MAX = 512


class WebsiteVisitor(models.Model):
    _inherit = "website.visitor"

    user_agent = fields.Char(
        string="Agent utilisateur", readonly=True, copy=False,
        help="La chaîne déclarée par le client, gardée en clair pour pouvoir"
             " reclasser l'historique quand un robot nouveau apparaît. Le"
             " paramètre bf_editorial_audience.ua_retention_days règle sa"
             " durée de conservation.",
    )
    is_bot = fields.Boolean(
        string="Robot déclaré", readonly=True, copy=False, index=True,
        help="Vrai quand l'agent déclaré correspond à un robot connu. Un agent"
             " se falsifie : ce drapeau mesure ce qui se déclare.",
    )
    agent_family = fields.Char(
        string="Famille d'agent", readonly=True, copy=False, index=True,
    )

    # --- capture ---------------------------------------------------------
    @api.model
    def _get_visitor_from_request(self, force_create=False, force_track_values=None):
        visitor = super()._get_visitor_from_request(
            force_create=force_create, force_track_values=force_track_values,
        )
        if visitor:
            visitor._bf_capture_user_agent()
        return visitor

    def _bf_capture_user_agent(self):
        """Relever l'agent du client courant, s'il apporte du neuf.

        ⚠️ Ne jamais laisser cette capture faire échouer une page publique.
        Une mesure qui casse le site qu'elle mesure ne vaut aucune mesure :
        toute erreur est journalisée et avalée.
        """
        if not request:
            return
        try:
            agent = (request.httprequest.user_agent.string or "").strip()
        except Exception:                       # pragma: no cover - défensif
            return
        agent = agent[:LONGUEUR_MAX] or False
        for visiteur in self:
            if visiteur.user_agent == agent:
                continue
            is_bot, famille = robots.classer(agent)
            try:
                visiteur.sudo().write({
                    "user_agent": agent,
                    "is_bot": is_bot,
                    "agent_family": famille,
                })
            except Exception:                   # pragma: no cover - défensif
                _logger.warning(
                    "Agent utilisateur non relevé pour le visiteur %s",
                    visiteur.id, exc_info=True,
                )

    # --- reclassement et ménage ------------------------------------------
    @api.model
    def _cron_reclass_agents(self, limit=5000):
        """Reclasser les visiteurs dont l'agent est connu mais pas rangé.

        Sert deux fois : au premier chargement du module, et chaque fois qu'une
        signature s'ajoute au fichier des robots. C'est toute la raison de
        garder la chaîne.
        """
        visiteurs = self.search([
            ("user_agent", "!=", False), ("agent_family", "=", False),
        ], limit=limit)
        for visiteur in visiteurs:
            is_bot, famille = robots.classer(visiteur.user_agent)
            visiteur.write({"is_bot": is_bot, "agent_family": famille})
        return len(visiteurs)

    @api.model
    def _cron_forget_agents(self):
        """Effacer les chaînes plus vieilles que la durée retenue.

        Le verdict et la famille restent : ce qui part, c'est l'identifiant
        d'appareil. À zéro, le module ne conserve rien de plus que ce que la
        décision d'Odoo conserve déjà, et ce ménage ne fait rien.
        """
        jours = int(self.env["ir.config_parameter"].sudo().get_param(
            "bf_editorial_audience.ua_retention_days", "0",
        ) or 0)
        if jours <= 0:
            return 0
        limite = fields.Datetime.subtract(fields.Datetime.now(), days=jours)
        vieux = self.search([
            ("user_agent", "!=", False),
            ("last_connection_datetime", "<", limite),
        ])
        if vieux:
            vieux.write({"user_agent": False})
        return len(vieux)
