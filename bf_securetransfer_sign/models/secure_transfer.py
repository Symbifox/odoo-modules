"""La barrière : entre le code confirmé et le contenu, une entente signée.

Tout tient dans ``_extra_access_gate`` — le seul point d'extension que le socle
expose. Le reste du module sert à le renseigner honnêtement.
"""
import logging

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class SecureTransfer(models.Model):
    _inherit = "secure.transfer"

    nda_required = fields.Boolean(
        string="NDA required",
        default=False,
        tracking=True,
        help="The visitor must sign the NDA before the message and files "
             "are shown. A separate signature request is created for each "
             "person, under their confirmed identity.",
    )
    nda_document = fields.Binary(
        string="NDA (PDF)", attachment=True,
        help="Empty = the brand's NDA.",
    )
    nda_filename = fields.Char(string="File name")
    nda_field_template_id = fields.Many2one(
        "bf.sign.field.template", string="Signature field template", ondelete="restrict",
    )
    nda_signed_count = fields.Integer(
        string="Signed NDAs", compute="_compute_nda_signed_count",
    )
    # Le motif « entente » s'ajoute aux trois du socle. Le champ est CALCULÉ et
    # non stocké : `selection_add` n'y demande donc aucune politique
    # `ondelete`, contrairement au journal d'accès.
    recipient_otp_status = fields.Selection(
        selection_add=[("nda", "Required: NDA to sign")],
    )

    # ------------------------------------------------------------------ identité
    def _recipient_otp_required(self):
        """⚠ Une entente EXIGE le code du destinataire.

        On ne fait pas signer un anonyme : la demande de signature se crée sur
        une identité confirmée, et un document signé « par quelqu'un » ne vaut
        rien. Le prédicat force donc le code dès qu'une entente est exigée —
        exactement comme le mode audience ouverte le fait dans le socle.

        Ce n'est pas qu'une question de droit. Sans cela, un transfert à
        entente **sans** code n'aurait aucune ligne d'audience, la barrière
        n'aurait personne à qui parler, et le visiteur tournerait dans une
        boucle de redirection (mesuré au QA du 2026-08-21).
        """
        self.ensure_one()
        return self.nda_required or super()._recipient_otp_required()

    @api.depends("nda_required")
    def _compute_recipient_otp_status(self):
        super()._compute_recipient_otp_status()
        for rec in self:
            # L'entente passe devant « instance » et « transfert » : c'est le
            # motif que l'opérateur doit lire, parce que c'est celui qui ajoute
            # une étape visible pour le destinataire.
            if rec.nda_required and rec.audience_mode != "open":
                rec.recipient_otp_status = "nda"

    @api.depends("audience_ids.nda_state")
    def _compute_nda_signed_count(self):
        for rec in self:
            rec.nda_signed_count = len(
                rec.audience_ids.filtered(lambda a: a.nda_state == "signed"))

    @api.model_create_multi
    def create(self, vals_list):
        """Hériter la politique de la marque, comme le socle le fait déjà pour
        l'avis de téléchargement. Sans cela, un envoi public échapperait en
        silence à l'entente que la même marque impose à tout le monde."""
        for vals in vals_list:
            if "nda_required" in vals or not vals.get("brand_id"):
                continue
            brand = self.env["secure.transfer.brand"].browse(vals["brand_id"])
            if brand.exists() and brand.nda_required:
                vals["nda_required"] = True
        return super().create(vals_list)

    @api.constrains("nda_required", "nda_document", "brand_id")
    def _check_nda_available(self):
        for rec in self:
            if rec.nda_required and not rec._nda_document_source():
                raise ValidationError(_(
                    "This transfer requires an NDA, but no document is "
                    "available, neither on the transfer nor on the \"%s\" "
                    "brand. Visitors would be stuck.",
                    rec.brand_id.display_name,
                ))

    # ------------------------------------------------------------------ résolution
    def _nda_document_source(self):
        """L'enregistrement qui porte le PDF à faire signer : le transfert
        quand il en a un, sinon la marque. Rend un recordset vide si aucun.

        Rendre l'ENREGISTREMENT plutôt que les octets : le binaire est stocké
        en pièce jointe, et le lire ici le chargerait en mémoire à chaque
        contrôle de barrière — c'est-à-dire à chaque requête d'un visiteur."""
        self.ensure_one()
        if self.nda_document:
            return self
        if self.brand_id.nda_document:
            return self.brand_id
        return self.browse()

    def _nda_langue_lecteur(self):
        """La langue de l'expéditeur du transfert, sinon celle de la société.

        Le journal d'accès et le fil du transfert se lisent par l'expéditeur.
        Jamais la langue d'OdooBot ni d'un compte partagé ; None laisse le
        contexte sans langue (la source, anglaise).
        """
        self.ensure_one()
        installees = {code for code, _nom in self.env["res.lang"].get_installed()}
        expediteur = self.sudo().create_uid
        langues = []
        if expediteur and expediteur.active and not expediteur.share \
                and expediteur.id != SUPERUSER_ID:
            langues.append(expediteur.lang)
        langues.append((self.sudo().company_id or self.env.company).partner_id.lang)
        return next((lang for lang in langues if lang in installees), None)

    def _nda_config(self):
        """(document_b64, nom_de_fichier, gabarit, texte_de_consentement)."""
        self.ensure_one()
        source = self._nda_document_source()
        if not source:
            return None
        brand = self.brand_id
        return {
            "document": source.nda_document,
            "filename": (source.nda_filename
                         or _("NDA.pdf")),
            "field_template": (self.nda_field_template_id
                               or brand.nda_field_template_id),
            "consent_text": brand.nda_consent_text or False,
        }

    # ------------------------------------------------------------------ le canal SMS
    def _audience_limits(self):
        """⚠ Une entente retire l'identification par mobile.

        ``bf.sign.signer.email`` est obligatoire, et fabriquer une adresse pour
        satisfaire un modèle dans une pièce qu'on veut opposable n'est pas une
        option. Plutôt que de laisser un visiteur s'identifier par SMS puis se
        heurter à une entente qu'il ne peut pas signer, on ne lui offre pas ce
        chemin. Le socle propage : la page cesse d'afficher le formulaire
        mobile, et ``_audience_admissible`` refuse le canal."""
        limits = super()._audience_limits()
        if self.nda_required:
            limits["allow_sms"] = False
        return limits

    # ------------------------------------------------------------------ la barrière
    def _extra_access_gate(self, member, token):
        """Renvoyer vers la page d'entente tant qu'elle n'est pas signée.

        L'état est LU dans la demande de signature, à chaque requête. Ni
        drapeau de session, ni rappel : un rappel manqué (bf_sign n'appelle son
        crochet source que sur un modèle doté d'un fil de discussion, ce que
        l'audience n'est pas) rouvrirait la porte sans que personne le voie.
        """
        self.ensure_one()
        if not self.nda_required:
            return super()._extra_access_gate(member, token)
        if not member:
            # Pas d'identité confirmée. On ne laisse pas passer — mais on ne
            # renvoie SURTOUT PAS vers `/s/<token>`, qui est la page d'où cette
            # méthode est appelée : ce serait une boucle de redirection par
            # construction (mesurée au QA du 2026-08-21). La page d'entente sait
            # expliquer l'état et proposer de refaire l'étape du code.
            return "/s/%s/nda" % token
        if member._nda_ok():
            return super()._extra_access_gate(member, token)
        return "/s/%s/nda" % token
