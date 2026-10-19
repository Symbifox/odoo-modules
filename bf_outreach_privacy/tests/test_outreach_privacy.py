"""Ce que ces tests doivent PROUVER, et pas seulement observer.

Quatre d'entre eux valent d'être signalés :

* `test_refusal_survives_campaign_deletion` échoue si l'inscription attend la
  destruction au lieu de se faire au moment du refus.
* `test_lift_never_erases_an_earlier_refusal` échoue si une levée retire des
  inscriptions dont la fiche levée n'est pas propriétaire.
* `test_attempts_without_answer_do_not_extend` échoue si l'ancrage DEM-001
  compte tous les essais au lieu des seuls contacts utiles.
* `test_touch_attachment_leaves_with_the_target` échoue si les interactions
  partent par la cascade SQL au lieu de l'ORM.

Chaque essai ne compte que ses propres enregistrements : la base peut porter
déjà des refus (un clone de production après la reprise d'installation).

⚠️ Rien ici n'est validé : TransactionCase annule tout, et c'est obligatoire,
le registre de destruction étant immuable.
"""
from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestOutreachPrivacy(TransactionCase):

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
        cls.Target = cls.env["bf.outreach.target"]
        cls.Touch = cls.env["bf.outreach.touch"]
        cls.Optout = cls.env["bf.outreach.optout"]
        cls.Line = cls.env["privacy.destruction.campaign.line"]
        cls.campaign = cls.env["bf.outreach.campaign"].create({"name": "Campagne vie privée"})
        cls.rule_active = cls.env.ref("bf_outreach_privacy.retention_outreach_target")
        cls.rule_refused = cls.env.ref("bf_outreach_privacy.retention_outreach_refused")
        cls.env.company.country_id = cls.env.ref("base.ca")
        cls.user = new_test_user(cls.env, login="dem_usager", groups="base.group_user,base.group_partner_manager,bf_outreach.group_bf_outreach_user")
        cls.manager = new_test_user(cls.env, login="dem_gestion",
                                    groups="base.group_user,base.group_partner_manager,bf_outreach.group_bf_outreach_manager")
        cls.officer = new_test_user(cls.env, login="vp_responsable",
                                    groups="base.group_user,privacy_consent.group_privacy_officer")
        cls.privacy_user = new_test_user(cls.env, login="vp_usager",
                                         groups="base.group_user,privacy_consent.group_privacy_user")

    # ------------------------------------------------------------------
    # Fabriques
    # ------------------------------------------------------------------

    def _target(self, name="Cible inc.", email=None, phone=None, mobile=None, campaign=None, **vals):
        return self.Target.create(dict(
            {"name": name, "campaign_id": (campaign or self.campaign).id,
             "email": email, "phone": phone, "mobile": mobile}, **vals))

    def _age(self, target, days):
        self.env.cr.execute(
            "UPDATE bf_outreach_target SET create_date = %s WHERE id = %s",
            (datetime.now() - timedelta(days=days), target.id))
        target.invalidate_recordset(["create_date"])

    def _touch(self, target, days_ago, outcome="no_answer", direction="out"):
        return self.Touch.create({
            "target_id": target.id, "kind": "call", "direction": direction,
            "outcome": outcome, "date": datetime.now() - timedelta(days=days_ago),
        })

    def _refuse(self, target, reason="Ne m'appelez plus"):
        target.write({"do_not_contact": True, "do_not_contact_reason": reason})
        return target

    def _entries(self, value):
        return self.Optout.search([("lookup", "=", value)])

    def _owned(self, record):
        return self.Optout.search([("origin_model", "=", record._name), ("origin_id", "=", record.id)])

    def _scan(self, **kw):
        buckets, held = self.Target._privacy_scan_expired(**kw)
        return {rule: set(rows.ids) for rule, rows in buckets.items()}, held

    def _lines(self, target):
        return self.Line.search([("res_model", "=", target._name), ("res_id", "=", target.id)])

    def _execute(self, rule, targets, user=None):
        self.Target._privacy_propose_lines(rule, targets)
        campaign = self.Target._privacy_open_campaign(rule)
        if user:
            campaign = campaign.with_user(user)
        campaign.action_approve()
        campaign.action_execute()
        return campaign

    # ------------------------------------------------------------------
    # Le registre des traitements et le calendrier
    # ------------------------------------------------------------------

    def test_purpose_and_rules_are_declared(self):
        purpose = self.env.ref("bf_outreach_privacy.purpose_outreach_prospecting")
        self.assertEqual(purpose.code, "outreach_prospecting")
        self.assertFalse(purpose.requires_consent)
        self.assertEqual(self.rule_active.code, "DEM-001")
        self.assertEqual(self.rule_active.total_retention_days, 3 * 365)
        self.assertEqual(self.rule_refused.code, "DEM-002")
        self.assertTrue(self.rule_active.requires_approval)
        self.assertTrue(self.rule_refused.requires_approval)

    def test_target_and_touch_are_classifiable(self):
        models_ = self.env["privacy.document.classification"]._privacy_classifiable_models()
        self.assertIn("bf.outreach.target", models_)
        self.assertIn("bf.outreach.touch", models_)

    # ------------------------------------------------------------------
    # La liste d'opposition
    # ------------------------------------------------------------------

    def test_refusal_engraves_hashes_not_coordinates(self):
        target = self._refuse(self._target(
            email="Jeanne.Tremblay@Exemple.test", phone="514 555-0101", mobile="438-555-0102"))
        rows = self._owned(target)
        self.assertEqual(len(rows), 3, "courriel, téléphone ET cellulaire")
        self.assertEqual(sorted(rows.mapped("key_kind")), ["email", "phone", "phone"])
        self.assertEqual(set(rows.mapped("reason")), {"Ne m'appelez plus"})
        dump = repr(rows.sudo().read())
        for clear in ("jeanne", "tremblay", "5550101", "555-0101", "5550102"):
            self.assertNotIn(clear, dump.lower(), "la coordonnée ne doit pas être conservée")
        self.assertEqual(rows[:1].noted_by_id, self.env.user)

    def test_every_address_of_the_field_is_engraved(self):
        self._refuse(self._target(email="jeanne@x.test, j.t@y.test"))
        self.assertTrue(self._entries("jeanne@x.test"))
        self.assertTrue(self._entries("j.t@y.test"))
        with self.assertRaises(UserError):
            self._target(name="Autre liste", email="info@x.test; jeanne@x.test")

    def test_refused_person_cannot_be_recreated(self):
        self._refuse(self._target(email="jeanne@exemple.test"))
        with self.assertRaises(UserError):
            self._target(name="Autre liste", email="  JEANNE@exemple.test ")

    def test_phone_refusal_is_recognised_in_another_format(self):
        self._refuse(self._target(phone="514-555-0199"))
        with self.assertRaises(UserError):
            self._target(name="Même numéro", mobile="+1 (514) 555 0199")

    def test_target_with_partner_coordinates_only_is_blocked(self):
        self._refuse(self._target(email="jeanne@exemple.test"))
        partner = self.env["res.partner"].create({"name": "Jeanne", "email": "jeanne@exemple.test"})
        with self.assertRaises(UserError):
            self.Target.with_context(bf_outreach_skip_onchange=True).create(
                {"name": "Par RPC", "campaign_id": self.campaign.id, "partner_id": partner.id})

    def test_import_filter_skips_instead_of_failing(self):
        self._refuse(self._target(email="jeanne@exemple.test"))
        skipped = []
        created = self.Target.with_context(
            bf_outreach_optout_filter=True, bf_outreach_optout_skipped=skipped,
        ).create([
            {"name": "Refusée", "campaign_id": self.campaign.id, "email": "jeanne@exemple.test"},
            {"name": "Nouvelle", "campaign_id": self.campaign.id, "email": "paul@exemple.test"},
        ])
        self.assertEqual(created.mapped("name"), ["Nouvelle"])
        self.assertEqual(sum(skipped), 1)
        self.assertFalse(self.Target.search([("name", "=", "Refusée")]))

    def test_import_wizard_skips_and_does_not_leak_its_context(self):
        self._refuse(self._target(email="jeanne@exemple.test"))
        partners = self.env["res.partner"].create([
            {"name": "Jeanne, nouvelle fiche", "email": "jeanne@exemple.test"},
            {"name": "Paul", "email": "paul@exemple.test"},
        ])
        # Une AUTRE campagne : dans la même, le dédoublonnage du module
        # l'écarterait avant nous, et l'essai ne prouverait rien.
        other = self.env["bf.outreach.campaign"].create({"name": "Nouvelle liste"})
        wizard = self.env["bf.outreach.target.import.wizard"].create({
            "campaign_id": other.id, "partner_ids": [(6, 0, partners.ids)],
        })
        action = wizard.action_import()
        names = set(self.Target.search([("campaign_id", "=", other.id)]).mapped("name"))
        self.assertEqual(names, {"Paul"})
        self.assertIn("liste d'opposition", " ".join(other.message_ids.mapped("body")))
        context = action.get("context") or {}
        self.assertNotIn("bf_outreach_optout_filter", context)
        self.assertNotIn("bf_outreach_optout_skipped", context)

    def test_refusal_survives_campaign_deletion(self):
        self._refuse(self._target(email="jeanne@exemple.test"))
        other = self.env["bf.outreach.campaign"].create({"name": "Autre campagne"})
        self.campaign.unlink()
        with self.assertRaises(UserError):
            self.Target.create({"name": "Revenue", "campaign_id": other.id,
                                "email": "jeanne@exemple.test"})

    def test_sibling_is_not_solicited_but_not_frozen(self):
        other = self.env["bf.outreach.campaign"].create({"name": "Même liste, autre campagne"})
        first = self._target(email="jeanne@exemple.test")
        sibling = self._target(email="Jeanne@Exemple.test", campaign=other)
        self.assertTrue(sibling._is_solicitable())
        self._refuse(first)
        self.assertFalse(sibling.do_not_contact, "pas de gel : il se propagerait")
        self.assertFalse(sibling._is_solicitable())
        first.with_user(self.manager).action_allow_contact_again()
        self.assertTrue(sibling._is_solicitable(), "la levée vaut aussi pour la sœur")

    def test_shared_switchboard_does_not_spread(self):
        """Un standard commun : une personne refuse, les autres ne sont ni gelées ni inscrites."""
        colleagues = [self._target(name=f"Employé {i}", email=f"e{i}@entreprise.test",
                                   phone="819 555 0100", mobile=f"819 555 01{10 + i}")
                      for i in range(5)]
        self._refuse(colleagues[0])
        for colleague in colleagues[1:]:
            self.assertFalse(colleague.do_not_contact)
            self.assertFalse(self._entries(colleague.email))
            self.assertFalse(self._entries(colleague.mobile))
        self.assertTrue(self._entries("+18195550100"))

    def test_lift_never_erases_an_earlier_refusal(self):
        other = self.env["bf.outreach.campaign"].create({"name": "Autre"})
        # La même personne, chargée dans deux campagnes AVANT tout refus.
        first = self._target(email="jeanne@exemple.test")
        second = self._target(email="jeanne@exemple.test", campaign=other)
        self._refuse(first, reason="vrai refus")
        first.unlink()
        # La seconde fiche, marquée par erreur, puis levée : le vrai refus reste.
        self._refuse(second, reason="erreur")
        second.with_user(self.manager).action_allow_contact_again()
        rows = self._entries("jeanne@exemple.test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.reason, "vrai refus")

    def test_author_is_kept_when_the_refusal_reaches_the_partner(self):
        partner = self.env["res.partner"].create({"name": "Jeanne", "email": "jeanne@exemple.test"})
        target = self._target(name="Jeanne", email="jeanne@exemple.test", partner_id=partner.id)
        target.with_user(self.user).write({"do_not_contact": True, "do_not_contact_reason": "non"})
        self.assertTrue(partner.outreach_opt_out)
        rows = self._entries("jeanne@exemple.test")
        self.assertEqual(rows.noted_by_id, self.user)
        self.assertEqual((rows.origin_model, rows.origin_id), (target._name, target.id))

    def test_editing_a_contact_to_a_refused_address_is_refused(self):
        self._refuse(self._target(email="jeanne@exemple.test"))
        target = self._target(name="Paul", email="paul@exemple.test")
        with self.assertRaises(UserError):
            target.write({"email": "jeanne@exemple.test"})

    def test_new_coordinate_of_a_refused_target_is_engraved(self):
        target = self._refuse(self._target(email="jeanne@exemple.test"))
        target.write({"mobile": "438 555 0177"})
        self.assertTrue(self._entries("+14385550177"))

    def test_outreach_user_notes_a_refusal(self):
        target = self._target(email="jeanne@exemple.test").with_user(self.user)
        target.write({"do_not_contact": True, "do_not_contact_reason": "au téléphone"})
        rows = self._owned(target)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.noted_by_id, self.user)

    def test_plain_user_cannot_lift(self):
        target = self._refuse(self._target(email="jeanne@exemple.test"))
        with self.assertRaises(AccessError):
            target.with_user(self.user).write({"do_not_contact": False})
        partner = self.env["res.partner"].create({"name": "Paul", "email": "paul@exemple.test"})
        partner.write({"outreach_opt_out": True})
        with self.assertRaises(AccessError):
            partner.with_user(self.user).write({"outreach_opt_out": False})
        self.assertTrue(self._entries("jeanne@exemple.test"))
        self.assertTrue(self._entries("paul@exemple.test"))

    def test_manager_lift_removes_the_entry(self):
        target = self._refuse(self._target(email="jeanne@exemple.test"))
        target.with_user(self.manager).action_allow_contact_again()
        self.assertFalse(self._entries("jeanne@exemple.test"))
        self._target(name="De retour", email="jeanne@exemple.test")

    def test_lift_transfers_to_another_live_refusal(self):
        first = self._refuse(self._target(email="jeanne@exemple.test"))
        partner = self.env["res.partner"].create({"name": "Jeanne", "email": "jeanne@exemple.test"})
        partner.write({"outreach_opt_out": True, "outreach_opt_out_reason": "au téléphone"})
        first.with_user(self.manager).action_allow_contact_again()
        rows = self._entries("jeanne@exemple.test")
        self.assertEqual(len(rows), 1, "le contact retient encore le refus")
        self.assertEqual((rows.origin_model, rows.origin_id), ("res.partner", partner.id))
        self.assertEqual(rows.reason, "au téléphone", "la preuve devient celle du détenteur")

    def test_partner_opt_out_engraves_and_lifts(self):
        partner = self.env["res.partner"].create(
            {"name": "Paul", "email": "paul@exemple.test", "phone": "514 555 0123"})
        partner.write({"outreach_opt_out": True})
        rows = self._owned(partner)
        self.assertEqual(set(rows.mapped("origin")), {"partner"})
        self.assertEqual(len(rows), 2)
        partner.with_user(self.manager).write({"outreach_opt_out": False})
        self.assertFalse(self._owned(partner))

    def test_partner_created_opted_out_is_engraved(self):
        self.env["res.partner"].create(
            {"name": "Import", "email": "importe@exemple.test", "outreach_opt_out": True})
        self.assertTrue(self._entries("importe@exemple.test"))

    def test_partner_opt_out_engraves_its_targets_own_coordinates(self):
        partner = self.env["res.partner"].create({"name": "Paul", "email": "paul@exemple.test"})
        self._target(name="Paul", email="paul.perso@exemple.test", partner_id=partner.id)
        partner.write({"outreach_opt_out": True})
        self.assertTrue(self._entries("paul.perso@exemple.test"))

    def test_engraving_is_idempotent_and_first_refusal_wins(self):
        target = self._refuse(self._target(email="jeanne@exemple.test"), reason="premier")
        self._refuse(target, reason="second")
        target._bf_engrave_refusal()
        rows = self._entries("jeanne@exemple.test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.reason, "premier")

    def test_lookup_works_without_reading_the_hash(self):
        self._refuse(self._target(email="jeanne@exemple.test"))
        Optout = self.Optout.with_user(self.privacy_user)
        self.assertEqual(len(Optout.search([("lookup", "=", "JEANNE@exemple.test")])), 1)
        self.assertFalse(Optout.search([("lookup", "=", "paul@exemple.test")]))
        self.assertNotIn("key_hash", Optout.fields_get())

    def test_install_reprise_engraves_existing_refusals(self):
        target = self._target(email="jeanne@exemple.test")
        # Un refus noté AVANT le pont : on contourne `write`.
        self.env.cr.execute(
            "UPDATE bf_outreach_target SET do_not_contact = true, do_not_contact_reason = %s "
            "WHERE id = %s", ("ancien refus", target.id))
        target.invalidate_recordset()
        self.assertFalse(self._entries("jeanne@exemple.test"))
        self.Optout._bf_engrave_existing()
        rows = self._entries("jeanne@exemple.test")
        self.assertEqual(rows.origin, "install")
        self.assertEqual(rows.reason, "ancien refus")

    # ------------------------------------------------------------------
    # Le balayage
    # ------------------------------------------------------------------

    def test_old_target_without_useful_contact_is_proposed(self):
        target = self._target(email="vieux@exemple.test")
        self._age(target, 4 * 365)
        buckets, _held = self._scan()
        self.assertIn(target.id, buckets.get(self.rule_active.id, set()))

    def test_young_target_is_not_proposed(self):
        target = self._target(email="jeune@exemple.test")
        self._age(target, 2 * 365)
        buckets, _held = self._scan()
        self.assertNotIn(target.id, buckets.get(self.rule_active.id, set()))

    def test_useful_contact_extends(self):
        target = self._target(email="joint@exemple.test")
        self._age(target, 4 * 365)
        self._touch(target, 365, outcome="reached")
        buckets, _held = self._scan()
        self.assertNotIn(target.id, buckets.get(self.rule_active.id, set()))

    def test_inbound_touch_extends(self):
        target = self._target(email="entrant@exemple.test")
        self._age(target, 4 * 365)
        self._touch(target, 30, outcome="no_answer", direction="in")
        buckets, _held = self._scan()
        self.assertNotIn(target.id, buckets.get(self.rule_active.id, set()))

    def test_attempts_without_answer_do_not_extend(self):
        target = self._target(email="muet@exemple.test")
        self._age(target, 4 * 365)
        for days in (10, 40, 70):
            self._touch(target, days, outcome="no_answer")
        self._touch(target, 5, outcome="voicemail")
        buckets, _held = self._scan()
        self.assertIn(target.id, buckets.get(self.rule_active.id, set()))

    def test_archived_target_is_scanned_too(self):
        target = self._target(email="archive@exemple.test")
        self._age(target, 4 * 365)
        target.active = False
        buckets, _held = self._scan()
        self.assertIn(target.id, buckets.get(self.rule_active.id, set()))

    def test_refused_target_is_proposed_without_delay(self):
        target = self._refuse(self._target(email="jeanne@exemple.test"))
        buckets, _held = self._scan()
        self.assertIn(target.id, buckets.get(self.rule_refused.id, set()))
        self.assertNotIn(target.id, buckets.get(self.rule_active.id, set()))

    def test_refusal_without_any_key_is_held(self):
        target = self._refuse(self._target(name="Sans coordonnée"))
        buckets, held = self._scan()
        self.assertNotIn(target.id, buckets.get(self.rule_refused.id, set()))
        self.assertTrue(held)

    def test_refusal_held_by_partner_is_proposed(self):
        partner = self.env["res.partner"].create({"name": "Sans courriel"})
        target = self._refuse(self._target(name="Sans courriel", partner_id=partner.id))
        self.assertTrue(partner.outreach_opt_out)
        buckets, _held = self._scan()
        self.assertIn(target.id, buckets.get(self.rule_refused.id, set()))

    def test_rule_without_duration_proposes_nothing(self):
        target = self._target(email="vieux@exemple.test")
        self._age(target, 4 * 365)
        self.rule_active.write({"active_retention_years": 0})
        buckets, held = self._scan()
        self.assertNotIn(self.rule_active.id, buckets)
        self.assertTrue(held)

    def test_dry_run_engraves_nothing(self):
        target = self._target(email="jeanne@exemple.test")
        self.env.cr.execute("UPDATE bf_outreach_target SET do_not_contact = true WHERE id = %s",
                            (target.id,))
        target.invalidate_recordset()
        self.Target._cron_privacy_propose_destruction(dry_run=True)
        self.assertFalse(self._entries("jeanne@exemple.test"))
        self.assertFalse(self._lines(target))

    def test_cron_proposes_once_and_destroys_nothing(self):
        target = self._target(email="vieux@exemple.test")
        self._age(target, 4 * 365)
        self.Target._cron_privacy_propose_destruction()
        self.Target._cron_privacy_propose_destruction()
        self.assertTrue(target.exists())
        line = self._lines(target)
        self.assertEqual(len(line), 1)
        self.assertEqual(line.campaign_id.state, "review")
        self.assertEqual(line.retention_expiry_date,
                         fields.Date.to_date(target.create_date) + timedelta(days=3 * 365))
        self.assertNotIn("vieux", line.res_name.lower())
        self.assertNotIn(target.name, line.res_name)

    def test_manual_skip_holds_the_target(self):
        target = self._target(email="vieux@exemple.test")
        self._age(target, 4 * 365)
        self.Target._cron_privacy_propose_destruction()
        self._lines(target).action_skip()
        self.Target._cron_privacy_propose_destruction()
        self.assertEqual(len(self._lines(target)), 1)

    def test_target_skipped_by_the_executor_comes_back(self):
        target = self._target(email="vieux@exemple.test")
        self._age(target, 4 * 365)
        self.Target._privacy_propose_lines(self.rule_active, target)
        touch = self._touch(target, 0, outcome="interested")
        campaign = self.Target._privacy_open_campaign(self.rule_active)
        campaign.action_approve()
        campaign.action_execute()
        self.assertEqual(self._lines(target).state, "skipped")
        # Trois ans plus tard, le contact utile a vieilli : la cible revient.
        self.env.cr.execute("UPDATE bf_outreach_touch SET date = %s WHERE id = %s",
                            (datetime.now() - timedelta(days=4 * 365), touch.id))
        touch.invalidate_recordset(["date"])
        buckets, _held = self._scan()
        self.assertIn(target.id, buckets.get(self.rule_active.id, set()))

    def test_native_scan_is_refused(self):
        campaign = self.env["privacy.destruction.campaign"].create(
            {"name": "À la main", "retention_calendar_id": self.rule_active.id})
        self.assertEqual(campaign.state, "draft")
        with self.assertRaises(UserError):
            campaign.action_scan()

    def test_draft_campaign_is_not_reused(self):
        manual = self.env["privacy.destruction.campaign"].create(
            {"name": "À la main", "retention_calendar_id": self.rule_active.id})
        self.assertNotEqual(self.Target._privacy_open_campaign(self.rule_active), manual)

    # ------------------------------------------------------------------
    # La destruction
    # ------------------------------------------------------------------

    def test_execution_destroys_and_registers(self):
        target = self._refuse(self._target(email="jeanne@exemple.test", note="<p>Notes</p>"))
        touch = self._touch(target, 1, outcome="not_interested", direction="in")
        target_id, touch_id = target.id, touch.id
        campaign = self._execute(self.rule_refused, target)
        self.assertEqual(campaign.executed_count, 1, campaign.line_ids.mapped("error_message"))
        self.assertFalse(self.Target.with_context(active_test=False).browse(target_id).exists())
        self.assertFalse(self.Touch.browse(touch_id).exists())
        entry = self.env["privacy.destruction.register"].search([
            ("res_model", "=", "bf.outreach.target"), ("res_id", "=", target_id)])
        self.assertEqual(len(entry), 1)
        self.assertIn("liste d'opposition", entry.document_description)
        self.assertIn("Ne sont PAS atteints", entry.document_description)
        self.assertNotIn("Cible inc.", entry.document_description + (entry.res_name or ""))
        # Le refus survit à la fiche.
        with self.assertRaises(UserError):
            self._target(name="Revenue", email="jeanne@exemple.test")

    def test_officer_who_is_not_an_outreach_user_executes(self):
        target = self._target(email="vieux@exemple.test")
        self._age(target, 4 * 365)
        campaign = self._execute(self.rule_active, target, user=self.officer)
        self.assertEqual(campaign.executed_count, 1, campaign.line_ids.mapped("error_message"))
        self.assertFalse(target.exists())

    def test_target_that_answered_since_is_skipped(self):
        target = self._target(email="vieux@exemple.test")
        self._age(target, 4 * 365)
        self.Target._privacy_propose_lines(self.rule_active, target)
        self._touch(target, 0, outcome="interested")
        campaign = self.Target._privacy_open_campaign(self.rule_active)
        campaign.action_approve()
        campaign.action_execute()
        self.assertEqual(campaign.executed_count, 0)
        self.assertTrue(target.exists())

    def test_lifted_refusal_is_skipped(self):
        target = self._refuse(self._target(email="jeanne@exemple.test"))
        self.Target._privacy_propose_lines(self.rule_refused, target)
        target.with_user(self.manager).action_allow_contact_again()
        campaign = self.Target._privacy_open_campaign(self.rule_refused)
        campaign.action_approve()
        campaign.action_execute()
        self.assertEqual(campaign.executed_count, 0)
        self.assertTrue(target.exists())

    def test_refusal_during_the_wait_is_still_destroyed(self):
        target = self._target(email="vieux@exemple.test")
        self._age(target, 4 * 365)
        self.Target._privacy_propose_lines(self.rule_active, target)
        # Refus par l'assistant : il crée une interaction entrante du jour.
        self.env["bf.outreach.exclude.wizard"].create({
            "target_ids": [(6, 0, target.ids)], "reason": "plus jamais"}).action_exclude()
        campaign = self.Target._privacy_open_campaign(self.rule_active)
        campaign.action_approve()
        campaign.action_execute()
        self.assertEqual(campaign.executed_count, 1, campaign.line_ids.mapped("error_message"))
        self.assertTrue(self._entries("vieux@exemple.test"))

    def test_register_never_names_the_person(self):
        target = self._target(name="Jeanne Tremblay", email="vieux@exemple.test")
        self._age(target, 4 * 365)
        campaign = self.Target._privacy_open_campaign(self.rule_active)
        # Une ligne comme celles du balayage natif, qui porte le nom.
        self.Line.create({
            "campaign_id": campaign.id, "res_model": target._name, "res_id": target.id,
            "res_name": "Jeanne Tremblay", "retention_calendar_id": self.rule_active.id,
            "destruction_method": "delete", "state": "pending"})
        target_id = target.id
        campaign.action_approve()
        campaign.action_execute()
        entry = self.env["privacy.destruction.register"].search([
            ("res_model", "=", "bf.outreach.target"), ("res_id", "=", target_id)])
        self.assertEqual(len(entry), 1)
        self.assertNotIn("Tremblay", (entry.res_name or "") + entry.document_description)

    def test_touch_attachment_leaves_with_the_target(self):
        target = self._target(email="vieux@exemple.test")
        touch = self._touch(target, 1, outcome="reached")
        self._age(target, 4 * 365)
        self.env.cr.execute("UPDATE bf_outreach_touch SET date = %s WHERE id = %s",
                            (datetime.now() - timedelta(days=4 * 365), touch.id))
        touch.invalidate_recordset(["date"])
        attachment = self.env["ir.attachment"].create({
            "name": "capture.png", "raw": b"x", "res_model": touch._name, "res_id": touch.id})
        campaign = self._execute(self.rule_active, target)
        self.assertEqual(campaign.executed_count, 1, campaign.line_ids.mapped("error_message"))
        self.assertFalse(attachment.exists())

    def test_campaign_deletion_takes_touch_attachments(self):
        target = self._target(email="vieux@exemple.test")
        touch = self._touch(target, 1, outcome="reached")
        attachment = self.env["ir.attachment"].create({
            "name": "capture.png", "raw": b"x", "res_model": touch._name, "res_id": touch.id})
        self.campaign.unlink()
        self.assertFalse(attachment.exists())

    def test_manual_deletion_refuses_to_destroy_the_only_proof(self):
        target = self._refuse(self._target(name="Sans coordonnée"))
        with self.assertRaises(UserError):
            target.unlink()

    def test_executor_refuses_to_destroy_the_only_proof(self):
        target = self._refuse(self._target(name="Sans coordonnée"))
        self.Target._privacy_propose_lines(self.rule_refused, target)
        campaign = self.Target._privacy_open_campaign(self.rule_refused)
        campaign.action_approve()
        campaign.action_execute()
        self.assertEqual(campaign.executed_count, 0)
        self.assertTrue(target.exists())
        self.assertFalse(self.env["privacy.destruction.register"].search([
            ("res_model", "=", "bf.outreach.target"), ("res_id", "=", target.id)]))

    def test_federation_local_copy_leaves_with_the_target(self):
        if "federation.outreach.report.line" not in self.env:
            self.skipTest("bf_federation_outreach n'est pas installé")
        target = self._target(email="vieux@exemple.test")
        report = self.env["federation.outreach.report"].create({
            "name": "Suivi", "campaign_ref": f"bf.outreach.campaign,{self.campaign.id}"})
        line = self.env["federation.outreach.report.line"].create({
            "report_id": report.id, "key": str(target.id), "name": target.name,
            "email": target.email})
        target.unlink()
        self.assertFalse(line.exists())
        self.assertTrue(report.exists())

    def test_anonymize_is_refused(self):
        target = self._target(email="vieux@exemple.test")
        self._age(target, 4 * 365)
        self.Target._privacy_propose_lines(self.rule_active, target)
        line = self._lines(target)
        line.destruction_method = "anonymize"
        with self.assertRaises(UserError):
            line._execute_destruction()
        self.assertTrue(target.exists())

    def test_line_without_record_is_not_certified(self):
        campaign = self.Target._privacy_open_campaign(self.rule_active)
        line = self.Line.create({
            "campaign_id": campaign.id, "res_model": "bf.outreach.target", "res_id": 0,
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
        target = self._target(email="vivant@exemple.test")
        with self.assertRaises(UserError):
            self.env["privacy.destruction.register"].create({
                "destruction_date": fields.Datetime.now(),
                "res_model": target._name, "res_id": target.id, "res_name": "x",
                "document_description": "essai", "destruction_method": "delete",
            })
