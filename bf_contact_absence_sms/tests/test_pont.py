# -*- coding: utf-8 -*-
"""Le pont SMS n'a pas de code serveur : ce qu'il faut éprouver, c'est qu'il
est bien câblé, et que la porte qu'il appelle existe toujours.

⚠️ Un pont qui n'est fait que d'actifs se casse en SILENCE : un patron dont
l'ancêtre a disparu fait tomber le paquet entier, et aucun essai Python ne le
voit. Ces essais gardent les trois choses qui, si elles bougent, cassent le
pont sans bruit.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_absence")
class TestPontSms(TransactionCase):

    def test_les_actifs_sont_declares(self):
        module = self.env["ir.module.module"].search(
            [("name", "=", "bf_contact_absence_sms")], limit=1)
        self.assertEqual(module.state, "installed")

    def test_la_porte_appelee_par_le_patron_existe(self):
        """Le patron OWL appelle `hint_for_thread` : si elle disparaissait ou
        redevenait privée, la Messagerie n'afficherait plus rien, sans erreur."""
        modele = self.env["bf.partner.absence"]
        self.assertTrue(hasattr(modele, "hint_for_thread"))
        charge = modele.hint_for_thread("res.partner", self.env.user.partner_id.id)
        self.assertIn("lines", charge)

    def test_le_fil_sms_porte_bien_un_partenaire(self):
        """Le patron lit `activeThread.partner_id` : le dictionnaire du fil
        doit continuer à le porter."""
        # ⚠️ `phone_normalized` est obligatoire et n'est PAS calculé depuis
        # `phone_raw` à la création : le fil se refuse sans lui.
        fil = self.env["sms.archive.thread"].create({
            "phone_raw": "+15145550199",
            "phone_normalized": "+15145550199",
            "partner_id": self.env.user.partner_id.id,
        })
        charge = fil._messenger_thread_dict()
        self.assertIn("partner_id", charge)
        self.assertEqual(charge["partner_id"], self.env.user.partner_id.id)
