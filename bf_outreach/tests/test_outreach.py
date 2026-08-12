# -*- coding: utf-8 -*-
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_outreach")
class TestOutreach(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage_todo = cls.env.ref("bf_outreach.stage_to_contact")
        cls.stage_contacted = cls.env.ref("bf_outreach.stage_contacted")
        cls.stage_won = cls.env.ref("bf_outreach.stage_won")
        cls.stage_lost = cls.env.ref("bf_outreach.stage_lost")
        # Tout est ancré sur « maintenant » : la cadence part de la date d'ajout
        # de la cible dès que celle-ci est postérieure au début de campagne,
        # donc des dates fixes rendraient les tests faux au fil du temps.
        cls.now = fields.Datetime.now()
        cls.today = cls.now.date()
        cls.start = cls.today - timedelta(days=30)
        cls.campaign = cls.env["bf.outreach.campaign"].create(
            {
                "name": "Campagne de test",
                "date_start": cls.start,
                "state": "running",
                "call_target_count": 3,
                "email_target_count": 2,
                "call_interval_days": 7,
                "email_interval_days": 14,
                "stop_on_reply": True,
                "working_days_only": False,
            }
        )

    def _make_target(self, name="Cible A", **values):
        target = self.env["bf.outreach.target"].create(
            dict({"name": name, "campaign_id": self.campaign.id}, **values)
        )
        return target

    def _log(self, target, kind="call", when=None, outcome="no_answer", direction="out"):
        return self.env["bf.outreach.touch"].create(
            {
                "target_id": target.id,
                "kind": kind,
                "outcome": outcome,
                "direction": direction,
                "date": when or self.now,
            }
        )

    # ------------------------------------------------------------------
    def test_default_stage_and_first_due_date(self):
        target = self._make_target()
        self.assertEqual(target.stage_id, self.stage_todo)
        # Jamais contactée : due dès le début de la campagne (ou son ajout).
        expected = max(self.start, target.create_date.date())
        self.assertEqual(expected, self.today)
        self.assertEqual(target.next_call_date, expected)
        self.assertEqual(target.next_email_date, expected)
        self.assertEqual(target.next_action_date, expected)
        # À égalité de date, l'appel passe avant le courriel.
        self.assertEqual(target.next_action_kind, "call")

    def test_cadence_advances_after_each_call(self):
        target = self._make_target()
        self._log(target, "call", self.now)
        self.assertEqual(target.call_count, 1)
        self.assertEqual(target.next_call_date, self.today + timedelta(days=7))
        self._log(target, "call", self.now + timedelta(days=7))
        self.assertEqual(target.next_call_date, self.today + timedelta(days=14))
        # Quota d'appels atteint : plus d'appel prévu, le courriel prend le relais.
        self._log(target, "call", self.now + timedelta(days=14))
        self.assertEqual(target.call_count, 3)
        self.assertFalse(target.next_call_date)
        self.assertEqual(target.next_action_kind, "email")

    def test_email_cadence_is_independent(self):
        target = self._make_target()
        self._log(target, "email", self.now, outcome="sent")
        self.assertEqual(target.email_count, 1)
        self.assertEqual(target.next_email_date, self.today + timedelta(days=14))
        # L'appel, lui, reste dû aujourd'hui.
        self.assertEqual(target.next_call_date, self.today)
        self.assertEqual(target.next_action_kind, "call")

    def test_letter_cadence(self):
        """Les lettres sont un canal à part entière, éteint par défaut."""
        target = self._make_target()
        self.assertFalse(target.next_letter_date)  # quota à 0
        self.campaign.letter_target_count = 2
        self.campaign.letter_interval_days = 30
        self.assertEqual(target.next_letter_date, self.today)
        # L'appel garde la priorité à égalité de date.
        self.assertEqual(target.next_action_kind, "call")
        self._log(target, "letter", self.now, outcome="sent")
        self.assertEqual(target.letter_count, 1)
        self.assertEqual(target.touch_count, 1)
        self.assertEqual(target.next_letter_date, self.today + timedelta(days=30))
        self._log(target, "letter", self.now + timedelta(days=30), outcome="sent")
        self.assertFalse(target.next_letter_date)
        # Les lettres comptent dans les contacts prévus de la campagne.
        self.campaign.invalidate_recordset()
        self.assertEqual(self.campaign.letter_count, 2)
        self.assertEqual(self.campaign.planned_touch_count, 3 + 2 + 2)

    def test_letter_only_campaign_orders_by_date(self):
        """Sans appel ni courriel prévus, la lettre devient la prochaine action."""
        self.campaign.write(
            {"call_target_count": 0, "email_target_count": 0, "letter_target_count": 1}
        )
        target = self._make_target()
        self.assertFalse(target.next_call_date)
        self.assertFalse(target.next_email_date)
        self.assertEqual(target.next_action_kind, "letter")
        self.assertEqual(target.next_action_date, self.today)

    def test_stop_on_reply(self):
        target = self._make_target()
        self._log(target, "email", outcome="replied")
        self.assertTrue(target.has_reply)
        self.assertFalse(target.next_action_date)
        # Sans l'option, la cadence continue.
        self.campaign.stop_on_reply = False
        self.assertTrue(target.next_action_date)

    def test_closing_stage_stops_cadence(self):
        target = self._make_target()
        target.stage_id = self.stage_won
        self.assertEqual(target.stage_type, "won")
        self.assertFalse(target.next_action_date)
        target.stage_id = self.stage_lost
        self.assertFalse(target.next_action_date)
        target.stage_id = self.stage_contacted
        self.assertTrue(target.next_action_date)

    def test_paused_campaign_stops_cadence(self):
        target = self._make_target()
        self.campaign.action_pause()
        self.assertFalse(target.next_action_date)
        self.campaign.action_start()
        self.assertTrue(target.next_action_date)

    def test_paused_until_pushes_next_action(self):
        target = self._make_target()
        later = self.today + timedelta(days=30)
        target.paused_until = later
        self.assertEqual(target.next_action_date, later)
        # Une date déjà passée ne repousse rien.
        target.paused_until = self.today - timedelta(days=5)
        self.assertEqual(target.next_action_date, self.today)

    def test_working_days_only_shifts_weekend(self):
        self.campaign.working_days_only = True
        target = self._make_target()
        # On règle l'intervalle pour que la prochaine relance tombe un samedi.
        days_to_saturday = (5 - self.today.weekday()) % 7 or 7
        self.campaign.call_interval_days = days_to_saturday
        self._log(target, "call", self.now)
        saturday = self.today + timedelta(days=days_to_saturday)
        self.assertEqual(saturday.weekday(), 5)
        # Reportée au lundi suivant.
        self.assertEqual(target.next_call_date, saturday + timedelta(days=2))

    def test_stage_group_expand_respects_campaign(self):
        """Les colonnes du kanban : étapes communes + celles de la campagne."""
        other = self.campaign.copy({"name": "Autre campagne"})
        private_stage = self.env["bf.outreach.stage"].create(
            {
                "name": "Étape réservée",
                "sequence": 60,
                "stage_type": "active",
                "campaign_ids": [(6, 0, other.ids)],
            }
        )
        Stage = self.env["bf.outreach.stage"]
        Target = self.env["bf.outreach.target"]
        mine = Target.with_context(
            default_campaign_id=self.campaign.id
        )._read_group_stage_ids(Stage, [])
        self.assertIn(self.stage_todo, mine)
        self.assertNotIn(private_stage, mine)
        theirs = Target.with_context(
            default_campaign_id=other.id
        )._read_group_stage_ids(Stage, [])
        self.assertIn(private_stage, theirs)

    def test_first_touch_advances_stage(self):
        target = self._make_target()
        self.assertEqual(target.stage_type, "todo")
        self._log(target, "call")
        self.assertEqual(target.stage_id, self.stage_contacted)

    def test_touch_is_traced_in_chatter(self):
        target = self._make_target()
        before = len(target.message_ids)
        self._log(target, "call", outcome="reached", direction="out")
        self.assertGreater(len(target.message_ids), before)

    def test_campaign_statistics(self):
        first = self._make_target("Cible 1")
        second = self._make_target("Cible 2")
        self._make_target("Cible 3")
        self._log(first, "call")
        self._log(first, "email", outcome="sent")
        self._log(second, "call", outcome="reached")
        self.campaign.invalidate_recordset()
        self.assertEqual(self.campaign.target_count, 3)
        self.assertEqual(self.campaign.contacted_count, 2)
        self.assertEqual(self.campaign.untouched_count, 1)
        self.assertEqual(self.campaign.call_count, 2)
        self.assertEqual(self.campaign.email_count, 1)
        self.assertEqual(self.campaign.touch_count, 3)
        self.assertEqual(self.campaign.replied_count, 1)  # « reached » vaut réponse
        # 3 cibles x (3 appels + 2 courriels) = 15 contacts prévus
        self.assertEqual(self.campaign.planned_touch_count, 15)
        self.assertAlmostEqual(self.campaign.progress, 100.0 * 3 / 15, places=4)
        self.assertAlmostEqual(self.campaign.coverage_rate, 100.0 * 2 / 3, places=4)

    def test_log_wizard_logs_and_moves_stage(self):
        first = self._make_target("Cible 1")
        second = self._make_target("Cible 2")
        wizard = self.env["bf.outreach.log.wizard"].create(
            {
                "target_ids": [(6, 0, (first | second).ids)],
                "kind": "call",
                "outcome": "no_answer",
                "date": self.now,
                "stage_id": self.stage_contacted.id,
                "snooze_days": 10,
                "summary": "Boîte vocale",
            }
        )
        wizard.action_log()
        for target in (first, second):
            self.assertEqual(target.call_count, 1)
            self.assertEqual(target.stage_id, self.stage_contacted)
            self.assertEqual(
                target.paused_until,
                fields.Date.context_today(target) + timedelta(days=10),
            )
            self.assertEqual(target.next_action_date, target.paused_until)

    def test_import_wizard_skips_duplicates_and_round_robins(self):
        users = self.env["res.users"]
        for index in (1, 2):
            users |= self.env["res.users"].create(
                {
                    "name": "Démarcheur %s" % index,
                    "login": "demarcheur_%s@example.com" % index,
                    "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
                }
            )
        self.campaign.member_ids = users
        partners = self.env["res.partner"].create(
            [
                {"name": "Alpha inc.", "is_company": True, "email": "a@example.com"},
                {"name": "Bêta inc.", "is_company": True, "phone": "555-0001"},
            ]
        )
        wizard = self.env["bf.outreach.target.import.wizard"].create(
            {
                "campaign_id": self.campaign.id,
                "partner_ids": [(6, 0, partners.ids)],
                "source": "Liste de test",
                "assign_mode": "team",
            }
        )
        wizard.action_import()
        targets = self.env["bf.outreach.target"].search(
            [("campaign_id", "=", self.campaign.id)]
        )
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets.mapped("source"), ["Liste de test"] * 2)
        self.assertEqual(set(targets.mapped("user_id").ids), set(users.ids))
        # Deuxième passage : rien n'est recréé.
        wizard.action_import()
        self.assertEqual(
            self.env["bf.outreach.target"].search_count(
                [("campaign_id", "=", self.campaign.id)]
            ),
            2,
        )

    def test_cron_creates_campaign_digest_activity(self):
        self.campaign.activity_mode = "campaign"
        self._make_target()
        self.env["bf.outreach.campaign"]._cron_generate_followup_activities()
        activities = self.env["mail.activity"].search(
            [
                ("res_model", "=", "bf.outreach.campaign"),
                ("res_id", "=", self.campaign.id),
            ]
        )
        self.assertEqual(len(activities), 1)
        # Deuxième exécution le même jour : pas de doublon.
        self.env["bf.outreach.campaign"]._cron_generate_followup_activities()
        self.assertEqual(
            self.env["mail.activity"].search_count(
                [
                    ("res_model", "=", "bf.outreach.campaign"),
                    ("res_id", "=", self.campaign.id),
                ]
            ),
            1,
        )

    def test_cron_creates_per_target_activity(self):
        self.campaign.activity_mode = "target"
        target = self._make_target()
        self.env["bf.outreach.campaign"]._cron_generate_followup_activities()
        activities = self.env["mail.activity"].search(
            [("res_model", "=", "bf.outreach.target"), ("res_id", "=", target.id)]
        )
        self.assertEqual(len(activities), 1)
        self.assertEqual(activities.date_deadline, target.next_action_date)
        self.env["bf.outreach.campaign"]._cron_generate_followup_activities()
        self.assertEqual(
            self.env["mail.activity"].search_count(
                [("res_model", "=", "bf.outreach.target"), ("res_id", "=", target.id)]
            ),
            1,
        )

    # ------------------------------------------------------------------
    # Exclusion « ne plus contacter » (LCAP)
    # ------------------------------------------------------------------
    def test_do_not_contact_freezes_cadence(self):
        target = self._make_target()
        self.assertTrue(target.next_action_date)
        target.write({"do_not_contact": True, "do_not_contact_reason": "Retirez-moi"})
        self.assertTrue(target.is_excluded)
        self.assertFalse(target.next_action_date)
        self.assertFalse(target.next_call_date)
        # Traçabilité : qui, quand, pourquoi.
        self.assertTrue(target.do_not_contact_date)
        self.assertEqual(target.do_not_contact_user_id, self.env.user)
        self.assertEqual(target.do_not_contact_reason, "Retirez-moi")

    def test_exclusion_propagates_to_partner_and_other_campaigns(self):
        partner = self.env["res.partner"].create(
            {"name": "Refus inc.", "is_company": True, "email": "refus@example.com"}
        )
        other = self.campaign.copy({"name": "Autre campagne", "state": "running"})
        first = self._make_target("Refus inc.", partner_id=partner.id)
        second = self.env["bf.outreach.target"].create(
            {"name": "Refus inc.", "campaign_id": other.id, "partner_id": partner.id}
        )
        self.assertTrue(second.next_action_date)
        first.write({"do_not_contact": True})
        self.assertTrue(partner.outreach_opt_out)
        self.assertTrue(partner.outreach_opt_out_date)
        # L'autre campagne est gelée elle aussi, sans avoir été touchée.
        self.assertTrue(second.is_excluded)
        self.assertFalse(second.next_action_date)

    def test_exclude_wizard_logs_the_refusal(self):
        target = self._make_target()
        wizard = self.env["bf.outreach.exclude.wizard"].create(
            {
                "target_ids": [(6, 0, target.ids)],
                "reason": "Ne me rappelez plus",
                "kind": "call",
                "log_touch": True,
            }
        )
        wizard.action_exclude()
        self.assertTrue(target.do_not_contact)
        self.assertFalse(target._is_solicitable())
        self.assertEqual(target.touch_count, 1)
        touch = target.touch_ids
        self.assertEqual(touch.outcome, "not_interested")
        self.assertEqual(touch.direction, "in")

    def test_import_refuses_excluded_partner(self):
        partner = self.env["res.partner"].create(
            {"name": "Déjà exclu inc.", "is_company": True, "outreach_opt_out": True}
        )
        wizard = self.env["bf.outreach.target.import.wizard"].create(
            {"campaign_id": self.campaign.id, "partner_ids": [(6, 0, partner.ids)]}
        )
        wizard.action_import()
        self.assertEqual(
            self.env["bf.outreach.target"].search_count(
                [("campaign_id", "=", self.campaign.id)]
            ),
            0,
        )

    # ------------------------------------------------------------------
    # Normalisation et dédoublonnage
    # ------------------------------------------------------------------
    def test_phone_and_email_normalisation(self):
        target = self._make_target(
            email="  Contact@Example.COM ", phone="(514) 555-0199"
        )
        self.assertEqual(target.email_normalized, "contact@example.com")
        self.assertEqual(target.phone_normalized, "+15145550199")
        # Un numéro illisible ne pollue pas le champ normalisé.
        target.phone = "poste 4"
        self.assertFalse(target.phone_normalized)

    def test_import_dedups_on_email_and_phone(self):
        self._make_target("Déjà là", email="doublon@example.com", phone="514-555-0111")
        same_email = self.env["res.partner"].create(
            {"name": "Autre raison sociale", "email": "Doublon@Example.com"}
        )
        same_phone = self.env["res.partner"].create(
            {"name": "Encore une autre", "phone": "(514) 555-0111"}
        )
        fresh = self.env["res.partner"].create(
            {"name": "Vraiment neuve", "email": "neuve@example.com"}
        )
        wizard = self.env["bf.outreach.target.import.wizard"].create(
            {
                "campaign_id": self.campaign.id,
                "partner_ids": [(6, 0, (same_email | same_phone | fresh).ids)],
            }
        )
        wizard.action_import()
        names = self.env["bf.outreach.target"].search(
            [("campaign_id", "=", self.campaign.id)]
        ).mapped("name")
        self.assertIn("Vraiment neuve", names)
        self.assertNotIn("Autre raison sociale", names)
        self.assertNotIn("Encore une autre", names)

    def test_campaign_pitch_follows_the_campaign(self):
        self.campaign.description = "<p>Accroche : on parle de conformité.</p>"
        target = self._make_target()
        self.assertIn("conformité", target.campaign_pitch)

    def test_create_lead_from_target(self):
        target = self._make_target(email="contact@example.com", phone="555-0002")
        self._log(target, "call", outcome="interested")
        target.action_create_lead()
        self.assertTrue(target.lead_id)
        self.assertEqual(target.lead_id.email_from, "contact@example.com")
        self.assertIn("Campagne de test", target.lead_id.description)
        # Retour vers la campagne, pour le rapport de pipeline.
        self.assertEqual(target.lead_id.outreach_target_id, target)
        self.assertEqual(target.lead_id.outreach_campaign_id, self.campaign)

    def test_privacy_consent_dnc_is_honoured_without_being_copied(self):
        """Le « ne pas contacter » du registre de consentement bloque la sollicitation.

        Ce champ appartient à privacy_consent : il est calculé, non stocké, et
        bf_cx_privacy s'en sert déjà. On le consulte, on ne le duplique pas.
        """
        partner = self.env["res.partner"].create({"name": "Consentement retiré inc."})
        target = self._make_target("Consentement retiré inc.", partner_id=partner.id)
        self.assertTrue(target._is_solicitable())
        if "do_not_contact" not in partner._fields:
            self.skipTest("privacy_consent n'est pas installé sur cette base")
        # On simule le registre sans écrire dedans : le champ est calculé.
        blocked = self.env["res.partner"].browse(partner.id)
        with patch.object(
            type(blocked), "_outreach_is_blocked", lambda records: True
        ):
            self.assertFalse(target._is_solicitable())
        # Le drapeau du module n'a PAS été touché : aucune duplication.
        self.assertFalse(partner.outreach_opt_out)
        self.assertFalse(target.is_excluded)

    # ------------------------------------------------------------------
    # Courriel de campagne
    # ------------------------------------------------------------------
    def test_chatter_email_becomes_a_touch(self):
        partner = self.env["res.partner"].create(
            {"name": "Destinataire inc.", "email": "dest@example.com"}
        )
        target = self._make_target(
            "Destinataire inc.", partner_id=partner.id, email="dest@example.com"
        )
        self.assertEqual(target.email_count, 0)
        target.message_post(
            body="<p>Bonjour, quelques mots sur nos services.</p>",
            subject="Prise de contact",
            partner_ids=partner.ids,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        self.assertEqual(target.email_count, 1)
        touch = target.touch_ids.filtered(lambda t: t.kind == "email")
        self.assertEqual(touch.outcome, "sent")
        self.assertEqual(touch.summary, "Prise de contact")
        self.assertTrue(touch.mail_message_id)
        # La cadence courriel a bien avancé.
        self.assertEqual(
            target.next_email_date, fields.Date.context_today(target) + timedelta(days=14)
        )

    def test_internal_note_is_not_a_touch(self):
        target = self._make_target(email="dest@example.com")
        target.message_post(body="<p>Note interne</p>", message_type="comment")
        self.assertEqual(target.email_count, 0)

    def test_touch_own_chatter_entry_does_not_loop(self):
        target = self._make_target(email="dest@example.com")
        self._log(target, "call")
        # L'interaction poste dans la discussion, ce qui ne doit pas engendrer
        # une seconde interaction.
        self.assertEqual(target.touch_count, 1)

    def test_send_refuses_excluded_target(self):
        target = self._make_target(email="dest@example.com")
        target.do_not_contact = True
        with self.assertRaises(UserError):
            target.action_send_campaign_email()

    def test_send_refuses_target_without_email(self):
        target = self._make_target()
        with self.assertRaises(UserError):
            target.action_send_campaign_email()

    def test_import_action_targets_the_campaign(self):
        action = self.campaign.action_import_targets_file()
        self.assertEqual(action["tag"], "import")
        self.assertEqual(action["params"]["model"], "bf.outreach.target")
        self.assertEqual(
            action["params"]["context"]["default_campaign_id"], self.campaign.id
        )

    def test_registered_in_universal_search(self):
        """Les cibles sont interrogeables depuis la recherche universelle."""
        Config = self.env.get("bf.universal.search.config")
        if Config is None:
            self.skipTest("bf_universal_search n'est pas installé sur cette base")
        model = self.env["ir.model"]._get("bf.outreach.target")
        config = Config.search([("model_id", "=", model.id)], limit=1)
        self.assertTrue(config, "aucune configuration de recherche pour les cibles")
        self.assertIn("name", config.search_fields)
        self.assertIn("phone", config.search_fields)

    def test_universal_search_registration_is_idempotent(self):
        from odoo.addons.bf_outreach.models.post_init import register_universal_search

        Config = self.env.get("bf.universal.search.config")
        if Config is None:
            self.skipTest("bf_universal_search n'est pas installé sur cette base")
        model = self.env["ir.model"]._get("bf.outreach.target")
        before = Config.search_count([("model_id", "=", model.id)])
        register_universal_search(self.env)
        self.assertEqual(
            Config.search_count([("model_id", "=", model.id)]),
            before,
            "un second passage a créé un doublon",
        )

    def test_mass_email_logs_a_touch_per_target(self):
        """L'envoi de masse ne passe pas par la discussion : on le journalise à part."""
        first = self._make_target("Masse 1", email="m1@example.com")
        second = self._make_target("Masse 2", email="m2@example.com")
        composer = self.env["mail.compose.message"].create(
            {
                "model": "bf.outreach.target",
                "composition_mode": "mass_mail",
                "subject": "Offre de service",
                "body": "<p>Bonjour</p>",
            }
        )
        composer._action_send_mail_mass_mail((first | second).ids)
        for target in (first, second):
            self.assertEqual(target.email_count, 1)
            self.assertEqual(target.touch_ids.summary, "Offre de service")
            self.assertEqual(target.touch_ids.outcome, "sent")
