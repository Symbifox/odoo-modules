# -*- coding: utf-8 -*-
"""Le nom légal sur l'acte signé, le nom d'usage partout ailleurs."""

import base64
import io
import os

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_sign.tests.common import BaseNeuve

LEGAL = "bf_preferred_name.group_legal_name"


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestNomDeLActe(BaseNeuve, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from cryptography.fernet import Fernet
        os.environ.setdefault("BF_SIGN_FERNET_KEY", Fernet.generate_key().decode())
        cls.personne = cls.env["res.partner"].create(
            {"name": "Sacha Exemple", "email": "sacha.exemple@exemple.example"})
        cls.personne.sudo().bf_legal_name = "Morgane Exemple-Légal"
        cls.sans_legal = cls.env["res.partner"].create(
            {"name": "Lou Exemple", "email": "lou.exemple@exemple.example"})

    @staticmethod
    def _pdf():
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.drawString(72, 720, "Acte d'essai")
        c.showPage()
        c.save()
        return base64.b64encode(buf.getvalue())

    def _demande(self, *partners):
        req = self.env["bf.sign.request"].create({
            "document_file": self._pdf(), "document_filename": "acte.pdf"})
        for i, partner in enumerate(partners):
            signer = self.env["bf.sign.signer"].create({
                "request_id": req.id, "name": partner.name, "email": partner.email,
                "partner_id": partner.id, "sequence": 10 + i})
            self.env["bf.sign.field"].create({
                "request_id": req.id, "signer_id": signer.id, "field_type": "signature",
                "page": 1, "pos_x": 0.5, "pos_y": 0.8, "width": 0.25, "height": 0.08})
        return req

    def test_fige_a_l_envoi(self):
        req = self._demande(self.personne, self.sans_legal)
        sacha, lou = req.signer_ids.sorted("sequence")
        self.assertFalse(sacha.sudo().bf_act_name, "rien avant l'envoi")
        req.action_send()
        self.assertEqual(sacha.sudo().bf_act_name, "Morgane Exemple-Légal")
        self.assertFalse(lou.sudo().bf_act_name)
        self.assertEqual(sacha._bf_act_name(), "Morgane Exemple-Légal")
        self.assertEqual(lou._bf_act_name(), "Lou Exemple", "sans nom légal, le nom")
        self.assertEqual(sacha.name, "Sacha Exemple", "le nom d'usage reste celui des courriels")
        # Figé : un nom légal changé après l'envoi ne change pas l'acte.
        self.personne.sudo().bf_legal_name = "Autre Nom"
        req.action_send()
        self.assertEqual(sacha.sudo().bf_act_name, "Morgane Exemple-Légal")

    def test_le_nom_legal_ne_va_pas_sur_la_fiche(self):
        req = self._demande(self.personne)
        req.action_send()
        self.assertEqual(self.personne.name, "Sacha Exemple")

    def test_ferme_hors_du_groupe(self):
        req = self._demande(self.personne)
        req.action_send()
        # Un gestionnaire des signatures, hors du groupe « Noms légaux » : il lit
        # le signataire (témoin), pas le nom de l'acte. Sans droits sur les
        # signatures, l'essai serait refusé de tout et ne prouverait rien.
        hors = new_test_user(self.env, login="hors.essai",
                             groups="base.group_user,bf_sign.group_sign_manager")
        self.assertEqual(req.signer_ids.with_user(hors).read(["name"])[0]["name"], "Sacha Exemple")
        with self.assertRaises(AccessError):
            req.signer_ids.with_user(hors).read(["bf_act_name"])

    def test_pave_nom_estampe(self):
        req = self._demande(self.personne)
        signer = req.signer_ids
        pave = self.env["bf.sign.field"].create({
            "request_id": req.id, "signer_id": signer.id, "field_type": "name",
            "fill_mode": "auto", "page": 1, "pos_x": 0.2, "pos_y": 0.5, "width": 0.3, "height": 0.04})
        req.action_send()
        self.assertEqual(pave._auto_value(), "Morgane Exemple-Légal")

    def test_certificat_et_verification(self):
        req = self._demande(self.personne)
        req.action_send()
        html = self.env["ir.actions.report"]._render_qweb_html(
            "bf_sign.action_report_sign_certificate", req.ids)[0].decode()
        self.assertIn("Morgane Exemple-Légal", html)
        self.assertNotIn(">Sacha Exemple<", html)
        self.assertIn("_bf_act_name()", self.env.ref("bf_sign.verify_page").get_combined_arch())

    def test_valeur_affichee_hors_du_groupe(self):
        # La colonne « Valeur » passait par l'acte.
        req = self._demande(self.personne)
        pave = self.env["bf.sign.field"].create({
            "request_id": req.id, "signer_id": req.signer_ids.id, "field_type": "name",
            "fill_mode": "auto", "page": 1, "pos_x": 0.2, "pos_y": 0.5, "width": 0.3, "height": 0.04})
        req.action_send()
        hors = new_test_user(self.env, login="hors.valeur.essai",
                             groups="base.group_user,base.group_allow_export,bf_sign.group_sign_manager")
        self.assertEqual(pave.with_user(hors).value_display, "Sacha Exemple")
        self.assertEqual(pave.with_user(hors).read(["value_display"])[0]["value_display"], "Sacha Exemple")
        export = pave.with_user(hors).export_data(["value_display"])["datas"]
        self.assertEqual(export, [["Sacha Exemple"]])
        dedans = new_test_user(self.env, login="dedans.valeur.essai",
                               groups="base.group_user,bf_sign.group_sign_manager," + LEGAL)
        self.assertEqual(pave.with_user(dedans).value_display, "Morgane Exemple-Légal")
        self.assertEqual(pave._display_value(), "Morgane Exemple-Légal", "l'estampage garde l'acte")

    def test_signataire_saisi_sans_fiche(self):
        # Rattaché à sa fiche par le courriel à l'envoi.
        req = self.env["bf.sign.request"].create({
            "document_file": self._pdf(), "document_filename": "acte.pdf"})
        signer = self.env["bf.sign.signer"].create({
            "request_id": req.id, "name": "Sacha Exemple", "email": "SACHA.exemple@exemple.example"})
        self.env["bf.sign.field"].create({
            "request_id": req.id, "signer_id": signer.id, "field_type": "signature",
            "page": 1, "pos_x": 0.5, "pos_y": 0.8, "width": 0.25, "height": 0.08})
        req.action_send()
        self.assertEqual(signer.partner_id, self.personne)
        self.assertEqual(signer._bf_act_name(), "Morgane Exemple-Légal")

    def test_fiche_d_une_autre_personne(self):
        # Le courriel du signataire n'est pas celui de
        # la fiche choisie, le nom légal de la fiche ne va pas sur l'acte.
        req = self.env["bf.sign.request"].create({
            "document_file": self._pdf(), "document_filename": "acte.pdf"})
        signer = self.env["bf.sign.signer"].create({
            "request_id": req.id, "name": "Lou Exemple", "email": "lou.autre@exemple.example",
            "partner_id": self.personne.id})
        self.env["bf.sign.field"].create({
            "request_id": req.id, "signer_id": signer.id, "field_type": "signature",
            "page": 1, "pos_x": 0.5, "pos_y": 0.8, "width": 0.25, "height": 0.08})
        req.action_send()
        self.assertEqual(signer._bf_act_name(), "Lou Exemple")

    def _signataire_sans_fiche(self, nom, courriel):
        req = self.env["bf.sign.request"].create({
            "document_file": self._pdf(), "document_filename": "acte.pdf"})
        signer = self.env["bf.sign.signer"].create({
            "request_id": req.id, "name": nom, "email": courriel})
        self.env["bf.sign.field"].create({
            "request_id": req.id, "signer_id": signer.id, "field_type": "signature",
            "page": 1, "pos_x": 0.5, "pos_y": 0.8, "width": 0.25, "height": 0.08})
        return req, signer

    def test_courriel_partage(self):
        # Le nom légal de l'AUTRE personne.
        aaron = self.env["res.partner"].create({"name": "Aaron Partage", "email": "maison@exemple.example"})
        aaron.sudo().bf_legal_name = "Aaron Légal-Secret"
        self.env["res.partner"].create({"name": "Zoé Partage", "email": "maison@exemple.example"})
        req, zoe = self._signataire_sans_fiche("Zoé Partage", "maison@exemple.example")
        req.action_send()
        self.assertEqual(zoe._bf_act_name(), "Zoé Partage")
        self.assertFalse(zoe.sudo().bf_act_name)

    def test_courriel_d_une_societe(self):
        acme = self.env["res.partner"].create(
            {"name": "Acme", "email": "acme@exemple.example", "is_company": True})
        acme.sudo().bf_legal_name = "Acme Holding Légale Inc."
        req, signer = self._signataire_sans_fiche("Kim Acme", "acme@exemple.example")
        req.action_send()
        self.assertEqual(signer._bf_act_name(), "Kim Acme")

    def test_rangee_de_cases_mesuree_sur_le_nom_de_l_acte(self):
        # Contrôlée sur le nom d'usage, coupée
        # au nom légal au scellement.
        from odoo.exceptions import UserError
        jo = self.env["res.partner"].create({"name": "Jo", "email": "jo@exemple.example"})
        jo.sudo().bf_legal_name = "Joséphine-Marguerite Tremblay-Longnom"
        req = self._demande(jo)
        signer = req.signer_ids
        nom = self.env["bf.sign.field"].create({
            "request_id": req.id, "signer_id": signer.id, "field_type": "name",
            "fill_mode": "auto", "page": 1, "pos_x": 0.2, "pos_y": 0.5, "width": 0.3, "height": 0.04})
        self.env["bf.sign.field"].create({
            "request_id": req.id, "signer_id": signer.id, "field_type": "cells",
            "fill_mode": "copy", "copy_from_id": nom.id, "cell_count": 10,
            "page": 1, "pos_x": 0.2, "pos_y": 0.6, "width": 0.3, "height": 0.04})
        with self.assertRaises(UserError):
            req.action_send()

    def test_societe_choisie_comme_signataire(self):
        # Une société n'a pas de nom légal de personne : même choisie
        # explicitement, son nom légal ne va pas sur l'acte.
        acme = self.env["res.partner"].create(
            {"name": "Acme", "email": "acme2@exemple.example", "is_company": True})
        acme.sudo().bf_legal_name = "Acme Holding Légale Inc."
        req = self._demande(acme)
        req.action_send()
        self.assertEqual(req.signer_ids._bf_act_name(), "Acme")
