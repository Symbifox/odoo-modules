from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import tagged

from .common import PersonaCase


@tagged("post_install", "-at_install")
class TestInference(PersonaCase):

    def test_register_ignores_words_starting_like_tu(self):
        Persona = self.Persona
        texts = ["Voici le tableau des taux et le test, vous verrez.", "Votre ton est parfait, vous savez."]
        self.assertEqual(Persona._register_from_texts(texts), "vous")

    def test_register_tu_wins_over_vous_for_the_organisation(self):
        texts = ["Tu verras, chez vous tout est prêt.", "Je te laisse regarder, t'as le temps. Vous êtes nombreux."]
        self.assertEqual(self.Persona._register_from_texts(texts), "tu")

    def test_register_undecided_when_tu_appears_once(self):
        # One "tu" among many "vous": not enough to tutoyer, too much to vouvoyer.
        texts = ["Vous verrez, votre dossier et vos pièces.", "Tu peux regarder. Vous aussi, votre équipe."]
        self.assertIsNone(self.Persona._register_from_texts(texts))

    def test_register_undecided_on_one_message(self):
        self.assertIsNone(self.Persona._register_from_texts(["Tu tu tu"]))

    def test_inference_reads_our_mail_to_the_person(self):
        for day in (3, 5, 8):
            self.sent([self.eli], body="<p>Bonjour Ada,</p><p>Tu peux regarder, je te laisse voir.</p><p>Merci!</p>", days=day)
        # Her own mail, quoting our salutation back and vouvoying: ignored.
        self.received(self.eli, body="<p>Bonjour Théo, vous avez raison.</p><blockquote>Bonjour Ada</blockquote>", days=2)
        vals = self.Persona._infer_persona_from_emails(self.eli.id)
        self.assertEqual(vals["addressing_style"], "tu")
        self.assertEqual(vals["preferred_salutation"], "Bonjour Ada")

    def test_inference_keeps_colleagues_apart(self):
        # Bruno writes "tu" a lot; that says nothing about Ada, same company.
        for day in (1, 2, 3):
            self.received(self.jul, body="<p>Tu sais, je te le dis, t'es bon.</p>", days=day)
        vals = self.Persona._infer_persona_from_emails(self.eli.id)
        self.assertNotEqual(vals.get("addressing_style"), "tu")

    def test_compound_first_name_without_hyphen(self):
        marie = self.Partner.create({"name": "Marie Ève Exemple", "email": "mm@example.net"})
        for day in (3, 5, 8):
            self.sent([marie], body="<p>Salut Marie Ève,</p><p>Texte.</p>", days=day)
        self.assertEqual(self.Persona._infer_persona_from_emails(marie.id)["preferred_salutation"], "Salut Marie Ève")

    def test_closing_stops_at_the_line(self):
        self.assertEqual(self.Persona._extract_closing("Texte.\n\nMerci\nBruno Exemple\nDirecteur"), "Merci")

    def test_compound_names_keep_their_case(self):
        for day in (3, 5, 8):
            self.sent([self.eli], body="<p>Bonjour Ada-Maud,</p><p>Texte.</p>", days=day)
        self.eli.name = "Ada-Maud Exemple"
        self.assertEqual(self.Persona._infer_persona_from_emails(self.eli.id)["preferred_salutation"], "Bonjour Ada-Maud")

    def test_no_salutation_from_acronyms_or_company_names(self):
        shop = self.Partner.create({"name": "Iris Exemple", "email": "ie@fromagerie.example", "parent_id": self.client.id})
        self.client.name = "Fromagerie Exemple"
        cpe = self.Partner.create({"name": "ACME", "email": "acme@acme.example"})
        for day in (3, 5, 8):
            self.sent([shop], body="<p>Bonjour Fromagerie,</p><p>Texte.</p>", days=day)
            self.sent([cpe], body="<p>Bonjour ACME,</p><p>Texte.</p>", days=day)
            self.received(cpe, body="<p>Bonjour Théo,</p>", days=day)
        self.assertFalse(self.Persona._infer_persona_from_emails(shop.id).get("preferred_salutation"))
        self.assertFalse(self.Persona._infer_persona_from_emails(cpe.id).get("preferred_salutation"))
        forgeron = self.Partner.create({"name": "ACME Le Petit Atelier", "email": "pa@acme.example"})
        lawyer = self.Partner.create({"name": "Me Stéphane Hardouin", "email": "sh@avocats.example"})
        for day in (3, 5, 8):
            self.received(forgeron, body="<p>Bonjour Théo,</p>", days=day)
            self.sent([lawyer], body="<p>Salut Me,</p><p>Texte.</p>", days=day)
        self.assertFalse(self.Persona._infer_persona_from_emails(forgeron.id).get("preferred_salutation"))
        self.assertFalse(self.Persona._infer_persona_from_emails(lawyer.id).get("preferred_salutation"))

    def test_wrong_salutation_detected(self):
        self.assertTrue(self.Persona._salutation_is_wrong("Bonjour Théo", self.eli))
        self.assertFalse(self.Persona._salutation_is_wrong("Bonjour Ada", self.eli))
        # A diminutive still addresses the contact.
        adeline = self.Partner.create({"name": "Adeline Exemple", "email": "adeline@exemple.example"})
        self.assertFalse(self.Persona._salutation_is_wrong("Salut Adel", adeline))


