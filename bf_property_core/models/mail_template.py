"""La bienvenue d'après-inscription, quand c'est la suite qui a invité.

🔴 Sans ceci, un copropriétaire invité par son syndicat
recevait, sitôt inscrit, « Bienvenue chez <la société qui héberge l'instance> ».
Le contrôleur d'inscription d'Odoo envoie toujours le même gabarit
(`auth_signup.mail_template_user_signup_account_created`) : c'est au moment de
l'envoi, et pour lui seul, que le gabarit du syndicat prend sa place. Toute
autre personne de l'instance garde la bienvenue ordinaire.
"""
from odoo import models

# Mises en page des courriels de la suite, par ordre de préférence : la première
# présente sur la base sert. `bluefox_branding` n'est PAS une dépendance ; installé,
# il range nos courriels avec ceux de la société (bandeau, accent, police, pied de
# marque), comme les factures, les résumés quotidiens et les banques d'heures.
# Sans lui, la mise en page légère d'Odoo. Même convention que `bf_babillard` et
# `bf_training`.
MAIL_LAYOUTS = (
    "bluefox_branding.bf_mail_layout",
    "mail.mail_notification_light",
)


def bf_mail_layout(env):
    """La mise en page de marque si elle est installée, sinon celle d'Odoo."""
    for xmlid in MAIL_LAYOUTS:
        if env.ref(xmlid, raise_if_not_found=False):
            return xmlid
    return False


class MailTemplate(models.Model):
    _inherit = "mail.template"

    def send_mail(self, res_id, force_send=False, raise_exception=False,
                  email_values=None, email_layout_xmlid=False):
        # 🔴 Seulement en superutilisateur, qui est la façon dont le contrôleur
        # d'inscription d'Odoo envoie ce courriel. `send_mail` est appelable par
        # RPC : une substitution ouverte à tout appelant, avec un `sudo()` qui
        # saute le contrôle d'accès du gabarit et des `email_values` relayés,
        # laissait un résident envoyer n'importe quel courriel à l'habillage du
        # syndicat, pièces jointes de la base comprises. Hors superutilisateur,
        # l'appel suit le chemin ordinaire d'Odoo, contrôle d'accès compris.
        welcome = self.env.ref(
            "auth_signup.mail_template_user_signup_account_created",
            raise_if_not_found=False)
        if self.env.su and welcome and len(self) == 1 and self == welcome:
            user = self.env["res.users"].browse(res_id).exists()
            organisation = user.partner_id.bf_property_invited_by_id if user else False
            if organisation:
                ours = self.env.ref("bf_property_core.mail_template_resident_welcome")
                return ours.with_context(
                    lang=user.lang, bf_organisation=organisation.name,
                ).send_mail(res_id, force_send=force_send,
                            raise_exception=raise_exception, email_values=email_values,
                            email_layout_xmlid=bf_mail_layout(self.env))
        return super().send_mail(
            res_id, force_send=force_send, raise_exception=raise_exception,
            email_values=email_values, email_layout_xmlid=email_layout_xmlid)
