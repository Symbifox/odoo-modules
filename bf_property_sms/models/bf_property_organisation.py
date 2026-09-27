"""Le canal texto du syndicat : l'interrupteur, et ce qu'il n'ouvre pas.

⚠️ **Deux interrupteurs, pas un.** Celui-ci ouvre le canal pour le syndicat ;
le consentement de la personne ouvre sa porte à elle. Les deux sont à l'arrêt
par défaut, et le premier sans le second n'envoie rien.

⚠️ **Ce que le fournisseur garde ne suit pas la purge.** Le journal des colis et
des visiteurs s'efface après la durée que le syndicat a fixée. Le fournisseur de
texto garde ce qu'il garde, et l'appareil du destinataire aussi. Le module
l'écrit à l'écran plutôt que de laisser croire à une conservation qui ne vaut
que chez lui.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

# ⚠️ Le message en dit le moins possible. Pas de numéro de porte, pas de
# transporteur, pas de référence de suivi : rien de tout cela n'est nécessaire
# pour que la personne descende chercher son colis, et tout cela traverserait un
# réseau de télécommunication.
PARCEL_TEMPLATE = "%(syndicat)s : un colis vous attend a la reception."
URGENT_TEMPLATE = "%(syndicat)s : %(title)s"


class BfPropertyOrganisation(models.Model):
    _inherit = "bf.property.organisation"

    sms_enabled = fields.Boolean(
        string="Avis par texto",
        default=False,
        help="⚠️ À l'arrêt par défaut. L'ouvrir ne suffit pas : chaque personne "
             "doit avoir consenti de son côté (art. 1070 al. 1 C.c.Q.).",
    )
    sms_line_id = fields.Many2one(
        "sms.archive.line",
        string="Ligne d'envoi",
        help="La ligne VoIP.ms depuis laquelle les avis partent.",
    )
    sms_consent_ids = fields.One2many(
        "bf.property.sms.consent", "organisation_id", string="Consentements"
    )
    sms_consent_count = fields.Integer(compute="_compute_sms_consent_count")

    def _compute_sms_consent_count(self):
        grouped = self.env["bf.property.sms.consent"]._read_group(
            [("organisation_id", "in", self.ids), ("active_consent", "=", True)],
            ["organisation_id"],
            ["__count"],
        )
        counts = {s.id: n for s, n in grouped}
        for syndicat in self:
            syndicat.sms_consent_count = counts.get(syndicat.id, 0)

    def _sms_ready(self):
        """Le canal est-il ouvert, et par quoi ? Rend la ligne ou rien."""
        self.ensure_one()
        if not self.sms_enabled or not self.sms_line_id:
            return self.env["sms.archive.line"].browse()
        return self.sms_line_id

    def _send_property_sms(self, partner, channel, body):
        """Envoie, ou explique en silence pourquoi il n'envoie pas.

        ⚠️ Rend un couple (envoyé, raison). Aucun appel ne lève : un colis
        s'enregistre même quand personne ne peut être prévenu, et le concierge
        n'a pas à voir une erreur parce qu'un occupant n'a pas donné son
        numéro. La raison est consignée au fil de l'enregistrement d'origine.
        """
        self.ensure_one()
        line = self._sms_ready()
        if not line:
            # ⚠️ Deux causes, deux phrases : « fermé »
            # se lisait aussi quand le canal était ouvert sans ligne choisie, et
            # envoyait le gestionnaire chercher le mauvais réglage.
            if not self.sms_enabled:
                return False, _("le canal texto du syndicat est fermé")
            return False, _(
                "aucune ligne d'envoi n'est choisie sur la fiche du syndicat"
            )
        consent = self.env["bf.property.sms.consent"]._for(partner, self, channel)
        if not consent:
            return False, _(
                "%s n'a pas consenti à être joint par texto pour ce canal"
            ) % partner.display_name
        # 🔴 Au nom du PROPRIÉTAIRE de la ligne.
        # `bf_sms_archive` n'autorise l'envoi qu'au propriétaire de la ligne, à
        # ses membres et au gestionnaire SMS, en lisant `env.user` : le `sudo`
        # ne le change pas. Le concierge qui enregistrait le colis obtenait
        # « Cette ligne ne vous est pas accessible » et le texto ne partait
        # jamais. Le droit de parler par cette ligne, c'est le syndicat qui
        # l'a donné en la choisissant ; l'appelant, lui, est déjà gardé.
        sender = line.sudo().owner_id or self.env.user
        try:
            self.env["sms.archive.message"].with_user(sender).sudo().action_send(
                line.id, consent.phone, body
            )
        except UserError as exc:
            return False, str(exc).rstrip(". ")
        return True, ""

    def action_view_sms_consents(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Consentements aux avis par texto"),
            "res_model": "bf.property.sms.consent",
            "view_mode": "list,form",
            "domain": [("organisation_id", "=", self.id)],
            "context": {"default_organisation_id": self.id},
        }
