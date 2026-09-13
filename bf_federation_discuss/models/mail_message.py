"""Le va-et-vient entre le canal et le chatter, et les deux gardes qui l'empêchent de tourner.

Le canal est la fenêtre, le chatter est le registre. Un message écrit dans le
canal est reposté au chatter de l'objet, d'où la fédération le porte déjà chez le
pair. Un message qui arrive au chatter reparaît dans le canal. Aucun nouveau
genre sur le réseau, aucune conversation en double.

🔴 **Le circuit est un anneau, et un anneau tourne si on ne l'ouvre pas.** Le
chemin complet est : canal d'ici → chatter d'ici → réseau → chatter du pair →
canal du pair. Sans garde, le chatter du pair reposterait dans son canal, qui
reposterait dans son chatter, qui repartirait sur le réseau. Deux drapeaux de
contexte suffisent, et ils sont posés au plus près de l'écriture qu'ils
décrivent :

* `federation_from_channel` : ce message de chatter vient du canal, donc il ne
  redescend pas au canal ;
* `federation_to_channel` : ce message de canal vient du chatter, donc il ne
  remonte pas au chatter.
"""

import logging

from markupsafe import Markup

from odoo import api, models

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = "mail.message"

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        ctx = self.env.context
        if not ctx.get("federation_to_channel"):
            messages._federation_channel_to_chatter()
        if not ctx.get("federation_from_channel"):
            messages._federation_chatter_to_channel()
        return messages

    # --- Canal → chatter -------------------------------------------------------------
    def _federation_channel_to_chatter(self):
        """Ce qui s'écrit dans le canal entre au registre, d'où le socle le fait voyager."""
        candidats = self.filtered(
            lambda m: m.model == "discuss.channel" and m.res_id
            and m.message_type in ("comment", "email") and m.body)
        if not candidats:
            return
        Channel = self.env["discuss.channel"].sudo()
        canaux = {c.id: c for c in Channel.browse(list({m.res_id for m in candidats})).exists()
                  if c.federation_link_id}
        comment = self.env.ref("mail.mt_comment").id
        for message in candidats:
            canal = canaux.get(message.res_id)
            if not canal:
                continue
            link = canal.federation_link_id
            if not link.active:
                continue
            record = link._record().exists()
            if not record or not hasattr(record, "message_post"):
                continue
            # 🔴 `message_post` ÉCHAPPE une chaîne : sans Markup, le registre recevrait
            # « &lt;p&gt;🔒 … » au lieu du HTML, et le marqueur d'exclusion ne serait plus
            # en tête du texte réduit. Le message privé traverserait.
            record.sudo().with_context(federation_from_channel=True).message_post(
                body=Markup(message.body or ""),
                author_id=message.author_id.id or False,
                message_type="comment",
                subtype_id=comment,
                attachment_ids=message.attachment_ids.ids or None,
            )

    # --- Chatter → canal -------------------------------------------------------------
    def _federation_chatter_to_channel(self):
        """Ce qui arrive au registre reparaît dans la fenêtre, si elle est ouverte."""
        federable = set(self.env["federation.federable"]._federation_models().values())
        candidats = self.filtered(
            lambda m: m.model in federable and m.res_id and m.body
            and m.message_type in ("comment", "email"))
        if not candidats:
            return
        note = self.env.ref("mail.mt_note").id
        pairs = {(m.model, m.res_id) for m in candidats}
        links = self.env["federation.link"].sudo().search(
            [("res_model", "in", sorted({m for m, _i in pairs})),
             ("res_id", "in", sorted({i for _m, i in pairs}))])
        if not links:
            return
        canaux = {c.federation_link_id.id: c for c in self.env["discuss.channel"].sudo().search(
            [("federation_link_id", "in", links.ids)])}
        by_record = {(l.res_model, l.res_id): l for l in links}
        for message in candidats:
            link = by_record.get((message.model, message.res_id))
            if not link:
                continue
            canal = canaux.get(link.id)
            if not canal:
                continue  # personne n'a ouvert le canal : rien à y mettre
            # Les notes internes restent au registre. Le canal est la conversation,
            # pas le journal de la fédération : y verser « l'échéance a bougé » le
            # noierait sous des avis que personne ne lit.
            if message.subtype_id.id == note:
                continue
            self.sudo().with_context(federation_to_channel=True).create({
                "model": "discuss.channel", "res_id": canal.id, "body": message.body,
                "message_type": "comment", "subtype_id": self.env.ref("mail.mt_comment").id,
                "author_id": message.author_id.id or False,
                "attachment_ids": [(6, 0, message.attachment_ids.ids)] if message.attachment_ids else False,
            })
