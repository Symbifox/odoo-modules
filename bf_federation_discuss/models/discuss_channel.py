"""Le canal d'un objet fédéré.

Le canal est la fenêtre, le chatter est le registre. Ce fichier ne fait que
tenir le lien entre les deux et poser les membres ; le va-et-vient des messages
est dans `mail_message.py`, où il tient en deux gardes.
"""

from odoo import _, api, fields, models


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    federation_link_id = fields.Many2one(
        "federation.link", string="Objet fédéré", index=True, copy=False, ondelete="set null",
        help="Le canal d'un objet partagé avec un pair. Ce qui s'y dit passe par le chatter "
             "de l'objet, qui reste le registre.")

    @api.model
    def _federation_open(self, link):
        """Le canal de ce lien, créé au premier besoin et jamais avant."""
        existant = self.sudo().search([("federation_link_id", "=", link.id)], limit=1)
        if existant:
            existant._federation_sync_members(link)
            return existant
        record = link._record().exists()
        nom = record._federation_mirror_name() if record else link.display_name
        canal = self.sudo().with_context(mail_create_nosubscribe=True).create({
            "name": _("%s ⇄ %s") % (nom[:80], link.peer_id.name),
            "channel_type": "group",
            "federation_link_id": link.id,
            "description": _("Conversation avec %s au sujet de « %s ». Ce qui s'écrit ici est "
                             "posté au chatter de l'objet, d'où il part chez le pair. Comptez "
                             "deux à quatre minutes par aller-retour.") % (link.peer_id.name, nom[:120]),
        })
        canal._federation_sync_members(link)
        return canal

    def _federation_sync_members(self, link=None):
        """Les membres du canal sont les gens d'ici que l'objet concerne. Jamais le pair :
        il a son propre canal, chez lui, avec ses propres gens."""
        self.ensure_one()
        link = link or self.federation_link_id
        if not link:
            return
        partners = link._notify_partners() | self.env.user.partner_id
        users = partners.mapped("user_ids").filtered(lambda u: u.active and not u.share)
        a_ajouter = users.mapped("partner_id") - self.channel_member_ids.mapped("partner_id")
        if a_ajouter:
            self.sudo().add_members(partner_ids=a_ajouter.ids)
