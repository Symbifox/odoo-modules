# Part of Healthy Fox. See LICENSE file for full copyright and licensing details.
from odoo import _, api, models
from odoo.exceptions import UserError, ValidationError

#: Les fiches santé à fil. Lu aussi par
#: l'écran (`static/src/xml/chatter_note_only.xml`) : garder les deux listes
#: identiques.
MODELES_NOTE_SEULE = (
    "health.condition",
    "health.lab.test",
    "health.medication",
    "health.mood.settings",
    "health.reduction.step",
    "health.screening",
    "health.workout",
)


class HealthNoteOnly(models.AbstractModel):
    """Fil d'une fiche santé : notes internes seulement, aucune notification.

    ⚠️ À placer AVANT `mail.thread` dans `_inherit` : sinon le `message_post`
    du cœur passe le premier et ce filtre ne joue jamais.

    🔴 Le bouton « Envoyer un message » d'Odoo
    envoyait le texte, et la fiche en lien, aux abonnés que la personne aurait
    ajoutés, par courriel. Masquer le bouton ne protège rien : le compositeur
    en mode commentaire, `message_post` par l'API et le message d'activité
    terminée passent par ici.

    - un message « discussion » (`mail.mt_comment`) devient une note interne ;
    - personne n'est ajouté en destinataire (une mention donnerait au
      partenaire la lecture du message, donc du texte de santé), et aucun
      numéro ne reçoit de SMS ;
    - le fil ne calcule aucun destinataire : ni courriel, ni boîte de
      réception, quel que soit le sous-type (activité terminée comprise) ;
    - 🔴 relecture adverse du 2026-10-08 : aucun avis direct
      (`message_notify`, que font l'invitation à suivre et l'activité assignée
      à autrui : le destinataire lisait le message, donc le nom et le texte de
      la fiche), et personne d'autre que la personne n'en devient abonnée.

    Le compositeur en masse, qui écrit ses courriels sans `message_post`, est
    refusé à part (`mail.compose.message`, plus bas).
    """

    _name = "bf.health.note.only"
    _description = "Fil santé : notes internes seulement"

    def message_post(self, **kwargs):
        comment = self.env["ir.model.data"]._xmlid_to_res_id("mail.mt_comment")
        if kwargs.get("subtype_xmlid") == "mail.mt_comment" or (
                kwargs.get("subtype_id") and kwargs["subtype_id"] == comment):
            kwargs.pop("subtype_id", None)
            kwargs["subtype_xmlid"] = "mail.mt_note"
        kwargs["partner_ids"] = []
        # Module `sms` : `_notify_thread` texte ces numéros même sans destinataire.
        kwargs.pop("sms_numbers", None)
        kwargs.pop("sms_pid_to_number", None)
        return super().message_post(**kwargs)

    def message_notify(self, **kwargs):
        return self.env["mail.message"]

    def _notify_get_recipients(self, message, msg_vals, **kwargs):
        return []

    def message_subscribe(self, partner_ids=None, subtype_ids=None):
        if not partner_ids:
            return super().message_subscribe(partner_ids=partner_ids, subtype_ids=subtype_ids)
        for rec in self:
            la_sienne = rec.sudo().create_uid.partner_id.id
            super(HealthNoteOnly, rec).message_subscribe(
                partner_ids=[p for p in partner_ids if p == la_sienne], subtype_ids=subtype_ids)
        return True


class MailActivity(models.Model):
    _inherit = "mail.activity"

    @api.constrains("user_id", "res_model", "res_id")
    def _check_bf_health_a_la_personne(self):
        """🔴 Relecture adverse du 2026-10-08 : une activité assignée à une autre
        personne lui donnait la lecture de son résumé, de sa note et du nom de
        la fiche, qu'elle ne peut pas lire. Une activité de Healthy Fox reste à
        la personne qui tient la fiche (ou au compte système, repli des crons
        quand cette personne est archivée)."""
        for act in self.sudo():
            if act.res_model not in MODELES_NOTE_SEULE or not act.user_id or not act.res_id:
                continue
            fiche = self.env[act.res_model].sudo().browse(act.res_id).exists()
            if not fiche:
                continue
            if act.user_id not in (fiche.create_uid | self.env.ref("base.user_root")):
                raise ValidationError(_(
                    "Une activité de Healthy Fox reste à la personne qui tient la fiche."))


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    def _action_send_mail(self, auto_commit=False):
        """Le mode masse écrit ses courriels lui-même, sans passer par le
        fil de la fiche. Il est refusé sur les fiches santé."""
        for wizard in self:
            if wizard.composition_mode == "mass_mail" and wizard.model in MODELES_NOTE_SEULE:
                raise UserError(_("Une fiche de Healthy Fox ne s'envoie pas par courriel."))
        return super()._action_send_mail(auto_commit=auto_commit)
