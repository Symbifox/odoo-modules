"""Le moteur de règles de routage : conditions, exceptions, actions, gardes.

Ce qui est éprouvé ici est ce qui casse en silence. Une règle qui ne se
déclenche pas ne lève rien : le courriel reste simplement dans la boîte, et
personne ne le remarque avant des semaines. Chaque cas ci-dessous correspond à
une manière précise de ne rien faire sans le dire.
"""

from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class RuleEngineCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.owner = Users.create({
            "name": "Propriétaire Règles",
            "login": "rules.owner@test.invalid",
            "email": "owner@exemple.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.other = Users.create({
            "name": "Autre Personne",
            "login": "rules.other@test.invalid",
            "email": "other@exemple.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.account = cls.env["bf.email.account"].create({
            "name": "Boîte règles",
            "user_id": cls.owner.id,
            "host": "imap.exemple.test",
            "port": 993,
            "login": "owner@exemple.test",
            "password": "x",
        })
        # The account creation seeds the stock rules; start from a clean slate
        # so a test asserts on its own rules and not on the catalogue.
        cls.env["bf.email.rule"].sudo().with_context(
            active_test=False).search([("user_id", "=", cls.owner.id)]).unlink()

    # ------------------------------------------------------------------
    def _rule(self, conditions, exceptions=(), match_type="all", **actions):
        vals = {
            "name": actions.pop("name", "Règle d'essai"),
            "scope": "user",
            "user_id": self.owner.id,
            "match_type": match_type,
            "condition_ids": [(0, 0, dict(c, kind="condition"))
                              for c in conditions],
            "exception_ids": [(0, 0, dict(c, kind="exception"))
                              for c in exceptions],
        }
        vals.update(actions)
        return self.env["bf.email.rule"].sudo().create(vals)

    def _email(self, **overrides):
        vals = {
            "date": "2026-08-23 10:00:00",
            "email_from": "Acme <expediteur@ailleurs.test>",
            "email_to": "owner@exemple.test",
            "email_cc": "",
            "subject": "Objet d'essai",
            "direction": "in",
            "source": "imap",
            "user_id": self.owner.id,
            "account_id": self.account.id,
            "body_preview": "Corps d'essai",
            "raw_headers": "From: expediteur@ailleurs.test",
        }
        vals.update(overrides)
        vals.setdefault(
            "message_id_header",
            "<%s@test.invalid>" % abs(hash(tuple(sorted(
                (k, str(v)) for k, v in vals.items())))),
        )
        return self.env["bf.email"].sudo().create(vals)

    def _ctx(self):
        return self.env["bf.email"]._rule_owner_context(self.owner)

    # ------------------------------------------------------------------
    # Conditions
    # ------------------------------------------------------------------
    def test_all_requires_every_condition(self):
        rule = self._rule([
            {"field_name": "subject", "operator": "contains", "value": "devis"},
            {"field_name": "has_attachments", "operator": "is_true"},
        ])
        no_attachment = self._email(subject="Votre devis")
        self.assertFalse(rule._match(no_attachment, self._ctx()))
        with_attachment = self._email(
            subject="Votre devis", has_attachments=True, attachment_count=1)
        self.assertTrue(rule._match(with_attachment, self._ctx()))

    def test_any_takes_the_first_that_holds(self):
        rule = self._rule([
            {"field_name": "subject", "operator": "contains", "value": "devis"},
            {"field_name": "subject", "operator": "contains", "value": "facture"},
        ], match_type="any")
        self.assertTrue(rule._match(self._email(subject="Une facture"),
                                    self._ctx()))
        self.assertFalse(rule._match(self._email(subject="Bonjour"),
                                     self._ctx()))

    def test_exception_cancels_the_rule(self):
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "devis"}],
            exceptions=[{"field_name": "email_from", "operator": "contains",
                         "value": "patron@exemple.test"}],
        )
        self.assertTrue(rule._match(self._email(subject="devis"), self._ctx()))
        from_boss = self._email(
            subject="devis", email_from="Le patron <patron@exemple.test>")
        self.assertFalse(rule._match(from_boss, self._ctx()))

    def test_a_rule_without_condition_never_fires(self):
        """The other reading — « no condition means everything » — would turn a
        half-written rule into a mailbox-wide action."""
        rule = self._rule([], set_handled=True)
        self.assertFalse(rule._match(self._email(), self._ctx()))

    def test_cc_only_is_the_shipped_example(self):
        """« Je suis seulement en c.c. » — the case the task itself cites."""
        rule = self._rule([{"field_name": "is_cc_to_me", "operator": "is_true"}])
        addressed = self._email(email_to="owner@exemple.test", email_cc="")
        self.assertFalse(rule._match(addressed, self._ctx()))
        cc_only = self._email(
            email_to="quelquun@ailleurs.test", email_cc="owner@exemple.test")
        self.assertTrue(rule._match(cc_only, self._ctx()))
        # Being in BOTH is not « only in cc ».
        both = self._email(
            email_to="owner@exemple.test", email_cc="owner@exemple.test")
        self.assertFalse(rule._match(both, self._ctx()))

    def test_address_pattern_matches_a_full_from_header(self):
        """`email_from` holds the raw header, so a `^…@` anchor never fires —
        the bug the shipped noreply rule carried before 18.0.9.11.0."""
        rule = self._rule([{
            "field_name": "email_from", "operator": "regex",
            "value": r"(?:^|[<\s:,;])(noreply|no-reply)@",
        }])
        self.assertTrue(rule._match(
            self._email(email_from='"Acme" <noreply@acme.test>'), self._ctx()))
        self.assertTrue(rule._match(
            self._email(email_from="noreply@acme.test"), self._ctx()))
        self.assertFalse(rule._match(
            self._email(email_from="vrai.humain@acme.test"), self._ctx()))

    def test_header_condition_reads_folded_continuation_lines(self):
        headers = (
            "From: expediteur@ailleurs.test\n"
            "Content-Type: multipart/mixed;\n"
            '\tboundary="frontiere"\n'
            "List-Unsubscribe: <mailto:stop@acme.test>\n"
        )
        record = self._email(raw_headers=headers)
        folded = self._rule([{
            "field_name": "header", "header_name": "Content-Type",
            "operator": "contains", "value": "frontiere",
        }])
        self.assertTrue(folded._match(record, self._ctx()))
        present = self._rule([{
            "field_name": "header", "header_name": "List-Unsubscribe",
            "operator": "is_set",
        }])
        self.assertTrue(present._match(record, self._ctx()))
        absent = self._rule([{
            "field_name": "header", "header_name": "X-Absent",
            "operator": "is_set",
        }])
        self.assertFalse(absent._match(record, self._ctx()))

    def test_contains_any_splits_on_commas_and_newlines(self):
        rule = self._rule([{
            "field_name": "subject", "operator": "contains_any",
            "value": "facture, invoice\nreçu",
        }])
        for subject in ("Votre facture", "Your invoice", "Un reçu"):
            self.assertTrue(rule._match(self._email(subject=subject),
                                        self._ctx()), subject)
        self.assertFalse(rule._match(self._email(subject="Bonjour"),
                                     self._ctx()))

    def test_a_broken_clause_does_not_stop_ingestion(self):
        """A clause that raises must answer « no », not take the message down."""
        rule = self._rule([{
            "field_name": "partner_field", "operator": "expr",
            "value": "p.champ_qui_nexiste_pas",
        }])
        self.assertFalse(rule._match(self._email(), self._ctx()))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def test_first_rule_to_claim_a_target_wins(self):
        self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "essai"}],
            name="Précise", sequence=5, set_category="client")
        self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "essai"}],
            name="Générique", sequence=50, set_category="marketing",
            set_priority="1")
        row = self._email(subject="Un essai")
        self.assertEqual(row.category, "client")
        # The later rule still gets to set what the earlier one left alone.
        self.assertEqual(row.priority, "1")

    def test_stop_processing_cuts_the_walk(self):
        self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "stop"}],
            name="Barrage", sequence=5, set_category="internal",
            stop_processing=True)
        self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "stop"}],
            name="Jamais atteinte", sequence=50, set_priority="3")
        row = self._email(subject="stop ici")
        self.assertEqual(row.category, "internal")
        self.assertEqual(row.priority, "0")

    def test_mark_unread_and_out_of_the_box(self):
        self._rule(
            [{"field_name": "is_cc_to_me", "operator": "is_true"}],
            set_status="new", set_handled=True, set_category="notification")
        row = self._email(
            email_to="quelquun@ailleurs.test", email_cc="owner@exemple.test")
        self.assertEqual(row.status, "new")
        self.assertTrue(row.is_handled)
        self.assertEqual(row.category, "notification")

    def test_route_to_a_colleague_moves_the_row(self):
        self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "pour toi"}],
            route_user_id=self.other.id)
        row = self._email(subject="C'est pour toi")
        self.assertEqual(row.user_id, self.other)

    def test_folder_placeholders_expand_from_the_message_date(self):
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "x"}],
            set_folder="Archives/{YYYY}/{MM}")
        row = self._email(subject="x", date="2026-02-14 12:00:00")
        self.assertEqual(rule._resolve_folder(row), "Archives/2026/02")

    def test_company_rules_apply_to_everyone(self):
        company_rule = self.env["bf.email.rule"].sudo().create({
            "name": "Règle d'organisation",
            "scope": "company",
            "user_id": False,
            "company_id": self.env.company.id,
            "condition_ids": [(0, 0, {
                "kind": "condition", "field_name": "subject",
                "operator": "contains", "value": "toute la boîte",
            })],
            "set_category": "internal",
        })
        self.assertFalse(company_rule.user_id)
        row = self._email(subject="toute la boîte")
        self.assertEqual(row.category, "internal")

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    def test_a_broken_regex_is_refused_at_save(self):
        with self.assertRaises(ValidationError):
            self._rule([{"field_name": "subject", "operator": "regex",
                         "value": "([unclosed"}])

    def test_an_operator_that_makes_no_sense_is_refused(self):
        with self.assertRaises(ValidationError):
            self._rule([{"field_name": "is_bulk", "operator": "contains",
                         "value": "oui"}])

    def test_a_condition_needing_a_value_is_refused_without_one(self):
        with self.assertRaises(ValidationError):
            self._rule([{"field_name": "subject", "operator": "contains"}])

    def test_an_external_forward_needs_the_explicit_tick(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.internal_domains", "exemple.test")
        with self.assertRaises(ValidationError):
            self._rule(
                [{"field_name": "subject", "operator": "contains", "value": "x"}],
                forward_to="dehors@ailleurs.test")

    def test_forward_refuses_a_message_it_already_forwarded(self):
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "x"}],
            forward_to="collegue@exemple.test")
        uuid = self.env["ir.config_parameter"].sudo().get_param("database.uuid")
        looped = self._email(
            subject="x",
            raw_headers="X-BF-Forwarded-By: %s\nX-BF-Forward-Hops: 1" % uuid)
        self.assertIn("boucle", rule._forward_blocked_reason(looped))

    def test_forward_refuses_an_automatic_message(self):
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "x"}],
            forward_to="collegue@exemple.test")
        vacation = self._email(
            subject="x", raw_headers="Auto-Submitted: auto-replied")
        self.assertIn("Auto-Submitted",
                      rule._forward_blocked_reason(vacation))

    def test_forward_refuses_an_outgoing_message_by_default(self):
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "x"}],
            forward_to="collegue@exemple.test")
        sent = self._email(subject="x", direction="out")
        self.assertEqual(rule._forward_blocked_reason(sent),
                         "courriel sortant")

    def test_replaying_rules_never_forwards(self):
        """Re-running a rule over an archive must not put old mail on the wire."""
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "rejeu"}],
            forward_to="collegue@exemple.test")
        row = self._email(subject="rejeu")
        before = self.env["bf.email.auto.log"].sudo().search_count(
            [("rule_id", "=", rule.id)])
        row._apply_rules(allow_outbound=False)
        after = self.env["bf.email.auto.log"].sudo().search_count(
            [("rule_id", "=", rule.id)])
        self.assertEqual(before, after)

    def test_a_person_cannot_hand_themselves_a_company_wide_rule(self):
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "x"}])
        with self.assertRaises(ValidationError):
            rule.with_user(self.owner).write(
                {"scope": "company", "user_id": False})

    # ------------------------------------------------------------------
    # Appliquer une règle après coup
    #
    # Le moteur ne tourne qu'à la création d'une ligne. Une règle écrite ce
    # soir ne dit donc rien du courrier arrivé ce matin, et c'est exactement
    # ce que personne ne devine : la règle est juste, la boîte ne bouge pas.
    # ------------------------------------------------------------------
    def test_applying_one_rule_leaves_the_other_rules_alone(self):
        """« Appliquer cette règle » applique celle-là, pas les douze autres."""
        row = self._email(subject="devis 2026")
        precise = self._rule(
            [{"field_name": "subject", "operator": "contains",
              "value": "devis"}],
            name="Précise", sequence=5, set_category="client")
        self._rule(
            [{"field_name": "subject", "operator": "contains",
              "value": "devis"}],
            name="Générique", sequence=50, set_priority="3")

        precise.action_apply_this_rule()
        row.invalidate_recordset()
        self.assertEqual(row.category, "client")
        self.assertEqual(row.priority, "0",
                         "l'autre règle n'avait pas été demandée")

    def test_applying_a_rule_reaches_mail_already_out_of_the_box(self):
        """Le rejeu ne regarde que la boîte.

        Une règle qui se contente de ranger ou de reclasser — dossier,
        catégorie, priorité, sans « sortir de la boîte » — ne pouvait donc
        rattraper strictement rien après coup.
        """
        row = self._email(subject="rapport trimestriel", is_handled=True)
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains",
              "value": "rapport"}],
            set_category="internal", set_priority="1")

        self.env["bf.email.rule"].with_user(self.owner).action_replay_rules()
        row.invalidate_recordset()
        self.assertEqual(row.priority, "0",
                         "le rejeu saute ce qui est déjà traité")

        rule.action_apply_this_rule()
        row.invalidate_recordset()
        self.assertEqual(row.priority, "1")
        self.assertEqual(row.category, "internal")

    def test_the_dry_run_and_the_apply_see_the_same_messages(self):
        """« Essayer » n'est un aperçu honnête que s'il montre la même liste."""
        row = self._email(subject="pareil des deux côtés")
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains",
              "value": "pareil"}], set_category="internal")
        preview = rule.action_test_rule()["domain"][0][2]
        self.assertIn(row.id, preview)
        self.assertEqual(preview, rule._matching_rows().ids)

    def test_applying_a_rule_never_forwards(self):
        """Rattraper d'anciens courriels ne les remet pas sur le fil."""
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains",
              "value": "rattrapage"}],
            forward_to="collegue@exemple.test")
        self._email(subject="rattrapage")
        before = self.env["bf.email.auto.log"].sudo().search_count(
            [("rule_id", "=", rule.id)])
        rule.action_apply_this_rule()
        after = self.env["bf.email.auto.log"].sudo().search_count(
            [("rule_id", "=", rule.id)])
        self.assertEqual(before, after)

    def test_a_rule_cannot_be_applied_to_someone_elses_box(self):
        """L'administrateur courriel LIT toutes les règles.

        En appliquer une écrit dans une boîte qu'il n'a pas le droit
        d'écrire : le bouton refuse avant, plutôt que de lever un AccessError
        à mi-chemin avec la moitié des lignes classées.
        """
        self.other.write({"groups_id": [(4, self.env.ref(
            "bf_email_management.group_email_admin").id)]})
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "x"}])
        with self.assertRaises(UserError):
            rule.with_user(self.other).action_apply_this_rule()

    def test_a_rule_does_not_move_a_date_that_already_happened(self):
        """``handled_at`` est le moment où la ligne est sortie de la boîte.

        Repasser une règle dessus ne doit pas déplacer cette date.
        """
        row = self._email(subject="déjà sorti")
        row.write({"is_handled": True, "handled_at": "2026-01-01 00:00:00"})
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains",
              "value": "déjà sorti"}], set_handled=True)
        rule.action_apply_this_rule()
        row.invalidate_recordset()
        self.assertEqual(str(row.handled_at), "2026-01-01 00:00:00")

    def test_a_selection_can_be_pushed_back_through_the_engine(self):
        """La sortie de secours depuis la liste : quelques lignes, à la main."""
        row = self._email(subject="action de masse")
        self._rule(
            [{"field_name": "subject", "operator": "contains",
              "value": "masse"}], set_category="internal")
        row.with_user(self.owner).action_apply_rules_now()
        row.invalidate_recordset()
        self.assertEqual(row.category, "internal")

    # ------------------------------------------------------------------
    # Ce que la règle annonce, et ce qu'elle peut tenir
    # ------------------------------------------------------------------
    def test_action_summary_names_every_action(self):
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "x"}],
            set_category="client", set_priority="2",
            set_folder="Comptabilité", set_handled=True)
        summary = rule.action_summary
        for fragment in ("catégorie", "priorité", "Comptabilité",
                         "sort de la boîte"):
            self.assertIn(fragment, summary)

    def test_a_folder_rule_names_the_account_that_cannot_honour_it(self):
        """Sans réécriture IMAP, « déplacer vers le dossier » ne fait rien.

        Cela ne se voyait que dans le journal, que personne ne lit.
        """
        self.account.writeback_archive = False
        rule = self._rule(
            [{"field_name": "subject", "operator": "contains", "value": "x"}],
            set_folder="Comptabilité")
        self.assertEqual(rule.folder_gap_accounts, self.account.name)

        self.account.writeback_archive = True
        rule.invalidate_recordset(["folder_gap_accounts"])
        self.assertFalse(rule.folder_gap_accounts)

    def test_the_sweep_refiles_where_the_rule_asked(self):
        """Le balayage de rattrapage envoyait tout aux archives.

        Une ligne qu'une règle destinait à « Comptabilité » finissait donc
        dans ``Archives/{YYYY}`` — sans contredire la règle à voix haute.
        """
        self.account.writeback_archive = False  # pas d'IMAP à la création
        self._rule(
            [{"field_name": "subject", "operator": "contains",
              "value": "facture"}], set_folder="Comptabilité")
        row = self._email(subject="facture 12")

        calls = []
        BfEmail = type(self.env["bf.email"])
        with patch.object(
            BfEmail, "_imap_writeback_move",
            lambda recs, folder, account=None: calls.append(
                (folder, recs.ids, account)),
        ):
            row._imap_writeback_where_rules_asked()
        self.assertEqual(calls, [("Comptabilité", row.ids, None)])

    def test_the_sweep_still_archives_what_no_rule_claims(self):
        """Aucune règle ne parle de cette ligne : archives, comme avant."""
        self.account.writeback_archive = False
        row = self._email(subject="rien de particulier")

        calls = []
        BfEmail = type(self.env["bf.email"])
        with patch.object(
            BfEmail, "_imap_writeback_move",
            lambda recs, folder, account=None: calls.append(
                (folder, recs.ids, account)),
        ):
            row._imap_writeback_where_rules_asked()
        self.assertEqual(calls, [(None, row.ids, None)])

    # ------------------------------------------------------------------
    # Recipes
    # ------------------------------------------------------------------
    def test_every_recipe_in_the_catalogue_builds_a_valid_rule(self):
        """The gallery is only useful if every entry survives its own
        constraints — a recipe that raises is a dead button."""
        from odoo.addons.bf_email_management.models.bf_email_rule import (
            RULE_RECIPES,
        )
        Rule = self.env["bf.email.rule"].sudo()
        for recipe in RULE_RECIPES:
            vals = Rule._recipe_to_vals(recipe, user=self.other)
            rule = Rule.create(vals)
            self.assertTrue(rule.condition_ids, recipe["key"])
            self.assertEqual(rule.recipe_key, recipe["key"])
            self.assertTrue(rule.condition_summary)
