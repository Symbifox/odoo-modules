# -*- coding: utf-8 -*-
from odoo import api, models


class MailMessage(models.Model):
    """La garde des commentaires fermés vaut aussi hors de `message_post`.

    🔴 `_mail_post_access = "read"` laisse toute l'audience créer un
    `mail.message` sur une publication. Un appel direct à
    `mail.message.create(...)` posait donc un commentaire sous une annonce qui
    promettait de n'en pas recevoir, et ses `partner_ids` donnaient à qui on
    voulait, même hors audience, l'accès au message et au titre.
    """

    _inherit = "mail.message"

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            Post = self.env["bf.babillard.post"]
            for vals in vals_list:
                if vals.get("model") != Post._name or not vals.get("res_id"):
                    continue
                post = Post.browse(vals["res_id"]).exists()
                if not post:
                    continue
                post.check_access("read")
                post._refuser_si_ferme(vals.get("message_type", "notification"),
                                       subtype_id=vals.get("subtype_id"))
                if vals.get("partner_ids"):
                    vals["partner_ids"] = [(6, 0, post._mentions_permises(
                        self._identifiants(vals["partner_ids"])))]
        return super().create(vals_list)

    @api.model
    def _identifiants(self, commandes):
        """Les identifiants d'une valeur x2many, quelle que soit sa forme."""
        if isinstance(commandes, int):
            return [commandes]
        identifiants = []
        for commande in commandes:
            if isinstance(commande, int):
                identifiants.append(commande)
            elif isinstance(commande, (list, tuple)) and commande:
                if commande[0] in (4, 3) and len(commande) > 1:
                    identifiants.append(commande[1])
                elif commande[0] == 6 and len(commande) > 2:
                    identifiants.extend(commande[2] or [])
        return identifiants
