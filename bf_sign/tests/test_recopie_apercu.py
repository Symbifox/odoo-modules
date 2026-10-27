"""Signature séquentielle visible et recopie d'un pavé.

1. En ordre séquentiel, le signataire suivant voit ce que les précédents ont
   apposé (la page servait le PDF d'origine), et le préparateur relit la saisie
   dans la fiche (`marker_label` / `value_display`).
2. Un pavé peut recopier un autre pavé de la même demande (un formulaire
   qui répète en page 3 les cases de sa page 1).
"""
import base64
import io
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools.pdf import PdfReader

from .common import BaseNeuve


def _pdf(pages=1):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(pages):
        c.drawString(72, 720, "Formulaire de test, page %d" % (i + 1))
        c.showPage()
    c.save()
    return buf.getvalue()


def _png_b64():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (200, 80), (0, 0, 0, 0)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _text(pdf_bytes):
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


class _Montage:
    """Une demande à deux signataires, chacun avec sa signature (page 1)."""

    def _request(self, order="sequential", pages=1, signers=2):
        req = self.env["bf.sign.request"].create({
            "document_file": base64.b64encode(_pdf(pages)),
            "document_filename": "formulaire.pdf",
            "signing_order": order,
            "require_signer_otp": False,
        })
        for i in range(signers):
            signer = self.env["bf.sign.signer"].create({
                "request_id": req.id, "name": "Signataire %d" % i,
                "email": "signataire%d@example.com" % i, "sequence": 10 + i,
            })
            self._pad(req, signer, "signature", pos_y=0.85)
        return req

    def _pad(self, req, signer, field_type="text", fill_mode="signer", page=1,
             pos_y=0.3, label=None, required=True, **extra):
        vals = {
            "request_id": req.id, "signer_id": signer.id, "field_type": field_type,
            "fill_mode": fill_mode, "page": page, "pos_x": 0.1, "pos_y": pos_y,
            "width": 0.5, "height": 0.04, "value_text": label, "required": required,
        }
        if field_type == "cells":
            vals["cell_count"] = 7
        vals.update(extra)
        return self.env["bf.sign.field"].create(vals)

    def _first_second(self, req):
        s = req.signer_ids.sorted("sequence")
        return s[0], s[1]

    def _sign(self, req, signer, values=None):
        return req.register_signer_signature(
            signer, _png_b64(), None, True, ip="1.2.3.4", user_agent="essai",
            field_values=values or {})


