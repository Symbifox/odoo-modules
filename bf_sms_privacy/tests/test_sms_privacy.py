"""Ce que ces tests doivent PROUVER, et pas seulement observer.

Chaque contrôle est écrit pour échouer si le comportement qu'il décrit
disparaît. Trois d'entre eux valent d'être signalés :

* `test_mms_attachment_leaves_with_the_message` échoue si l'on retire le
  passage par l'ORM sur les parties MMS — c'est le défaut qui laisserait des
  photos orphelines pendant que le registre certifie la destruction.
* `test_archived_meeting_still_holds_the_call` échoue si l'on lit les comptes
  rendus sans `active_test=False`.
* `test_foreign_model_relays_to_super` échoue si la surcharge cesse de relayer,
  ce qui ferait taire les gardes des autres ponts.
"""
from datetime import date, datetime, timedelta

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSmsPrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ Créer un enregistrement pose un `mail.message` daté de MAINTENANT.
        # Sans ce contexte, les fixtures naissent bavardes et lentes.
        cls.env = cls.env(context=dict(
            cls.env.context,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
            tracking_disable=True,
        ))
        cls.Message = cls.env["sms.archive.message"]
        cls.Call = cls.env["call.archive.call"]
        cls.Tombstone = cls.env["sms.archive.privacy.tombstone"]
        cls.Line = cls.env["privacy.destruction.campaign.line"]
        cls.thread = cls.env["sms.archive.thread"].create({
            "phone_normalized": "+15550001111",
            "phone_raw": "555-000-1111",
            "contact_name": "Fixture",
            "owner_id": cls.env.uid,
        })
        cls.rule_sms = cls.env.ref("bf_sms_privacy.retention_sms_message")
        cls.rule_call = cls.env.ref("bf_sms_privacy.retention_call_log")
        cls.rule_meeting = cls.env.ref("bf_sms_privacy.retention_call_meeting")

    # ------------------------------------------------------------------
    # Fabriques
    # ------------------------------------------------------------------

    def _message(self, days_ago, body="coucou", tag="m"):
        sent = datetime.now() - timedelta(days=days_ago)
        return self.Message.create({
            "thread_id": self.thread.id,
            "message_hash": f"hash-{tag}-{days_ago}-{body}",
            "direction": "in",
            "body": body,
            "date_sent": sent,
            "owner_id": self.env.uid,
        })

    def _call(self, days_ago, tag="c"):
        return self.Call.create({
            "thread_id": self.thread.id,
            "call_hash": f"callhash-{tag}-{days_ago}",
            "call_type": "incoming",
            "date": datetime.now() - timedelta(days=days_ago),
            "duration": 60,
            "owner_id": self.env.uid,
        })

    def _lines_for(self, record):
        return self.Line.search([
            ("res_model", "=", record._name), ("res_id", "=", record.id),
        ])

    # ------------------------------------------------------------------
    # Les règles
    # ------------------------------------------------------------------

    def test_rules_carry_the_decided_durations(self):
        """Les durées inscrites au calendrier sont celles qui ont été décidées."""
        self.assertEqual(self.rule_sms.code, "TEL-001")
        self.assertEqual(self.rule_sms.total_retention_days, 1095)
        self.assertEqual(self.rule_call.code, "TEL-002")
        self.assertEqual(self.rule_call.total_retention_days, 1095)
        self.assertEqual(self.rule_meeting.code, "TEL-003")
        self.assertEqual(self.rule_meeting.total_retention_days, 2190)
        for rule in (self.rule_sms, self.rule_call, self.rule_meeting):
            self.assertEqual(rule.destruction_method, "delete")
            self.assertTrue(rule.requires_approval)

    # ------------------------------------------------------------------
    # Le balayage
    # ------------------------------------------------------------------

    def test_scan_separates_expired_from_recent(self):
        old = self._message(days_ago=1200, tag="old")
        recent = self._message(days_ago=100, tag="recent")
        buckets, _held = self.Message._privacy_scan_expired()
        found = self.Message.browse()
        for rows in buckets.values():
            found |= rows
        self.assertIn(old, found)
        self.assertNotIn(recent, found)

    def test_expiry_date_is_the_anchor_plus_the_rule(self):
        msg = self._message(days_ago=1200, tag="expiry")
        expected = msg.date_sent.date() + timedelta(days=1095)
        self.assertEqual(msg._privacy_expiry_date(), expected)

    # ------------------------------------------------------------------
    # La proposition
    # ------------------------------------------------------------------

    def test_cron_proposes_without_destroying(self):
        old = self._message(days_ago=1200, tag="propose")
        self.Message._cron_privacy_propose_destruction()
        lines = self._lines_for(old)
        self.assertEqual(len(lines), 1, "la ligne échue doit être proposée")
        self.assertEqual(lines.state, "pending")
        self.assertEqual(lines.retention_calendar_id, self.rule_sms)
        self.assertEqual(lines.campaign_id.state, "review",
                         "la campagne attend une approbation humaine")
        self.assertTrue(old.exists(), "le cron ne détruit rien")

    def test_second_pass_does_not_duplicate(self):
        old = self._message(days_ago=1200, tag="dedup")
        self.Message._cron_privacy_propose_destruction()
        self.Message._cron_privacy_propose_destruction()
        self.assertEqual(len(self._lines_for(old)), 1)

    def test_dry_run_proposes_nothing(self):
        old = self._message(days_ago=1200, tag="dry")
        proposed = self.Message._cron_privacy_propose_destruction(dry_run=True)
        self.assertEqual(proposed, 0)
        self.assertFalse(self._lines_for(old))

    def test_native_scan_is_refused_on_a_cron_fed_campaign(self):
        self._message(days_ago=1200, tag="scanguard")
        self.Message._cron_privacy_propose_destruction()
        campaign = self.env["privacy.destruction.campaign"].search(
            [("retention_calendar_id", "=", self.rule_sms.id)], limit=1,
        )
        self.assertTrue(campaign)
        with self.assertRaises(UserError):
            campaign.action_scan()

    # ------------------------------------------------------------------
    # Les appels et les comptes rendus
    # ------------------------------------------------------------------

    def _skip_without_meeting_link(self):
        if "meeting_record_ids" not in self.Call._fields:
            self.skipTest("bf_meeting_call_archive n'est pas installé")

    def test_unlinked_old_call_falls_under_tel_002(self):
        call = self._call(days_ago=1200, tag="lonely")
        buckets, _held = self.Call._privacy_scan_expired()
        self.assertIn(call, buckets.get(self.rule_call.id, self.Call.browse()))

    def test_recent_meeting_holds_the_call_past_three_years(self):
        self._skip_without_meeting_link()
        call = self._call(days_ago=1200, tag="held")
        self.env["meeting.record"].create({
            "name": "Rencontre récente",
            "date": datetime.now() - timedelta(days=30),
            "call_archive_id": call.id,
        })
        buckets, held = self.Call._privacy_scan_expired()
        proposed = self.Call.browse()
        for rows in buckets.values():
            proposed |= rows
        self.assertNotIn(call, proposed,
                         "un appel cité par un compte rendu vivant reste")
        self.assertTrue(held, "la rétention doit être motivée, pas muette")

    def test_archived_meeting_still_holds_the_call(self):
        """🔴 Le piège : `meeting.record` porte `active`.

        Lire sans `active_test=False` ferait passer un compte rendu archivé
        pour absent, et l'appel repasserait sous les trois ans alors que le
        compte rendu qui le cite est encore là.
        """
        self._skip_without_meeting_link()
        call = self._call(days_ago=1200, tag="archived")
        record = self.env["meeting.record"].create({
            "name": "Rencontre rangée",
            "date": datetime.now() - timedelta(days=30),
            "call_archive_id": call.id,
        })
        record.active = False
        buckets, _held = self.Call._privacy_scan_expired()
        proposed = self.Call.browse()
        for rows in buckets.values():
            proposed |= rows
        self.assertNotIn(call, proposed)

    def test_old_meeting_lets_the_call_go_under_tel_003(self):
        self._skip_without_meeting_link()
        call = self._call(days_ago=2600, tag="expired")
        self.env["meeting.record"].create({
            "name": "Vieille rencontre",
            "date": datetime.now() - timedelta(days=2400),
            "call_archive_id": call.id,
        })
        buckets, _held = self.Call._privacy_scan_expired()
        self.assertIn(call, buckets.get(self.rule_meeting.id, self.Call.browse()))
        self.assertNotIn(call, buckets.get(self.rule_call.id, self.Call.browse()))

    # ------------------------------------------------------------------
    # La destruction
    # ------------------------------------------------------------------

    def _executed_line(self, record, method="delete", rule=None):
        campaign = self.env["privacy.destruction.campaign"].create({
            "name": "Campagne d'essai",
            "retention_calendar_id": (rule or self.rule_sms).id,
            "cutoff_date": date.today(),
        })
        return self.Line.create({
            "campaign_id": campaign.id,
            "res_model": record._name,
            "res_id": record.id,
            "res_name": record.display_name,
            "retention_calendar_id": (rule or self.rule_sms).id,
            "destruction_method": method,
            "state": "pending",
        })

    def test_mms_attachment_leaves_with_the_message(self):
        """🔴 Le défaut le plus coûteux : la cascade SQL saute l'ORM.

        `sms.archive.mms.part.message_id` est `ondelete="cascade"`. Supprimer
        le message efface les parties au niveau SQL, sans que l'ORM n'appelle
        leur `unlink` — donc sans balayer `ir_attachment`. Sans le passage
        explicite par l'ORM, ce test laisse une pièce jointe orpheline.
        """
        msg = self._message(days_ago=1200, tag="mms")
        msg.is_mms = True
        attachment = self.env["ir.attachment"].create({
            "name": "photo.jpg",
            "type": "binary",
            "datas": b"aGVsbG8gd29ybGQ=",
            "mimetype": "image/jpeg",
            "res_model": "sms.archive.mms.part",
            "res_id": 0,
        })
        part = self.env["sms.archive.mms.part"].create({
            "message_id": msg.id,
            "content_type": "image/jpeg",
            "filename": "photo.jpg",
            "attachment_id": attachment.id,
        })
        attachment.res_id = part.id
        attachment_id, part_id = attachment.id, part.id

        self._executed_line(msg)._execute_destruction()

        self.assertFalse(msg.exists())
        self.assertFalse(
            self.env["sms.archive.mms.part"].browse(part_id).exists())
        self.assertFalse(
            self.env["ir.attachment"].browse(attachment_id).exists(),
            "la pièce jointe MMS doit partir avec le message, pas rester "
            "orpheline au filestore",
        )

    def test_anonymize_is_refused(self):
        msg = self._message(days_ago=1200, tag="anon")
        line = self._executed_line(msg, method="anonymize")
        with self.assertRaises(UserError):
            line._execute_destruction()
        self.assertTrue(msg.exists())

    def test_already_gone_is_skipped_not_certified(self):
        msg = self._message(days_ago=1200, tag="ghost")
        line = self._executed_line(msg)
        msg.unlink()
        line._execute_destruction()
        self.assertEqual(line.state, "skipped")

    def test_foreign_model_relays_to_super(self):
        """⚠️ La surcharge doit relayer, sinon elle fait taire les autres ponts.

        Un modèle inconnu prend le chemin du socle, qui répond « modèle non
        trouvé ». Si la surcharge cessait de relayer, la ligne resterait en
        attente et ce contrôle échouerait.
        """
        campaign = self.env["privacy.destruction.campaign"].create({
            "name": "Campagne étrangère",
            "retention_calendar_id": self.rule_sms.id,
            "cutoff_date": date.today(),
        })
        line = self.Line.create({
            "campaign_id": campaign.id,
            "res_model": "modele.qui.nexiste.pas",
            "res_id": 1,
            "res_name": "Étranger",
            "destruction_method": "delete",
            "state": "pending",
        })
        line._execute_destruction()
        self.assertEqual(line.state, "skipped")

    # ------------------------------------------------------------------
    # Les pierres tombales
    # ------------------------------------------------------------------

    def test_destruction_engraves_the_hash(self):
        msg = self._message(days_ago=1200, tag="grave")
        content_hash = msg.message_hash
        self._executed_line(msg)._execute_destruction()
        self.assertTrue(self.Tombstone.search([
            ("content_hash", "=", content_hash),
            ("res_model", "=", "sms.archive.message"),
        ]))

    def test_engraved_hash_cannot_be_reimported(self):
        msg = self._message(days_ago=1200, tag="resurrect")
        content_hash = msg.message_hash
        self._executed_line(msg)._execute_destruction()

        revenant = self.Message.create({
            "thread_id": self.thread.id,
            "message_hash": content_hash,
            "direction": "in",
            "body": "coucou",
            "date_sent": datetime.now() - timedelta(days=1200),
            "owner_id": self.env.uid,
        })
        self.assertFalse(revenant,
                         "un import qui recroise l'empreinte doit passer outre")

    def test_a_fresh_hash_still_goes_through(self):
        """La pierre tombale ne doit pas bloquer le courant normal."""
        msg = self._message(days_ago=1200, tag="blocked")
        self._executed_line(msg)._execute_destruction()
        fresh = self._message(days_ago=1, tag="fresh")
        self.assertTrue(fresh.exists())

    def test_engraving_is_idempotent(self):
        msg = self._message(days_ago=1200, tag="idem")
        content_hash = msg.message_hash
        msg._privacy_engrave_tombstones()
        msg._privacy_engrave_tombstones()
        self.assertEqual(len(self.Tombstone.search([
            ("content_hash", "=", content_hash),
        ])), 1)

    # ------------------------------------------------------------------
    # Le registre
    # ------------------------------------------------------------------

    def _register_vals(self, res_id):
        """Minimum exigé par le registre : approbateur, description, base légale."""
        return {
            "res_model": "sms.archive.message",
            "res_id": res_id,
            "res_name": f"Message #{res_id}",
            "destruction_method": "delete",
            "approved_by_id": self.env.user.id,
            "document_description": "Message SMS archivé",
            "legal_basis": "Art. 3.2 LPRPSP — règle TEL-001",
            "retention_calendar_id": self.rule_sms.id,
        }

    def test_register_refuses_to_certify_a_survivor(self):
        msg = self._message(days_ago=1200, tag="survivor")
        with self.assertRaises(UserError):
            self.env["privacy.destruction.register"].create(
                self._register_vals(res_id=msg.id))

    def test_register_entry_carries_the_scope_statement(self):
        msg = self._message(days_ago=1200, tag="scope")
        msg_id = msg.id
        msg.unlink()
        entry = self.env["privacy.destruction.register"].create(
            self._register_vals(res_id=msg_id))
        self.assertIn("done/", entry.document_description or "")
        self.assertIn("pierre tombale", entry.document_description or "")

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def test_models_are_classifiable(self):
        allowed = self.env["privacy.document.classification"]._privacy_classifiable_models()
        self.assertIn("sms.archive.message", allowed)
        self.assertIn("call.archive.call", allowed)
        self.assertIn("sms.archive.mms.part", allowed)
