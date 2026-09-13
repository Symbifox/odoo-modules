from odoo import _, fields, models
from odoo.exceptions import UserError


class FederationLink(models.Model):
    _inherit = "federation.link"

    channel_id = fields.Many2one("discuss.channel", string="Canal", compute="_compute_channel",
                                 search="_search_channel")
    has_channel = fields.Boolean(string="Canal ouvert", compute="_compute_channel")

    def _compute_channel(self):
        canaux = {c.federation_link_id.id: c for c in self.env["discuss.channel"].sudo().search(
            [("federation_link_id", "in", self.ids)])} if self.ids else {}
        for link in self:
            canal = canaux.get(link.id) or self.env["discuss.channel"]
            link.channel_id = canal
            link.has_channel = bool(canal)

    def _search_channel(self, operator, value):
        canaux = self.env["discuss.channel"].sudo().search([("id", operator, value)])
        return [("id", "in", canaux.mapped("federation_link_id").ids)]

    def _federation_may_read(self, partners):
        """Parmi ces contacts, ceux dont l'utilisateur interne peut LIRE l'objet lié.

        C'est le seul contrôle qui ait un acteur à juger. Au moment où un message
        est créé, il n'y en a pas : Odoo pose les messages de chatter et de canal
        en superutilisateur, donc `create_uid` vaut `__system__` et `author_id` est
        souvent le partenaire de la société, qui ne porte aucun utilisateur. Une
        sonde au banc l'a montré avant qu'on écrive la mauvaise garde.
        """
        self.ensure_one()
        record = self._record().exists()
        if not record:
            return self.env["res.partner"]
        gardes = self.env["res.partner"]
        for partenaire in partners:
            utilisateur = partenaire.sudo().user_ids.filtered(
                lambda u: u.active and not u.share)[:1]
            if not utilisateur:
                continue
            lecteur = record.with_user(utilisateur)
            permis = lecteur.has_access("read") if hasattr(lecteur, "has_access") else True
            if permis:
                gardes |= partenaire
        return gardes

    def action_open_channel(self):
        """Ouvrir (et créer au besoin) le canal de cet objet, puis y aller."""
        self.ensure_one()
        if not self.active:
            raise UserError(_("Ce lien n'est plus actif : rien ne partirait du canal."))
        canal = self.env["discuss.channel"]._federation_open(self)
        return {"type": "ir.actions.act_window", "res_model": "discuss.channel",
                "res_id": canal.id, "view_mode": "form", "target": "current",
                "name": canal.name}
