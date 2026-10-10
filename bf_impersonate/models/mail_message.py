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

    # Ce qu'un courriel en file reprend de son message (par délégation).
    _BF_OUTGOING_FIELDS = frozenset({
        "attachment_ids", "body", "email_from", "mail_server_id", "message_id", "model",
        "partner_ids", "record_alias_domain_id", "reply_to", "res_id", "subject",
    })
    # Des avis encore programmés (mail.message.schedule) relisent le message au
    # départ : son sous-type, par exemple, décide qui les reçoit par courriel.
    _BF_SCHEDULED_FIELDS = _BF_OUTGOING_FIELDS | {
        "author_id", "email_add_signature", "email_layout_xmlid", "is_internal",
        "message_type", "subtype_id",
    }

    def write(self, vals):
        # Modifier le message d'un courriel encore en file, ou d'avis encore
        # programmés, c'est choisir ce qui part, par le parent plutôt que par le
        # courriel lui-même. Les messages publiés pendant l'incarnation n'ont ni
        # l'un ni l'autre (courriels refusés, avis envoyés sur-le-champ).
        names = set(vals)
        if imp.current() and not imp.in_dry() and (
                (self._BF_OUTGOING_FIELDS & names
                 and self.sudo().mail_ids.filtered(lambda m: m.state == "outgoing"))
                or (self._BF_SCHEDULED_FIELDS & names
                    and self.env["mail.message.schedule"].sudo().search_count(
                        [("mail_message_id", "in", self.ids)], limit=1))):
            raise UserError(_(
                "Nothing is sent while you see Symbifox as someone else: no "
                "email, no text message. Go back to your own account to send it."))
        return super().write(vals)

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
