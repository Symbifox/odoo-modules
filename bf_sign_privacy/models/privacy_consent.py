from odoo import _, fields, models
from odoo.exceptions import UserError


class PrivacyConsent(models.Model):
    """Troisième voie de signature pour un consentement : la signature maison.

    Les deux voies existantes (DocuSeal, LibreSign) restent en place : des
    consentements déjà signés portent leurs identifiants, et la preuve
    historique doit rester lisible.
    """

    _name = "privacy.consent"
    _inherit = ["privacy.consent", "bf.sign.mixin"]

    bf_sign_status = fields.Selection(
        selection=[
            ("sent", "Envoyé"),
            ("completed", "Signé"),
            ("refused", "Refusé"),
        ],
        string="Signature maison",
        readonly=True,
        copy=False,
    )
    bf_sign_sent_at = fields.Datetime(
        string="Signature demandée le", readonly=True, copy=False,
    )
    bf_sign_completed_at = fields.Datetime(
        string="Signé le", readonly=True, copy=False,
    )

    # ── Crochets bf.sign.mixin ───────────────────────────────────────────────
    def _sign_report_ref(self):
        """L'avis de consentement, rendu par ce module.

        ⚠️ PAS `privacy_consent.action_report_consent_certificate` : ce
        rapport-là porte le modèle `privacy.consent.evidence`, pas
        `privacy.consent`. Lui passer un consentement lève un MissingError au
        rendu (il cherche une preuve portant le même identifiant). Et sur le
        fond, un certificat atteste un geste DÉJÀ posé — ce n'est pas ce
        qu'on fait signer.
        """
        return "bf_sign_privacy.action_report_consent_form"

    def _sign_default_signers(self):
        """La personne concernée, c'est-à-dire le sujet du consentement.

        ⚠️ Le repli du mixin ne convient PAS ici : il cherche `partner_id`,
        qui n'existe pas sur `privacy.consent`. Le sujet est
        `subject_partner_id`, et il est requis.
        """
        self.ensure_one()
        partner = self.subject_partner_id
        if not partner:
            return []
        return [{
            "name": partner.name,
            "email": partner.email,
            "partner_id": partner.id,
        }]

    def _sign_document_filename(self):
        self.ensure_one()
        return "consentement-%s.pdf" % (self.id or "nouveau")

    def _sign_on_signed(self, request):
        """Signature complétée : même traitement que les deux autres voies."""
        self.ensure_one()
        self._process_bf_sign_completion(request)

    def _sign_on_refused(self, request, signer, reason=None):
        """Refus de signer : on le consigne, sans toucher au consentement.

        Refuser de SIGNER n'est pas refuser le consentement : la personne
        peut très bien l'accorder par le portail ensuite. `action_refuse()`
        fermerait la porte pour une raison qu'elle n'a pas donnée.
        """
        self.ensure_one()
        self.write({"bf_sign_status": "refused"})
        self.message_post(
            body=_(
                "Signature déclinée par %(signer)s%(reason)s. Le consentement "
                "reste dans son état actuel : décliner la signature n'est pas "
                "refuser le consentement.",
                signer=signer.display_name if signer else _("le signataire"),
                reason=_(" — motif : %s", reason) if reason else "",
            ),
            message_type="notification",
        )

    # ── Actions ──────────────────────────────────────────────────────────────
    def action_send_bf_sign(self):
        """Envoyer le consentement en signature par bf_sign.

        ⚠️ La garde est ICI, au point d'envoi. Une fois la demande partie, le
        signataire a le lien : la refuser au retour ne rappellerait rien.
        """
        self.ensure_one()
        if self.status in ("withdrawn", "refused"):
            raise UserError(_(
                "Ce consentement est %s : il ne peut pas être envoyé en "
                "signature. Créer un nouveau consentement, ou le renouveler.",
                dict(self._fields["status"]._description_selection(self.env)).get(
                    self.status, self.status),
            ))
        if not self.subject_partner_id.email:
            raise UserError(_(
                "La personne concernée (%s) n'a pas d'adresse courriel : "
                "il n'y a nulle part où envoyer la demande de signature.",
                self.subject_partner_id.display_name,
            ))
        action = self.action_send_for_signature()
        self.write({
            "bf_sign_status": "sent",
            "bf_sign_sent_at": fields.Datetime.now(),
        })
        return action

    def _process_bf_sign_completion(self, request):
        """Consigner la signature maison et accorder le consentement.

        Calqué sur `_process_libresign_completion` : preuve `pdf_signed`,
        méthode de collecte « signature », puis `action_grant()`. Diverger
        ici produirait deux définitions de « consentement signé ».
        """
        self.ensure_one()
        self.write({
            "bf_sign_status": "completed",
            "bf_sign_completed_at": fields.Datetime.now(),
            "collection_method": "signature",
        })

        attachment = request.signed_attachment_id
        if attachment:
            self.env["privacy.consent.evidence"].create({
                "consent_id": self.id,
                "evidence_type": "pdf_signed",
                "attachment_file": attachment.datas,
                "attachment_filename": attachment.name or "consentement-signe.pdf",
                "note": _(
                    "Signé via bf_sign (demande %(name)s, empreinte SHA-256 "
                    "du document signé : %(hash)s)",
                    name=request.name,
                    hash=request.hash_signed or _("non calculée"),
                ),
            })

        if self.status != "granted":
            self.action_grant()

        self.message_post(
            body=_("Consentement signé par signature électronique maison "
                   "(bf_sign) et accordé."),
            message_type="notification",
        )
