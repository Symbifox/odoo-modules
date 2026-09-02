# -*- coding: utf-8 -*-
"""Ce qu'un canal LinkedIn porte en plus.

Deux champs, et les deux existent pour la même raison : un jeton LinkedIn dure
60 jours et rien ne le renouvelle tout seul. On ne peut pas éviter l'échéance ;
on peut refuser de la découvrir le matin où une diffusion échoue.
"""

from odoo import _, api, fields, models

PREAVIS_JOURS = 7


class SocialChannel(models.Model):
    _inherit = "bf.social.channel"

    linkedin_member_urn = fields.Char(
        string="URN du membre", readonly=True, copy=False,
        help="Résolu à la vérification des identifiants. C'est l'auteur"
             " déclaré de chaque publication.",
    )
    linkedin_token_expiry = fields.Date(
        string="Expiration du jeton",
        help="La date que LinkedIn a donnée en délivrant le jeton, à recopier"
             " ici. Rien ne la lit dans le jeton : c'est une note, et c'est"
             " elle qui déclenche le préavis.",
    )
    linkedin_token_days_left = fields.Integer(
        string="Jours restants", compute="_compute_linkedin_days_left",
    )

    @api.depends("linkedin_token_expiry")
    def _compute_linkedin_days_left(self):
        for canal in self:
            reste = canal._linkedin_days_left()
            canal.linkedin_token_days_left = reste if reste is not None else 0

    def _linkedin_days_left(self):
        """Jours avant expiration, ou None si personne n'a noté la date."""
        self.ensure_one()
        if not self.linkedin_token_expiry:
            return None
        return (self.linkedin_token_expiry - fields.Date.context_today(self)).days

    @api.model
    def _cron_warn_linkedin_expiry(self):
        """Prévenir avant que le jeton ne tombe, pas après.

        Le message part au chatter du canal : c'est là que quelqu'un le lira
        en venant coller le nouveau jeton, et ça laisse une trace datée de
        l'avertissement.
        """
        canaux = self.search([
            ("network", "=", "linkedin"),
            ("linkedin_token_expiry", "!=", False),
        ])
        prevenus = self.browse()
        for canal in canaux:
            reste = canal._linkedin_days_left()
            if reste is None or reste > PREAVIS_JOURS:
                continue
            if reste < 0:
                corps = _(
                    "Le jeton LinkedIn de ce canal est expiré depuis %s"
                    " jour(s). Les diffusions échouent jusqu'à ce qu'un"
                    " nouveau jeton soit collé.", abs(reste),
                )
            else:
                corps = _(
                    "Le jeton LinkedIn de ce canal expire dans %s jour(s)."
                    " Un jeton de membre ne se renouvelle pas tout seul :"
                    " il faut en générer un neuf depuis l'application.", reste,
                )
            canal.message_post(body=corps)
            prevenus |= canal
        return len(prevenus)
