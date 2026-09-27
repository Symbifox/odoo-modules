"""Ce que le pont envoie, et surtout ce qu'il refuse d'envoyer.

Quatre idées, et chacune est un endroit où un pont de notification ordinaire se
tromperait :

1. **Le numéro n'est pas au registre de droit.** Art. 1070 al. 1 C.c.Q. : les
   autres renseignements personnels n'y sont qu'avec le consentement exprès.
   Aucun texto ne part sans une ligne de consentement en vigueur, quel que soit
   le réglage du syndicat.
2. **Deux interrupteurs, pas un.** Le syndicat ouvre le canal, la personne
   consent. Le premier sans le second n'envoie rien.
3. **L'auditoire de l'annonce prime sur le consentement.** Consentir à être
   joint ne donne pas droit à ce qui ne vous regarde pas.
4. **Un envoi qui échoue n'empêche pas d'enregistrer un colis.** Le concierge a
   le colis dans les mains.
"""
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger
from psycopg2 import IntegrityError

SEND = ("odoo.addons.bf_sms_archive.models.sms_message"
        ".SmsArchiveMessage.action_send")


@tagged("post_install", "-at_install")
class TestPropertySms(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat du texto", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble du texto", "organisation_id": cls.syndicat.id}
        )
        cls.owner_unit = cls.env["bf.property.unit"].create(
            {"name": "T-1", "building_id": cls.building.id, "quote_part": 600.0}
        )
        cls.rented_unit = cls.env["bf.property.unit"].create(
            {"name": "T-2", "building_id": cls.building.id, "quote_part": 400.0}
        )
        cls.owner = cls.env["res.partner"].create(
            {"name": "Propriétaire texto", "email": "proptexto@example.invalid"}
        )
        cls.landlord = cls.env["res.partner"].create(
            {"name": "Bailleur texto", "email": "bailleurtexto@example.invalid"}
        )
        cls.tenant = cls.env["res.partner"].create(
            {"name": "Locataire texto", "email": "loctexto@example.invalid"}
        )
        cls.outsider = cls.env["res.partner"].create(
            {"name": "Étranger texto", "email": "etrangertexto@example.invalid"}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.owner_unit.id, "partner_id": cls.owner.id}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.rented_unit.id, "partner_id": cls.landlord.id}
        )
        cls.rented_unit.write({"is_rented": True, "occupant_id": cls.tenant.id})

        # ⚠️ Le champ s'appelle `label`, pas `name`, sur sms.archive.line.
        cls.line = cls.env["sms.archive.line"].create(
            {"label": "Ligne d'essai", "did": "5145550000",
             "owner_id": cls.env.user.id}
        )

    def _consent(self, partner, **kw):
        vals = {
            "organisation_id": self.syndicat.id,
            "partner_id": partner.id,
            "phone": "+15145551234",
        }
        vals.update(kw)
        return self.env["bf.property.sms.consent"].create(vals)

    def _open_channel(self):
        self.syndicat.write({"sms_enabled": True, "sms_line_id": self.line.id})

    def _parcel(self, partner=None, unit=None):
        return self.env["bf.property.parcel"].create(
            {
                "organisation_id": self.syndicat.id,
                "building_id": self.building.id,
                "unit_id": (unit or self.owner_unit).id,
                "partner_id": (partner or self.owner).id,
                "carrier": "Postes Canada",
            }
        )

    # ── 1. Aucun texto sans consentement ──

    def test_no_consent_means_no_text_even_with_the_channel_open(self):
        """Art. 1070 al. 1 : le numéro n'est au registre qu'avec le consentement."""
        self._open_channel()
        with patch(SEND) as send:
            parcel = self._parcel()
            self.assertFalse(send.called)
        trail = " ".join(parcel.message_ids.mapped("body"))
        self.assertIn("Aucun avis par texto", trail)
        self.assertIn("consenti", trail)

    def test_a_consent_opens_the_door(self):
        self._open_channel()
        self._consent(self.owner)
        with patch(SEND) as send:
            self._parcel()
            self.assertTrue(send.called)
            args = send.call_args[0]
            self.assertEqual(args[1], "+15145551234")

    def test_a_withdrawn_consent_closes_it_again(self):
        self._open_channel()
        consent = self._consent(self.owner)
        consent.action_withdraw()
        self.assertFalse(consent.active_consent)
        with patch(SEND) as send:
            self._parcel()
            self.assertFalse(send.called)

    def test_a_withdrawal_does_not_erase_the_consent(self):
        """Effacer empêcherait de dire ce qui était permis lors d'un envoi fait."""
        consent = self._consent(self.owner)
        consent.action_withdraw()
        self.assertTrue(consent.exists())
        self.assertTrue(consent.given_date)
        self.assertTrue(consent.withdrawn_date)

    def test_a_channel_can_be_refused_on_its_own(self):
        self._open_channel()
        self._consent(self.owner, channel_parcel=False)
        with patch(SEND) as send:
            self._parcel()
            self.assertFalse(send.called)

    # ── 2. Deux interrupteurs ──

    def test_a_closed_channel_sends_nothing_even_with_consent(self):
        self._consent(self.owner)
        self.assertFalse(self.syndicat.sms_enabled)
        with patch(SEND) as send:
            parcel = self._parcel()
            self.assertFalse(send.called)
        self.assertIn("canal texto du syndicat est fermé",
                      " ".join(parcel.message_ids.mapped("body")))

    def test_an_open_channel_without_a_line_sends_nothing(self):
        self.syndicat.sms_enabled = True
        self._consent(self.owner)
        with patch(SEND) as send:
            parcel = self._parcel()
            self.assertFalse(send.called)
        # « fermé » se disait aussi ici, alors que le canal était ouvert.
        body = " ".join(parcel.message_ids.mapped("body"))
        self.assertIn("aucune ligne d'envoi", body)
        self.assertNotIn("fermé", body)

    def test_a_concierge_who_is_not_on_the_line_still_sends(self):
        """🔴 Le texto partait seulement si celui
        qui enregistrait le colis était membre de la ligne. Il part maintenant
        au nom du propriétaire de la ligne que le syndicat a choisie."""
        concierge = self.env["res.users"].create({
            "name": "Concierge hors ligne", "login": "concierge_hors_ligne",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_property_core.group_bf_property_manager").id])],
        })
        self.assertFalse(self.line._is_usable_by(concierge))
        self._open_channel()
        self._consent(self.owner)
        seen = {}

        def capture(message_self, *args, **kwargs):
            seen["user"] = message_self.env.user
            return True

        with patch(SEND, autospec=True, side_effect=capture):
            parcel = self.env["bf.property.parcel"].with_user(concierge).create({
                "organisation_id": self.syndicat.id, "building_id": self.building.id,
                "unit_id": self.owner_unit.id, "partner_id": self.owner.id,
                "carrier": "Postes Canada"})
        self.assertEqual(seen.get("user"), self.line.owner_id)
        self.assertIn("envoyé", " ".join(parcel.sudo().message_ids.mapped("body")))

    def test_a_carrier_error_is_not_followed_by_a_double_period(self):
        self._open_channel()
        self._consent(self.owner)
        with patch(SEND, side_effect=UserError("Cette ligne ne vous est pas accessible.")):
            parcel = self._parcel()
        self.assertNotIn("..", " ".join(parcel.message_ids.mapped("body")))

    # ── 3. Le consentement ne donne pas droit à ce qui ne vous regarde pas ──

    def test_an_owners_only_announcement_never_reaches_a_tenant(self):
        """Consentir à être joint n'est pas consentir à tout recevoir."""
        self._open_channel()
        self._consent(self.owner)
        self._consent(self.tenant)
        announcement = self.env["bf.property.announcement"].create(
            {
                "name": "Vote sur la toiture",
                "organisation_id": self.syndicat.id,
                "audience": "owners",
                "date_start": fields.Date.context_today(self.env.user),
            }
        )
        announcement.action_publish()
        with patch(SEND) as send:
            announcement.action_send_urgent_sms()
        reached = [call[0][1] for call in send.call_args_list]
        self.assertEqual(len(reached), 1)
        self.assertEqual(announcement.sms_sent_count, 1)

    def test_an_announcement_for_everyone_reaches_both(self):
        self._open_channel()
        self._consent(self.owner)
        self._consent(self.tenant)
        announcement = self.env["bf.property.announcement"].create(
            {
                "name": "Panne d'eau chaude",
                "organisation_id": self.syndicat.id,
                "audience": "all",
                "date_start": fields.Date.context_today(self.env.user),
            }
        )
        announcement.action_publish()
        with patch(SEND) as send:
            announcement.action_send_urgent_sms()
        self.assertEqual(announcement.sms_sent_count, 2)
        self.assertTrue(send.call_count, 2)

    def test_an_unpublished_announcement_is_not_sent(self):
        """Le portail doit pouvoir la relire : un texto renvoie à quelque chose."""
        self._open_channel()
        self._consent(self.owner)
        announcement = self.env["bf.property.announcement"].create(
            {
                "name": "Pas encore publiée",
                "organisation_id": self.syndicat.id,
                "audience": "all",
                "date_start": fields.Date.context_today(self.env.user),
            }
        )
        with self.assertRaises(UserError):
            announcement.action_send_urgent_sms()

    def test_an_announcement_is_not_sent_twice(self):
        self._open_channel()
        self._consent(self.owner)
        announcement = self.env["bf.property.announcement"].create(
            {
                "name": "Une seule fois",
                "organisation_id": self.syndicat.id,
                "audience": "all",
                "date_start": fields.Date.context_today(self.env.user),
            }
        )
        announcement.action_publish()
        with patch(SEND):
            announcement.action_send_urgent_sms()
            with self.assertRaises(UserError):
                announcement.action_send_urgent_sms()

    # ── 4. Un envoi raté n'empêche pas d'enregistrer ──

    def test_a_failing_carrier_does_not_stop_the_parcel(self):
        """Le concierge a le colis dans les mains."""
        self._open_channel()
        self._consent(self.owner)
        with patch(SEND, side_effect=UserError("VoIP.ms injoignable")):
            parcel = self._parcel()
        self.assertTrue(parcel.exists())
        self.assertEqual(parcel.state, "held")
        self.assertIn("injoignable", " ".join(parcel.message_ids.mapped("body")))

    # ── Le message en dit le moins possible ──

    def test_the_message_says_the_least_it_can(self):
        """Pas de numéro de porte, pas de transporteur, pas de suivi."""
        self._open_channel()
        self._consent(self.owner)
        with patch(SEND) as send:
            self._parcel()
        body = send.call_args[0][2]
        self.assertIn(self.syndicat.name, body)
        self.assertNotIn(self.owner_unit.name, body)
        self.assertNotIn("Postes Canada", body)

    # ── Le consentement se rattache à quelqu'un d'ici ──

    def test_a_consent_needs_someone_who_lives_there(self):
        with self.assertRaises(ValidationError):
            self._consent(self.outsider)

    def test_a_consent_is_unique_per_person_and_syndicat(self):
        self._consent(self.owner)
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self._consent(self.owner)
            self.env.flush_all()

    def test_a_withdrawal_cannot_predate_the_consent(self):
        with self.assertRaises(ValidationError):
            self._consent(
                self.owner,
                given_date=fields.Date.context_today(self.env.user),
                withdrawn_date=fields.Date.subtract(
                    fields.Date.context_today(self.env.user), days=1
                ),
            )

    # ── 5. Qui décide d'envoyer ──

    def test_a_resident_cannot_fire_the_urgent_blast(self):
        """🔴 La garde d'autorité avant tout envoi.

        Sans garde, un résident appelait la méthode par RPC : un texto partait
        chez le fournisseur, puis l'`AccessError` de l'écriture finale annulait
        la transaction. Les droits avaient sauvé la base de données, pas le
        réseau téléphonique — et comme `sms_sent_date` restait vide, l'appel se
        rejouait sans limite.

        Ce que le test mesure n'est donc PAS « la méthode lève », c'est
        « AUCUN texto n'est parti ».
        """
        self._open_channel()
        self._consent(self.owner)
        resident = self.env["res.users"].create(
            {
                "name": "Propriétaire texto",
                "login": "proptexto@example.invalid",
                "partner_id": self.owner.id,
                "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
            }
        )
        announcement = self.env["bf.property.announcement"].create(
            {
                "name": "Alerte détournée",
                "organisation_id": self.syndicat.id,
                "audience": "all",
                "date_start": fields.Date.context_today(self.env.user),
            }
        )
        announcement.action_publish()
        with patch(SEND) as send:
            with self.assertRaises(AccessError):
                announcement.with_user(resident).action_send_urgent_sms()
            self.assertEqual(send.call_count, 0)
        self.assertFalse(announcement.sms_sent_date)
