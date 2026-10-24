"""Ce que ces essais doivent PROUVER, et pas seulement observer.

Quatre d'entre eux valent d'être signalés :

* `test_colleague_message_does_not_extend_a_person` échoue si l'ancrage d'un
  persona de personne compte toute la société (la décision est le silence de
  la PERSONNE : un employé parti chez un client actif doit pouvoir partir).
* `test_internal_note_does_not_extend` échoue si une note interne compte comme
  un échange.
* `test_execution_takes_the_payer_history` échoue si la destruction laisse le
  fil du persona, où vit l'historique suivi de la qualité de payeur.
* `test_partner_deletion_takes_the_thread` échoue si le persona part par la
  cascade SQL de `partner_id` au lieu de l'ORM.

Chaque essai ne compte que ses propres enregistrements : la base peut porter
déjà des personas (un clone de production).

⚠️ Rien ici n'est validé : TransactionCase annule tout, et c'est obligatoire,
le registre de destruction étant immuable.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPersonaPrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
            tracking_disable=True,
            mail_notrack=True,
        ))
        cls.Partner = cls.env["res.partner"]
        cls.Persona = cls.env["contact.persona"]
        cls.Message = cls.env["mail.message"]
        cls.Line = cls.env["privacy.destruction.campaign.line"]
        cls.Register = cls.env["privacy.destruction.register"]
        cls.rule = cls.env.ref("bf_persona_privacy.retention_persona")
        cls.me = cls.env.ref("base.user_admin").partner_id
        cls.comment = cls.env.ref("mail.mt_comment")
        cls.note = cls.env.ref("mail.mt_note")
        cls.client = cls.Partner.create({"name": "Exemple Inc.", "is_company": True})
        cls.officer = new_test_user(cls.env, login="vp_responsable_per",
                                    groups="base.group_user,privacy_consent.group_privacy_officer")

    # ------------------------------------------------------------------
    # Fabriques
    # ------------------------------------------------------------------

    def _person(self, name="Ada Exemple", company=True):
        return self.Partner.create({
            "name": name, "email": f"{name.split()[0].lower()}@exemple.example",
            "parent_id": self.client.id if company else False,
        })

    def _persona(self, partner=None, age_days=4 * 365, **vals):
        persona = self.Persona.create(dict({"partner_id": (partner or self._person()).id}, **vals))
        self._age(persona, age_days)
        return persona

    def _age(self, persona, days):
        self.env.cr.execute("UPDATE contact_persona SET create_date = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(days=days), persona.id))
        persona.invalidate_recordset(["create_date"])

    def _ago(self, days):
        return fields.Datetime.now() - timedelta(days=days)

    def _received(self, author, days):
        return self.Message.create({
            "model": "res.partner", "res_id": author.id, "message_type": "email",
            "author_id": author.id, "body": "<p>x</p>", "date": self._ago(days)})

    def _sent(self, to, days, subtype=None):
        return self.Message.create({
            "model": "res.partner", "res_id": to.id, "message_type": "comment",
            "subtype_id": (subtype or self.comment).id, "author_id": self.me.id,
            "body": "<p>x</p>", "partner_ids": [(6, 0, to.ids)], "date": self._ago(days)})

    def _invoice(self, partner, days, paid=True):
        move = self.env["account.move"].create({"move_type": "out_invoice", "partner_id": partner.id})
        # Validée, antidatée et réglée en SQL : le pont ne lit que l'état, la date,
        # le titulaire et l'état du paiement. Une vraie validation dépend du plan
        # comptable du locataire (sur certaines bases, le compte de revenus par défaut est
        # déprécié) et une date verrouillée empêcherait d'en valider une vieille.
        # ⚠️ Vider les calculs en attente D'ABORD : sinon le recalcul de
        # `payment_state` réécrit « non payée » par-dessus le SQL, et une facture
        # réglée retient le persona comme une impayée (l'essai passerait à tort).
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE account_move SET state = 'posted', invoice_date = %s, payment_state = %s "
            "WHERE id = %s",
            (fields.Date.context_today(self.env.user) - timedelta(days=days),
             "paid" if paid else "not_paid", move.id))
        move.invalidate_recordset(["state", "invoice_date", "payment_state"])
        self.assertEqual(move.payment_state, "paid" if paid else "not_paid")
        return move

    def _scan(self, **kw):
        buckets, held = self.Persona._privacy_scan_expired(**kw)
        return set(buckets.get(self.rule.id, self.Persona).ids), held

    def _lines(self, persona):
        return self.Line.search([("res_model", "=", persona._name), ("res_id", "=", persona.id)])

    def _execute(self, personas, user=None):
        self.Persona._privacy_propose_lines(self.rule, personas)
        campaign = self.Persona._privacy_open_campaign(self.rule)
        if user:
            campaign = campaign.with_user(user)
        campaign.action_approve()
        campaign.action_execute()
        return campaign

    # ------------------------------------------------------------------
    # Le registre des traitements et le calendrier
    # ------------------------------------------------------------------

    def test_purpose_and_rule_are_declared(self):
        purpose = self.env.ref("bf_persona_privacy.purpose_persona_relationship")
        self.assertEqual(purpose.code, "persona_relationship")
        self.assertFalse(purpose.requires_consent)
        self.assertEqual(self.rule.code, "PER-001")
        self.assertEqual(self.rule.total_retention_days, 3 * 365)
        self.assertTrue(self.rule.requires_approval)
        self.assertEqual(self.rule.applies_to_model, "contact.persona")

    def test_three_models_are_classifiable(self):
        models_ = self.env["privacy.document.classification"]._privacy_classifiable_models()
        for model in ("contact.persona", "contact.persona.kpi", "contact.cc.rule"):
            self.assertIn(model, models_)

    def test_persona_can_be_classified(self):
        persona = self._persona()
        cls = self.env["privacy.document.classification"].create({
            "res_model": "contact.persona", "res_id": persona.id,
            "pi_category": "financial", "subject_partner_id": persona.partner_id.id,
            "retention_calendar_id": self.rule.id})
        self.assertTrue(cls.exists())

    # ------------------------------------------------------------------
    # L'ancrage : le silence de la personne
    # ------------------------------------------------------------------

    def test_silent_old_persona_is_proposed(self):
        persona = self._persona()
        self.assertIn(persona.id, self._scan()[0])

    def test_young_persona_is_not_proposed(self):
        persona = self._persona(age_days=2 * 365)
        self.assertNotIn(persona.id, self._scan()[0])

    def test_message_from_the_person_extends(self):
        persona = self._persona()
        self._received(persona.partner_id, 30)
        self.assertNotIn(persona.id, self._scan()[0])

    def test_message_to_the_person_extends(self):
        persona = self._persona()
        self._sent(persona.partner_id, 30)
        self.assertNotIn(persona.id, self._scan()[0])

    def test_old_messages_do_not_extend(self):
        persona = self._persona()
        self._received(persona.partner_id, 4 * 365)
        self._sent(persona.partner_id, 4 * 365)
        self.assertIn(persona.id, self._scan()[0])
        self.assertEqual(self._lines(persona), self.Line)

    def test_internal_note_does_not_extend(self):
        persona = self._persona()
        self._sent(persona.partner_id, 30, subtype=self.note)
        self.assertIn(persona.id, self._scan()[0])

    def test_colleague_message_does_not_extend_a_person(self):
        persona = self._persona()
        colleague = self._person("Bruno Exemple")
        self._received(colleague, 30)
        self._sent(colleague, 30)
        self.assertIn(persona.id, self._scan()[0])

    def test_company_persona_counts_its_people(self):
        persona = self._persona(self.client)
        self._received(self._person("Bruno Exemple"), 30)
        self.assertNotIn(persona.id, self._scan()[0])

    def test_archived_colleague_still_counts_for_the_company(self):
        persona = self._persona(self.client)
        gone = self._person("Bruno Exemple")
        self._received(gone, 30)
        gone.active = False
        self.assertNotIn(persona.id, self._scan()[0])

    def test_company_invoice_extends_the_company(self):
        persona = self._persona(self.client)
        self._invoice(self.client, 30)
        self.assertNotIn(persona.id, self._scan()[0])

    def test_company_invoice_does_not_extend_its_employee(self):
        persona = self._persona()
        self._invoice(self.client, 30)
        self.assertIn(persona.id, self._scan()[0])

    def test_individual_client_invoice_extends(self):
        persona = self._persona(self._person("Clara Exemple", company=False))
        self._invoice(persona.partner_id, 30)
        self.assertNotIn(persona.id, self._scan()[0])

    def test_unpaid_invoice_holds(self):
        persona = self._persona(self.client)
        self._invoice(self.client, 4 * 365, paid=False)
        ids, held = self._scan()
        self.assertNotIn(persona.id, ids)
        self.assertGreaterEqual(held.get("facture impayée", 0), 1)

    def test_unpaid_invoice_to_the_person_holds(self):
        persona = self._persona()
        self._invoice(persona.partner_id, 4 * 365, paid=False)
        self.assertNotIn(persona.id, self._scan()[0])

    def test_unpaid_invoice_to_a_duplicate_holds(self):
        persona = self._persona()
        double = self.Partner.create({"name": "Ada E.", "email": persona.partner_id.email})
        self._invoice(double, 4 * 365, paid=False)
        self.assertNotIn(persona.id, self._scan()[0])

    def test_archived_persona_is_scanned_too(self):
        persona = self._persona()
        persona.active = False
        self.assertIn(persona.id, self._scan()[0])

    def test_email_to_a_follower_extends(self):
        # Un envoi à un dossier dont la personne est seulement abonnée : elle
        # n'est pas dans partner_ids, mais la notification par courriel l'atteste.
        persona = self._persona()
        message = self.Message.create({
            "model": "res.partner", "res_id": self.client.id, "message_type": "comment",
            "subtype_id": self.comment.id, "author_id": self.me.id, "body": "<p>x</p>",
            "date": self._ago(30)})
        self.env["mail.notification"].create({
            "mail_message_id": message.id, "res_partner_id": persona.partner_id.id,
            "notification_type": "email", "notification_status": "sent"})
        self.assertNotIn(persona.id, self._scan()[0])

    def test_notification_type_send_extends(self):
        persona = self._persona()
        self.Message.create({
            "model": "res.partner", "res_id": persona.partner_id.id,
            "message_type": "notification", "subtype_id": self.comment.id,
            "author_id": self.me.id, "body": "<p>Ordre du jour</p>",
            "partner_ids": [(6, 0, persona.partner_id.ids)], "date": self._ago(30)})
        self.assertNotIn(persona.id, self._scan()[0])

    def test_send_under_the_company_name_extends(self):
        persona = self._persona()
        self.Message.create({
            "model": "res.partner", "res_id": persona.partner_id.id, "message_type": "comment",
            "subtype_id": self.comment.id, "author_id": self.env.company.partner_id.id,
            "body": "<p>x</p>", "partner_ids": [(6, 0, persona.partner_id.ids)],
            "date": self._ago(30)})
        self.assertNotIn(persona.id, self._scan()[0])

    def test_duplicate_contact_at_the_same_address_extends(self):
        persona = self._persona()
        double = self.Partner.create({"name": "Ada E.", "email": persona.partner_id.email,
                                      "active": False})
        self._received(double, 30)
        self.assertNotIn(persona.id, self._scan()[0])

    def test_email_without_known_author_extends(self):
        persona = self._persona()
        self.Message.create({
            "model": "res.partner", "res_id": self.client.id, "message_type": "email",
            "author_id": False, "email_from": f"Ada <{persona.partner_id.email}>",
            "body": "<p>x</p>", "date": self._ago(30)})
        self.assertNotIn(persona.id, self._scan()[0])

    def test_similar_address_does_not_extend(self):
        persona = self._persona()
        self.Message.create({
            "model": "res.partner", "res_id": self.client.id, "message_type": "email",
            "author_id": False, "email_from": f"nada.{persona.partner_id.email}",
            "body": "<p>x</p>", "date": self._ago(30)})
        self.assertIn(persona.id, self._scan()[0])

    def test_unified_inbox_email_extends(self):
        if "bf.email" not in self.env:
            self.skipTest("bf_email_management absent")
        persona = self._persona()
        self.env["bf.email"].create({
            "date": self._ago(30), "direction": "in", "subject": "Bonjour",
            "email_from": persona.partner_id.email, "partner_id": persona.partner_id.id})
        self.assertNotIn(persona.id, self._scan()[0])

    def test_archived_rule_stops_an_approved_campaign(self):
        persona = self._persona()
        self.Persona._privacy_propose_lines(self.rule, persona)
        campaign = self.Persona._privacy_open_campaign(self.rule)
        campaign.action_approve()
        self.rule.active = False
        campaign.action_execute()
        self.assertTrue(persona.exists())
        self.assertEqual(self._lines(persona).state, "skipped")

    def test_persona_of_our_own_company_is_never_proposed(self):
        persona = self._persona(self.env.company.partner_id)
        ids, held = self._scan()
        self.assertNotIn(persona.id, ids)
        self.assertTrue(any("nos sociétés" in k for k in held))

    def test_similar_address_in_the_inbox_does_not_extend(self):
        if "bf.email" not in self.env:
            self.skipTest("bf_email_management absent")
        persona = self._persona()
        self.env["bf.email"].create({
            "date": self._ago(30), "direction": "in", "subject": "Bonjour",
            "email_from": f"nada.{persona.partner_id.email}"})
        self.assertIn(persona.id, self._scan()[0])

    def test_inbox_address_without_contact_extends(self):
        if "bf.email" not in self.env:
            self.skipTest("bf_email_management absent")
        persona = self._persona()
        self.env["bf.email"].create({
            "date": self._ago(30), "direction": "in", "subject": "Bonjour",
            "email_from": f"Ada <{persona.partner_id.email}>"})
        self.assertNotIn(persona.id, self._scan()[0])

    def test_archived_rule_proposes_nothing(self):
        persona = self._persona()
        self.rule.active = False
        self.assertEqual(self.Persona._cron_privacy_propose_destruction(), 0)
        self.assertFalse(self._lines(persona))

    def test_rule_without_duration_proposes_nothing(self):
        persona = self._persona()
        self.rule.write({"active_retention_years": 0, "semi_active_retention_years": 0})
        ids, held = self._scan()
        self.assertFalse(ids)
        self.assertTrue(held)
        self.assertEqual(self.Persona._cron_privacy_propose_destruction(), 0)
        self.assertFalse(self._lines(persona))

    # ------------------------------------------------------------------
    # La proposition
    # ------------------------------------------------------------------

    def test_cron_proposes_once_and_destroys_nothing(self):
        persona = self._persona()
        self.Persona._cron_privacy_propose_destruction()
        self.Persona._cron_privacy_propose_destruction()
        self.assertTrue(persona.exists())
        line = self._lines(persona)
        self.assertEqual(len(line), 1)
        self.assertEqual(line.campaign_id.state, "review")
        self.assertEqual(line.retention_expiry_date,
                         fields.Date.to_date(persona.create_date) + timedelta(days=3 * 365))
        self.assertNotIn("Ada", line.res_name)
        self.assertNotIn("Exemple", line.res_name)

    def test_dry_run_proposes_nothing(self):
        persona = self._persona()
        self.assertEqual(self.Persona._cron_privacy_propose_destruction(dry_run=True), 0)
        self.assertFalse(self._lines(persona))

    def test_manual_skip_holds_the_persona(self):
        persona = self._persona()
        self.Persona._cron_privacy_propose_destruction()
        self._lines(persona).action_skip()
        self.Persona._cron_privacy_propose_destruction()
        self.assertEqual(len(self._lines(persona)), 1)

    def test_native_scan_is_refused(self):
        campaign = self.env["privacy.destruction.campaign"].create(
            {"name": "À la main", "retention_calendar_id": self.rule.id})
        with self.assertRaises(UserError):
            campaign.action_scan()

    def test_draft_campaign_is_not_reused(self):
        manual = self.env["privacy.destruction.campaign"].create(
            {"name": "À la main", "retention_calendar_id": self.rule.id})
        self.assertNotEqual(self.Persona._privacy_open_campaign(self.rule), manual)

    # ------------------------------------------------------------------
    # La destruction
    # ------------------------------------------------------------------

    def test_execution_destroys_and_registers(self):
        persona = self._persona()
        partner = persona.partner_id
        kpi = self.env["contact.persona.kpi"].create({"persona_id": persona.id, "name": "Heures"})
        rule = self.env["contact.cc.rule"].create({
            "persona_id": persona.id, "cc_partner_ids": [(6, 0, self._person("Bruno Exemple").ids)]})
        attachment = self.env["ir.attachment"].create({
            "name": "note.txt", "raw": b"x", "res_model": "contact.persona", "res_id": persona.id})
        persona_id = persona.id
        campaign = self._execute(persona)
        self.assertEqual(campaign.executed_count, 1, campaign.line_ids.mapped("error_message"))
        self.assertFalse(self.Persona.with_context(active_test=False).browse(persona_id).exists())
        self.assertFalse(kpi.exists())
        self.assertFalse(rule.exists())
        self.assertFalse(attachment.exists())
        self.assertTrue(partner.exists(), "le contact relève de ses propres règles")
        entry = self.Register.search([("res_model", "=", "contact.persona"), ("res_id", "=", persona_id)])
        self.assertEqual(len(entry), 1)
        self.assertIn("Ne sont PAS atteints", entry.document_description)
        self.assertIn("cote de payeur", entry.document_description)
        self.assertNotIn("Ada", (entry.res_name or "") + entry.document_description)

    def test_execution_takes_the_payer_history(self):
        persona = self._persona()
        tracked = persona.with_context(tracking_disable=False, mail_notrack=False)
        tracked.payer_quality = "slow"
        # Le suivi n'écrit ses valeurs qu'au pré-commit, que TransactionCase n'atteint jamais.
        self.env.flush_all()
        self.env.cr.precommit.run()
        tracked.payer_quality = "poor"
        self.env.flush_all()
        self.env.cr.precommit.run()
        domain = [("model", "=", "contact.persona"), ("res_id", "=", persona.id)]
        messages = self.Message.sudo().search(domain)
        self.assertTrue(messages.tracking_value_ids, "l'essai doit d'abord produire un historique")
        tracking = messages.tracking_value_ids
        self._execute(persona)
        self.assertFalse(self.Message.sudo().search(domain))
        self.assertFalse(tracking.exists())

    def test_kpi_attachment_leaves_with_the_persona(self):
        # Échoue si les indicateurs partent par la cascade SQL de `persona_id` :
        # c'est leur `unlink` qui balaie leurs pièces jointes.
        persona = self._persona()
        kpi = self.env["contact.persona.kpi"].create({"persona_id": persona.id, "name": "Heures"})
        attachment = self.env["ir.attachment"].create({
            "name": "releve.csv", "raw": b"x", "res_model": "contact.persona.kpi", "res_id": kpi.id})
        persona.unlink()
        self.assertFalse(attachment.exists())

    def test_officer_who_is_not_a_persona_user_executes(self):
        persona = self._persona()
        self.assertFalse(self.officer.has_group("bf_persona.group_persona_user"))
        campaign = self._execute(persona, user=self.officer)
        self.assertEqual(campaign.executed_count, 1, campaign.line_ids.mapped("error_message"))
        self.assertFalse(persona.exists())

    def test_person_who_wrote_since_is_skipped(self):
        persona = self._persona()
        self.Persona._privacy_propose_lines(self.rule, persona)
        self._received(persona.partner_id, 0)
        campaign = self.Persona._privacy_open_campaign(self.rule)
        campaign.action_approve()
        campaign.action_execute()
        self.assertEqual(campaign.executed_count, 0)
        self.assertTrue(persona.exists())
        self.assertEqual(self._lines(persona).state, "skipped")

    def test_unpaid_invoice_since_is_skipped(self):
        persona = self._persona()
        self.Persona._privacy_propose_lines(self.rule, persona)
        self._invoice(persona.partner_id, 4 * 365, paid=False)
        campaign = self.Persona._privacy_open_campaign(self.rule)
        campaign.action_approve()
        campaign.action_execute()
        self.assertTrue(persona.exists())
        line = self._lines(persona)
        self.assertEqual(line.state, "skipped")
        self.assertIn("impayée", line.error_message)

    def test_line_without_rule_is_still_rechecked(self):
        persona = self._persona()
        campaign = self.Persona._privacy_open_campaign(self.rule)
        line = self.Line.create({
            "campaign_id": campaign.id, "res_model": persona._name, "res_id": persona.id,
            "res_name": "Persona", "destruction_method": "delete", "state": "pending"})
        self._received(persona.partner_id, 0)
        line._execute_destruction()
        self.assertEqual(line.state, "skipped")
        self.assertTrue(persona.exists())

    def test_review_column_shows_the_contact_to_the_officer(self):
        persona = self._persona(self._person("Jeanne Fictive"))
        self._received(persona.partner_id, 4 * 365)
        self.Persona._privacy_propose_lines(self.rule, persona)
        line = self._lines(persona).with_user(self.officer)
        self.assertIn("Jeanne Fictive", line.bf_persona_review)
        self.assertIn("reçu", line.bf_persona_review)

    def test_register_never_names_the_person(self):
        persona = self._persona(self._person("Jeanne Fictive"))
        campaign = self.Persona._privacy_open_campaign(self.rule)
        # Une ligne comme celles du balayage natif, qui porte le nom.
        self.Line.create({
            "campaign_id": campaign.id, "res_model": persona._name, "res_id": persona.id,
            "res_name": "Jeanne Fictive", "retention_calendar_id": self.rule.id,
            "destruction_method": "delete", "state": "pending"})
        persona_id = persona.id
        campaign.action_approve()
        campaign.action_execute()
        entry = self.Register.search([("res_model", "=", "contact.persona"), ("res_id", "=", persona_id)])
        self.assertEqual(len(entry), 1)
        self.assertNotIn("Fictive", (entry.res_name or "") + entry.document_description)

    def test_classifications_are_retired(self):
        persona = self._persona()
        kpi = self.env["contact.persona.kpi"].create({"persona_id": persona.id, "name": "Heures"})
        Classification = self.env["privacy.document.classification"]
        mine = Classification.create([
            {"res_model": "contact.persona", "res_id": persona.id, "pi_category": "financial"},
            {"res_model": "contact.persona.kpi", "res_id": kpi.id, "pi_category": "other"},
        ])
        self._execute(persona)
        self.assertFalse(any(mine.with_context(active_test=False).mapped("active")))

    def test_kpi_classified_alone_leaves_the_persona(self):
        persona = self._persona(age_days=10)
        kpi = self.env["contact.persona.kpi"].create({"persona_id": persona.id, "name": "Heures"})
        campaign = self.env["privacy.destruction.campaign"].create(
            {"name": "Indicateur", "retention_calendar_id": self.env.ref(
                "privacy_consent.retention_calendar_correspondence").id})
        line = self.Line.create({
            "campaign_id": campaign.id, "res_model": kpi._name, "res_id": kpi.id,
            "res_name": "Heures", "destruction_method": "delete", "state": "pending"})
        line._execute_destruction()
        self.assertFalse(kpi.exists())
        self.assertTrue(persona.exists())

    def test_partner_deletion_takes_the_thread(self):
        persona = self._persona(self._person("Clara Exemple", company=False))
        persona.message_post(body="Trace dans le fil du persona")
        domain = [("model", "=", "contact.persona"), ("res_id", "=", persona.id)]
        self.assertTrue(self.Message.sudo().search(domain))
        persona.partner_id.unlink()
        self.assertFalse(persona.exists())
        self.assertFalse(self.Message.sudo().search(domain))

    def test_erasure_request_takes_the_persona_and_says_so(self):
        partner = self._person("Clara Exemple", company=False)
        persona = self._persona(partner, age_days=10)
        persona_id = persona.id
        Request = self.env["privacy.destruction.request"]
        request = Request.create_partner_destruction_request(partner.id, reason="Effacement")
        self.assertIn(persona_id, request.classification_ids.filtered(
            lambda c: c.res_model == "contact.persona").mapped("res_id"))
        self.assertEqual(request.state, "pending")
        request.action_approve()
        request.action_execute()
        self.assertFalse(self.Persona.with_context(active_test=False).browse(persona_id).exists())
        self.assertIn(f"Persona #{persona_id} SUPPRIMÉ", request.execution_log)
        self.assertIn("ne s'anonymise pas", request.notes)

    def test_manual_erasure_request_by_an_officer_without_persona_role(self):
        # Saisie à la main (sans create_partner_destruction_request), méthode
        # « suppression » : le socle aurait sauté le persona faute de droit
        # d'écriture du responsable.
        partner = self._person("Clara Exemple", company=False)
        persona = self._persona(partner, age_days=10)
        persona_id = persona.id
        delete_rule = self.env["privacy.retention.calendar"].search(
            [("destruction_method", "=", "delete"), ("active", "=", True)], limit=1)
        request = self.env["privacy.destruction.request"].create({
            "request_type": "erasure_right", "partner_id": partner.id,
            "trigger_date": fields.Datetime.now(), "scheduled_date": fields.Date.today(),
            "retention_calendar_id": delete_rule.id, "notes": "Effacement saisi à la main"})
        self.assertFalse(request.classification_ids)
        request.action_approve()
        request.with_user(self.officer).action_execute()
        self.assertFalse(self.Persona.with_context(active_test=False).browse(persona_id).exists())
        self.assertIn(f"Persona #{persona_id}", request.execution_log)

    def test_merge_takes_the_source_thread(self):
        a = self._person("Clara Exemple", company=False)
        b = self.Partner.create({"name": "Clara E.", "email": a.email})
        kept = self._persona(a, age_days=10)
        gone = self._persona(b, age_days=10)
        gone.message_post(body="Trace dans le fil du persona fusionné")
        gone_id = gone.id
        self.env["base.partner.merge.automatic.wizard"]._merge((a | b).ids, dst_partner=a)
        self.assertTrue(kept.exists())
        self.assertFalse(self.Persona.with_context(active_test=False).browse(gone_id).exists())
        self.assertFalse(self.Message.sudo().search(
            [("model", "=", "contact.persona"), ("res_id", "=", gone_id)]))

    def test_retired_classification_no_longer_names_the_person(self):
        persona = self._persona()
        cls = self.env["privacy.document.classification"].create({
            "res_model": "contact.persona", "res_id": persona.id, "pi_category": "medical",
            "subject_partner_id": persona.partner_id.id, "notes": "Confié : un diagnostic"})
        self._execute(persona)
        cls = cls.with_context(active_test=False)
        self.assertFalse(cls.active)
        self.assertFalse(cls.subject_partner_id)
        self.assertNotIn("diagnostic", cls.notes or "")

    def test_register_carries_categories_and_number_only(self):
        persona = self._persona()
        persona_id = persona.id
        self._execute(persona)
        entry = self.Register.search([("res_model", "=", "contact.persona"), ("res_id", "=", persona_id)])
        self.assertEqual(entry.res_name, f"Persona #{persona_id}")
        self.assertIn("financial", entry.pi_categories)

    def test_anonymize_is_refused(self):
        persona = self._persona()
        self.Persona._privacy_propose_lines(self.rule, persona)
        line = self._lines(persona)
        line.destruction_method = "anonymize"
        with self.assertRaises(UserError):
            line._execute_destruction()
        self.assertTrue(persona.exists())

    def test_line_without_record_is_not_certified(self):
        campaign = self.Persona._privacy_open_campaign(self.rule)
        line = self.Line.create({
            "campaign_id": campaign.id, "res_model": "contact.persona", "res_id": 0,
            "res_name": "vide", "destruction_method": "delete", "state": "pending"})
        line._execute_destruction()
        self.assertEqual(line.state, "skipped")

    def test_foreign_model_relays_to_super(self):
        tag = self.env["res.partner.category"].create({"name": "Étiquette jetable"})
        campaign = self.env["privacy.destruction.campaign"].create(
            {"name": "Autre", "retention_calendar_id": self.env.ref(
                "privacy_consent.retention_calendar_correspondence").id})
        line = self.Line.create({
            "campaign_id": campaign.id, "res_model": tag._name, "res_id": tag.id,
            "res_name": tag.name, "destruction_method": "delete", "state": "pending"})
        line._execute_destruction()
        self.assertFalse(tag.exists())

    def test_register_refuses_to_certify_a_survivor(self):
        # ⚠️ Redondant avec la garde du socle (privacy_consent 18.0.5.0.0) : un
        # mutant qui retire la nôtre survit. Gardé comme contrat.
        persona = self._persona()
        with self.assertRaises(UserError):
            self.Register.create({
                "destruction_date": fields.Datetime.now(),
                "res_model": persona._name, "res_id": persona.id, "res_name": "x",
                "document_description": "essai", "destruction_method": "delete",
            })
