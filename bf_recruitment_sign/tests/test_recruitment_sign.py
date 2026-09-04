# Part of bf_recruitment_sign. Voir LICENSE.
"""Ce qu'on prouve ici.

1. C'est le PDF BRANDÉ qui part sous la signature, pas le corps nu rendu par
   le rapport. La paire le prouve : les deux sorties diffèrent.
2. Une lettre en brouillon ne part pas.
3. 🔴 Sur une lignée de `bf_sign` sans `_sign_document_file`, le pont LÈVE au
   lieu de faire signer un document sans en-tête.
4. La candidature apprend la signature et le refus, et son étape ne bouge pas.
"""

import base64
import io
import re
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


def _sans_horodatage(pdf_bytes):
    """Retirer les dates que tout PDF embarque, pour comparer deux rendus."""
    return re.sub(rb"/(Creation|Mod)Date\s*\(D:[^)]*\)", b"", pdf_bytes)


def _papier_entete():
    """Un PDF d'une page, minimal, qui tient lieu de papier en-tête."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter as page_letter
    tampon = io.BytesIO()
    c = canvas.Canvas(tampon, pagesize=page_letter)
    c.drawString(72, 720, "PAPIER EN-TETE ESSAI")
    c.showPage()
    c.save()
    return tampon.getvalue()


@tagged("post_install", "-at_install")
class TestRecruitmentSign(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.job = cls.env["hr.job"].create({
            "name": "Ferblantière", "company_id": cls.company.id,
        })
        cls.recruiter = cls.env["res.users"].create({
            "name": "sign_recruteur", "login": "sign_recruteur",
            "email": "signrec@example.invalid",
            "company_id": cls.company.id,
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hr_recruitment.group_hr_recruitment_user").id,
            ])],
        })

    def _offer(self):
        candidate = self.env["hr.candidate"].create({
            "partner_name": "Rosalie Turcotte",
            "email_from": "rosalie@example.invalid",
            "company_id": self.company.id,
        })
        applicant = self.env["hr.applicant"].create({
            "candidate_id": candidate.id, "job_id": self.job.id,
            "company_id": self.company.id, "salary_proposed": 72000.0,
        })
        action = applicant.with_user(self.recruiter).action_draft_offer_letter()
        return applicant, self.env["letter.document"].browse(action["res_id"])

    # ------------------------------------------------------------------
    # 1. Le bon document
    # ------------------------------------------------------------------

    def test_the_signed_document_is_the_branded_pdf(self):
        """⚠️ `force_report_rendering` est OBLIGATOIRE ici.

        En mode test, `ir.actions.report._render_qweb_pdf` rend du **HTML** au
        lieu d'un PDF (« fallback to render_html »), faute de travailleurs pour
        appeler wkhtmltopdf. Sans ce contexte, l'assertion « ça commence par
        %PDF- » tombe pour une raison qui n'a rien à voir avec le module, et se
        lit comme un défaut du pont.
        """
        _applicant, letter = self._offer()
        letter = letter.with_context(force_report_rendering=True)
        rendu = base64.b64decode(letter._sign_document_file())
        self.assertTrue(
            rendu.startswith(b"%PDF-"),
            "Ce n'est pas un PDF : %r" % rendu[:40],
        )
        # ⚠️ Deux rendus ne sont JAMAIS identiques octet pour octet : un PDF
        # embarque sa date de création à la seconde. On la retire des deux.
        self.assertEqual(
            _sans_horodatage(rendu), _sans_horodatage(letter._get_pdf_binary()),
            "Le pont ne rend pas le même document que la lettre elle-même.",
        )

    def test_the_uploaded_letterhead_is_UNDER_the_signature(self):
        """🔴 La thèse du module, prouvée par la paire.

        Le rapport seul ne porte PAS le papier en-tête téléversé : la
        superposition se fait APRÈS, dans `_get_pdf_binary()`. Si les deux
        sorties étaient identiques, `_sign_document_file()` ne servirait à rien
        et le repli par rapport suffirait, y compris sur les vieilles lignées.
        """
        _applicant, letter = self._offer()
        letter = letter.with_context(force_report_rendering=True)
        letter.company_id.sudo().write({
            "letterhead_pdf": base64.b64encode(_papier_entete()),
            "letterhead_pdf_filename": "entete.pdf",
        })
        letter.letterhead_style = "pdf_overlay"

        rapport_seul, _ext = self.env["ir.actions.report"].with_context(
            force_report_rendering=True
        )._render_qweb_pdf(
            "bf_letter_writer.action_report_letter_document", letter.ids)
        sous_signature = base64.b64decode(letter._sign_document_file())

        self.assertTrue(sous_signature.startswith(b"%PDF-"))
        self.assertNotEqual(
            _sans_horodatage(sous_signature), _sans_horodatage(rapport_seul),
            "Le document signé est celui du rapport nu : le papier en-tête "
            "téléversé n'est pas sous la signature, et le candidat signerait "
            "autre chose que ce qu'il a lu.",
        )

    def test_the_report_fallback_is_declared(self):
        """Le repli existe, pour les lignées sans le crochet."""
        _applicant, letter = self._offer()
        self.assertEqual(
            letter._sign_report_ref(),
            "bf_letter_writer.action_report_letter_document",
        )

    def test_the_filename_names_the_candidate_and_the_job(self):
        _applicant, letter = self._offer()
        nom = letter._sign_document_filename()
        self.assertIn("Rosalie Turcotte", nom)
        self.assertIn("Ferblantière", nom)
        self.assertTrue(nom.endswith(".pdf"))

    # ------------------------------------------------------------------
    # 2. Les refus
    # ------------------------------------------------------------------

    def test_a_draft_letter_does_not_go_to_signature(self):
        _applicant, letter = self._offer()
        self.assertEqual(letter.state, "draft")
        with self.assertRaises(UserError, msg=(
            "Un brouillon est parti en signature."
        )):
            letter.action_send_for_signature()

    def test_an_uploaded_letterhead_on_an_old_bf_sign_raises(self):
        """🔴 Le défaut du pont inerte, pris de face.

        Sur une lignée sans `_sign_document_file`, la surcharge du pont n'est
        jamais appelée : c'est le rapport qui sert, donc un document SANS
        l'en-tête téléversé. Le pont doit lever, pas signer autre chose.
        """
        _applicant, letter = self._offer()
        letter.action_finalize()
        letter.letterhead_style = "pdf_overlay"
        with patch.object(
            type(letter), "_sign_supports_document_file", lambda self: False
        ):
            with self.assertRaises(UserError, msg=(
                "Un document sans en-tête est parti en signature en silence."
            )):
                letter.action_send_for_signature()

    def test_a_generated_letterhead_goes_through_on_an_old_bf_sign(self):
        """La paire : la garde vise le seul mode qui diverge, pas tous."""
        _applicant, letter = self._offer()
        letter.action_finalize()
        letter.letterhead_style = "banner"
        with patch.object(
            type(letter), "_sign_supports_document_file", lambda self: False
        ):
            action = letter.action_send_for_signature()
        self.assertEqual(action["res_model"], "bf.sign.request")

    def test_the_lineage_probe_asks_the_mixin_not_itself(self):
        """🔴 Le contrôle qui rend la garde honnête.

        Le pont surcharge `_sign_document_file` : `hasattr(self, ...)`
        répondrait donc **toujours** oui, y compris sur une lignée qui ne
        l'appelle jamais. C'est la forme exacte du pont inerte : un pont
        qu'on croit actif et qui est inerte. La sonde doit lire la MIXIN.
        """
        _applicant, letter = self._offer()
        self.assertTrue(
            hasattr(letter, "_sign_document_file"),
            "La lecture naïve : elle répond oui parce que c'est nous.",
        )
        self.assertTrue(
            letter._sign_supports_document_file(),
            "Sur ce banc, bf_sign porte le crochet : la sonde doit dire oui.",
        )
        # La mixin d'une vieille lignée, simulée : un objet sans le crochet.
        class _MixinSansCrochet:
            pass

        with patch.object(
            type(letter), "_sign_installed_mixin",
            lambda self: _MixinSansCrochet(),
        ):
            self.assertFalse(
                letter._sign_supports_document_file(),
                "La sonde répond oui alors que la mixin installée n'a pas le "
                "crochet : elle se lit elle-même, et la garde du papier "
                "en-tête ne se déclenchera jamais chez un client au catalogue.",
            )

    # ------------------------------------------------------------------
    # 3. Ce que la candidature apprend
    # ------------------------------------------------------------------

    def test_signing_tells_the_applicant_without_deciding_for_anyone(self):
        applicant, letter = self._offer()
        letter.action_finalize()
        action = letter.action_send_for_signature()
        request = self.env["bf.sign.request"].browse(action["res_id"])
        etape_avant, cloture_avant = applicant.stage_id, applicant.date_closed
        avant = self.env["mail.message"].search_count([
            ("model", "=", "hr.applicant"), ("res_id", "=", applicant.id)])

        letter._sign_on_signed(request)

        apres = self.env["mail.message"].search_count([
            ("model", "=", "hr.applicant"), ("res_id", "=", applicant.id)])
        self.assertEqual(apres, avant + 1, "La candidature n'a rien appris.")
        self.assertEqual(letter.state, "sent")
        applicant.invalidate_recordset()
        self.assertEqual(applicant.stage_id, etape_avant,
                         "La signature a déplacé l'étape toute seule.")
        self.assertEqual(
            applicant.date_closed, cloture_avant,
            "La signature a posé une date d'embauche : la personne compte "
            "désormais parmi les embauches du poste sans avoir commencé.",
        )

    def test_a_refusal_is_told_to_the_applicant_with_its_reason(self):
        applicant, letter = self._offer()
        letter.action_finalize()
        action = letter.action_send_for_signature()
        request = self.env["bf.sign.request"].browse(action["res_id"])
        signer = request.signer_ids[:1]
        letter._sign_on_refused(request, signer, reason="Offre trop basse")
        dernier = self.env["mail.message"].search(
            [("model", "=", "hr.applicant"), ("res_id", "=", applicant.id)],
            order="id desc", limit=1)
        self.assertIn("refus", (dernier.body or "").lower())
        self.assertIn("Offre trop basse", dernier.body or "")
