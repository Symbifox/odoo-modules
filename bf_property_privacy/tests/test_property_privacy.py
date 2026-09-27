"""Ce que le pont reflète, et ce qu'il refuse de faire à la place du module.

Trois idées :

1. **Le dossier Loi 25 est un miroir, pas une vanne.** Ce qui décide si un texto
   part reste la ligne de consentement du module. Un deuxième interrupteur
   finirait par contredire le premier.
2. **La durée déclarée doit être celle qui s'applique.** Une politique de
   conservation qui annonce autre chose que ce que le code fait est pire que pas
   de politique.
3. **Rien de rétroactif.** Ouvrir aujourd'hui un dossier pour un consentement
   donné l'an dernier fabriquerait une preuve qui ment sur sa propre date.
"""
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

SEND = ("odoo.addons.bf_sms_archive.models.sms_message"
        ".SmsArchiveMessage.action_send")


@tagged("post_install", "-at_install")
class TestPropertyPrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat vie privée", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble vie privée", "organisation_id": cls.syndicat.id}
        )
        cls.unit = cls.env["bf.property.unit"].create(
            {"name": "P-1", "building_id": cls.building.id, "quote_part": 1000.0}
        )
        cls.owner = cls.env["res.partner"].create(
            {"name": "Consentante", "email": "consentante@example.invalid"}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.unit.id, "partner_id": cls.owner.id}
        )
        cls.line = cls.env["sms.archive.line"].create(
            {"label": "Ligne vie privée", "did": "5145550000",
             "owner_id": cls.env.user.id}
        )

    def _consent(self, **kw):
        vals = {
            "organisation_id": self.syndicat.id,
            "partner_id": self.owner.id,
            "phone": "+15145551234",
        }
        vals.update(kw)
        return self.env["bf.property.sms.consent"].create(vals)

    # ── 1. Le dossier reflète, il ne commande pas ──

    def test_a_consent_opens_its_file_in_the_processing_register(self):
        consent = self._consent(note="Recueilli au comptoir, formulaire signé.")
        formal = consent.privacy_consent_id
        self.assertTrue(formal)
        self.assertEqual(formal.status, "granted")
        self.assertEqual(formal.subject_partner_id, self.owner)
        self.assertEqual(
            formal.purpose_id,
            self.env.ref("bf_property_privacy.purpose_property_sms"),
        )
        self.assertIn("comptoir", formal.notes)

    def test_the_file_carries_the_version_of_the_notice_that_was_shown(self):
        """Sans version, la chaîne de preuve ne dit pas ce que la personne a lu."""
        consent = self._consent()
        self.assertEqual(
            consent.privacy_consent_id.notice_version_id,
            self.env.ref("bf_property_privacy.notice_property_sms_version_1"),
        )

    def test_the_file_is_dated_from_the_consent_not_from_today(self):
        given = fields.Date.subtract(
            fields.Date.context_today(self.env.user), days=40
        )
        consent = self._consent(given_date=given)
        self.assertEqual(consent.privacy_consent_id.granted_at.date(), given)

    def test_a_withdrawal_closes_the_file_the_same_day(self):
        consent = self._consent()
        consent.action_withdraw()
        formal = consent.privacy_consent_id
        self.assertEqual(formal.status, "withdrawn")
        self.assertEqual(formal.withdrawn_at.date(), consent.withdrawn_date)
        self.assertIn("1070", formal.withdrawal_reason)

    def test_the_file_does_not_decide_whether_a_text_goes_out(self):
        """🔴 Un deuxième interrupteur finirait par contredire le premier.

        Le dossier Loi 25 est révoqué à la main, la ligne du module reste en
        vigueur : c'est elle qui commande, et le texto part. L'inverse ferait
        dépendre un envoi d'un enregistrement qu'un autre module peut modifier
        sans que le syndicat le sache.
        """
        self.syndicat.write(
            {"sms_enabled": True, "sms_line_id": self.line.id}
        )
        consent = self._consent()
        consent.privacy_consent_id.sudo().status = "withdrawn"
        self.assertTrue(consent.active_consent)
        with patch(SEND) as send:
            self.env["bf.property.parcel"].create(
                {
                    "organisation_id": self.syndicat.id,
                    "building_id": self.building.id,
                    "unit_id": self.unit.id,
                    "partner_id": self.owner.id,
                    "carrier": "Postes Canada",
                }
            )
            self.assertTrue(send.called)

    def test_an_unopened_file_never_blocks_a_consent(self):
        """⚠️ Un dossier de conformité qui n'a pas pu s'ouvrir est un problème à
        régler, pas une raison d'empêcher un syndicat de recueillir le
        consentement qu'on lui demande de recueillir."""
        with patch(
            "odoo.addons.bf_property_privacy.models.bf_property_sms_consent"
            ".BfPropertySmsConsent._privacy_notice",
            return_value=self.env["privacy.notice"],
        ):
            consent = self._consent()
        self.assertTrue(consent.exists())
        self.assertTrue(consent.active_consent)
        self.assertFalse(consent.privacy_consent_id)

    def test_a_withdrawn_line_gets_no_file_at_all(self):
        consent = self._consent()
        consent.action_withdraw()
        consent.privacy_consent_id.sudo().unlink()
        consent.invalidate_recordset(["privacy_consent_id"])
        consent._privacy_mirror_grant()
        self.assertFalse(consent.privacy_consent_id)

    # ── 2. La durée déclarée contre la durée appliquée ──

    def test_a_matching_retention_says_nothing(self):
        policy = self.env.ref(
            "bf_property_privacy.retention_property_access_log"
        )
        self.syndicat.log_retention_days = policy.retention_days
        self.assertFalse(self.syndicat.privacy_retention_warning)
        self.assertEqual(
            self.syndicat.privacy_retention_declared, policy.retention_days
        )

    def test_a_divergent_retention_is_said_out_loud(self):
        """🔴 Ce qu'un enquêteur demande n'est pas ce qu'on a écrit, c'est ce
        qu'on a gardé."""
        policy = self.env.ref(
            "bf_property_privacy.retention_property_access_log"
        )
        self.syndicat.log_retention_days = policy.retention_days + 275
        warning = self.syndicat.privacy_retention_warning
        self.assertIn(str(policy.retention_days), warning)
        self.assertIn(str(policy.retention_days + 275), warning)

    def test_no_purge_at_all_is_the_widest_gap(self):
        """⚠️ Zéro n'est pas une durée courte, c'est l'absence de purge."""
        self.syndicat.log_retention_days = 0
        self.assertIn(
            "ne purge pas", self.syndicat.privacy_retention_warning
        )

    # ── 3. Ce que le pont déclare, et ce qu'il ne fait pas ──

    def test_the_register_purpose_asks_for_no_consent(self):
        """Le nom et l'adresse postale sont au registre par l'effet de la loi.

        Y accoler un consentement laisserait croire qu'on peut le refuser.
        """
        purpose = self.env.ref("bf_property_privacy.purpose_property_register")
        self.assertFalse(purpose.requires_consent)
        self.assertFalse(purpose.requires_express_opt_in)

    def test_the_sms_purpose_demands_an_express_opt_in(self):
        purpose = self.env.ref("bf_property_privacy.purpose_property_sms")
        self.assertTrue(purpose.requires_consent)
        self.assertTrue(purpose.requires_express_opt_in)

    def test_the_visitor_log_declares_a_retention_and_no_consent(self):
        """Le visiteur n'a aucun compte d'où retirer quoi que ce soit : ce qui
        reste est d'informer et de ne pas garder au-delà de la nécessité."""
        purpose = self.env.ref(
            "bf_property_privacy.purpose_property_access_log"
        )
        self.assertFalse(purpose.requires_consent)
        policy = self.env.ref(
            "bf_property_privacy.retention_property_access_log"
        )
        self.assertEqual(policy.purpose_id, purpose)
        self.assertEqual(policy.destruction_method, "delete")
        self.assertIn("plaque", policy.data_categories.lower())

    def test_the_bridge_destroys_nothing_of_its_own(self):
        """⚠️ La purge du journal existe déjà et elle est éprouvée. Router les
        mêmes pièces vers une deuxième mécanique ferait courir le risque de
        certifier ce que personne n'a fait."""
        module_files = self.env["ir.model.data"].search(
            [("module", "=", "bf_property_privacy"),
             ("model", "=", "privacy.destruction.request")]
        )
        self.assertFalse(module_files)
        self.assertNotIn(
            "destruction_request_id",
            self.env["bf.property.sms.consent"]._fields,
        )
