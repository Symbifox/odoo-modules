"""Le visiteur et SON entente.

Une demande de signature par personne, ancrée sur la ligne d'audience — celle
que le socle crée dès qu'une identité est confirmée, dans les deux modes. C'est
ce qui permet de dire, plus tard, non pas « l'entente a été signée » mais
« cette personne-là l'a signée, tel jour, depuis telle adresse ».
"""
import base64
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class SecureTransferAudience(models.Model):
    _inherit = "secure.transfer.audience"

    # Champ typé : ici, c'est légitime — ce module DÉPEND de bf_sign. C'est
    # précisément pour pouvoir l'écrire que le pont existe (le poser dans le
    # socle aurait rendu bf_sign obligatoire pour tous les locataires).
    nda_request_id = fields.Many2one(
        "bf.sign.request", string="Entente signée", readonly=True,
        ondelete="set null", copy=False,
    )
    nda_state = fields.Selection(
        selection=[
            ("none", "Aucune"),
            ("pending", "À signer"),
            ("signed", "Signée"),
            ("refused", "Refusée"),
        ],
        string="Entente", compute="_compute_nda_state", store=True,
    )
    nda_signed_on = fields.Datetime(string="Entente signée le", readonly=True)

    @api.depends("nda_request_id.state", "transfer_id.nda_required")
    def _compute_nda_state(self):
        for rec in self:
            if not rec.transfer_id.nda_required:
                rec.nda_state = "none"
            elif not rec.nda_request_id:
                rec.nda_state = "pending"
            elif rec.nda_request_id.state == "signed":
                rec.nda_state = "signed"
            elif rec.nda_request_id.state == "refused":
                rec.nda_state = "refused"
            else:
                rec.nda_state = "pending"

    # ------------------------------------------------------------------ lecture d'état
    def _nda_ok(self):
        """Ce visiteur peut-il passer la barrière ?

        ⚠ Lecture directe de l'état de la demande — pas du champ calculé
        stocké, qui pourrait être en retard d'un recalcul, et surtout pas d'un
        drapeau qu'on aurait posé nous-mêmes. La question « a-t-il signé ? »
        n'a qu'une seule source de vérité : bf_sign."""
        self.ensure_one()
        if not self.transfer_id.nda_required:
            return True
        request_rec = self.sudo().nda_request_id
        if not request_rec or request_rec.state != "signed":
            return False
        self._nda_seal_once(request_rec)
        return True

    def _nda_seal_once(self, request_rec):
        """À la PREMIÈRE fois qu'on constate la signature : horodater, verser
        l'entente signée au fil du transfert et l'inscrire au journal d'accès.

        Fait ici plutôt que dans un crochet de bf_sign parce que le crochet
        source ne se déclenche que sur un modèle doté d'un fil de discussion —
        ce que l'audience n'est pas. Constater vaut mieux qu'attendre d'être
        prévenu. Le garde-fou d'idempotence est ``nda_signed_on``."""
        self.ensure_one()
        if self.nda_signed_on:
            return
        self.sudo().nda_signed_on = request_rec.signed_on or fields.Datetime.now()
        transfer = self.transfer_id
        transfer._log(
            "nda_signed", actor=self.display_identity,
            note=_("Entente de confidentialité signée — empreinte SHA-256 : %s")
            % (request_rec.hash_signed or _("(non scellée)")),
        )
        # L'entente signée rejoint le fil du transfert : c'est là que
        # l'expéditeur ira la chercher, pas dans une autre application.
        try:
            attachments = self.env["ir.attachment"].sudo()
            for att in (request_rec.signed_attachment_id,
                        request_rec.certificate_attachment_id):
                if att:
                    attachments |= att.sudo().copy({
                        "res_model": transfer._name, "res_id": transfer.id})
            transfer.sudo().message_post(
                body=_("Entente de confidentialité signée par %(who)s "
                       "(demande %(ref)s).",
                       who=self.display_identity, ref=request_rec.name),
                attachment_ids=attachments.ids)
        except Exception:  # noqa: BLE001 — la preuve est déjà au journal
            _logger.exception(
                "bf_securetransfer_sign: dépôt de l'entente signée au fil de "
                "%s impossible", transfer.name)

    # ------------------------------------------------------------------ création
    def _nda_ensure_request(self, ip=None, ua=None):
        """La demande de signature de CE visiteur, créée au besoin.

        Sous le verrou de la ligne du transfert : deux onglets ouverts au même
        instant ne doivent pas produire deux ententes pour la même personne —
        on ne saurait ensuite laquelle fait foi."""
        self.ensure_one()
        if self.nda_request_id:
            return self.sudo().nda_request_id
        if self.identity_kind != "email" or not self.email:
            # Verrou de cohérence : `_audience_limits` retire déjà le canal
            # mobile quand une entente est exigée. Si on arrive ici, c'est
            # qu'une ligne mobile préexistait au réglage — elle ne peut pas
            # signer, et fabriquer une adresse serait pire.
            return self.env["bf.sign.request"]
        transfer = self.transfer_id
        config = transfer._nda_config()
        if not config:
            return self.env["bf.sign.request"]
        transfer._lock_row()
        self.invalidate_recordset(["nda_request_id"])
        if self.sudo().nda_request_id:
            return self.sudo().nda_request_id
        vals = {
            # L'identité a déjà été prouvée par le code du transfert, envoyé à
            # cette même adresse. Un second code ici n'ajouterait aucune preuve
            # et coûterait un abandon sur deux.
            "require_signer_otp": False,
            "title": _("Entente de confidentialité — %s")
            % (transfer.subject or transfer.name),
            "signing_order": "parallel",
        }
        if config["consent_text"]:
            vals["consent_text"] = config["consent_text"]
        request_rec = self.env["bf.sign.request"].sudo().create_from_record(
            self,
            document_file=config["document"],
            document_filename=config["filename"],
            signers=[{"name": self.email, "email": self.email}],
            field_template=config["field_template"] or None,
            vals=vals,
        )
        # `action_send` valide le PDF, fige l'empreinte d'origine, pose
        # l'échéance et ouvre la signature. Le contexte coupe le courriel
        # d'invitation : le visiteur est DEVANT nous, dans son navigateur, et
        # sur une audience de cinquante personnes cela ferait cinquante
        # courriels dont aucun n'était demandé.
        request_rec.with_context(st_nda_silent=True).action_send()
        self.sudo().nda_request_id = request_rec.id
        transfer._log(
            "nda_requested", actor=self.display_identity, ip=ip, ua=ua,
            note=_("Entente de confidentialité à signer (%s)") % request_rec.name)
        return request_rec

    def _nda_signing_url(self, ip=None, ua=None):
        """L'URL de signature personnelle de ce visiteur, ou ''."""
        self.ensure_one()
        request_rec = self._nda_ensure_request(ip=ip, ua=ua)
        if not request_rec:
            return ""
        signer = request_rec.sudo().signer_ids[:1]
        return signer._signing_url() if signer else ""

    def _nda_document_bytes(self):
        """Les octets de l'entente à présenter en aperçu, ou None."""
        self.ensure_one()
        config = self.transfer_id._nda_config()
        if not config or not config["document"]:
            return None
        return base64.b64decode(config["document"])
