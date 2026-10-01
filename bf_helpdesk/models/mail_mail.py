import re

from odoo import models

_MUTE_PLACEHOLDER = "/helpdesk/courriels/__bf_mute__"
# Au rendu, Odoo rend les liens relatifs absolus : le lien générique arrive
# précédé de l'adresse du site. Remplacer le seul chemin collait une seconde
# adresse derrière la première (https://…comhttps://…), un lien mort dans chaque
# courriel client.
_MUTE_HREF = re.compile(r"(?:https?://[^\s\"'<>]*?)?" + re.escape(_MUTE_PLACEHOLDER))
_MUTE_REGEX = re.compile(r'<span id="bf_hd_mute".*?</span>', re.DOTALL)


class MailMail(models.Model):
    _inherit = "mail.mail"

    def _personalize_outgoing_body(self, body, partner=False, recipients_follower_status=None):
        """Lien « ne plus recevoir les courriels de cette demande », signé par destinataire.

        Le gabarit maître pose un lien générique ; chaque client reçoit le
        sien, signé pour ce billet et pour lui. Un agent, ou un courriel sans
        destinataire connu (accusé envoyé à une simple adresse), ne reçoit
        pas de lien.
        """
        body = super()._personalize_outgoing_body(
            body, partner=partner, recipients_follower_status=recipients_follower_status)
        if not body or _MUTE_PLACEHOLDER not in body:
            return body
        is_client = partner and not any(not u.share for u in partner.sudo().user_ids)
        if self.model == "helpdesk.ticket" and self.res_id and is_client:
            ticket = self.env["helpdesk.ticket"].sudo().browse(self.res_id)
            url = ticket._bf_mute_url(partner.id)
            return _MUTE_HREF.sub(lambda _m: url, body)
        return re.sub(_MUTE_REGEX, "", body)
