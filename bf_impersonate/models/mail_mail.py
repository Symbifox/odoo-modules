import logging

from odoo import _, api, models
from odoo.exceptions import UserError

from .. import impersonation as imp

_logger = logging.getLogger(__name__)


class MailMail(models.Model):
    _inherit = "mail.mail"

    @api.model_create_multi
    def create(self, vals_list):
        # Rien ne sort au nom de quelqu'un, en sudo() compris. À blanc, le
        # courriel serait annulé avec le reste de la requête (To-do publie un
        # message d'accueil en s'ouvrant) : on le laisse se créer.
        if imp.current() and not imp.in_dry():
            _logger.warning("bf_impersonate: courriel refusé (%s)",
                            ", ".join(str(v.get("email_to") or v.get("recipient_ids") or "?")
                                      for v in vals_list))
            raise UserError(_(
                "Nothing is sent while you see Symbifox as someone else: no "
                "email, no text message. Go back to your own account to send it."))
        return super().create(vals_list)

    def write(self, vals):
        # Un courriel en file part ensuite par cron : le modifier, c'est choisir
        # ce qui part (sous une cible administratrice, seule à y avoir accès),
        # qu'on touche à son contenu, à son état ou au message dont il hérite.
        # Le message lui-même est gardé dans mail_message.py.
        if imp.current() and not imp.in_dry():
            _logger.warning("bf_impersonate: modification d'un courriel en file refusée")
            raise UserError(_(
                "Nothing is sent while you see Symbifox as someone else: no "
                "email, no text message. Go back to your own account to send it."))
        return super().write(vals)
