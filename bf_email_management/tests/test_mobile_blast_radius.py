"""Bornes de dégât : envois en masse, actions groupées, suppressions.

Les autres fichiers vérifient qu'on ne peut pas faire ce qui est interdit.
Celui-ci vérifie qu'on ne peut pas faire *trop* de ce qui est permis — la
catégorie de risque où le porteur du jeton est légitime mais l'ampleur ne
l'est pas : téléphone volé, bogue client, ou simplement le doigt qui glisse
sur « répondre à tous » d'un fil de soixante personnes.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestMobileBlastRadius(MobileApiCase):

    # ------------------------------------------------------- envoi en masse
    def test_a_send_to_too_many_people_is_refused(self):
        recipients = ["destinataire%d@test.invalid" % i for i in range(60)]
        with self.assertRaises(UserError):
            self.as_owner().mobile_compose(
                to=recipients, subject="Diffusion", body="Bonjour.")

    def test_a_refused_mass_send_creates_no_contacts(self):
        """Le plafond est contrôlé AVANT la résolution des adresses.

        Résoudre crée un ``res.partner`` par adresse inconnue : compter après
        aurait rempli la base de 5 000 fiches pour un envoi finalement refusé.
        """
        Partner = self.env["res.partner"]
        before = Partner.search_count([])
        recipients = ["masse%d@test.invalid" % i for i in range(200)]
        with self.assertRaises(UserError):
            self.as_owner().mobile_compose(
                to=recipients, subject="Diffusion", body="Bonjour.")
        self.assertEqual(Partner.search_count([]), before,
                         "des fiches contact ont été créées malgré le refus")

    def test_a_reply_with_too_many_manual_recipients_is_refused(self):
        recipients = ["destinataire%d@test.invalid" % i for i in range(60)]
        with self.assertRaises(UserError):
            self.as_owner().browse(self.inbound.id).mobile_reply(
                mode="forward", body="Pour information.", to=recipients)

    def test_a_normal_send_is_untouched(self):
        """Le plafond ne doit pas gêner l'usage réel."""
        result = self.as_owner().mobile_compose(
            to=["client@acme.test", "second@test.invalid"],
            cc=["copie@test.invalid"], subject="Normal", body="Bonjour.")
        self.assertTrue(result["ok"])

    # ------------------------------------------------------ quota d'envoi
    def test_a_device_cannot_send_without_limit(self):
        """Un jeton volé ne doit pas pouvoir servir de relais de diffusion."""
        record = self.as_owner().browse(self.inbound.id)
        # Fenêtre déjà pleine.
        self.device.sudo().write({
            "send_window_start": self.env.cr.now(),
            "send_count": 100,
        })
        with self.assertRaises(UserError):
            record.mobile_reply(mode="reply", body="Encore un.",
                                device=self.device)

    def test_the_quota_window_rolls_over(self):
        from datetime import timedelta
        from odoo import fields
        record = self.as_owner().browse(self.inbound.id)
        self.device.sudo().write({
            "send_window_start": fields.Datetime.now() - timedelta(hours=2),
            "send_count": 100,
        })
        # Fenêtre expirée : le compteur repart, l'envoi passe.
        result = record.mobile_reply(mode="reply", body="Nouvelle fenêtre.",
                                     device=self.device)
        self.assertTrue(result["ok"])
        self.assertEqual(self.device.sudo().send_count, 1)

    def test_the_quota_does_not_apply_without_a_device(self):
        """Le bureau et l'ORM ne passent jamais d'appareil : rien ne change
        pour eux."""
        record = self.as_owner().browse(self.inbound.id)
        for i in range(5):
            record.mobile_reply(mode="reply", body="Message %d." % i)

    def test_a_duplicate_replay_does_not_consume_quota(self):
        """Un rejeu reconnu comme doublon n'envoie rien : il ne doit rien
        coûter non plus, sinon une file hors ligne rejouée épuiserait le
        plafond sans qu'un seul courriel ne parte."""
        record = self.as_owner().browse(self.inbound.id)
        record.mobile_reply(mode="reply", body="Une fois.",
                            device=self.device, client_token="jeton-quota")
        consumed = self.device.sudo().send_count
        for _ in range(5):
            record.mobile_reply(mode="reply", body="Une fois.",
                                device=self.device, client_token="jeton-quota")
        self.assertEqual(self.device.sudo().send_count, consumed)

    # --------------------------------------------------- actions groupées
    def test_a_bulk_action_over_the_cap_is_refused(self):
        """Archiver déplace AUSSI chaque message côté IMAP : une liste sans
        borne, c'est un nombre sans borne d'opérations sur la boîte réelle.

        Les identifiants sont ceux d'un courriel que l'usager POSSÈDE, répétés :
        avec des identifiants quelconques, c'est le contrôle de propriété qui
        lèverait en premier (``AccessError`` dérive de ``UserError``) et le test
        passerait au vert même sans plafond. Vérifié par mutation.
        """
        # Le nombre est écrit EN DUR, pas importé de la constante : importer
        # la valeur ferait suivre le test à toute mutation et il ne pourrait
        # plus rien détecter. Relever le plafond doit casser ce test — c'est
        # exactement le moment où quelqu'un doit relire cette décision.
        owned = [self.inbound.id] * 150
        with self.assertRaisesRegex(UserError, "Trop de courriels"):
            self.as_owner().mobile_set_handled(owned, handled=True)

    def test_a_normal_bulk_action_still_works(self):
        counts = self.as_owner().mobile_set_handled(
            [self.inbound.id, self.with_attachment.id], handled=True)
        self.assertIsInstance(counts, dict)

    # -------------------------------------------------------- suppressions
    def test_the_upload_sweep_only_ever_touches_staged_uploads(self):
        """La purge est cadrée sur son modèle-marqueur : aucune autre pièce
        jointe de la base ne doit être concernée."""
        bystander = self.env["ir.attachment"].create({
            "name": "document-client.pdf",
            "raw": b"contenu important",
            "res_model": "res.partner",
            "res_id": self.partner.id,
        })
        self.env.cr.execute(
            "UPDATE ir_attachment SET create_date = now() - interval '90 days' "
            "WHERE id = %s", (bystander.id,))
        self.env["ir.attachment"].invalidate_model(["create_date"])

        self.as_owner()._gc_uploads(hours=1)

        self.assertTrue(bystander.exists(),
                        "la purge a touché une pièce jointe étrangère")

    # ------------------------------------------- symétrie archive/restaure
    def _archived_row(self):
        """Une ligne dans l'état exact que laisse un archivage."""
        row = self.as_owner().browse(self.inbound.id)
        row.write({"is_handled": True, "imap_in_inbox": False,
                   "imap_folder": "Archives/2026"})
        return row

    def test_restore_asks_imap_to_put_the_message_back(self):
        """Archiver déplace le message sur le serveur ; restaurer doit le
        ramener.

        Sans cela la ligne revient « en boîte » côté Odoo alors que le message
        reste dans ``Archives/{YYYY}`` — et comme le cron miroir a déjà mis
        ``imap_in_inbox`` à faux, elle échoue au filtre de la boîte : plus
        joignable ni depuis l'app ni depuis la vraie INBOX.
        """
        self.account.writeback_archive = True
        row = self._archived_row()
        with patch.object(type(row), "_imap_writeback_restore",
                          autospec=True) as restore:
            row.action_unhandle()
        restore.assert_called_once()
        self.assertEqual(restore.call_args[0][0].ids, [self.inbound.id])
        self.assertFalse(self.inbound.is_handled)

    def test_restore_skips_imap_when_writeback_is_off(self):
        """Le compte pilote la bilatéralité ; désactivé, on ne touche à rien."""
        self.account.writeback_archive = False
        row = self._archived_row()
        with patch.object(type(row), "_imap_writeback_restore",
                          autospec=True) as restore:
            row.action_unhandle()
        restore.assert_not_called()
        self.assertFalse(self.inbound.is_handled)

    def test_a_failing_imap_restore_still_unhandles_the_row(self):
        """Un serveur injoignable ne doit pas empêcher la remise en boîte
        côté Odoo — sinon le bouton paraît mort."""
        self.account.writeback_archive = True
        row = self._archived_row()
        with patch.object(type(row), "_imap_writeback_restore", autospec=True,
                          side_effect=OSError("serveur IMAP injoignable")):
            row.action_unhandle()
        self.assertFalse(self.inbound.is_handled)

    def test_the_device_sweep_spares_signed_in_devices(self):
        """La purge des connexions abandonnées ne doit pas déconnecter
        quelqu'un."""
        self.env["bf.email.mobile.device"]._gc_pending()
        self.assertTrue(self.device.exists())
        self.assertTrue(
            self.env["bf.email.mobile.device"]._resolve(self.device.device_token))
