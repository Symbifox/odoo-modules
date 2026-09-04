# Part of bf_recruitment_sign. Voir LICENSE.
import base64

from odoo import _, models
from odoo.exceptions import UserError

LETTER_REPORT = "bf_letter_writer.action_report_letter_document"


class LetterDocument(models.Model):
    _name = "letter.document"
    _inherit = ["letter.document", "bf.sign.mixin"]

    # ------------------------------------------------------------------
    # Ce qui part sous la signature
    # ------------------------------------------------------------------

    def _sign_report_ref(self):
        """Le repli, pour une lignée de `bf_sign` sans `_sign_document_file`.

        ⚠️ Ce rapport rend le CORPS de la lettre. Il porte l'en-tête dans
        quatre modes sur cinq, parce que ceux-là sont dessinés par le gabarit
        QWeb lui-même. Le cinquième, `pdf_overlay`, superpose un PDF téléversé
        APRÈS le rendu : le rapport seul ne l'a pas.
        """
        return LETTER_REPORT

    def _sign_document_file(self):
        """Le PDF complet, en-tête compris.

        `_get_pdf_binary()` rend le rapport PUIS superpose le papier en-tête de
        la société. C'est le seul document qui soit celui que le destinataire a
        lu, et c'est donc le seul qu'on ait le droit de lui faire signer.
        """
        self.ensure_one()
        return base64.b64encode(self._get_pdf_binary())

    def _sign_document_filename(self):
        self.ensure_one()
        if self.applicant_id:
            return "Offre - %s - %s.pdf" % (
                (self.applicant_id.partner_name or "").replace("/", "-"),
                (self.applicant_id.job_id.name or "").replace("/", "-"),
            )
        return super()._sign_document_filename()

    # ------------------------------------------------------------------
    # Les deux refus
    # ------------------------------------------------------------------

    def _sign_installed_mixin(self):
        """La mixin telle qu'elle est INSTALLÉE, isolée pour être éprouvable.

        Une couture, et elle est délibérée : sans elle, aucun contrôle ne peut
        distinguer une sonde qui lit la mixin d'une sonde qui se lit elle-même,
        parce que sur un banc à jour les deux répondent oui. Une mutation
        passerait alors sans faire tomber un seul test.
        """
        return self.env["bf.sign.mixin"]

    def _sign_supports_document_file(self):
        """La lignée de `bf_sign` installée consulte-t-elle le crochet ?

        🔴 On interroge la MIXIN, pas soi-même : la surcharge ci-dessus existe
        toujours ici, donc `hasattr(self, ...)` répondrait oui même sur une
        lignée qui ne l'appelle jamais. C'est exactement la forme du défaut du
        un pont qu'on croit actif et qui est inerte.
        """
        return hasattr(self._sign_installed_mixin(), "_sign_document_file")

    def action_send_for_signature(self):
        for letter in self:
            if letter.state == "draft":
                raise UserError(_(
                    "La lettre %(ref)s est encore un brouillon. On signe un "
                    "texte arrêté : finalisez-la d'abord.",
                    ref=letter.reference or letter.display_name,
                ))
            if letter.letterhead_style == "pdf_overlay" and not letter._sign_supports_document_file():
                raise UserError(_(
                    "Cette lettre est montée sur un papier en-tête téléversé, "
                    "et la version de bf_sign installée ne sait pas recevoir un "
                    "PDF déjà rendu : elle referait le rapport, et ferait donc "
                    "signer un document SANS l'en-tête, différent de celui que "
                    "le destinataire a lu.\n\n"
                    "Deux sorties : mettre bf_sign à niveau (18.0.3.22.0 ou "
                    "plus récent), ou choisir un en-tête généré pour cette "
                    "lettre."
                ))
        return super().action_send_for_signature()

    # ------------------------------------------------------------------
    # Ce que la candidature apprend
    # ------------------------------------------------------------------

    def _sign_on_signed(self, request):
        """Consigner la signature, sans décider à la place de personne.

        ⚠️ On ne pose PAS `date_closed` et on ne déplace pas l'étape. Une offre
        signée n'est pas une entrée en fonction, et `date_closed` est la date
        d'EMBAUCHE : la poser d'office ferait compter la personne parmi les
        embauches du poste, et fausserait le coût par embauche.
        """
        self.ensure_one()
        super()._sign_on_signed(request)
        if self.state != "sent":
            self.sudo().write({"state": "sent"})
        applicant = self.applicant_id
        if not applicant:
            return
        applicant.sudo().message_post(body=_(
            "<p>L'offre d'emploi a été <strong>signée</strong> "
            "(demande %(ref)s). Le document signé est joint à la lettre "
            "%(letter)s.</p><p>L'étape de la candidature n'a pas bougé : une "
            "offre signée n'est pas une entrée en fonction.</p>",
            ref=request.name, letter=self.reference or self.display_name,
        ))

    def _sign_on_refused(self, request, signer, reason=None):
        self.ensure_one()
        super()._sign_on_refused(request, signer, reason=reason)
        applicant = self.applicant_id
        if not applicant:
            return
        corps = _(
            "<p>L'offre d'emploi a été <strong>refusée</strong> par "
            "%(who)s (demande %(ref)s).</p>",
            who=signer.name or signer.email or _("le signataire"),
            ref=request.name,
        )
        if reason:
            corps += _("<p>Motif donné : %s</p>", reason)
        applicant.sudo().message_post(body=corps)
