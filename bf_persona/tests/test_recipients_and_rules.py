from odoo.tests import Form, tagged

from .common import PersonaCase


@tagged("post_install", "-at_install")
class TestRecipientsAndRules(PersonaCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.p_eli = cls.Persona.create({
            "partner_id": cls.eli.id, "addressing_style": "tu",
            "preferred_salutation": "Bonjour Ada",
        })
        cls.p_jul = cls.Persona.create({"partner_id": cls.jul.id, "addressing_style": "vous"})
        cls.Rule = cls.env["contact.cc.rule"]

    # --- whose persona -----------------------------------------------------

    def test_banner_follows_the_recipient_not_the_record_contact(self):
        # The composer is opened on a record whose contact is Bruno; the
        # message goes to Ada. Ada's persona is the one to show.
        composer = self.composer([self.eli], res_id=self.jul.id)
        hint = str(composer.persona_hint_html)
        self.assertIn("Ada Exemple", hint)
        self.assertNotIn("Bruno Exemple", hint)

    def test_banner_is_computed_while_the_composer_is_filled_in(self):
        # The browser computes the banner by onchange on a record that does not
        # exist yet; creating the composer directly does not exercise that path.
        self.Rule.create({"persona_id": self.p_eli.id, "cc_partner_ids": [(6, 0, self.hm.ids)], "mandatory": True})
        composer = Form(self.env["mail.compose.message"].with_context(
            default_model="res.partner", default_res_ids=[self.jul.id],
            default_composition_mode="comment", default_partner_ids=[self.eli.id],
        ))
        hint = str(composer.persona_hint_html)
        self.assertIn("Ada Exemple", hint)
        self.assertIn("Copie obligatoire manquante", hint)
        composer.partner_cc_ids.add(self.hm)
        self.assertNotIn("Copie obligatoire manquante", str(composer.persona_hint_html))

    def test_every_recipient_in_to_and_cc_gets_a_line(self):
        hint = str(self.composer([self.eli], cc=[self.jul]).persona_hint_html)
        self.assertIn("<b>Ada Exemple</b>", hint)
        self.assertIn("<b>Bruno Exemple</b>", hint)

    def test_company_persona_covers_its_people(self):
        company = self.Persona.create({"partner_id": self.client.id, "custom_appellations": "Pièces jointes, pas de liens"})
        other = self.Partner.create({"name": "Nora Exemple", "email": "nora@exemple.example", "parent_id": self.client.id})
        hint = str(self.composer([other]).persona_hint_html)
        self.assertIn("Pièces jointes, pas de liens", hint)
        self.assertTrue(company)

    def test_no_recipient_falls_back_to_the_record_contact(self):
        hint = str(self.composer([], res_id=self.eli.id).persona_hint_html)
        self.assertIn("Ada Exemple", hint)

    # --- copy rules ---------------------------------------------------------

    def test_mandatory_copy_missing_then_added_in_cc(self):
        self.Rule.create({"persona_id": self.p_eli.id, "cc_partner_ids": [(6, 0, self.hm.ids)], "mandatory": True})
        composer = self.composer([self.eli])
        self.assertIn("Copie obligatoire manquante :</b> Clara Exemple", str(composer.persona_hint_html))
        composer.action_apply_persona()
        self.assertIn(self.hm, composer.partner_cc_ids)
        self.assertNotIn(self.hm, composer.partner_ids)
        self.assertNotIn("Copie obligatoire manquante", str(composer.persona_hint_html))

    def test_someone_already_in_cc_or_to_is_not_missing(self):
        self.Rule.create({"persona_id": self.p_eli.id, "cc_partner_ids": [(6, 0, self.hm.ids)], "mandatory": True})
        self.assertNotIn("manquante", str(self.composer([self.eli], cc=[self.hm]).persona_hint_html))
        self.assertNotIn("manquante", str(self.composer([self.eli, self.hm]).persona_hint_html))

    def test_rule_applies_whatever_the_category(self):
        category = self.env.ref("bf_persona.category_billing")
        self.Rule.create({
            "persona_id": self.p_eli.id, "cc_partner_ids": [(6, 0, self.hm.ids)],
            "mandatory": True, "category_id": category.id,
        })
        self.assertIn("manquante", str(self.composer([self.eli]).persona_hint_html))

    def test_non_mandatory_rule_is_only_proposed(self):
        self.Rule.create({"persona_id": self.p_eli.id, "cc_partner_ids": [(6, 0, self.hm.ids)]})
        composer = self.composer([self.eli])
        self.assertIn("Habituellement inclus :</b> Clara Exemple", str(composer.persona_hint_html))
        composer.action_apply_persona()
        self.assertNotIn(self.hm, composer.partner_cc_ids | composer.partner_ids)

    def test_never_rule_warns_and_removes(self):
        self.Rule.create({"persona_id": self.p_eli.id, "rule_type": "never", "cc_partner_ids": [(6, 0, self.hm.ids)]})
        composer = self.composer([self.eli], cc=[self.hm])
        self.assertIn("Ne pas mettre en copie :</b> Clara Exemple", str(composer.persona_hint_html))
        composer.action_apply_persona()
        self.assertNotIn(self.hm, composer.partner_cc_ids | composer.partner_ids | composer.partner_bcc_ids)
        self.assertNotIn(self.eli, composer.partner_cc_ids)
        self.assertIn(self.eli, composer.partner_ids)

    def test_suggested_and_rejected_rules_do_nothing(self):
        suggested = self.Rule.create({
            "persona_id": self.p_eli.id, "cc_partner_ids": [(6, 0, self.hm.ids)],
            "mandatory": True, "state": "suggested",
        })
        self.assertNotIn("Clara", str(self.composer([self.eli]).persona_hint_html))
        suggested.action_reject()
        self.assertNotIn("Clara", str(self.composer([self.eli]).persona_hint_html))
        suggested.action_confirm()
        self.assertIn("Clara", str(self.composer([self.eli]).persona_hint_html))

    def test_salutation_left_alone_with_several_recipients(self):
        composer = self.composer([self.eli, self.jul], body="<p>Texte.</p>")
        composer.action_apply_persona()
        self.assertNotIn("Bonjour Ada", str(composer.body))