@tagged("post_install", "-at_install")
class TestSeed(PersonaCase):

    def test_people_of_a_company_are_seeded(self):
        for day in (1, 2, 3):
            self.received(self.eli, days=day)
        self.Persona.cron_seed_personas(limit=500)
        self.assertTrue(self.Persona.search([("partner_id", "=", self.eli.id)]))

    def test_people_we_write_to_are_seeded(self):
        for day in (1, 2, 3):
            self.sent([self.jul], days=day)
        self.Persona.cron_seed_personas(limit=500)
        self.assertTrue(self.Persona.search([("partner_id", "=", self.jul.id)]))

    def test_below_threshold_not_seeded(self):
        self.received(self.eli, days=1)
        self.sent([self.eli], days=2)
        self.Persona.cron_seed_personas(limit=500)
        self.assertFalse(self.Persona.search([("partner_id", "=", self.eli.id)]))

    def test_ineligible_contacts_are_skipped(self):
        role = self.Partner.create({"name": "Comptabilité", "email": "comptabilite.fournisseur@example.net"})
        header = self.Partner.create({"name": '"Alex Exemple" <ax@example.net>', "email": "ax@example.net"})
        for partner in (role, header):
            for day in (1, 2, 3):
                self.received(partner, days=day)
        self.Persona.cron_seed_personas(limit=500)
        self.assertFalse(self.Persona.search([("partner_id", "in", (role | header).ids)]))
        self.assertEqual(self.Persona._ineligibility_reason(header), "nom de fiche mal formé")
        self.assertEqual(self.Persona._ineligibility_reason(role), "boîte de rôle")
        self.assertEqual(self.Persona._ineligibility_reason(self.me), "utilisateur interne")
        for name in ("Facturation", "Comptes payables", "Spam"):
            partner = self.Partner.create({"name": name, "email": "x.y@example.net"})
            self.assertEqual(self.Persona._ineligibility_reason(partner), "boîte de rôle", name)
        self.assertIsNone(self.Persona._ineligibility_reason(self.eli))
        # A director who writes from info@ is a person, not a role mailbox.
        director = self.Partner.create({"name": "Ada Directrice", "email": "info@acme.example"})
        self.assertIsNone(self.Persona._ineligibility_reason(director))
        mailbox = self.Partner.create({"name": "Boutique", "email": "info@boutique.example"})
        self.assertEqual(self.Persona._ineligibility_reason(mailbox), "boîte de rôle")
        # One of us under another record.
        twin = self.Partner.create({"name": self.env.ref("base.user_admin").name, "email": "moi@ailleurs.example"})
        self.assertEqual(self.Persona._ineligibility_reason(twin), "même nom qu'un utilisateur interne")

    def test_archived_persona_is_not_recreated(self):
        self.Persona.create({"partner_id": self.eli.id, "active": False})
        for day in (1, 2, 3):
            self.received(self.eli, days=day)
        self.Persona.cron_seed_personas(limit=500)
        self.assertEqual(self.Persona.with_context(active_test=False).search_count([("partner_id", "=", self.eli.id)]), 1)
        self.assertFalse(self.Persona.search([("partner_id", "=", self.eli.id)]))

    def test_limit_takes_the_most_active_first(self):
        for day in range(1, 7):
            self.received(self.jul, days=day)
        for day in (1, 2, 3):
            self.received(self.eli, days=day)
        existing = set(self.Persona.with_context(active_test=False).search([]).partner_id.ids)
        eligible_before = [p for p in (self.jul, self.eli) if p.id not in existing]
        self.assertEqual(len(eligible_before), 2)
        # Whatever else the database holds, Bruno outranks Ada.
        self.Persona.cron_seed_personas(limit=10000)
        self.assertTrue(self.Persona.search([("partner_id", "=", self.jul.id)]))


