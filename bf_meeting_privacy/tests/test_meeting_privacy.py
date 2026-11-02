from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_meeting_privacy")
class TestMeetingPrivacyBridge(TransactionCase):
    """Le pont doit déclarer, classifier, conserver — et ne rien certifier
    qu'il n'ait vraiment fait."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Register = cls.env["privacy.destruction.register"]
        cls.Classification = cls.env["privacy.document.classification"]
        cls.raw_rule = cls.env.ref("bf_meeting_privacy.retention_meeting_raw_material")
        cls.record_rule = cls.env.ref("bf_meeting_privacy.retention_meeting_record")

        cls.externe = cls.env["res.partner"].create({
            "name": "Cliente Externe (test pont vie privée)",
            "email": "cliente@nunyabiznu.invalid",
        })
        cls.interne_partner = cls.env["res.partner"].create({
            "name": "Employé Interne (test pont vie privée)",
            "email": "employe@nunyabiznu.invalid",
        })
        cls.interne_user = cls.env["res.users"].create({
            "name": "Employé Interne (test pont vie privée)",
            "login": "interne-essai@nunyabiznu.invalid",
            "partner_id": cls.interne_partner.id,
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

        cls.meeting = cls.env["meeting.record"].create({
            "name": "Rencontre d'essai — pont vie privée",
            "date": "2026-01-15 14:00:00",
            "source_type": "audio",
            "source_filename": "rencontre.m4a",
            "source_nc_path": "/Meetings/2026/rencontre.m4a",
            "verbatim": "Intervenante : bonjour. Cliente : bonjour.",
            "verbatim_html": "<p>Intervenante : bonjour.</p>",
            "report_state": "sent",
        })
        cls.env["meeting.attendance"].create({
            "meeting_id": cls.meeting.id,
            "partner_id": cls.externe.id,
        })
        cls.env["meeting.attendance"].create({
            "meeting_id": cls.meeting.id,
            "partner_id": cls.interne_partner.id,
        })

    def _entries(self, meeting=None):
        return self.Register.search([
            ("res_model", "=", "meeting.record"),
            ("res_id", "=", (meeting or self.meeting).id),
        ])

    # ------------------------------------------------------------------
    # 1. Modèles classifiables
    # ------------------------------------------------------------------

    def test_bridge_adds_meeting_models_without_dropping_the_base(self):
        """La surcharge COMPOSE : elle ajoute sans retirer."""
        allowed = self.Classification._privacy_classifiable_models()
        self.assertIn("meeting.record", allowed)
        self.assertIn("meeting.agenda", allowed)
        # Le socle est toujours là — une redéfinition de la constante l'aurait
        # emporté.
        self.assertIn("res.partner", allowed)
        self.assertIn("privacy.consent", allowed)

    def test_cascading_children_stay_out(self):
        """Présences, décisions et sujets ne sont PAS classifiables.

        Ils meurent avec leur parent ; les ouvrir fabriquerait des
        classifications orphelines que la campagne balaierait ensuite comme
        des documents à détruire.
        """
        allowed = self.Classification._privacy_classifiable_models()
        for model in ("meeting.attendance", "meeting.decision", "meeting.topic"):
            self.assertNotIn(model, allowed)

    def test_meeting_record_can_be_classified(self):
        classification = self.Classification.create({
            "res_model": "meeting.record",
            "res_id": self.meeting.id,
            "pi_category": "identification",
            "retention_calendar_id": self.record_rule.id,
        })
        self.assertEqual(classification.res_name, self.meeting.display_name)

    def test_system_model_still_refused(self):
        with self.assertRaises(ValidationError):
            self.Classification.create({
                "res_model": "ir.module.module",
                "res_id": 1,
                "pi_category": "other",
            })

    # ------------------------------------------------------------------
    # 2. Règles de conservation
    # ------------------------------------------------------------------

    def test_raw_material_is_shorter_than_the_minutes(self):
        """La matière première ne doit pas survivre au document qu'elle sert."""
        self.assertLess(
            self.raw_rule.total_retention_days,
            self.record_rule.total_retention_days,
        )

    def test_purpose_declared_without_a_consent_it_cannot_refuse(self):
        purpose = self.env.ref("bf_meeting_privacy.purpose_meeting_minutes")
        self.assertFalse(purpose.requires_consent)
        self.assertEqual(purpose.context_scope, "meeting")

    def test_cron_is_shipped_disabled(self):
        """Une purge irréversible ne s'allume pas toute seule à l'installation."""
        cron = self.env.ref("bf_meeting_privacy.cron_privacy_purge_raw_material")
        self.assertFalse(cron.active)

    # ------------------------------------------------------------------
    # 3. Purge de la matière première
    # ------------------------------------------------------------------

    def test_purge_clears_the_transcript_and_writes_one_entry(self):
        self.assertFalse(self._entries())
        purged = self.meeting._privacy_purge_raw_material(reason="essai")
        self.assertTrue(purged)
        self.assertFalse(self.meeting.verbatim)
        self.assertFalse(self.meeting.verbatim_html)

        entries = self._entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries.destruction_method, "delete")
        # Le compte rendu, lui, est toujours là.
        self.assertTrue(self.meeting.exists())
        self.assertEqual(self.meeting.name, "Rencontre d'essai — pont vie privée")

    def test_purge_declares_a_partial_scope(self):
        """🔴 RENC-002 détruit une PARTIE de l'enregistrement, et doit le dire.

        L'entrée porte `res_model = meeting.record` et un `res_id` qui existe
        toujours, avec la méthode « Suppression ». Depuis `privacy_consent`
        18.0.5.0.0, la garde du registre refuse exactement ce
        triplet — sauf si la portée est déclarée partielle.

        ⚠ Sans cette déclaration, la purge aurait lieu et l'entrée serait
        perdue EN SILENCE : l'appel au registre est enveloppé, dans
        `_privacy_purge_raw_material`, d'un try/except qui journalise sans
        relever, pour que consigner ne puisse jamais annuler une purge déjà
        faite. Le verbatim serait détruit sans trace. Ce test est ce qui
        empêche la régression la plus coûteuse du pont.
        """
        self.meeting._privacy_purge_raw_material(reason="essai portée")
        entry = self._entries()
        self.assertEqual(len(entry), 1, "La purge n'a produit aucune entrée.")
        self.assertEqual(entry.destruction_scope, "partial")
        self.assertIn("verbatim", entry.res_field or "")
        # Ce que l'entrée affirme doit rester vrai : le compte rendu subsiste.
        self.assertTrue(self.meeting.exists())

    def test_entry_names_the_material_still_held_outside_odoo(self):
        """L'entrée doit dire ce qu'elle NE couvre pas."""
        self.meeting._privacy_purge_raw_material()
        description = self._entries().document_description
        self.assertIn("/Meetings/2026/rencontre.m4a", description)
        self.assertIn("HORS d'Odoo", description)

    def test_entry_does_not_carry_a_copy_of_the_transcript(self):
        """Recopier le verbatim au registre le rendrait indestructible.

        Le registre refuse `write` et `unlink` : une copie y serait figée pour
        toujours, ce qui est l'inverse du but.
        """
        verbatim = self.meeting.verbatim
        self.meeting._privacy_purge_raw_material()
        entry = self._entries()
        self.assertNotIn(verbatim, entry.document_description or "")
        self.assertNotIn("bonjour", entry.document_description or "")

    def test_second_purge_certifies_nothing(self):
        """Rien à détruire, rien à certifier."""
        self.meeting._privacy_purge_raw_material()
        self.assertEqual(len(self._entries()), 1)
        again = self.meeting._privacy_purge_raw_material()
        self.assertFalse(again)
        self.assertEqual(len(self._entries()), 1)

    def test_button_refuses_when_there_is_nothing_to_purge(self):
        self.meeting._privacy_purge_raw_material()
        with self.assertRaises(UserError):
            self.meeting.action_privacy_purge_raw_material()

    # ------------------------------------------------------------------
    # 4. Le cron
    # ------------------------------------------------------------------

    def test_cron_reads_the_rule_instead_of_a_hardcoded_delay(self):
        """Changer la règle change ce que le cron emporte."""
        Meeting = self.env["meeting.record"]
        # La règle vaut 1 an ; la rencontre du 2026-01-15 n'y est pas encore.
        self.meeting.write({"date": "2026-06-15 14:00:00"})
        Meeting._cron_privacy_purge_raw_material()
        self.assertTrue(self.meeting.verbatim)

        # On raccourcit la règle : la même rencontre devient échue.
        self.raw_rule.write({"active_retention_years": 0,
                             "semi_active_retention_years": 0})
        self.assertEqual(self.raw_rule.total_retention_days, 0)
        # ⚠️ Une règle à 0 jour n'autorise pas une purge du jour même.
        Meeting._cron_privacy_purge_raw_material()
        self.assertTrue(self.meeting.verbatim)

    def test_cron_spares_a_report_not_yet_sent(self):
        """Effacer la matière d'un brouillon empêcherait de le finir."""
        self.meeting.write({
            "date": "2020-01-15 14:00:00",
            "report_state": "draft",
        })
        self.env["meeting.record"]._cron_privacy_purge_raw_material()
        self.assertTrue(self.meeting.verbatim)

        self.meeting.write({"report_state": "sent"})
        self.env["meeting.record"]._cron_privacy_purge_raw_material()
        self.assertFalse(self.meeting.verbatim)

    # ------------------------------------------------------------------
    # 5. Consentement à l'écran
    # ------------------------------------------------------------------

    def test_external_attendee_without_consent_is_named(self):
        self.assertTrue(self.meeting.privacy_is_recorded)
        self.assertEqual(self.meeting.privacy_consent_state, "missing")
        self.assertIn(self.externe, self.meeting.privacy_missing_consent_partner_ids)

    def test_internal_attendee_is_not_asked_for_consent(self):
        """L'équipe relève du lien d'emploi, pas du consentement."""
        self.assertNotIn(self.interne_partner, self.meeting.privacy_external_partner_ids)
        self.assertNotIn(
            self.interne_partner, self.meeting.privacy_missing_consent_partner_ids
        )

    def test_granted_recording_consent_covers_the_meeting(self):
        self.env["privacy.consent"].create({
            "subject_partner_id": self.externe.id,
            "purpose_id": self.env.ref("privacy_consent.purpose_recording").id,
            "status": "granted",
        })
        self.meeting.invalidate_recordset()
        self.assertEqual(self.meeting.privacy_consent_state, "ok")

    def test_a_typed_meeting_is_out_of_the_recording_regime(self):
        typed = self.env["meeting.record"].create({
            "name": "Notes saisies à la main — pont vie privée",
            "date": "2026-01-15 14:00:00",
            "source_type": "text",
        })
        self.env["meeting.attendance"].create({
            "meeting_id": typed.id,
            "partner_id": self.externe.id,
        })
        self.assertFalse(typed.privacy_is_recorded)
        self.assertEqual(typed.privacy_consent_state, "na")

    # ------------------------------------------------------------------
    # 6. La campagne ne certifie que ce qui a eu lieu
    # ------------------------------------------------------------------

    def _line(self, rule, method="delete", meeting=None):
        meeting = meeting or self.meeting
        campaign = self.env["privacy.destruction.campaign"].create({
            "name": "Campagne d'essai — pont vie privée",
            "retention_calendar_id": rule.id,
            "cutoff_date": "2026-08-24",
        })
        return self.env["privacy.destruction.campaign.line"].create({
            "campaign_id": campaign.id,
            "res_model": "meeting.record",
            "res_id": meeting.id,
            "res_name": meeting.display_name,
            "retention_calendar_id": rule.id,
            "destruction_method": method,
            "state": "pending",
        })

    def test_campaign_refuses_the_raw_material_rule(self):
        """RENC-002 par campagne inscrirait « compte rendu détruit ». Faux."""
        line = self._line(self.raw_rule)
        with self.assertRaises(UserError):
            line._execute_destruction()
        # Rien n'a bougé : ni le verbatim, ni le compte rendu.
        self.assertTrue(self.meeting.verbatim)
        self.assertTrue(self.meeting.exists())

    def test_campaign_delete_really_deletes(self):
        """🔴 Le socle aurait ARCHIVÉ : `meeting.record` porte un champ `active`."""
        line = self._line(self.record_rule, method="delete")
        meeting_id = self.meeting.id
        line._execute_destruction()
        self.assertFalse(
            self.env["meeting.record"]
            .with_context(active_test=False)
            .browse(meeting_id)
            .exists()
        )

    def test_campaign_refuses_anonymisation(self):
        """Un verbatim nomme ses auteurs d'un bout à l'autre."""
        line = self._line(self.record_rule, method="anonymize")
        with self.assertRaises(UserError):
            line._execute_destruction()
        self.assertTrue(self.meeting.exists())

    def test_campaign_refuses_a_record_already_gone(self):
        """Sinon le registre certifie une destruction que personne n'a faite."""
        line = self._line(self.record_rule, method="delete")
        self.meeting.unlink()
        with self.assertRaises(UserError):
            line._execute_destruction()

    def test_other_models_keep_the_base_behaviour(self):
        """Le pont ne détourne QUE ses propres modèles."""
        partner = self.env["res.partner"].create({
            "name": "Contact hors pont (test)",
        })
        campaign = self.env["privacy.destruction.campaign"].create({
            "name": "Campagne d'essai — hors pont",
            "cutoff_date": "2026-08-24",
        })
        line = self.env["privacy.destruction.campaign.line"].create({
            "campaign_id": campaign.id,
            "res_model": "res.partner",
            "res_id": partner.id,
            "res_name": partner.display_name,
            "destruction_method": "delete",
            "state": "pending",
        })
        partner_id = partner.id
        line._execute_destruction()
        # On constate le comportement du socle, on ne le change pas. Depuis
        # `privacy_consent` 18.0.5.0.0, « Suppression » supprime
        # vraiment : ce test disait auparavant `assertFalse(partner.active)`,
        # parce que le socle se contentait d'archiver tout modèle porteur d'un
        # champ `active` — le défaut même que le pont corrigeait pour ses
        # propres modèles.
        self.assertFalse(
            self.env["res.partner"].with_context(
                active_test=False
            ).browse(partner_id).exists(),
            "Le socle a archivé au lieu de supprimer : le pont ne détourne que "
            "ses modèles, il ne doit pas masquer une régression du socle.",
        )

    def test_line_without_a_rule_is_not_mistaken_for_renc_002(self):
        """`env.ref` rend False, pas un recordset vide : la comparaison doit
        se faire par id, sinon une ligne SANS règle passerait pour RENC-002."""
        campaign = self.env["privacy.destruction.campaign"].create({
            "name": "Campagne d'essai — sans règle",
            "cutoff_date": "2026-08-24",
        })
        line = self.env["privacy.destruction.campaign.line"].create({
            "campaign_id": campaign.id,
            "res_model": "meeting.record",
            "res_id": self.meeting.id,
            "res_name": self.meeting.display_name,
            "destruction_method": "delete",
            "state": "pending",
        })
        meeting_id = self.meeting.id
        line._execute_destruction()
        self.assertFalse(
            self.env["meeting.record"]
            .with_context(active_test=False)
            .browse(meeting_id)
            .exists()
        )
