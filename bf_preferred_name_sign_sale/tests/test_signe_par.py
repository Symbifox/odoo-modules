# -*- coding: utf-8 -*-
"""« Signé par » d'un devis : le nom légal figé du signataire."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestSignePar(TransactionCase):

    def test_signe_par_porte_le_nom_de_l_acte(self):
        client = self.env["res.partner"].create({"name": "Sacha Exemple", "email": "j@exemple.example"})
        devis = self.env["sale.order"].create({"partner_id": client.id})
        req = self.env["bf.sign.request"].create({"document_filename": "devis.pdf"})
        sacha = self.env["bf.sign.signer"].create({
            "request_id": req.id, "name": "Sacha Exemple", "email": "j@exemple.example",
            "partner_id": client.id})
        lou = self.env["bf.sign.signer"].create({
            "request_id": req.id, "name": "Lou Exemple", "email": "l@exemple.example"})
        sacha.sudo().write({"bf_act_name": "Morgane Exemple-Légal"})
        devis._sign_on_signed(req)
        self.assertEqual(devis.signed_by, "Morgane Exemple-Légal, Lou Exemple")

    def test_le_pdf_de_confirmation_porte_le_nom_de_l_acte(self):
        # La confirmation partait avant le bon nom.
        from unittest.mock import patch
        client = self.env["res.partner"].create({"name": "Sacha Exemple", "email": "j2@exemple.example"})
        devis = self.env["sale.order"].create({"partner_id": client.id})
        req = self.env["bf.sign.request"].create({"document_filename": "devis.pdf"})
        sacha = self.env["bf.sign.signer"].create({
            "request_id": req.id, "name": "Sacha Exemple", "email": "j2@exemple.example",
            "partner_id": client.id})
        sacha.sudo().write({"bf_act_name": "Morgane Exemple-Légal"})
        vu = []
        Order = type(devis)
        if not hasattr(Order, "_validate_order"):
            self.skipTest("bf_sign_sale ne confirme pas la commande")
        with patch.object(Order, "_validate_order", autospec=True,
                          side_effect=lambda order, *a, **k: vu.append(order.signed_by)):
            devis._sign_on_signed(req)
        self.assertEqual(vu, ["Morgane Exemple-Légal"])

    def test_la_cle_ne_sert_qu_a_cette_commande(self):
        client = self.env["res.partner"].create({"name": "Lou Client"})
        devis = self.env["sale.order"].create({"partner_id": client.id})
        autre = self.env["sale.order"].create({"partner_id": client.id})
        cle = {devis.id: "Morgane Exemple-Légal"}
        autre.with_context(bf_act_signed_by=cle).write({"signed_by": "Quelqu'un"})
        self.assertEqual(autre.signed_by, "Quelqu'un")
        devis.write({"signed_by": "X"})
        devis.with_context(bf_act_signed_by=cle).write({"signed_by": False})
        self.assertFalse(devis.signed_by, "une remise à zéro reste une remise à zéro")