@tagged("post_install", "-at_install")
class TestFacts(PersonaCase):

    def test_unanswered_messages_make_it_to_watch(self):
        persona = self.Persona.create({"partner_id": self.eli.id})
        self.received(self.eli, days=40)
        for day in (30, 20, 10):
            self.sent([self.eli], days=day)
        persona._refresh_relationship_facts()
        self.assertEqual(persona.unanswered_count, 3)
        self.assertEqual(persona.relationship_health, "watch")
        self.assertIn("3 courriels sans réponse", persona.health_reason)
        self.assertIn("3 courriels sans réponse", persona.claude_context_summary)

    def test_answered_relationship_is_healthy(self):
        persona = self.Persona.create({"partner_id": self.eli.id})
        self.sent([self.eli], days=5)
        self.received(self.eli, days=4)
        persona._refresh_relationship_facts()
        self.assertEqual(persona.unanswered_count, 0)
        self.assertEqual(persona.relationship_health, "healthy")
        self.assertFalse(persona.health_reason)

    def test_a_quiet_end_is_not_a_degradation(self):
        # Busy onboarding months ago, then silence: nothing owed, nothing wrong.
        persona = self.Persona.create({"partner_id": self.eli.id, "relationship_health": "degraded"})
        for day in range(100, 130, 3):
            self.sent([self.eli], days=day)
            self.received(self.eli, days=day - 1)
        persona._refresh_relationship_facts()
        self.assertEqual(persona.relationship_health, "na")

    def test_recent_unanswered_is_not_yet_a_concern(self):
        persona = self.Persona.create({"partner_id": self.eli.id})
        self.received(self.eli, days=6)
        for day in (4, 2):
            self.sent([self.eli], days=day)
        persona._refresh_relationship_facts()
        self.assertEqual(persona.unanswered_count, 2)
        self.assertEqual(persona.relationship_health, "healthy")

    def test_words_median_ignores_quoted_history(self):
        persona = self.Persona.create({"partner_id": self.eli.id})
        quote = "<blockquote>" + "mot " * 500 + "</blockquote>"
        for day in (3, 2, 1):
            self.sent([self.eli], body="<p>" + "mot " * 20 + "</p>" + quote, days=day)
        persona._refresh_relationship_facts()
        self.assertEqual(persona.our_words_median, 20)

    def test_gen_is_given_the_measured_length_as_a_ceiling(self):
        """Gen is told how long our mail to this person usually runs.

        Below the long-message threshold the median is a ceiling, not a target:
        the point is to write like we already write to them, or shorter.
        """
        persona = self.Persona.create({"partner_id": self.eli.id})
        for day in (3, 2, 1):
            self.sent([self.eli], body="<p>" + "mot " * 30 + "</p>", days=day)
        persona._refresh_relationship_facts()
        self.assertEqual(persona.our_words_median, 30)
        summary = persona.claude_context_summary
        self.assertIn("30 mots", summary)
        self.assertIn("ne pas dépasser", summary)

    def test_gen_is_told_to_write_shorter_when_we_over_write(self):
        persona = self.Persona.create({"partner_id": self.eli.id})
        for day in (3, 2, 1):
            self.sent([self.eli], body="<p>" + "mot " * 200 + "</p>", days=day)
        persona._refresh_relationship_facts()
        self.assertEqual(persona.our_words_median, 200)
        self.assertIn("plus court", persona.claude_context_summary)

    def test_no_length_line_without_a_measure(self):
        persona = self.Persona.create({"partner_id": self.hm.id})
        persona._refresh_relationship_facts()
        self.assertEqual(persona.our_words_median, 0)
        self.assertNotIn("mots de médiane", persona.claude_context_summary)

    def test_words_ignore_signature_and_links(self):
        persona = self.Persona.create({"partner_id": self.eli.id})
        signature = (
            "<div><b>%s</b> (il/lui)<br/>Consultant principal<br/>"
            '<a href="https://example.com/r/agenda">Prendre rendez-vous</a> '
            '<a href="https://example.com/r/fichier">M\'envoyer un fichier</a></div>'
        ) % self.me.name
        body = '<p>Bonjour Ada, voici <a href="https://l.example.com/abc">le lien</a> promis.</p><p>Au plaisir!</p>' + signature
        for day in (3, 2, 1):
            self.sent([self.eli], body=body, days=day)
        persona._refresh_relationship_facts()
        # "Bonjour Ada voici le lien promis Au plaisir" = 8 words.
        self.assertEqual(persona.our_words_median, 8)

    def test_a_colleague_answering_for_the_group_counts(self):
        persona = self.Persona.create({"partner_id": self.eli.id})
        for day in (30, 20, 10):
            self.sent([self.eli, self.jul], days=day)
        self.received(self.jul, days=5)
        persona._refresh_relationship_facts()
        self.assertEqual(persona.unanswered_count, 0)

    def test_internal_notes_do_not_count_as_sent(self):
        persona = self.Persona.create({"partner_id": self.eli.id})
        note = self.env.ref("mail.mt_note")
        self.Message.create({
            "model": "res.partner", "res_id": self.eli.id, "message_type": "comment",
            "subtype_id": note.id, "author_id": self.me.id, "body": "<p>note</p>",
            "partner_ids": [(6, 0, self.eli.ids)], "date": self.days_ago(20),
        })
        persona._refresh_relationship_facts()
        self.assertEqual(persona.outbound_count_90d, 0)


