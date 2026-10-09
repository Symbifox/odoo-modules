"""Les messages créés pendant une incarnation.

La garde vit ici, à la création du message, plutôt que sur ``mail.thread`` :
hériter de ``mail.thread`` fait réinitialiser tous les modèles qui en héritent
(la plupart des modèles métier) à l'installation et à chaque montée.

* Seule une note interne sans destinataire passe. Garder le seul ``mail.mail``
  ne suffit pas : avec ``mail_post_defer`` (quand il est installé), les avis d'un
  message partent plus tard, par cron, hors de la requête et donc hors de toute
  garde. Un avis (``message_notify``), un message aux abonnés, une discussion :
  refusés.
* L'auteur est l'incarnateur, jamais la personne ; le corps n'est jamais touché
  (l'OCA y insérait « Logged in as X », qui partait dans les courriels).
* Le message est rattaché à l'entrée du journal.

À blanc (lecture seule), tout est annulé avec la requête : rien à refuser.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .. import impersonation as imp



class MailMessage(models.Model):
    _inherit = "mail.message"

    bf_impersonate_session_id = fields.Many2one(
        "bf.impersonate.session", string="Posted while impersonating",
        readonly=True, copy=False, ondelete="set null")

    @api.model_create_multi
    def create(self, vals_list):
        payload = imp.current()
        if payload and not imp.in_dry():
            Users = self.env["res.users"].sudo()
            origin_partner = Users.browse(payload["from_uid"]).partner_id
            internal = self.env["mail.message.subtype"].sudo().search(
                [("internal", "=", True)]).ids
            for vals in vals_list:
                self._bf_impersonate_check(vals, internal)
                # Toujours l'incarnateur : un auteur passé à la main (la direction,
                # un collègue) ferait signer la note par un tiers.
                vals["author_id"] = origin_partner.id
                vals["email_from"] = origin_partner.email_formatted or vals.get("email_from")
                vals.setdefault("bf_impersonate_session_id", payload["journal_id"])
        return super().create(vals_list)

    @api.model
    def _bf_impersonate_check(self, vals, internal_subtypes):
        message_type = vals.get("message_type", "comment")
        partners = vals.get("partner_ids") or []
        if (
            message_type not in ("comment", "notification")
            or vals.get("model") == "discuss.channel"
            or any(partners)
            or (message_type == "comment" and vals.get("subtype_id") not in internal_subtypes)
        ):
            raise UserError(_(
                "While you see Symbifox as someone else, only internal notes without "
                "recipients can be posted. Go back to your own account to send a message."))
