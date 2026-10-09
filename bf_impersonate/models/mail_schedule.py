"""Les envois différés pendant une incarnation.

* « Envoyer plus tard » crée un ``mail.scheduled.message`` sans aucun
  ``mail.message`` : un cron le publierait ensuite sous la personne,
  destinataires et courriels compris. Le créer ou le modifier : refusé.
* ``mail.message.schedule`` porte les avis d'un message déjà publié, mis en file
  pour un cron (``mail_post_defer`` le fait pour TOUS les avis). Hors de la
  requête, ils échapperaient à toute garde : on les envoie tout de suite, par la
  voie normale, et les gardes de ``mail.mail`` s'appliquent. Un avis dans la
  boîte Odoo passe ; un abonné avisé par courriel fait refuser la modification.

À blanc, tout est annulé avec la requête : rien à faire.
"""
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

from .. import impersonation as imp

_logger = logging.getLogger(__name__)


def _refuse_if_impersonating(model):
    if imp.current() and not imp.in_dry():
        _logger.warning("bf_impersonate: envoi différé refusé (%s)", model)
        raise UserError(_(
            "Nothing is sent while you see Symbifox as someone else: no "
            "email, no text message. Go back to your own account to send it."))


class MailScheduledMessage(models.Model):
    _inherit = "mail.scheduled.message"

    @api.model_create_multi
    def create(self, vals_list):
        _refuse_if_impersonating(self._name)
        return super().create(vals_list)

    def write(self, vals):
        # Seul son auteur modifie un envoi programmé : c'est exactement la
        # personne vue. L'annuler (unlink) n'envoie rien et reste permis.
        _refuse_if_impersonating(self._name)
        return super().write(vals)


class MailMessageSchedule(models.Model):
    _inherit = "mail.message.schedule"

    @api.model_create_multi
    def create(self, vals_list):
        schedules = super().create(vals_list)
        if imp.current() and not imp.in_dry():
            _logger.info("bf_impersonate: avis différés envoyés sur-le-champ (%s)", schedules.ids)
            schedules._send_notifications()
        return schedules
