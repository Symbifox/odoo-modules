"""Le raccourci depuis une fiche de contact."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = "res.partner"

    linkpage_ids = fields.One2many("bf.linkpage", "partner_id", string="Pages de liens")
    linkpage_count = fields.Integer(compute="_compute_linkpage_count")

    @api.depends("linkpage_ids")
    def _compute_linkpage_count(self):
        # Un `read_group` plutôt qu'une boucle : la liste des contacts affiche
        # le bouton, et une requête par ligne s'y verrait.
        counts = dict(
            self.env["bf.linkpage"]._read_group(
                [("partner_id", "in", self.ids)],
                groupby=["partner_id"],
                aggregates=["__count"],
            )
        )
        for partner in self:
            partner.linkpage_count = counts.get(partner, 0)

    def action_view_linkpages(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Pages de liens"),
            "res_model": "bf.linkpage",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {
                "default_partner_id": self.id,
                "default_name": self.name,
                "default_kind": "org" if self.is_company else "owner",
            },
        }

    def action_create_org_linkpage(self):
        """Créer la page d'accueil de cette organisation.

        Elle naît PUBLIÉE, comme les pages d'employés, et pour la même raison :
        une page créée en brouillon rend un 404 indiscernable d'une adresse
        inconnue, et c'est toujours après l'envoi du courriel qu'on s'en
        aperçoit. Son adresse est opaque et son échéance armée : c'est là que
        se joue sa discrétion, pas dans l'état.
        """
        self.ensure_one()
        if not self.is_company:
            raise UserError(_(
                "« %s » est une personne. Une page d'organisation se crée "
                "depuis la fiche de l'entreprise.", self.display_name,
            ))
        existante = self.env["bf.linkpage"].search(
            [("partner_id", "=", self.id), ("kind", "=", "org")], limit=1
        )
        if existante:
            return {
                "type": "ir.actions.act_window",
                "res_model": "bf.linkpage",
                "res_id": existante.id,
                "view_mode": "form",
            }
        template = self.env["bf.linkpage.template"]._for_org()
        page = self.env["bf.linkpage"].create({
            "name": self.name,
            "partner_id": self.id,
            "kind": "org",
            "state": "published",
            "template_id": template.id if template else False,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.linkpage",
            "res_id": page.id,
            "view_mode": "form",
        }

    def action_create_linkpage(self):
        """Créer la page de ce contact, gabarit du groupe appliqué."""
        self.ensure_one()
        Template = self.env["bf.linkpage.template"]
        user = self.env["res.users"].search([("partner_id", "=", self.id)], limit=1)
        template = Template._for_user(user) if user else Template.browse()
        page = self.env["bf.linkpage"].create({
            "name": self.name,
            "partner_id": self.id,
            "user_id": user.id if user else False,
            "kind": "owner",
            "template_id": template.id if template else False,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.linkpage",
            "res_id": page.id,
            "view_mode": "form",
        }
