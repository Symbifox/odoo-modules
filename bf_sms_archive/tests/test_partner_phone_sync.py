"""Le rattachement d'un fil inscrit le numéro dans la fiche contact (18.0.5.10.0).

Ce que le module promettait avant : la Messagerie affiche le nom du contact.
Ce qu'il promet maintenant : le numéro lui-même atterrit dans la fiche, au
champ Mobile — sinon il reste connu de la seule Messagerie, invisible au CRM,
et le prochain fil ouvert sur ce numéro ne se rattache pas tout seul.

Les cas éprouvés ici : l'inscription elle-même, le respect d'un numéro déjà
inscrit (quel qu'en soit le formatage et le champ), le refus d'écraser un mobile
différent, la création d'un contact depuis un numéro inconnu, et le retour rendu
à la Messagerie pour qu'elle puisse l'annoncer.
"""

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestPartnerPhoneSync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Thread = cls.env["sms.archive.thread"]
        cls.Partner = cls.env["res.partner"]

    def _thread(self, phone="+15145550101"):
        return self.Thread.create({
            "phone_normalized": phone,
            "contact_name": "Inconnu",
            "owner_id": self.env.user.id,
        })

    # ── Inscription ────────────────────────────────────────────────

    def test_link_writes_mobile(self):
        """Un contact sans numéro reçoit celui du fil, au champ Mobile."""
        thread = self._thread()
        partner = self.Partner.create({"name": "Sans numéro"})
        thread.write({"partner_id": partner.id})
        self.assertEqual(partner.mobile, "+15145550101")
        self.assertFalse(partner.phone, "le champ Téléphone n'est pas touché")

    def test_link_leaves_a_trace(self):
        """L'écriture chez le contact se lit dans son chatter."""
        thread = self._thread()
        partner = self.Partner.create({"name": "Sans numéro"})
        before = len(partner.message_ids)
        thread.write({"partner_id": partner.id})
        self.assertEqual(len(partner.message_ids), before + 1)
        self.assertIn("+15145550101", partner.message_ids[0].body)

    # ── Numéro déjà connu : ne rien faire ──────────────────────────

    def test_same_number_other_format_is_a_noop(self):
        """« (514) 555-0101 » et « +15145550101 » sont le même numéro."""
        thread = self._thread()
        partner = self.Partner.create({
            "name": "Déjà mobile", "mobile": "(514) 555-0101",
        })
        before = len(partner.message_ids)
        thread.write({"partner_id": partner.id})
        self.assertEqual(partner.mobile, "(514) 555-0101", "formatage préservé")
        self.assertEqual(len(partner.message_ids), before, "aucune note inutile")

    def test_number_already_in_phone_field_is_a_noop(self):
        """Le numéro inscrit au Téléphone n'est pas recopié au Mobile."""
        thread = self._thread()
        partner = self.Partner.create({
            "name": "Déjà téléphone", "phone": "514-555-0101",
        })
        thread.write({"partner_id": partner.id})
        self.assertFalse(partner.mobile)

    # ── Mobile occupé : ne jamais écraser ──────────────────────────

    def test_other_mobile_is_never_overwritten(self):
        """Un mobile différent reste en place ; le fil laisse une note."""
        thread = self._thread()
        partner = self.Partner.create({
            "name": "Autre mobile", "mobile": "+15145559999",
        })
        before = len(partner.message_ids)
        thread.write({"partner_id": partner.id})
        self.assertEqual(partner.mobile, "+15145559999")
        self.assertEqual(len(partner.message_ids), before + 1)
        body = partner.message_ids[0].body
        self.assertIn("+15145550101", body)
        self.assertIn("+15145559999", body)

    # ── Chemins de la Messagerie ───────────────────────────────────

    def test_messenger_link_reports_what_it_did(self):
        """La Messagerie apprend que le numéro a été inscrit."""
        thread = self._thread()
        partner = self.Partner.create({"name": "Sans numéro"})
        data = self.Thread.messenger_link_partner(thread.id, partner.id)
        self.assertTrue(data["mobile_added"])
        self.assertEqual(partner.mobile, "+15145550101")

    def test_messenger_link_stays_quiet_when_nothing_written(self):
        """Rien d'inscrit, rien d'annoncé."""
        thread = self._thread()
        partner = self.Partner.create({
            "name": "Déjà mobile", "mobile": "+15145550101",
        })
        data = self.Thread.messenger_link_partner(thread.id, partner.id)
        self.assertFalse(data["mobile_added"])

    def test_created_partner_gets_a_mobile(self):
        """Un contact créé depuis un fil porte le numéro au Mobile."""
        thread = self._thread("+15145550202")
        self.Thread.messenger_create_partner(thread.id, "Nouveau contact")
        self.assertEqual(thread.partner_id.name, "Nouveau contact")
        self.assertEqual(thread.partner_id.mobile, "+15145550202")
        self.assertFalse(thread.partner_id.phone)

    # ── Appariement automatique : jamais de doublon ────────────────

    def test_auto_match_does_not_duplicate_the_number(self):
        """Le fil apparié par numéro ne réécrit pas ce numéro dans la fiche."""
        partner = self.Partner.create({
            "name": "Apparié", "phone": "5145550303",
        })
        thread = self.Thread._get_or_create("+15145550303", self.env.user.id)
        self.assertEqual(thread.partner_id, partner)
        self.assertFalse(thread.partner_id.mobile)

    # ── Droits : le report est un service, jamais une condition ────

    def test_link_survives_a_user_without_contact_rights(self):
        """Sans droit d'écriture sur les contacts, le fil se lie quand même."""
        user = new_test_user(
            self.env, login="sms_no_contact_rights",
            groups="bf_sms_archive.group_sms_user",
        )
        partner = self.Partner.create({"name": "Sans numéro"})
        thread = self.Thread.create({
            "phone_normalized": "+15145550404",
            "owner_id": user.id,
        })
        thread.with_user(user).write({"partner_id": partner.id})
        self.assertEqual(thread.partner_id, partner, "le rattachement tient")
        self.assertFalse(partner.mobile, "le report est simplement sauté")
