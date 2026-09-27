"""Le consentement de l'art. 1070 al. 1, rattaché au dossier Loi 25.

Le module tient déjà une ligne de consentement par personne et par syndicat :
datée, révocable, avec le canal accepté. C'est ce que le Code civil exige, et
c'est complet de ce point de vue. Mais du point de vue de la Loi 25, cette ligne
est une île : elle n'apparaît nulle part au registre des activités de
traitement, et un responsable qui doit répondre « sur quoi repose la collecte de
ce numéro » doit aller la chercher dans un module qu'il ne connaît peut-être
pas.

Le pont fait donc une seule chose, et il la fait dans les deux sens : quand une
ligne naît, un `privacy.consent` naît avec elle, accordé, rattaché à l'avis et
à sa version ; quand elle est retirée, celui-là est révoqué le même jour.

⚠️ **Le consentement reste celui du module, et le pont ne le déplace pas.** Ce
qui décide si un texto part, c'est `bf.property.sms.consent`, et il continue de
le décider seul. Faire dépendre l'envoi du dossier Loi 25 introduirait un
deuxième interrupteur, donc un jour où les deux ne diront plus la même chose et
où le syndicat ne saura pas lequel croire. Le dossier Loi 25 est un miroir, pas
une vanne.

⚠️ **Le pont ne recrée rien pour l'existant.** Installer ce module sur une base
qui porte déjà des consentements ne fabrique pas rétroactivement des dossiers
Loi 25 datés d'aujourd'hui : cela donnerait une chaîne de preuve qui ment sur sa
propre date. Les lignes antérieures gardent leur date et restent sans dossier,
et l'écran le dit.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class BfPropertySmsConsent(models.Model):
    _name = "bf.property.sms.consent"
    _inherit = "bf.property.sms.consent"

    privacy_consent_id = fields.Many2one(
        "privacy.consent",
        string="Dossier Loi 25",
        ondelete="set null",
        copy=False,
        readonly=True,
        help="Le consentement formel correspondant au registre des activités "
             "de traitement. C'est un miroir : ce qui décide si un texto part "
             "reste la ligne ci-dessus.",
    )
    privacy_consent_status = fields.Selection(
        related="privacy_consent_id.status",
        string="Statut au dossier Loi 25",
        readonly=True,
    )

    def _privacy_notice(self):
        return self.env.ref(
            "bf_property_privacy.notice_property_sms", raise_if_not_found=False
        )

    @api.model_create_multi
    def create(self, vals_list):
        consents = super().create(vals_list)
        consents._privacy_mirror_grant()
        return consents

    def _privacy_mirror_grant(self):
        """Ouvre le dossier Loi 25 des lignes en vigueur qui n'en ont pas.

        ⚠️ Ne lève jamais. Un dossier de conformité qui n'a pas pu s'ouvrir est
        un problème à régler, pas une raison d'empêcher un syndicat de
        recueillir le consentement qu'on lui demande de recueillir. L'échec est
        journalisé et l'écran montre la ligne comme non rattachée.
        """
        notice = self._privacy_notice()
        if not notice:
            _logger.warning(
                "bf_property_privacy : avis de consentement introuvable, "
                "les lignes ne sont pas rattachées au registre Loi 25."
            )
            return
        Consent = self.env["privacy.consent"].sudo()
        for record in self:
            if record.privacy_consent_id or not record.active_consent:
                continue
            try:
                formal = Consent.create(
                    {
                        "subject_partner_id": record.partner_id.id,
                        "notice_id": notice.id,
                        "purpose_id": notice.purpose_id.id,
                        "status": "granted",
                        "granted_at": fields.Datetime.to_datetime(
                            record.given_date
                        ),
                        # Le syndicat recueille au comptoir ou par formulaire ;
                        # le module ne présume pas d'un portail qu'il n'a pas.
                        "collection_method": "written",
                        "notes": record.note or "",
                    }
                )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "bf_property_privacy : dossier Loi 25 non ouvert pour %s",
                    record.partner_id.display_name,
                )
                continue
            record.privacy_consent_id = formal.id

    def action_withdraw(self):
        """Ferme la ligne, puis le dossier Loi 25 qui la reflète."""
        result = super().action_withdraw()
        for record in self:
            formal = record.privacy_consent_id.sudo()
            if not formal or formal.status == "withdrawn":
                continue
            formal.write(
                {
                    "status": "withdrawn",
                    "withdrawn_at": fields.Datetime.to_datetime(
                        record.withdrawn_date
                    ),
                    "withdrawal_reason": _(
                        "Retiré au registre du syndicat (art. 1070 al. 1 "
                        "C.c.Q.), le %s."
                    )
                    % record.withdrawn_date,
                }
            )
        return result
