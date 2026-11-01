import re

from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from odoo.addons.bf_property_access.tests.common import AccessCase


@tagged("post_install", "-at_install")
class TestAccessPrivacy(AccessCase):

    def test_purpose_notice_and_retention_are_declared(self):
        purpose = self.env.ref("bf_property_access_privacy.purpose_property_access_control")
        self.assertFalse(purpose.requires_consent)
        notice = self.env.ref("bf_property_access_privacy.notice_property_access_control")
        self.assertIn("biométrique", notice.body_fr)
        self.assertIn("biometric", notice.body_en)
        self.assertIn("tablette", notice.body_fr)
        self.assertIn("tablet", notice.body_en)
        policy = self.env.ref("bf_property_access_privacy.retention_property_access_control")
        self.assertEqual((policy.retention_days, policy.destruction_method), (90, "delete"))

    def test_declared_versus_applied_retention(self):
        self.org.access_log_retention_days = 90
        self.assertFalse(self.org.access_retention_warning)
        self.org.access_log_retention_days = 30
        self.assertIn("30", self.org.access_retention_warning)
        self.org.access_log_retention_days = 0
        self.assertIn("ne purge pas", self.org.access_retention_warning)

    def test_efvp_template_is_prefilled(self):
        self.make_point(credential_kind="card")
        self.make_point(name="Porte 101", point_type="unit_door", unit_id=self.unit1.id)
        self.own(self.alice, self.unit1)
        self.sync()
        self.org.access_log_retention_days = 30
        html = self.env["ir.actions.report"].with_user(self.manager)._render_qweb_html(
            "bf_property_access_privacy.report_access_efvp", self.org.ids)[0].decode()
        html = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))
        for attendu in ("Portier nord", "Porte 101", "1934", "Biométrie", "30 jours", "90 jours",
                        "Aucun connecteur", "art. 3.3", "diffèrent"):
            self.assertIn(attendu, html)
        self.assertNotIn("Alice Exemple", html, "le gabarit ne nomme personne")

    def test_efvp_needs_read_access_to_the_organisation(self):
        outsider = new_test_user(self.env, login="acces_efvp_sans", groups="base.group_user", tz="America/Toronto")
        with self.assertRaises(AccessError):
            self.org.with_user(outsider).action_print_access_efvp()
        action = self.org.with_user(self.concierge).action_print_access_efvp()
        self.assertEqual(action["report_name"], "bf_property_access_privacy.report_access_efvp")

    # Relecture 1 du lot 1 bis (2026-10-31)
    def test_r1_1_portal_reading_the_organisation_is_not_enough(self):
        portal = new_test_user(self.env, login="acces_efvp_portail", groups="base.group_portal",
                               tz="America/Toronto")
        with self.assertRaises(AccessError):
            self.org.with_user(portal)._efvp_values()

    def test_r1_3_notice_promises_only_what_is_true(self):
        notice = self.env.ref("bf_property_access_privacy.notice_property_access_control")
        fr = re.sub(r"\s+", " ", str(notice.body_fr))
        en = re.sub(r"\s+", " ", str(notice.body_en))
        self.assertIn("Le logiciel ne conserve aucun code", fr)
        self.assertIn("hors du Québec", fr)
        self.assertIn("outside Québec", en)
        self.assertNotIn("rien de plus", fr)

    def test_r1_4_declared_categories_cover_signals_end_reasons_and_notes(self):
        policy = self.env.ref("bf_property_access_privacy.retention_property_access_control")
        for mot in ("signal de départ", "motif de fin", "notes libres"):
            self.assertIn(mot, policy.data_categories)

    def test_r1_5_retention_gap_follows_the_policy_that_applies(self):
        policy = self.env.ref("bf_property_access_privacy.retention_property_access_control")
        org = self.org

        def regarde():
            org.invalidate_recordset(["access_retention_warning", "access_retention_declared"])
            return org.access_retention_warning

        org.access_log_retention_days = policy.retention_days
        self.assertFalse(regarde())
        policy.active = False
        self.assertIn("Aucune politique", regarde(), "politique archivée : rien n'est déclaré")
        policy.active = True
        # Une politique propre à la société de l'organisation l'emporte.
        autre = self.env["res.company"].create({"name": "Société B (banc)"})
        org.company_id = autre
        self.env["privacy.retention.policy"].create({
            "purpose_id": policy.purpose_id.id, "retention_days": 30, "company_id": autre.id})
        org.access_log_retention_days = 30
        self.assertFalse(regarde(), "30 déclarés pour sa société, 30 appliqués")
        self.assertEqual(org.access_retention_declared, 30)

    def test_r1_5_zero_declared_and_zero_applied_agree(self):
        policy = self.env.ref("bf_property_access_privacy.retention_property_access_control")
        policy.retention_days = 0
        self.org.access_log_retention_days = 0
        self.org.invalidate_recordset(["access_retention_warning"])
        self.assertFalse(self.org.access_retention_warning)

    def test_r1_10_buildings_without_points_are_listed(self):
        html = self.env["ir.actions.report"].with_user(self.manager)._render_qweb_html(
            "bf_property_access_privacy.report_access_efvp", self.org.ids)[0].decode()
        self.assertIn("Immeuble (banc)", html)

    # Relecture 2 du lot 1 bis (2026-10-31)
    def test_r2_1_an_organisation_of_another_company_finds_the_common_policy(self):
        policy = self.env.ref("bf_property_access_privacy.retention_property_access_control")
        self.assertFalse(policy.company_id, "politique commune à toutes les sociétés")
        self.assertFalse(self.env.ref("bf_property_access_privacy.purpose_property_access_control").company_id)
        autre = self.env["res.company"].create({"name": "Société C (banc)"})
        self.org.company_id = autre
        self.org.access_log_retention_days = policy.retention_days
        self.org.invalidate_recordset(["access_retention_warning", "access_retention_declared"])
        self.assertEqual(self.org.access_retention_declared, policy.retention_days)
        self.assertFalse(self.org.access_retention_warning)

    # Relecture 2 du lot 4 (2026-10-31) : les accès temporaires au registre
    def test_lot4_notice_and_efvp_cover_temporary_access(self):
        notice = self.env.ref("bf_property_access_privacy.notice_property_access_control")
        fr = re.sub(r"\s+", " ", str(notice.body_fr))
        self.assertIn("annoncez un visiteur", fr)
        summary = re.sub(r"\s+", " ", str(self.env.ref(
            "bf_property_access_privacy.purpose_property_access_control").plain_language_summary))
        self.assertIn("accès temporaire", summary)
        policy = self.env.ref("bf_property_access_privacy.retention_property_access_control")
        self.assertIn("Accès temporaires", policy.data_categories)
        html = self.env["ir.actions.report"].with_user(self.manager)._render_qweb_html(
            "bf_property_access_privacy.report_access_efvp", self.org.ids)[0].decode()
        html = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))
        self.assertIn("retiré à la fin de sa plage", html)
