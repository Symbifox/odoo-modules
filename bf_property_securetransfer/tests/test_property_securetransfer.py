"""Ce que le pont remet, ce qu'il refuse de remettre, et ce qu'il avoue.

⚠️ L'envoi lui-même est simulé. `bf_securetransfer` téléverse sur S3, et le
banc d'essai n'a pas de seau : éprouver le transport ici ne dirait rien de plus que
« boto3 fonctionne ». Ce qui s'éprouve, c'est la logique du pont — quelle pièce,
à qui, à quelles conditions, et ce qu'il dit quand le lien meurt sans avoir été
ouvert.
"""
import base64
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged

SEND = ("odoo.addons.bf_securetransfer.wizards.secure_send_wizard"
        ".SecureSendWizard.action_send")


@tagged("post_install", "-at_install")
class TestPropertySecureTransfer(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.email = "syndic@example.invalid"
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {
                "name": "Syndicat de la remise",
                "fraction_base": 1000,
                "promoter_handover_date": fields.Date.to_date("2020-05-01"),
            }
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble de la remise", "organisation_id": cls.syndicat.id}
        )
        cls.unit = cls.env["bf.property.unit"].create(
            {"name": "R-1", "building_id": cls.building.id, "quote_part": 1000.0}
        )
        cls.seller = cls.env["res.partner"].create(
            {"name": "Vendeuse", "email": "vendeuse@example.invalid"}
        )
        cls.mute = cls.env["res.partner"].create(
            {"name": "Sans courriel"}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.unit.id, "partner_id": cls.seller.id}
        )
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        cls.transfer = cls.env["secure.transfer"].create(
            {
                "name": "Transfert d'essai",
                "brand_id": cls.brand.id,
                "sender_email": "syndic@example.invalid",
            }
        )

    def _attestation(self, **kw):
        vals = {
            "organisation_id": self.syndicat.id,
            "unit_id": self.unit.id,
            "requester_partner_id": self.seller.id,
            "request_date": fields.Date.context_today(self.env.user),
        }
        vals.update(kw)
        return self.env["bf.property.attestation"].create(vals)

    def _sent(self):
        """La valeur que rend l'assistant : une action vers le transfert."""
        return {
            "type": "ir.actions.act_window",
            "res_model": "secure.transfer",
            "res_id": self.transfer.id,
        }

    # ── La remise ──

    def test_the_attestation_goes_out_and_the_transfer_comes_back(self):
        attestation = self._attestation()
        with patch(SEND, return_value=self._sent()):
            result = attestation.action_secure_send()
        self.assertEqual(result, self.transfer)
        self.assertEqual(attestation.secure_transfer_id, self.transfer)
        self.assertTrue(attestation.secure_sent_date)

    def test_the_delivered_copy_is_frozen_on_the_record(self):
        """⚠️ Le transfert efface la sienne. Une attestation régénérée six mois
        plus tard ne dirait pas la même chose : elle est datée de sa remise.

        ⚠️ `force_report_rendering` : sous `--test-enable`, Odoo rend du HTML
        au lieu d'un PDF pour ne pas payer wkhtmltopdf à chaque test. Sans ce
        contexte, l'épreuve dirait « une pièce est gelée » sans jamais avoir
        regardé la pièce qui part en production.
        """
        attestation = self._attestation().with_context(force_report_rendering=True)
        with patch(SEND, return_value=self._sent()):
            attestation.action_secure_send()
        frozen = attestation.secure_frozen_attachment_id
        self.assertTrue(frozen)
        self.assertEqual(frozen.res_model, "bf.property.attestation")
        self.assertEqual(frozen.res_id, attestation.id)
        self.assertTrue(frozen.name.endswith(".pdf"))
        self.assertTrue(base64.b64decode(frozen.datas).startswith(b"%PDF"))

    def test_a_piece_does_not_go_out_twice(self):
        attestation = self._attestation()
        with patch(SEND, return_value=self._sent()):
            attestation.action_secure_send()
            with self.assertRaises(UserError):
                attestation.action_secure_send()

    def test_a_recipient_without_an_email_is_refused(self):
        """Le lien sécurisé n'aurait nulle part où aller."""
        attestation = self._attestation(requester_partner_id=self.mute.id)
        with patch(SEND, return_value=self._sent()) as send:
            with self.assertRaises(UserError):
                attestation.action_secure_send()
            self.assertFalse(send.called)

    def test_a_cancelled_attestation_is_not_delivered(self):
        attestation = self._attestation()
        attestation.action_cancel()
        with patch(SEND, return_value=self._sent()) as send:
            with self.assertRaises(UserError):
                attestation.action_secure_send()
            self.assertFalse(send.called)

    def test_a_wizard_that_returns_nothing_attaches_nothing(self):
        """🔴 Sans identifiant rendu, la fiche ne doit RIEN garder.

        L'ancienne version retrouvait le transfert par « le dernier créé par
        moi », ce qui cède dès que deux envois se croisent.
        """
        attestation = self._attestation()
        with patch(SEND, return_value=False):
            with self.assertRaises(UserError):
                attestation.action_secure_send()
        self.assertFalse(attestation.secure_transfer_id)
        self.assertFalse(attestation.secure_sent_date)

    # ── Envoyer n'est pas remettre ──

    def test_an_expired_link_nobody_opened_is_said_out_loud(self):
        attestation = self._attestation()
        with patch(SEND, return_value=self._sent()):
            attestation.action_secure_send()
        self.assertFalse(attestation.secure_unread)
        self.transfer.write({"state": "expired", "download_count": 0})
        attestation.invalidate_recordset(["secure_unread"])
        self.assertTrue(attestation.secure_unread)

    def test_an_expired_link_that_was_opened_is_a_delivery(self):
        attestation = self._attestation()
        with patch(SEND, return_value=self._sent()):
            attestation.action_secure_send()
        self.transfer.write({"state": "expired", "download_count": 1})
        attestation.invalidate_recordset(["secure_unread"])
        self.assertFalse(attestation.secure_unread)

    # ── Les régimes qui n'ont pas de document imprimable ──

    def test_the_charge_statement_now_hands_over_its_own_document(self):
        """Le trou nommé à la construction du pont est refermé.

        Ce régime n'avait aucun document imprimable, alors que c'est celui dont
        le délai joue CONTRE le syndicat : le pont remettait donc ce que le
        syndicat avait bien voulu joindre. Depuis `bf_property_finance`
        18.0.3.3.0, l'état s'imprime, et la copie remise est gelée sur la fiche
        comme celle de l'attestation.
        """
        statement = self.env["bf.property.charge.statement"].create(
            {
                "organisation_id": self.syndicat.id,
                "unit_id": self.unit.id,
                "requester_partner_id": self.seller.id,
                "request_date": fields.Date.context_today(self.env.user),
            }
        )
        statement.issued_date = fields.Date.context_today(self.env.user)
        self.assertEqual(statement.state, "issued")
        with patch(SEND, return_value=self._sent()):
            statement.with_context(
                force_report_rendering=True
            ).action_secure_send()
        frozen = statement.secure_frozen_attachment_id
        self.assertTrue(frozen)
        self.assertTrue(frozen.name.endswith(".pdf"))
        self.assertTrue(base64.b64decode(frozen.datas).startswith(b"%PDF"))
        self.assertEqual(statement.secure_transfer_id, self.transfer)

    def test_a_statement_still_requested_is_refused(self):
        statement = self.env["bf.property.charge.statement"].create(
            {
                "organisation_id": self.syndicat.id,
                "unit_id": self.unit.id,
                "requester_partner_id": self.seller.id,
                "request_date": fields.Date.context_today(self.env.user),
            }
        )
        self.assertEqual(statement.state, "requested")
        with self.assertRaises(UserError):
            statement.action_secure_send()

    def test_a_disclosure_before_the_privacy_review_is_refused(self):
        """🔴 Une pièce partie par un lien sécurisé ne se caviarde plus.

        L'autorisation de l'art. 1068.2 ne couvre pas les renseignements
        personnels des autres copropriétaires, et la revue se fait avant.
        """
        disclosure = self.env["bf.property.disclosure"].create(
            {
                "organisation_id": self.syndicat.id,
                "unit_id": self.unit.id,
                "requester_partner_id": self.seller.id,
                "request_date": fields.Date.context_today(self.env.user),
            }
        )
        self.assertEqual(disclosure.state, "requested")
        with patch(SEND, return_value=self._sent()) as send:
            with self.assertRaises(UserError):
                disclosure.action_secure_send()
            self.assertFalse(send.called)

    # ── Ce que le pont ne duplique pas, et ce qu'il ne laisse pas traîner ──

    def _provided_disclosure(self):
        disclosure = self.env["bf.property.disclosure"].create(
            {
                "organisation_id": self.syndicat.id,
                "unit_id": self.unit.id,
                "requester_partner_id": self.seller.id,
                "request_date": fields.Date.context_today(self.env.user),
            }
        )
        self.env["ir.attachment"].create(
            {
                "name": "declaration.pdf",
                "datas": base64.b64encode(b"%PDF-1.4 declaration"),
                "res_model": disclosure._name,
                "res_id": disclosure.id,
            }
        )
        disclosure.provided_date = fields.Date.context_today(self.env.user)
        self.assertEqual(disclosure.state, "provided")
        return disclosure

    def test_a_joined_piece_is_not_duplicated_on_the_record(self):
        """⚠️ La pièce jointe EST déjà la copie de la fiche.

        La geler une deuxième fois doublerait la liste des pièces sans rien
        ajouter à ce que le syndicat peut prouver.
        """
        disclosure = self._provided_disclosure()
        before = self.env["ir.attachment"].search_count(
            [("res_model", "=", disclosure._name), ("res_id", "=", disclosure.id)]
        )
        with patch(SEND, return_value=self._sent()):
            disclosure.action_secure_send()
        after = self.env["ir.attachment"].search_count(
            [("res_model", "=", disclosure._name), ("res_id", "=", disclosure.id)]
        )
        self.assertEqual(after, before)
        self.assertFalse(disclosure.secure_frozen_attachment_id)
        self.assertEqual(disclosure.secure_transfer_id, self.transfer)

    def test_a_failed_send_leaves_no_bytes_behind(self):
        """🔴 Les copies de travail n'ont ni modèle ni fiche.

        Si l'envoi échoue et qu'on les laisse là, personne ne les reverra
        jamais — mais elles resteront dans les sauvegardes nocturnes.
        """
        Attachment = self.env["ir.attachment"]
        attestation = self._attestation()
        orphans = lambda: Attachment.search_count(
            [("res_model", "=", False), ("name", "like", attestation.name)]
        )
        before = orphans()
        with patch(SEND, side_effect=UserError("S3 injoignable")):
            with self.assertRaises(UserError):
                attestation.action_secure_send()
        self.assertEqual(orphans(), before)
        self.assertFalse(attestation.secure_transfer_id)

    def test_the_link_gets_a_brand_without_being_asked(self):
        """Sans marque, le lien n'aurait pas de domaine où vivre."""
        captured = {}

        def capture(wizard_self):
            captured["brand"] = wizard_self.brand_id
            captured["retention"] = wizard_self.retention_days
            return self._sent()

        attestation = self._attestation()
        with patch(SEND, autospec=True, side_effect=capture):
            attestation.action_secure_send()
        self.assertTrue(captured["brand"])
        self.assertFalse(captured["brand"].fixed_recipient)
        # Une pièce se lit une fois : ce n'est pas un partage de travail. La
        # durée est celle que la marque offre (30 jours si elle les offre).
        self.assertEqual(captured["retention"], attestation._secure_retention_days())

    def test_a_resident_cannot_hand_a_piece_to_a_third_party(self):
        """🔴 La garde d'autorité passe AVANT l'assistant d'envoi.

        Un fichier téléversé chez un tiers et un lien déjà parti ne se
        rappellent pas par un `rollback` : ce que le test mesure n'est pas
        « la méthode lève », c'est « l'assistant n'a jamais été appelé ».
        """
        resident = self.env["res.users"].create(
            {
                "name": "Vendeuse",
                "login": "vendeuse@example.invalid",
                "partner_id": self.seller.id,
                "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
            }
        )
        attestation = self._attestation()
        with patch(SEND, return_value=self._sent()) as send:
            with self.assertRaises(AccessError):
                attestation.with_user(resident).action_secure_send()
            self.assertFalse(send.called)
        self.assertFalse(attestation.secure_transfer_id)

    # ── Les pièces du fil et la durée offerte par la marque ──

    def test_a_thread_attachment_never_reaches_the_buyer(self):
        """🔴 L'original versé au fil n'est pas une pièce à remettre.

        Même `res_model`, même `res_id` : la seule différence est qu'il est
        rattaché à un message. C'est elle qui décide, pas le nom du fichier.
        """
        disclosure = self._provided_disclosure()
        original = self.env["ir.attachment"].create(
            {
                "name": "original-non-caviarde.pdf",
                "datas": base64.b64encode(b"%PDF-1.4 renseignements personnels"),
                "res_model": disclosure._name,
                "res_id": disclosure.id,
            }
        )
        disclosure.message_post(body="Reçu du copropriétaire.", attachment_ids=original.ids)
        names = [name for name, _content in disclosure._secure_joined_documents()]
        self.assertEqual(names, ["declaration.pdf"])

    def test_retention_follows_what_the_brand_offers(self):
        """30 jours si la marque les offre, sinon la plus longue offerte."""
        attestation = self._attestation()
        brand_cls = type(self.env["secure.transfer.brand"])
        with patch.object(brand_cls, "_effective_limits", create=True,
                          return_value={"expiry_choices": [1, 7]}):
            self.assertEqual(attestation._secure_retention_days(), 7)
        with patch.object(brand_cls, "_effective_limits", create=True,
                          return_value={"expiry_choices": [1, 7, 30]}):
            self.assertEqual(attestation._secure_retention_days(), 30)


@tagged("post_install", "-at_install")
class TestManagerCanUseTheBridge(TransactionCase):
    """🔴 Sans ce droit, le gestionnaire voyait le bouton sans
    pouvoir s'en servir. Il tient l'outil au niveau utilisateur, jamais admin."""

    def test_the_manager_holds_the_tool_as_a_user(self):
        manager = self.env["res.users"].create({
            "name": "Gestionnaire outillé", "login": "gest_outil_bf_property_securetransfer",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_property_core.group_bf_property_manager").id])],
        })
        self.assertTrue(manager.has_group("bf_securetransfer.group_securetransfer_user"))
        self.assertFalse(manager.has_group("bf_securetransfer.group_securetransfer_manager"))
