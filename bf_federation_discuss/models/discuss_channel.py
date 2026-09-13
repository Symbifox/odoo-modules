"""Le canal d'un objet fédéré.

Le canal est la fenêtre, le chatter est le registre. Ce fichier ne fait que
tenir le lien entre les deux et poser les membres ; le va-et-vient des messages
est dans `mail_message.py`, où il tient en deux gardes.
"""

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


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

    def _add_members(self, *, guests=None, partners=None, users=None, **kwargs):
        """🔴 Un canal fédéré ne se peuple pas librement.

        `add_members` est publique et un membre peut en inviter un autre. Sur un
        canal ordinaire c'est le bon comportement ; sur un canal fédéré, entrer
        dans le canal donne le droit de parler chez le pair, parce que tout ce qui
        s'y écrit part au chatter de l'objet puis sur le réseau. On n'y admet donc
        que les gens qui pourraient déjà LIRE cet objet.

        Le contrôle est ici et pas à la création du message : au moment du message,
        il n'y a pas d'acteur à juger. Odoo pose les messages de chatter et de canal
        en superutilisateur, `create_uid` vaut alors `__system__` et `author_id` est
        souvent le partenaire de la société, qui ne porte aucun utilisateur.
        """
        fedérés = self.filtered("federation_link_id")
        if fedérés and (partners or users):
            # ⚠️ On peut entrer dans un canal par `partners` OU par `users` : filtrer
            # l'un sans l'autre laisserait la porte grande ouverte à côté.
            candidats = (partners or self.env["res.partner"]) | (
                users.partner_id if users else self.env["res.partner"])
            for canal in fedérés:
                permis = canal.federation_link_id._federation_may_read(candidats)
                refuses = candidats - permis
                if refuses:
                    _logger.info(
                        "bf_federation_discuss: %s écarté(s) du canal %s, sans accès à %s,%s",
                        len(refuses), canal.id, canal.federation_link_id.res_model,
                        canal.federation_link_id.res_id)
                candidats = permis
            partners = candidats
            users = None
            if not partners and not guests:
                return self.env["discuss.channel.member"]
        return super()._add_members(guests=guests, partners=partners, users=users, **kwargs)

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
