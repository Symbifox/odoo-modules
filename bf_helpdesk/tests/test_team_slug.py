from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_slug")
class TestTeamSlug(TransactionCase):
    """Slug auto-generation, uniqueness, public_form_url compute, opt-in fields."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Team = cls.env["helpdesk.ticket.team"]
        cls.IModelFields = cls.env["ir.model.fields"]

    def test_slugify_basic(self):
        self.assertEqual(self.Team._slugify("Acme Support"), "acme-support")
        self.assertEqual(self.Team._slugify("Acme — Support"), "acme-support")
        self.assertEqual(self.Team._slugify("Café & Co"), "caf-co")
        self.assertEqual(self.Team._slugify("  multi   spaces  "), "multi-spaces")
        self.assertFalse(self.Team._slugify(""))
        self.assertFalse(self.Team._slugify(None))

    def test_create_auto_slug(self):
        team = self.Team.create({"name": "Acme Corp Support"})
        self.assertEqual(team.slug, "acme-corp-support")

    def test_create_explicit_slug_preserved(self):
        team = self.Team.create({"name": "Acme Corp", "slug": "custom-slug"})
        self.assertEqual(team.slug, "custom-slug")

    def test_slug_uniqueness_collision(self):
        a = self.Team.create({"name": "Duplicate Team"})
        b = self.Team.create({"name": "Duplicate Team"})
        self.assertEqual(a.slug, "duplicate-team")
        # Second team gets suffix when slug conflict detected
        self.assertNotEqual(b.slug, a.slug)
        self.assertTrue(b.slug.startswith("duplicate-team-"))

    def test_public_form_url_disabled(self):
        team = self.Team.create({"name": "Off Team"})
        team.public_form_enabled = False
        self.assertFalse(team.public_form_url)

    def test_public_form_url_enabled(self):
        team = self.Team.create({"name": "On Team"})
        team.public_form_enabled = True
        # web.base.url is always set
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        self.assertEqual(team.public_form_url, f"{base}/support/on-team")

    def test_post_init_opted_in_fields(self):
        """Critical regression: bf_helpdesk's post_init must un-blacklist
        helpdesk.ticket fields for the website form builder.
        """
        FIELDS = ["name", "description", "partner_name",
                  "partner_email", "team_id", "channel_id", "attachment_ids"]
        rows = self.IModelFields.search([
            ("model", "=", "helpdesk.ticket"),
            ("name", "in", FIELDS),
        ])
        for row in rows:
            self.assertFalse(
                row.website_form_blacklisted,
                f"Field {row.name} on helpdesk.ticket must be opted-in for website forms",
            )
