# -*- coding: utf-8 -*-
"""A refused signature is noted in the salesperson's language, not the signer's browser's."""
from types import SimpleNamespace

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSignRefusalLanguage(TransactionCase):

    def test_refusal_note_reads_in_the_salesperson_language(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_sign_sale"], ["fr_CA"], overwrite=True)
        vendeur = self.env["res.users"].create({
            "name": "Vendeur fr_CA", "login": "vendeur.signature@example.test", "lang": "fr_CA",
            "groups_id": [(6, 0, [self.env.ref("sales_team.group_sale_salesman").id])],
        })
        commande = self.env["sale.order"].create({
            "partner_id": self.env["res.partner"].create({"name": "Client signature"}).id,
            "user_id": vendeur.id,
        })
        demande = SimpleNamespace(name="SIGN/0001")
        signataire = SimpleNamespace(name="Signer en_US", email="signer@example.test")
        # The public refusal route runs in the signer's language.
        commande.with_context(lang="en_US")._sign_on_refused(demande, signataire, reason="Trop cher")
        note = commande.message_ids.filtered(lambda m: "SIGN/0001" in (m.body or ""))[:1]
        self.assertIn("Signature refusée par Signer en_US", note.body)
        self.assertIn("Motif : Trop cher", note.body)
