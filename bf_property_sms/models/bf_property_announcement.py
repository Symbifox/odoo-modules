"""L'avis urgent part par texto, à ceux que l'annonce vise et qui ont consenti.

⚠️ **L'auditoire de l'annonce décide qui reçoit**, exactement comme au portail :
une annonce réservée aux copropriétaires ne part pas au locataire, même si le
locataire a consenti aux avis urgents. Le consentement dit « je veux être
joint », il ne dit pas « montrez-moi ce qui ne me regarde pas ».

⚠️ Rien ne part tout seul. L'envoi est un bouton, pas un effet de la
publication : une annonce se publie souvent bien avant d'être urgente, et
« urgent » est un jugement que le syndicat pose, pas le module.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .bf_property_organisation import URGENT_TEMPLATE


class BfPropertyAnnouncement(models.Model):
    # ⚠️ `_name` explicite : Odoo ne le déduit du `_inherit` que lorsque
    # celui-ci est une chaîne. Passé en liste, le modèle se retrouve sans
    # nom et le chargement s'arrête.
    _name = "bf.property.announcement"
    _inherit = ["bf.property.announcement", "bf.property.organisation.authority"]

    sms_sent_date = fields.Datetime(
        string="Avis par texto envoyé le", readonly=True, copy=False
    )
    sms_sent_count = fields.Integer(
        string="Destinataires joints", readonly=True, copy=False
    )

    def action_send_urgent_sms(self):
        """Envoie l'annonce par texto à qui elle vise et qui a consenti.

        🔴 La garde d'autorité est la PREMIÈRE ligne, avant toute lecture et
        tout envoi. Sans elle, un résident appelait la
        méthode par RPC, un texto partait chez le fournisseur, et
        l'`AccessError` de l'écriture finale annulait la transaction. La base
        de données était sauve ; le texto, lui, était parti — et comme
        `sms_sent_date` restait vide, l'appel se rejouait sans limite.
        """
        self._ensure_organisation_decides(_("Envoyer un avis urgent par texto"))
        self.ensure_one()
        if not self.published:
            raise UserError(
                _("Publiez l'annonce avant de l'envoyer par texto : le portail "
                  "doit pouvoir la relire.")
            )
        if self.sms_sent_date:
            raise UserError(
                _("« %s » a déjà été envoyée par texto le %s.")
                % (self.name, fields.Datetime.to_string(self.sms_sent_date))
            )
        units = self.env["bf.property.unit"].sudo()
        syndicat = self.organisation_id.sudo()
        body = URGENT_TEMPLATE % {"syndicat": syndicat.name, "title": self.name}

        sent, skipped = 0, []
        for consent in syndicat.sms_consent_ids.filtered(
            lambda c: c.active_consent and c.channel_urgent
        ):
            # ⚠️ L'auditoire de l'annonce, pas seulement le consentement.
            audiences = units._portal_audiences_for(consent.partner_id)
            if self.audience not in audiences.get(syndicat.id, set()):
                skipped.append(consent.partner_id.display_name)
                continue
            ok, reason = syndicat._send_property_sms(
                consent.partner_id, "urgent", body
            )
            if ok:
                sent += 1
            else:
                skipped.append("%s (%s)" % (consent.partner_id.display_name, reason))

        self.write({"sms_sent_date": fields.Datetime.now(), "sms_sent_count": sent})
        self.message_post(
            body=_("Avis par texto : %(sent)d joint(s).%(skipped)s")
            % {
                "sent": sent,
                "skipped": (_(" Non joints : %s.") % ", ".join(skipped))
                if skipped else "",
            }
        )
        return True