@tagged("post_install", "-at_install")
class TestSuggestedRules(PersonaCase):

    def test_usual_copy_becomes_a_suggestion_once(self):
        persona = self.Persona.create({"partner_id": self.eli.id})
        for day in (1, 2, 3, 4):
            self.sent([self.eli], cc=[self.jul], days=day)
        self.sent([self.eli], days=5)
        self.Persona.cron_suggest_cc_rules()
        rule = persona.cc_rule_ids
        self.assertEqual(len(rule), 1)
        self.assertEqual(rule.state, "suggested")
        self.assertEqual(rule.cc_partner_ids, self.jul)
        self.assertIn("4 de nos 5 courriels", rule.evidence)
        self.assertIn("dont 4 en copie", rule.evidence)
        rule.action_reject()
        self.Persona.cron_suggest_cc_rules()
        self.assertEqual(self.env["contact.cc.rule"].search_count([("persona_id", "=", persona.id)]), 1)

    def test_occasional_copy_is_not_suggested(self):
        persona = self.Persona.create({"partner_id": self.eli.id})
        for day in range(1, 9):
            self.sent([self.eli], cc=[self.jul] if day <= 3 else None, days=day)
        self.Persona.cron_suggest_cc_rules()
        self.assertFalse(persona.cc_rule_ids)


