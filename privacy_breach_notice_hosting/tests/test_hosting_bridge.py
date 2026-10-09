import unittest

from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, tagged

from odoo.addons.privacy_breach_notice.tests.common import BreachNoticeCase


@tagged("post_install", "-at_install")
class TestHostingBridge(BreachNoticeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager.write({"groups_id": [(4, cls.env.ref("hosting_management.group_hosting_manager").id)]})
        template = cls.env["hosting.service"].search([], limit=1)
        if not template:
            raise unittest.SkipTest("aucun service d'hébergement de référence dans cette base")
        cls.service = template.copy({"name": "Nextcloud du client", "partner_id": cls.rprp.id})
        cls.event = cls.env["hosting.security.event"].with_user(cls.manager).create({
            "name": "Mot de passe CardDAV exposé", "event_type": "credential_leak",
            "description": "<p>Un module l'écrivait dans un journal.</p>",
            "resolution": "<p>Mot de passe changé.</p>",
            "service_ids": [(6, 0, cls.service.ids)]})

    def test_le_non_se_motive_aussi(self):
        with self.assertRaises(ValidationError):
            self.event.privacy_assessment = "no"
        self.event.write({"privacy_assessment": "no", "privacy_rationale": "Le compte ne lit que l'agenda de l'hébergeur."})
        self.assertEqual(self.event.privacy_assessment, "no")

    def test_organisations_proposees_depuis_les_services(self):
        with Form(self.event.with_user(self.manager)) as form:
            form.privacy_assessment = "yes"
            form.privacy_rationale = "Les contacts du client étaient lisibles."
        self.assertEqual(self.event.privacy_partner_ids, self.org,
                         "l'organisation, pas le contact rattaché au service")

    def test_preparer_un_avis_par_organisation(self):
        with self.assertRaises(UserError):
            self.event.action_prepare_privacy_notices()
        self.event.write({"privacy_assessment": "yes", "privacy_rationale": "Contacts lisibles.",
                          "privacy_partner_ids": [(6, 0, self.org.ids)]})
        self.event.action_prepare_privacy_notices()
        self.event.action_prepare_privacy_notices()
        notice = self.event.privacy_notice_ids
        self.assertEqual(len(notice), 1, "un seul avis par organisation, même cliqué deux fois")
        self.assertEqual(notice.state, "draft")
        self.assertEqual(notice.officer_email, "rprp@client-essai.invalid")
        self.assertIn("Mot de passe CardDAV exposé", notice.circumstances)
        self.assertIn("journal", notice.circumstances)
        self.assertEqual(notice.measures_taken, "Mot de passe changé.")
        self.assertEqual(notice.discovered_at, self.event.event_date)

    def test_un_technicien_sans_role_lit_l_evenement(self):
        """Lecture de tous les champs (export, RPC sans liste) : les avis lui sont simplement cachés."""
        technicien = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Technicien hébergement", "login": "tech-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("hosting_management.group_hosting_manager").id])]})
        self.assertFalse(technicien.has_group("privacy_consent.group_privacy_user"))
        valeurs = self.event.with_user(technicien).read()[0]
        self.assertNotIn("privacy_notice_ids", valeurs)
