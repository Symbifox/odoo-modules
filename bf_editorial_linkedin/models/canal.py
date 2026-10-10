# -*- coding: utf-8 -*-
"""Ce qu'un canal LinkedIn porte en plus.

Deux champs, et les deux existent pour la même raison : un jeton LinkedIn dure
60 jours et rien ne le renouvelle tout seul. On ne peut pas éviter l'échéance ;
on peut refuser de la découvrir le matin où une diffusion échoue.
"""

from odoo import SUPERUSER_ID, api, fields, models

PREAVIS_JOURS = 7


class SocialChannel(models.Model):
    _inherit = "bf.social.channel"

    linkedin_member_urn = fields.Char(
        string="Member URN", readonly=True, copy=False,
        help="Resolved when the credentials are checked. It is the "
             "declared author of each post.",
    )
    linkedin_token_expiry = fields.Date(
        string="Token expiry",
        help="The date LinkedIn gave when it issued the token, to copy "
             "here. Nothing reads it from the token: it is a note, and it "
             "is what triggers the advance warning.",
    )
    linkedin_token_days_left = fields.Integer(
        string="Days left", compute="_compute_linkedin_days_left",
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
            # Le travail planifié n'a la langue de personne : on écrit dans celle
            # de qui lira le chatter du canal.
            lu = canal.with_context(lang=canal._linkedin_langue_lecteur())
            if reste < 0:
                corps = lu.env._(
                    "This channel's LinkedIn token expired %s day(s) ago. "
                    "Posts fail until a new token is pasted.", abs(reste),
                )
            else:
                corps = lu.env._(
                    "This channel's LinkedIn token expires in %s day(s). "
                    "A member token does not renew itself: a new one must "
                    "be generated from the app.", reste,
                )
            canal.message_post(body=corps)
            prevenus |= canal
        return len(prevenus)

    def _linkedin_langue_lecteur(self):
        """La langue de qui a créé le canal, sinon celle de la société.

        Jamais celle d'OdooBot ni d'un compte partagé. None laisse le contexte
        sans langue : le texte s'écrit alors dans la langue source (l'anglais).
        """
        self.ensure_one()
        installees = {code for code, _nom in self.env["res.lang"].get_installed()}
        createur = self.create_uid
        langues = []
        if createur and createur.active and not createur.share and createur.id != SUPERUSER_ID:
            langues.append(createur.lang)
        langues.append(self.env.company.partner_id.lang)
        return next((lang for lang in langues if lang in installees), None)
