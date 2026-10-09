"""Ce qui se rattache à un message du fil suit la règle du fil.

Le « renvoi » d'Odoo (`mail.resend.message`, `sms.resend`) renvoie un message existant à chaque
partenaire qui a une notification sur ce message : il crée en sudo un `mail.mail` rattaché au
message, sans créer de message. Créer la notification ne demande que la lecture du message. Un
lecteur renvoyait ainsi le courriel officiel, PDF signé joint, chez un autre client ; et se
notifier lui-même lui donnait l'écriture du cœur sur les messages et leurs pièces.
"""
from odoo import api, models

WATCHED = "privacy.breach.notice"
REBIND = {"mail_message_id", "res_partner_id", "mail_mail_id"}
READ_ONLY_FIELDS = {"is_read", "read_date"}


def _check(env, messages, existing_only=False):
    """Ne coûte qu'un filtre en mémoire quand rien ne touche un avis (cas de tout le système)."""
    res_ids = messages._breach_res_ids()
    if res_ids:
        env["mail.message"]._breach_check_register(res_ids, existing_only=existing_only)


class MailNotification(models.Model):
    _inherit = "mail.notification"

    @api.model_create_multi
    def create(self, vals_list):
        notifications = super().create(vals_list)
        _check(self.env, notifications.sudo().mail_message_id)
        return notifications

    def _breach_guard_existing(self, vals=None):
        """Sur un message d'avis, chacun marque SA notification comme lue ; tout le reste (type,
        statut, numéro, échec, annulation des échecs d'autrui) est le registre. Le renvoi par SMS
        réécrivait une notification existante au lieu d'en créer une."""
        if self.env["mail.message"]._breach_register_actor_is_system():  # la file d'envoi, la passerelle
            return
        ours = self.sudo().filtered(lambda n: n.mail_message_id.model == WATCHED and n.mail_message_id.res_id)
        if not ours:
            return
        own_read = (vals is not None and not (set(vals) - READ_ONLY_FIELDS)
                    and ours.res_partner_id == self.env.user.partner_id)
        if not own_read:
            _check(self.env, ours.mail_message_id, existing_only=True)

    def write(self, vals):
        self._breach_guard_existing(vals)
        res = super().write(vals)
        if REBIND & set(vals):
            _check(self.env, self.sudo().mail_message_id)
        return res

    def unlink(self):
        self._breach_guard_existing()
        return super().unlink()


class MailMail(models.Model):
    _inherit = "mail.mail"

    @api.model_create_multi
    def create(self, vals_list):
        mails = super().create(vals_list)
        _check(self.env, mails.sudo().mail_message_id)
        return mails

    def write(self, vals):
        _check(self.env, self.sudo().mail_message_id, existing_only=True)
        res = super().write(vals)
        if "mail_message_id" in vals:
            _check(self.env, self.sudo().mail_message_id)
        return res
