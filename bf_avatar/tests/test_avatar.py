import base64

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..lib import render

PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="))


@tagged("post_install", "-at_install")
class TestAvatar(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env["ir.config_parameter"].sudo()
        cls.params.set_param("bf_avatar.style", "initials")
        cls.params.set_param("bf_avatar.palette", "#1f84af")

    def _raw(self, record):
        attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", record._name), ("res_field", "=", "image_1920"),
            ("res_id", "=", record.id)], limit=1)
        return attachment.with_context(bin_size=False).raw, attachment.mimetype

    def _set_style(self, style):
        settings = self.env["res.config.settings"].create({"bf_avatar_style": style})
        settings.execute()

    def test_new_user_gets_the_house_avatar(self):
        user = new_test_user(self.env, login="av_new", name="Mireille Fictive")
        raw, mimetype = self._raw(user.partner_id)
        self.assertEqual(mimetype, "image/svg+xml")
        self.assertIn(render.MARKER.encode(), raw)
        self.assertIn(b">M</text>", raw)

    def test_style_change_redraws_generated_and_spares_uploads(self):
        drawn = new_test_user(self.env, login="av_drawn", name="Alex Roy")
        photo = new_test_user(self.env, login="av_photo", name="Sam Gagnon")
        photo.image_1920 = PNG
        self._set_style("open_peeps")
        raw, _mimetype = self._raw(drawn.partner_id)
        self.assertIn(b'viewBox="0 0 704 704"', raw)
        raw, mimetype = self._raw(photo.partner_id)
        self.assertEqual(mimetype, "image/png")
        # Back to Odoo: the pass hands the generated ones back to Odoo's generator.
        self._set_style("odoo")
        raw, _mimetype = self._raw(drawn.partner_id)
        self.assertNotIn(render.MARKER.encode(), raw)
        self.assertTrue(render.is_generated_svg(raw))
        self.assertEqual(self._raw(photo.partner_id)[1], "image/png")

    def test_an_uploaded_svg_is_not_redrawn(self):
        user = new_test_user(self.env, login="av_svg", name="Logo Person")
        uploaded = b'<svg xmlns="http://www.w3.org/2000/svg"><circle cx="5" cy="5" r="4"/></svg>'
        user.partner_id.sudo().image_1920 = base64.b64encode(uploaded)
        self._set_style("notionists")
        raw, _mimetype = self._raw(user.partner_id)
        self.assertEqual(raw, uploaded)

    def test_contacts_get_an_avatar_companies_keep_their_icon(self):
        person = self.env["res.partner"].create({"name": "Julie Contact"})
        company = self.env["res.partner"].create({"name": "Acme", "is_company": True})
        self.assertIn(render.MARKER.encode(), base64.b64decode(person.avatar_128))
        self.assertNotIn(render.MARKER.encode(), base64.b64decode(company.avatar_128))
        self.params.set_param("bf_avatar.contacts", "False")
        person.invalidate_recordset(["avatar_128"])
        self.assertNotIn(render.MARKER.encode(), base64.b64decode(person.avatar_128))

    def test_composer_acts_on_the_caller_only(self):
        self.params.set_param("bf_avatar.style", "open_peeps")
        me = new_test_user(self.env, login="av_me", name="Me Myself", groups="base.group_user")
        other = new_test_user(self.env, login="av_other", name="Someone Else", groups="base.group_user")
        before_other = self._raw(other.partner_id)[0]
        Users = self.env["res.users"].with_user(me)
        data = Users.bf_avatar_composer()
        self.assertEqual(data["style"], "open_peeps")
        config = dict(data["config"], head="afro", face="smileBig")
        Users.bf_avatar_save(config)
        raw, mimetype = self._raw(me.partner_id)
        # Written with sudo: a plain user would have had the SVG stored as text.
        self.assertEqual(mimetype, "image/svg+xml")
        self.assertIn(render.MARKER.encode(), raw)
        self.assertEqual(me.sudo().bf_avatar_config["head"], "afro")
        self.assertEqual(self._raw(other.partner_id)[0], before_other)
        # A style change keeps the person's composition.
        self._set_style("open_peeps")
        self.assertEqual(self._raw(me.partner_id)[0], raw)
        Users.bf_avatar_reset()
        self.assertFalse(me.sudo().bf_avatar_config)
        self.assertNotEqual(self._raw(me.partner_id)[0], raw)

    def test_saved_composition_holds_only_known_values(self):
        self.params.set_param("bf_avatar.style", "open_peeps")
        me = new_test_user(self.env, login="av_hostile", name="Hostile Input", groups="base.group_user")
        Users = self.env["res.users"].with_user(me)
        Users.bf_avatar_save({"head": '"/><script>', "evil": "x", "colors": {"skin": "</svg>"}})
        stored = me.sudo().bf_avatar_config
        data = render.load_style("open_peeps")
        self.assertIn(stored["head"], data["slots"]["head"])
        self.assertNotIn("evil", stored)
        self.assertIn(stored["colors"]["skin"], data["colors"]["skin"])

    def test_composer_thumbnails_and_refusals(self):
        self.params.set_param("bf_avatar.style", "notionists")
        me = new_test_user(self.env, login="av_thumbs", name="Thumb Nail", groups="base.group_user")
        Users = self.env["res.users"].with_user(me)
        config = Users.bf_avatar_composer()["config"]
        thumbs = Users.bf_avatar_preview(config, "glasses")["thumbnails"]
        self.assertEqual(len(thumbs), 12)  # 11 glasses + none
        with self.assertRaises(UserError):
            Users.bf_avatar_preview(config, "../etc")
        portal = new_test_user(self.env, login="av_portal", name="Portal P", groups="base.group_portal")
        with self.assertRaises(UserError):
            self.env["res.users"].with_user(portal).bf_avatar_composer()
        self.params.set_param("bf_avatar.style", "initials")
        with self.assertRaises(UserError):
            Users.bf_avatar_composer()

    def test_employee_follows_the_composer(self):
        if "hr.employee" not in self.env:
            self.skipTest("hr is not installed")
        self.params.set_param("bf_avatar.style", "open_peeps")
        me = new_test_user(self.env, login="av_emp", name="Emma Ployee", groups="base.group_user")
        employee = self.env["hr.employee"].create({"name": "Emma Ployee", "user_id": me.id})
        photo_user = new_test_user(self.env, login="av_emp2", name="Paul Photo", groups="base.group_user")
        photo_employee = self.env["hr.employee"].create({"name": "Paul Photo", "user_id": photo_user.id})
        photo_employee.image_1920 = PNG
        Users = self.env["res.users"].with_user(me)
        Users.bf_avatar_save(dict(Users.bf_avatar_composer()["config"], head="turban"))
        self.assertEqual(self._raw(employee)[0], self._raw(me.partner_id)[0])
        Users2 = self.env["res.users"].with_user(photo_user)
        Users2.bf_avatar_save(Users2.bf_avatar_composer()["config"])
        self.assertEqual(self._raw(photo_employee)[1], "image/png")
