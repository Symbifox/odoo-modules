"""Signatures follow their template.

Signatures are written, not live-computed — the value has to sit in
``res.users.signature`` because that is the field Odoo, the composer and every
third-party module read. The cost of that choice is that editing the template
does nothing until somebody re-renders. That was the standing gotcha in this
module's README: « après avoir modifié un gabarit, lancer l'action serveur ».

Here the template does the asking. Writing ``body_html`` on a template that a
company (or a person) uses as its signature re-renders everyone it drives, so
« ça se met à jour tout seul » is now true, and the manual button stays for the
cases where a company turned propagation off.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class MailTemplate(models.Model):
    _inherit = "mail.template"

    bf_is_signature = fields.Boolean(
        string="Modèle de signature",
        help="Marque ce gabarit comme un bloc de signature courriel. Sans "
             "cette case, il n'apparaît ni dans la galerie ni dans les "
             "sélecteurs de signature.\n\n"
             "Le marqueur est explicite plutôt que déduit du modèle "
             "res.users : Odoo livre lui-même des gabarits sur res.users "
             "(invitation 2FA, création de compte portail), et les proposer "
             "comme signatures ferait d'une invitation 2FA le pied de page "
             "de quelqu'un.",
    )

    def _bf_signature_admin(self):
        return self.env.su or self.env.user.has_group("base.group_system")

    @api.model_create_multi
    def create(self, vals_list):
        # Un gabarit de signature se rend en sudo et finit dans la signature de
        # toute une société : seule une personne administratrice en fait un.
        # Dans Odoo, un interne peut créer ses propres gabarits ; le marqueur
        # est ce qui les ferait entrer dans la galerie de tout le monde. Le
        # `default_` du contexte compte aussi : il s'applique après ce contrôle.
        if not self._bf_signature_admin() and (
                self.env.context.get("default_bf_is_signature")
                or any(vals.get("bf_is_signature") for vals in vals_list)):
            raise AccessError(_(
                "Seule une personne administratrice peut créer un modèle de "
                "signature."))
        return super().create(vals_list)

    def write(self, vals):
        if not self._bf_signature_admin() and (
                "bf_is_signature" in vals
                or any(self.sudo().mapped("bf_is_signature"))):
            raise AccessError(_(
                "Seule une personne administratrice peut modifier un modèle "
                "de signature."))
        res = super().write(vals)
        if "body_html" in vals and not self.env.context.get("install_mode"):
            self._bf_refresh_signatures()
        return res

    def _bf_refresh_signatures(self):
        """Re-render every signature driven by these templates.

        ``install_mode`` is excluded by the caller: during an upgrade the
        template is rewritten before the company brand data is, so a render
        here would use half-updated values. The install hook, and each
        version's migration, end with a full pass instead.
        """
        Company = self.env["res.company"].sudo()
        Users = self.env["res.users"].sudo()

        companies = Company.search([
            ("bf_signature_template_id", "in", self.ids),
            ("bf_signature_auto_apply", "=", True),
        ])
        users = Users.search([
            ("share", "=", False),
            "|",
            ("company_id", "in", companies.ids),
            ("bf_signature_template_id", "in", self.ids),
        ])
        # A personal override still obeys its company's propagation switch —
        # otherwise turning propagation off would only half work.
        users = users.filtered(lambda u: u.company_id.bf_signature_auto_apply)
        if not users:
            return
        users._bf_apply_company_signature()
        _logger.info(
            "bf_multi_company_email: %s signature(s) régénérée(s) après "
            "modification de gabarit %s", len(users), self.ids,
        )