@tagged("post_install", "-at_install", "bf_sign")
class TestRecopie(_Montage, BaseNeuve, TransactionCase):

    # ── Résolution ────────────────────────────────────────────────────────────
    def test_recopie_reprend_la_valeur_saisie_par_la_source(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier, label="Noms")
        nom_p3 = self._pad(req, premier, fill_mode="copy", page=1, pos_y=0.6,
                           label="Noms", copy_from_id=nom.id)
        req.action_send()
        self.assertEqual(nom_p3._display_value(), "")
        self._sign(req, premier, {str(nom.id): "Tremblay"})
        self.assertEqual(nom_p3._display_value(), "Tremblay")
        # Résolue au rendu, jamais recopiée en base.
        self.assertFalse(nom_p3.filled_value)

    def test_recopie_ecrite_en_cases_d_une_valeur_ecrite_en_ligne(self):
        req = self._request()
        premier, _second = self._first_second(req)
        cp = self._pad(req, premier, label="Code postal")
        cp_cases = self._pad(req, premier, "cells", "copy", pos_y=0.6, copy_from_id=cp.id)
        req.action_send()
        self._sign(req, premier, {str(cp.id): "H0H0H0"})
        self.assertEqual(cp_cases._display_value(), "H0H0H0")

    def test_recopie_d_une_case_a_cocher(self):
        req = self._request()
        premier, _second = self._first_second(req)
        fem = self._pad(req, premier, "checkbox", required=False, label="féminin")
        fem_p3 = self._pad(req, premier, "checkbox", "copy", pos_y=0.6, copy_from_id=fem.id)
        req.action_send()
        self.assertFalse(fem_p3._is_checked())
        self._sign(req, premier, {str(fem.id): "on"})
        self.assertTrue(fem_p3._is_checked())

    def test_recopie_d_une_source_fixe_ou_d_un_autre_signataire(self):
        req = self._request()
        premier, second = self._first_second(req)
        fixe = self._pad(req, premier, fill_mode="fixed", label="Organisme Essai")
        chez_second = self._pad(req, second, fill_mode="copy", pos_y=0.6,
                                 copy_from_id=fixe.id)
        self.assertEqual(chez_second._display_value(), "Organisme Essai")

    def test_la_recopie_n_est_pas_demandee_au_signataire(self):
        """Obligatoire ou non, une recopie ne se saisit pas : la signature passe
        sans elle, et une valeur postée pour elle est ignorée."""
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier, label="Noms")
        copie = self._pad(req, premier, fill_mode="copy", pos_y=0.6, required=True,
                          copy_from_id=nom.id)
        req.action_send()
        self._sign(req, premier, {str(nom.id): "Tremblay", str(copie.id): "Forgé"})
        self.assertFalse(copie.filled_value)
        self.assertEqual(copie._display_value(), "Tremblay")

    def test_la_recopie_est_apposee_au_scellement(self):
        # Deux signataires : le premier signe sans déclencher le scellement (son
        # certificat passe par wkhtmltopdf), on appelle l'apposition qu'il emploie.
        req = self._request()
        signer, _second = self._first_second(req)
        nom = self._pad(req, signer, label="Noms")
        self._pad(req, signer, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        req.action_send()
        self._sign(req, signer, {str(nom.id): "Tremblay"})
        stamped = req._stamp_document(base64.b64decode(req.document_file))
        self.assertEqual(_text(stamped).count("Tremblay"), 2)

    # ── Contraintes ───────────────────────────────────────────────────────────
    def test_une_recopie_designe_toujours_sa_source(self):
        req = self._request()
        premier, _second = self._first_second(req)
        with self.assertRaisesRegex(ValidationError, "Choisissez le pavé"):
            self._pad(req, premier, fill_mode="copy")

    def test_seule_une_recopie_designe_une_source(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier)
        with self.assertRaises(ValidationError):
            self._pad(req, premier, fill_mode="signer", copy_from_id=nom.id)

    def test_pas_de_recopie_d_une_recopie(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier)
        copie = self._pad(req, premier, fill_mode="copy", copy_from_id=nom.id)
        with self.assertRaises(ValidationError):
            self._pad(req, premier, fill_mode="copy", copy_from_id=copie.id)

    def test_pas_de_recopie_de_soi_meme(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier)
        copie = self._pad(req, premier, fill_mode="copy", copy_from_id=nom.id)
        with self.assertRaisesRegex(ValidationError, "AUTRE pavé"):
            copie.copy_from_id = copie

    def test_pas_de_recopie_d_une_autre_demande(self):
        req, autre = self._request(), self._request()
        premier, _second = self._first_second(req)
        ailleurs = self._pad(autre, self._first_second(autre)[0])
        with self.assertRaises(ValidationError):
            self._pad(req, premier, fill_mode="copy", copy_from_id=ailleurs.id)

    def test_une_case_a_cocher_ne_recopie_qu_une_case_a_cocher(self):
        req = self._request()
        premier, _second = self._first_second(req)
        texte = self._pad(req, premier)
        case = self._pad(req, premier, "checkbox", required=False)
        with self.assertRaises(ValidationError):
            self._pad(req, premier, "checkbox", "copy", copy_from_id=texte.id)
        with self.assertRaises(ValidationError):
            self._pad(req, premier, "text", "copy", copy_from_id=case.id)

    def test_une_signature_ne_se_recopie_pas(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier)
        with self.assertRaisesRegex(ValidationError, "signature ou un paraphe"):
            self._pad(req, premier, "signature", "copy", copy_from_id=nom.id)

    def test_la_source_ne_devient_pas_recopie_ni_d_un_autre_genre(self):
        """La contrainte relit les recopies quand la SOURCE change."""
        req = self._request()
        premier, _second = self._first_second(req)
        nom, autre = self._pad(req, premier), self._pad(req, premier, pos_y=0.5)
        self._pad(req, premier, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        with self.assertRaises(ValidationError):
            nom.write({"fill_mode": "copy", "copy_from_id": autre.id})
        with self.assertRaises(ValidationError):
            nom.field_type = "checkbox"

    def test_retirer_la_source_rend_ses_recopies_a_la_saisie(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier)
        copie = self._pad(req, premier, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        nom.unlink()
        self.assertEqual(copie.fill_mode, "signer")
        self.assertFalse(copie.copy_from_id)

    def test_retirer_source_et_recopie_ensemble(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier)
        copie = self._pad(req, premier, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        (nom | copie).unlink()
        self.assertFalse((nom | copie).exists())

    def test_le_lien_se_fige_a_l_envoi(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom, autre = self._pad(req, premier), self._pad(req, premier, pos_y=0.5)
        copie = self._pad(req, premier, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        req.action_send()
        with self.assertRaises(UserError):
            copie.copy_from_id = autre

    # ── Duplication et modèles ────────────────────────────────────────────────
    def test_dupliquer_la_demande_rattache_la_recopie_a_la_copie(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier, label="Noms")
        self._pad(req, premier, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        dup = req.copy()
        copie = dup.field_ids.filtered(lambda f: f.fill_mode == "copy")
        self.assertEqual(len(copie), 1)
        self.assertEqual(copie.copy_from_id.request_id, dup)
        self.assertEqual(copie.copy_from_id.value_text, "Noms")

    def test_le_modele_garde_le_lien_et_le_repose(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier, label="Noms")
        self._pad(req, premier, fill_mode="copy", pos_y=0.6, label="Noms p3",
                  copy_from_id=nom.id)
        tmpl = self.env["bf.sign.field.template"].browse(
            req.save_field_template("Formulaire", anchor_last_page=False))
        ligne = tmpl.line_ids.filtered(lambda l: l.fill_mode == "copy")
        self.assertEqual(ligne.copy_from_line_id.value_text, "Noms")

        neuve = self._request()
        neuve.apply_field_template(tmpl.id)
        copie = neuve.field_ids.filtered(lambda f: f.fill_mode == "copy")
        self.assertEqual(copie.value_text, "Noms p3")
        self.assertEqual(copie.copy_from_id.request_id, neuve)
        self.assertEqual(copie.copy_from_id.value_text, "Noms")

    def test_modele_sans_le_signataire_de_la_source(self):
        """La source n'est pas posée faute de signataire : la recopie reste une
        case à remplir au lieu d'un pavé qui n'apposerait rien."""
        req = self._request()
        premier, second = self._first_second(req)
        nom = self._pad(req, second, label="Noms")
        self._pad(req, premier, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        tmpl_id = req.save_field_template("Formulaire", anchor_last_page=False)
        neuve = self._request(signers=1)
        neuve.apply_field_template(tmpl_id)
        posee = neuve.field_ids.filtered(lambda f: f.field_type == "text")
        self.assertEqual(posee.fill_mode, "signer")
        self.assertFalse(posee.copy_from_id)

    def test_contraintes_des_lignes_de_modele(self):
        tmpl = self.env["bf.sign.field.template"].create({"name": "T", "line_ids": [
            (0, 0, {"field_type": "text", "value_text": "Noms"}),
            (0, 0, {"field_type": "checkbox"}),
        ]})
        texte, case = tmpl.line_ids.sorted("id")
        with self.assertRaisesRegex(ValidationError, "Choisissez la ligne"):
            self.env["bf.sign.field.template.line"].create(
                {"template_id": tmpl.id, "field_type": "text", "fill_mode": "copy"})
        with self.assertRaises(ValidationError):
            self.env["bf.sign.field.template.line"].create(
                {"template_id": tmpl.id, "field_type": "text", "fill_mode": "copy",
                 "copy_from_line_id": case.id})
        copie = self.env["bf.sign.field.template.line"].create(
            {"template_id": tmpl.id, "field_type": "cells", "fill_mode": "copy",
             "copy_from_line_id": texte.id})
        self.assertIn("Noms", texte.display_name)
        texte.unlink()
        self.assertEqual(copie.fill_mode, "signer")
        self.assertFalse(copie.copy_from_line_id)

    # ── Cas limites ───────────────────────────────────────────────────────────
    def test_retirer_le_signataire_de_la_source_rend_la_recopie_a_la_saisie(self):
        """La cascade de la base supprimait la source sans passer par l'ORM."""
        req = self._request()
        premier, second = self._first_second(req)
        nom = self._pad(req, second, label="Noms")
        copie = self._pad(req, premier, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        second.unlink()
        self.assertFalse(nom.exists())
        self.assertEqual(copie.fill_mode, "signer")
        self.assertFalse(copie.copy_from_id)

    def test_l_envoi_refuse_une_recopie_sans_source(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier)
        copie = self._pad(req, premier, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        # Seule une écriture sous l'ORM y mène désormais : on la simule.
        self.env.cr.execute("UPDATE bf_sign_field SET copy_from_id = NULL WHERE id = %s",
                            (copie.id,))
        copie.invalidate_recordset(["copy_from_id"])
        with self.assertRaisesRegex(UserError, "plus de pavé source"):
            req.action_send()

    def test_l_envoi_refuse_une_recopie_signee_avant_sa_source(self):
        """Le premier signe d'abord : sa recopie d'une case du second serait
        signée vide, puis remplie au scellement."""
        req = self._request()
        premier, second = self._first_second(req)
        nom = self._pad(req, second, label="Noms")
        self._pad(req, premier, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        with self.assertRaisesRegex(UserError, "avant que leur source"):
            req.action_send()

    def test_en_parallele_une_recopie_d_un_autre_signataire_est_refusee(self):
        req = self._request(order="parallel")
        premier, second = self._first_second(req)
        nom = self._pad(req, premier, label="Noms")
        self._pad(req, second, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        with self.assertRaisesRegex(UserError, "avant que leur source"):
            req.action_send()

    def test_l_envoi_accepte_les_sources_connues_a_temps(self):
        req = self._request(order="parallel")
        premier, second = self._first_second(req)
        fixe = self._pad(req, premier, fill_mode="fixed", label="Organisme Essai")
        nom_auto = self._pad(req, premier, "name", "auto", pos_y=0.4)
        self._pad(req, second, fill_mode="copy", pos_y=0.6, copy_from_id=fixe.id)
        self._pad(req, second, fill_mode="copy", pos_y=0.7, copy_from_id=nom_auto.id)
        req.action_send()
        self.assertEqual(req.state, "sent")

    def test_en_sequentiel_la_source_du_signataire_precedent_est_acceptee(self):
        req = self._request()
        premier, second = self._first_second(req)
        nom = self._pad(req, premier, label="Noms")
        self._pad(req, second, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        req.action_send()
        self.assertEqual(req.state, "sent")

    def test_une_saisie_trop_longue_pour_les_cases_de_sa_recopie_est_refusee(self):
        req = self._request()
        premier, _second = self._first_second(req)
        cp = self._pad(req, premier, label="Code postal")
        self._pad(req, premier, "cells", "copy", pos_y=0.6, copy_from_id=cp.id, cell_count=6)
        req.action_send()
        with self.assertRaisesRegex(UserError, "repris dans 6 cases"):
            self._sign(req, premier, {str(cp.id): "H0H 0H0"})
        self._sign(req, premier, {str(cp.id): "H0H0H0"})
        self.assertEqual(cp.filled_value, "H0H0H0")

    def test_l_envoi_refuse_des_cases_trop_courtes_pour_une_valeur_connue(self):
        """Valeur fixe, nom automatique ou choix de liste : le signataire n'y peut
        rien, la rangée se règle avant l'envoi."""
        for source in ({"fill_mode": "fixed", "label": "Organisme d'essai"},
                       {"field_type": "name", "fill_mode": "auto"},
                       {"field_type": "select", "option_values": "Conseillère principale\nAdjointe"}):
            req = self._request()
            premier, _second = self._first_second(req)
            vals = dict(source)
            src = self._pad(req, premier, vals.pop("field_type", "text"), **vals)
            self._pad(req, premier, "cells", "copy", pos_y=0.6, copy_from_id=src.id, cell_count=6)
            with self.assertRaisesRegex(UserError, "plus courtes que la valeur"):
                req.action_send()

    def test_des_cases_assez_longues_pour_la_valeur_connue_partent(self):
        req = self._request()
        premier, _second = self._first_second(req)
        src = self._pad(req, premier, fill_mode="fixed", label="H0H0H0")
        self._pad(req, premier, "cells", "copy", pos_y=0.6, copy_from_id=src.id, cell_count=6)
        req.action_send()
        self.assertEqual(req.state, "sent")

    def test_dupliquer_un_modele_garde_ses_recopies(self):
        tmpl = self.env["bf.sign.field.template"].create({"name": "Formulaire", "line_ids": [
            (0, 0, {"field_type": "text", "value_text": "Noms"})]})
        source = tmpl.line_ids
        self.env["bf.sign.field.template.line"].create(
            {"template_id": tmpl.id, "field_type": "text", "fill_mode": "copy",
             "value_text": "Noms p3", "copy_from_line_id": source.id})
        dup = tmpl.copy()
        copie = dup.line_ids.filtered(lambda l: l.fill_mode == "copy")
        self.assertEqual(len(copie), 1)
        self.assertEqual(copie.copy_from_line_id.template_id, dup)
        self.assertEqual(copie.copy_from_line_id.value_text, "Noms")
        # L'original n'a pas bougé.
        self.assertEqual(tmpl.line_ids.filtered(
            lambda l: l.fill_mode == "copy").copy_from_line_id, source)


@tagged("post_install", "-at_install", "bf_sign")
class TestApercuSequentiel(_Montage, BaseNeuve, TransactionCase):

    def test_personne_n_a_signe_le_document_d_origine(self):
        req = self._request()
        premier, second = self._first_second(req)
        self._pad(req, premier, label="Noms")
        req.action_send()
        self.assertEqual(req._signing_preview(second),
                         base64.b64decode(req.document_file))

    def test_le_suivant_voit_ce_que_le_premier_a_saisi(self):
        req = self._request(pages=3)
        premier, second = self._first_second(req)
        nom = self._pad(req, premier, label="Noms")
        self._pad(req, premier, fill_mode="copy", page=3, copy_from_id=nom.id)
        req.action_send()
        original = base64.b64decode(req.document_file)
        hash_avant = req.hash_original
        self._sign(req, premier, {str(nom.id): "Tremblay"})
        self.assertEqual(req._signing_preview_fields(second).signer_id, premier)
        apercu = req._signing_preview(second)
        self.assertNotEqual(apercu, original)
        self.assertEqual(_text(apercu).count("Tremblay"), 2)
        # Construit à la demande : l'original et son empreinte ne bougent pas.
        self.assertEqual(base64.b64decode(req.document_file), original)
        self.assertEqual(req.hash_original, hash_avant)
        self.assertTrue(req._document_intact())

    def test_une_saisie_d_un_signataire_qui_n_a_pas_signe_n_est_pas_montree(self):
        req = self._request(order="parallel")
        premier, second = self._first_second(req)
        self._pad(req, second, label="NAS")
        req.action_send()
        self.assertFalse(req._signing_preview_fields(premier))

    def test_l_apercu_ne_porte_pas_le_qr_de_verification(self):
        req = self._request()
        req.verify_qr = True
        premier, second = self._first_second(req)
        nom = self._pad(req, premier)
        req.action_send()
        self._sign(req, premier, {str(nom.id): "Tremblay"})
        with patch.object(type(req), "_draw_verify_qr") as qr:
            req._signing_preview(second)
        qr.assert_not_called()

    def test_un_aperçu_impossible_sert_l_original(self):
        req = self._request()
        premier, second = self._first_second(req)
        nom = self._pad(req, premier)
        req.action_send()
        self._sign(req, premier, {str(nom.id): "Tremblay"})
        with patch.object(type(req), "_stamp_document", side_effect=ValueError("pdf")), \
                self.assertLogs("odoo.addons.bf_sign.models.bf_sign_request", "ERROR"):
            self.assertEqual(req._signing_preview(second),
                             base64.b64decode(req.document_file))

    def test_la_fiche_montre_la_saisie_au_preparateur(self):
        req = self._request()
        premier, _second = self._first_second(req)
        nom = self._pad(req, premier, label="Noms")
        fem = self._pad(req, premier, "checkbox", required=False, pos_y=0.5, label="féminin")
        masc = self._pad(req, premier, "checkbox", required=False, pos_y=0.55)
        copie = self._pad(req, premier, "checkbox", "copy", pos_y=0.6, copy_from_id=fem.id)
        req.action_send()
        # Avant le passage du signataire, rien n'est « non coché » : rien n'est rempli.
        self.assertEqual((fem | masc | copie).mapped("value_display"), ["", "", ""])
        self._sign(req, premier, {str(nom.id): "Tremblay", str(fem.id): "on"})
        (nom | fem | masc | copie).invalidate_recordset(["value_display"])
        self.assertEqual(nom.marker_label, "Noms")
        self.assertEqual(nom.value_display, "Tremblay")
        self.assertEqual(fem.value_display, "Cochée")
        self.assertEqual(masc.value_display, "Non cochée")
        self.assertEqual(copie.value_display, "Cochée")
        signature = req.field_ids.filtered(
            lambda f: f.field_type == "signature" and f.signer_id == premier)
        self.assertEqual(signature.value_display, "Signée")

    def test_en_parallele_l_aperçu_reste_l_original(self):
        """Un signataire n'a pas à lire ce qu'un autre a saisi avant que tous
        aient signé : en parallèle, personne ne précède personne."""
        req = self._request(order="parallel")
        premier, second = self._first_second(req)
        nom = self._pad(req, premier, label="NAS")
        req.action_send()
        self._sign(req, premier, {str(nom.id): "123456789"})
        self.assertFalse(req._signing_preview_fields(second))
        self.assertEqual(req._signing_preview(second), base64.b64decode(req.document_file))

    def test_qui_revient_apres_avoir_signe_relit_ses_marques(self):
        req = self._request()
        premier, second = self._first_second(req)
        nom = self._pad(req, premier, label="Noms")
        req.action_send()
        self.assertFalse(req._signing_preview_fields(premier))
        self._sign(req, premier, {str(nom.id): "Tremblay"})
        self.assertIn(nom, req._signing_preview_fields(premier))
        # Les pavés d'un signataire qui vient APRÈS ne sont jamais montrés.
        self.assertFalse(req._signing_preview_fields(premier).filtered(
            lambda f: f.signer_id == second))

    def test_qui_revient_ne_voit_pas_les_signataires_suivants(self):
        """A revient sur sa page après la signature de B : ce que B a saisi ne
        lui est pas montré (C peut encore refuser)."""
        req = self._request(signers=3)
        a, b, _c = req.signer_ids.sorted("sequence")
        nom_a = self._pad(req, a, label="Noms")
        nas_b = self._pad(req, b, label="NAS", pos_y=0.5)
        req.action_send()
        self._sign(req, a, {str(nom_a.id): "Tremblay"})
        self._sign(req, b, {str(nas_b.id): "123456789"})
        vus = req._signing_preview_fields(a)
        self.assertIn(nom_a, vus)
        self.assertNotIn(nas_b, vus)


@tagged("post_install", "-at_install", "bf_sign")
class TestApercuRoutes(_Montage, BaseNeuve, HttpCase):

    def _url(self, req, signer, suffix=""):
        return "/sign/%s/%s%s" % (req.id, signer.access_token, suffix)

    def test_la_route_sert_l_aperçu_rempli_sans_cache(self):
        req = self._request()
        premier, second = self._first_second(req)
        nom = self._pad(req, premier, label="Noms")
        req.action_send()
        self._sign(req, premier, {str(nom.id): "Tremblay"})
        res = self.url_open(self._url(req, second, "/document"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("Tremblay", _text(res.content))
        self.assertEqual(res.headers.get("Cache-Control"), "no-store")

    def test_la_marque_d_une_recopie_suit_sa_source_sur_la_page(self):
        req = self._request(signers=1)
        signer = req.signer_ids
        nom = self._pad(req, signer, label="Noms")
        copie = self._pad(req, signer, fill_mode="copy", pos_y=0.6, label="Noms p3",
                          copy_from_id=nom.id)
        req.action_send()
        page = self.url_open(self._url(req, signer)).text
        self.assertIn('data-input="field_%s"' % nom.id, page)
        # La recopie pointe le champ de sa SOURCE et n'a pas de champ à elle.
        self.assertEqual(page.count('data-input="field_%s"' % nom.id), 2)
        self.assertNotIn('name="field_%s"' % copie.id, page)
        self.assertIn("Noms p3", page)

    def test_la_recopie_d_une_valeur_deja_apposee_s_affiche(self):
        req = self._request()
        premier, second = self._first_second(req)
        nom = self._pad(req, premier, label="Noms")
        self._pad(req, second, fill_mode="copy", pos_y=0.6, copy_from_id=nom.id)
        req.action_send()
        self._sign(req, premier, {str(nom.id): "Tremblay"})
        page = self.url_open(self._url(req, second)).text
        self.assertIn("Tremblay", page)

    def test_la_recopie_d_une_case_cochee_s_affiche_coche(self):
        req = self._request()
        premier, second = self._first_second(req)
        fem = self._pad(req, premier, "checkbox", required=False, label="feminin")
        copie = self._pad(req, second, "checkbox", "copy", pos_y=0.6, label="fem p3",
                          copy_from_id=fem.id)
        req.action_send()
        self._sign(req, premier, {str(fem.id): "on"})
        page = self.url_open(self._url(req, second)).text
        bloc = page[page.index("fem p3"):][:400]
        self.assertIn("✓", bloc)
        self.assertNotIn(">on<", bloc)
