"""L'accès au portail d'un résident, donné par le gestionnaire.

🔴 Joué avec un vrai compte de gestionnaire,
inviter un résident au portail et lui renvoyer son accès exigeaient
l'administrateur (`portal.wizard` et `res.partner` en écriture sont réservés
à la création de contacts). Le portail de la suite n'existait donc que pour
les syndicats dont l'administrateur faisait le geste à la main.

Le geste passe en `sudo`, APRÈS la garde du gestionnaire, et il est borné :
une personne sans adresse ou qui a un compte INTERNE est écartée, jamais
convertie. Le portail n'est pas une porte vers le back-office.
"""
from odoo import _, fields, models
from odoo.exceptions import AccessError
from odoo.tools import email_normalize

from .mail_template import bf_mail_layout


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Qui a ouvert le portail à cette personne. Sert à une seule chose : que la
    # bienvenue d'après-inscription soit celle du syndicat (ou du bailleur), et
    # non celle de la société qui héberge l'instance.
    bf_property_invited_by_id = fields.Many2one(
        "bf.property.organisation", string="Invité au portail par",
        readonly=True, copy=False, index="btree_not_null")

    def _bf_property_grant_portal(self, template_xmlid, organisation):
        """Invite au portail, ou renvoie l'accès. Rend (invités, renvoyés, écartés).

        🔴 L'invitation est celle du SYNDICAT (ou du bailleur), pas celle de la
        société qui héberge l'instance. L'assistant
        du portail envoie toujours le gabarit global
        `portal.mail_template_data_portal_welcome`, que l'habillage d'un
        locataire récrit pour SES clients : un copropriétaire recevait « vos
        factures, vos comptes rendus et le suivi de votre mandat », sans le nom
        de son syndicat. Le compte se crée donc ici, par la même fonction que
        l'assistant, et c'est `template_xmlid` qui part.

        ⚠️ Les modules `portal` et `auth_signup` sont requis à l'exécution :
        seuls les modules qui en dépendent (portail de l'occupant, portail du
        locataire) appellent cette méthode, avec leur propre gabarit.
        """
        if not (self.env.su or self.env.user.has_group(
                "bf_property_core.group_bf_property_manager")):
            raise AccessError(
                _("Donner l'accès au portail relève du syndicat, pas de l'occupant."))
        template = self.env.ref(template_xmlid)
        group_portal = self.env.ref("base.group_portal")
        group_public = self.env.ref("base.group_public")
        Partner = self.env["res.partner"]
        invited, resent, skipped = Partner, Partner, Partner
        for partner in self:
            email = email_normalize(partner.email or "")
            if not email:
                skipped |= partner
                continue
            users = partner.sudo().with_context(active_test=False).user_ids
            if users.filtered(lambda u: not u.share):
                skipped |= partner
                continue
            user = users.filtered("active")[:1]
            if user:
                # 🔴 Le renvoi est lui aussi celui du syndicat :
                # `action_reset_password` envoyait le courriel
                # générique d'Odoo, « compte Odoo » et signature OdooBot.
                signup_type = "reset"
                resent |= partner
            else:
                user = users[:1]
                company = partner.company_id or self.env.company
                if not user:
                    user = self.env["res.users"].sudo().with_company(company).with_context(
                        no_reset_password=True)._create_user_from_template({
                            "email": email, "login": email, "partner_id": partner.id,
                            "company_id": company.id, "company_ids": [(6, 0, company.ids)],
                        })
                # Un accès retiré passait au groupe public : même geste que l'assistant.
                user.write({"active": True,
                            "groups_id": [(4, group_portal.id), (3, group_public.id)]})
                signup_type = "signup"
                invited |= partner
            partner.sudo().write({"bf_property_invited_by_id": organisation.id})
            partner.sudo().signup_prepare(signup_type=signup_type)
            lang = user.lang or self.env.lang
            # 🔴 PAS `signup_force_type_in_url=""`, que l'assistant du portail
            # emploie : le lien menait à /web/login?token=…, une simple page de
            # connexion sans champ pour choisir un mot de passe. Le résident ne
            # pouvait pas entrer (lien suivi jusqu'au bout).
            url = partner.sudo().with_context(lang=lang)._get_signup_url_for_action()[partner.id]
            template.sudo().with_context(
                lang=lang, portal_url=url, bf_organisation=organisation.sudo().name,
            ).send_mail(user.id, force_send=True,
                        email_layout_xmlid=bf_mail_layout(self.env))
        return invited, resent, skipped

    def _bf_property_portal_summary(self, invited, resent, skipped):
        """La phrase du fil et de la bannière, la même aux deux endroits."""
        parts = []
        if invited:
            parts.append(_("Invités : %s.") % ", ".join(invited.mapped("name")))
        if resent:
            parts.append(_("Accès renvoyé : %s.") % ", ".join(resent.mapped("name")))
        if skipped:
            parts.append(_("Écartés (sans adresse ou compte interne) : %s.")
                         % ", ".join(skipped.mapped("name")))
        return " ".join(parts) or _("Personne à inviter.")
