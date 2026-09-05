import logging

from odoo import models

_logger = logging.getLogger(__name__)


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def action_notify(self):
        """Ne jamais envoyer à un client le courriel « activité attribuée ».

        Odoo prévient l'assigné d'une activité par courriel comme effet de bord
        de `create()` et d'une réattribution (`mail/models/mail_activity.py` :
        `create()` → `action_notify()` → `record.message_notify(...)`). Le seul
        garde-fou natif est « l'assigné n'est pas celui qui écrit » : rien ne
        vérifie que l'assigné est des nôtres. N'importe quel module qui
        planifie une activité sur un enregistrement dont le responsable se
        trouve être un contact de portail envoie donc à ce contact un rappel
        interne qu'il ne peut même pas ouvrir — un usager de portail n'a pas de
        vue des activités.

        Mesuré en production : six courriels de ce type sont partis vers un
        contact de portail responsable d'un mandat, sur quatre mois et demi,
        tous issus d'un même cron de suivi.

        L'activité elle-même n'est pas touchée : elle reste visible à l'interne,
        seule la notification vers un compte `share` est abandonnée.
        """
        internes = self.filtered(lambda act: not act.user_id.sudo().share)
        ecartees = self - internes
        if ecartees:
            _logger.info(
                "bf_follower_cleanup : notification d'activité supprimée pour "
                "%d activité(s) assignée(s) à un compte portail (%s)",
                len(ecartees),
                ", ".join(sorted(set(ecartees.user_id.sudo().mapped("login")))),
            )
        if not internes:
            return None
        return super(MailActivity, internes).action_notify()