@tagged("post_install", "-at_install")
class TestRepair(PersonaCase):

    def _as_seeded(self, persona):
        # Seeded personas were created by the superuser.
        self.env.cr.execute("UPDATE contact_persona SET create_uid = 1 WHERE id = %s", (persona.id,))
        persona.invalidate_recordset(["create_uid"])

    def test_plan_then_apply(self):
        wrong = self.Persona.create({
            "partner_id": self.eli.id, "preferred_salutation": "Bonjour Théo",
            "addressing_style": "vous", "tone_summary": "tense",
        })
        robot = self.Partner.create({"name": "Nextcloud Robot", "email": "nextcloud@example.net"})
        robot_persona = self.Persona.create({"partner_id": robot.id})
        dup_partner = self.Partner.create({"name": "Exemple", "email": "ada@exemple.example"})
        dup = self.Persona.create({"partner_id": dup_partner.id})
        for persona in (wrong, robot_persona, dup):
            self._as_seeded(persona)
        for day in (3, 5, 8):
            self.sent([self.eli], body="<p>Bonjour Ada,</p><p>Tu peux regarder, je te laisse voir.</p>", days=day)
        self.received(self.eli, days=1)
        self.env["contact.persona.kpi"].create({
            "persona_id": wrong.id, "name": "Dernière interaction", "source": "email_management",
        })

        plan = self.Persona._repair_legacy_personas(apply=False)
        rows = {(r[0], r[2], r[3]): r for r in plan}
        self.assertIn((robot_persona.id, "archive", "active"), rows)
        self.assertIn((dup.id, "archive", "active"), rows)
        self.assertEqual(rows[(wrong.id, "write", "preferred_salutation")][5], "Bonjour Ada")
        self.assertEqual(rows[(wrong.id, "write", "addressing_style")][5], "tu")
        self.assertEqual(rows[(wrong.id, "write", "tone_summary")][5], "na")
        self.assertTrue(wrong.active and robot_persona.active, "a plan must not write")

        self.Persona._repair_legacy_personas(apply=True)
        self.assertEqual(wrong.preferred_salutation, "Bonjour Ada")
        self.assertEqual(wrong.addressing_style, "tu")
        self.assertEqual(wrong.tone_summary, "na")
        self.assertFalse(robot_persona.active)
        self.assertFalse(dup.active)
        self.assertFalse(self.env["contact.persona.kpi"].search([("name", "=", "Dernière interaction")]))

    def test_hand_written_values_survive(self):
        persona = self.Persona.create({"partner_id": self.eli.id})
        self._as_seeded(persona)
        # A person set the register by hand in an earlier transaction: Odoo
        # leaves a tracking value authored by that person (created here
        # directly, since tracking does not fire within the creating transaction).
        persona.addressing_style = "vous"
        field = self.env["ir.model.fields"]._get("contact.persona", "addressing_style")
        self.Message.create({
            "model": "contact.persona", "res_id": persona.id, "message_type": "notification",
            "author_id": self.env.ref("base.user_admin").partner_id.id,
            "tracking_value_ids": [(0, 0, {
                "field_id": field.id, "old_value_char": "Auto", "new_value_char": "Vouvoiement",
            })],
        })
        self.assertIn("addressing_style", self.Persona._human_touched_fields()[persona.id])
        for day in (3, 5, 8):
            self.sent([self.eli], body="<p>Tu peux regarder, je te laisse voir.</p>", days=day)
        plan = self.Persona._repair_legacy_personas(apply=False)
        self.assertNotIn((persona.id, "write", "addressing_style"), {(r[0], r[2], r[3]) for r in plan})

    def test_plausible_values_are_not_churned(self):
        persona = self.Persona.create({
            "partner_id": self.eli.id, "preferred_salutation": "Salut Ada",
            "closing_formula": "À bientôt", "addressing_style": "tu",
        })
        self._as_seeded(persona)
        for day in (3, 5, 8):
            self.sent([self.eli], body="<p>Bonjour Ada,</p><p>Tu peux regarder, je te laisse voir.</p><p>Au plaisir</p>", days=day)
        plan = self.Persona._repair_legacy_personas(apply=False)
        self.assertFalse([r for r in plan if r[0] == persona.id])

    def test_company_and_phone_only_personas_are_kept(self):
        company = self.Persona.create({"partner_id": self.client.id, "custom_appellations": "Pièces jointes"})
        phone_only = self.Partner.create({"name": "Bruno Téléphone"})
        by_phone = self.Persona.create({"partner_id": phone_only.id})
        director = self.Partner.create({"name": "Ada Directrice", "email": "info@acme.example"})
        director_persona = self.Persona.create({"partner_id": director.id})
        for persona in (company, by_phone, director_persona):
            self._as_seeded(persona)
        archived = {r[0] for r in self.Persona._repair_legacy_personas(apply=False) if r[2] == "archive"}
        self.assertFalse(archived & {company.id, by_phone.id, director_persona.id})

    def test_personas_created_by_a_person_are_left_alone(self):
        robot = self.Partner.create({"name": "Info", "email": "info@example.net"})
        persona = self.Persona.with_user(self.env.ref("base.user_admin")).create({"partner_id": robot.id})
        plan = self.Persona._repair_legacy_personas(apply=False)
        self.assertNotIn(persona.id, {r[0] for r in plan})


@tagged("post_install", "-at_install")
class TestPrivateEntryPoints(PersonaCase):

    def test_crons_and_repair_cannot_be_called_by_rpc(self):
        for name in ("cron_seed_personas", "cron_refresh_relationship", "cron_suggest_cc_rules",
                     "cron_detect_relationship_degradation", "cron_recompute_payment_delay",
                     "cron_flag_stale_tones", "_repair_legacy_personas"):
            with self.assertRaises(AccessError, msg=name):
                get_public_method(self.Persona, name)
