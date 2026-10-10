"""System emails in the brand (18.0.3.26.0).

Odoo's "light" layout is named in Python by the security notices, the 2FA code
and invitation, and some twenty modules; the password reset and the new device
alert are self-contained QWeb views. None goes through a mail.template the hook
could rewrite. Each test renders through the path Odoo really uses, because the
three paths do not hand the layout the same values: `_render_encapsulate` gives
only `message`, `model_description` and `company`, so one name the layout reads
outside that set is a KeyError on every security notice.
"""

from lxml import etree
from markupsafe import Markup

from odoo.tests import TransactionCase, tagged

from odoo.addons.bluefox_branding.hooks import post_init_hook

LIGHT = 'mail.mail_notification_light'


@tagged('post_install', '-at_install')
class TestCourrielsSysteme(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, mail_notify_force_send=False))
        cls.company = cls.env.company
        cls.company.write({'email': 'bonjour@societe.test'})
        cls.user = cls.env['res.users'].create({
            'name': 'Essai Courriel', 'login': 'essai.courriel', 'email': 'essai@courriel.test',
            'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)],
            'groups_id': [(6, 0, [cls.env.ref('base.group_portal').id])],
        })
        actives = dict(cls.env['res.lang'].get_installed())
        cls.fr = 'fr_CA' if 'fr_CA' in actives else None

    def _assert_branded(self, html):
        self.assertIn('/brand/logo/%d/brand' % self.company.id, html)
        self.assertIn('background-color:#F8FAFC', html)
        self.assertNotIn('Verdana', html)
        self.assertNotIn('Powered by', html)

    def _last_mail(self, **domain):
        return self.env['mail.mail'].search([(k, '=', v) for k, v in domain.items()],
                                            order='id desc', limit=1)

    # -- The light layout, by its three paths --------------------------------

    def test_light_layout_minimal_context(self):
        """`_render_encapsulate`: the security notice path, the leanest one."""
        html = self.env['mail.render.mixin']._render_encapsulate(LIGHT, Markup('<p>CORPS-ESSAI</p>'), add_context={
            'message': self.env['mail.message'].sudo().new(dict(body=Markup('<p>CORPS-ESSAI</p>'), record_name='X')),
            'model_description': 'Account',
            'company': self.company,
        })
        self._assert_branded(html)
        self.assertIn('CORPS-ESSAI', html)

    def test_light_layout_forced_on_send_mail(self):
        """`send_mail(email_layout_xmlid=...)`: the 2FA code and invitation path."""
        template = self.env['mail.template'].create({
            'name': 'Essai light', 'model_id': self.env.ref('base.model_res_partner').id,
            'subject': 'ESSAI-LIGHT', 'body_html': '<p>CORPS-LIGHT</p>', 'email_to': 'x@essai.test'})
        template.send_mail(self.user.partner_id.id, email_layout_xmlid=LIGHT)
        mail = self._last_mail(subject='ESSAI-LIGHT')
        self._assert_branded(mail.body_html)
        self.assertIn('CORPS-LIGHT', mail.body_html)

    def test_light_layout_on_notification(self):
        """`message_post(email_layout_xmlid=...)`: the module path, with its button."""
        recipient = self.env['res.partner'].create({'name': 'Destinataire', 'email': 'dest@essai.test'})
        # mail_post_defer (OCA) holds notifications 30 s for a cron unless told otherwise.
        self.user.partner_id.with_context(mail_defer_seconds=0).message_post(
            body=Markup('<p>CORPS-AVIS</p>'), subject='ESSAI-AVIS', partner_ids=recipient.ids,
            message_type='comment', subtype_xmlid='mail.mt_comment', email_layout_xmlid=LIGHT)
        mail = self._last_mail(subject='ESSAI-AVIS')
        self.assertTrue(mail, 'the notification email was not created')
        self._assert_branded(mail.body_html)
        self.assertIn('CORPS-AVIS', mail.body_html)

    def test_recruitment_anchor_still_found(self):
        """hr_recruitment extends the light layout with this xpath: it must still match."""
        arch = etree.fromstring(self.env.ref(LIGHT).get_combined_arch())
        self.assertTrue(arch.xpath("//t//table[@role='presentation']"))

    # -- The security notice -------------------------------------------------

    def _security_notice(self, user, **kwargs):
        user._notify_security_setting_update('ESSAI-SECURITE', 'Your account login has been updated', **kwargs)
        return self._last_mail(subject='ESSAI-SECURITE')

    def test_security_notice(self):
        mail = self._security_notice(self.user)
        body = mail.body_html
        self._assert_branded(body)
        self.assertIn('Hello Essai Courriel', body)
        self.assertNotIn('Dear', body)
        self.assertNotIn('odoo.com', body)
        self.assertNotIn('Contact your administrator', body)
        self.assertIn('mailto:bonjour@societe.test', body)
        self.assertIn('/web/reset_password', body)
        if self.user._notify_security_setting_update_prepare_values('x').get('suggest_2fa'):
            # Same base as the password reset button.
            self.assertIn(self.user.get_base_url() + '/my/security', body)

    def test_security_notice_without_password_reset(self):
        """The email change notice suggests no reset: no button, no "Then:"."""
        body = self._security_notice(self.user, suggest_password_reset=False).body_html
        self.assertNotIn('/web/reset_password', body)
        self.assertNotIn('Then:', body)
        self.assertIn('If this was not you:', body)

    def test_security_notice_without_company_email(self):
        self.company.email = False
        body = self._security_notice(self.user).body_html
        self.assertIn('contact your administrator', body)

    def test_security_notice_in_french(self):
        if not self.fr:
            self.skipTest('fr_CA must be active')
        self.env['ir.module.module']._load_module_terms(['bluefox_branding'], [self.fr], overwrite=True)
        mail = self._security_notice(self.user.with_context(lang=self.fr))
        self.assertIn('Bonjour', mail.body_html)
        self.assertIn("Si c'est vous, vous n'avez rien à faire.", mail.body_html)

    # -- Self-contained views ------------------------------------------------

    def _render_view(self, xmlid, add_context=None):
        return self.env['mail.render.mixin']._render_template(
            self.env.ref(xmlid), 'res.users', self.user.ids, engine='qweb_view',
            add_context=add_context, options={'post_process': True})[self.user.id]

    def test_password_reset_view(self):
        self.user.partner_id.signup_prepare(signup_type='reset')
        html = self._render_view('auth_signup.reset_password_email')
        self._assert_branded(html)
        self.assertIn('Choose a new password', html)
        self.assertIn('reset_password', html)

    def test_new_device_view(self):
        html = self._render_view('auth_signup.alert_login_new_device', self.user._prepare_new_device_notice_values())
        self._assert_branded(html)
        self.assertIn('A new device was used', html)
        self.assertIn('mailto:bonjour@societe.test', html)

    # -- Access templates: a tenant's hand edit is kept ----------------------

    def _langs(self):
        return [code for code, _name in self.env['res.lang'].get_installed()]

    def _age(self, template):
        self.env.cr.execute("UPDATE mail_template SET create_date = now() - interval '30 days' WHERE id = %s",
                            [template.id])
        template.invalidate_recordset(['create_date'])

    def test_hand_edited_template_is_kept(self):
        template = self.env.ref('auth_signup.mail_template_user_signup_account_created')
        for lang in self._langs():
            template.with_context(lang=lang).body_html = '<p>RETOUCHE-LOCATAIRE</p>'
        self._age(template)
        post_init_hook(self.env)
        for lang in self._langs():
            self.assertIn('RETOUCHE-LOCATAIRE', template.with_context(lang=lang).body_html, lang)

    def test_untouched_template_is_branded(self):
        template = self.env.ref('auth_signup.set_password_email')
        post_init_hook(self.env)
        self.assertIn('brand_primary', template.body_html)
        self.assertEqual(template.email_layout_xmlid, 'bluefox_branding.bf_mail_layout')
        # A later pass rewrites what an earlier one wrote: it carries our colour variable.
        self._age(template)
        for lang in self._langs():
            template.with_context(lang=lang).body_html = '<t t-set="brand_primary" t-value="1"/><p>ANCIENNE-VERSION</p>'
        post_init_hook(self.env)
        for lang in self._langs():
            self.assertNotIn('ANCIENNE-VERSION', template.with_context(lang=lang).body_html, lang)

    # -- The 18.0.3.26.0 migration rewrites its own templates only -----------

    def test_targeted_replay_leaves_other_overrides_alone(self):
        """Replaying every override would wipe a tenant's edit of the portal invitation."""
        portal = self.env.ref('portal.mail_template_data_portal_welcome')
        for lang in self._langs():
            portal.with_context(lang=lang).body_html = '<p>RETOUCHE-PORTAIL</p>'
        post_init_hook(self.env, only={'auth_signup.set_password_email'})
        for lang in self._langs():
            self.assertIn('RETOUCHE-PORTAIL', portal.with_context(lang=lang).body_html, lang)
        self.assertIn('brand_primary', self.env.ref('auth_signup.set_password_email').body_html)

    def test_calendar_invitation_has_one_card(self):
        """The invitation goes out through the light layout: its own shell made a card in the card."""
        post_init_hook(self.env, only={'calendar.calendar_template_meeting_invitation'})
        guest = self.env['res.partner'].create({'name': 'Invite Essai', 'email': 'invite@essai.test'})
        event = self.env['calendar.event'].with_context(mail_defer_seconds=0).create({
            'name': 'ESSAI-AGENDA', 'start': '2030-01-15 14:00:00', 'stop': '2030-01-15 15:00:00',
            'partner_ids': [(6, 0, guest.ids)]})
        attendee = event.attendee_ids.filtered(lambda a: a.partner_id == guest)
        attendee._send_mail_to_attendees(self.env.ref('calendar.calendar_template_meeting_invitation'))
        mail = self.env['mail.mail'].search([('recipient_ids', 'in', guest.ids)], order='id desc', limit=1)
        self.assertTrue(mail, 'no invitation email')
        self.assertEqual(mail.body_html.count('box-shadow:0 4px 24px'), 1)
        self.assertEqual(mail.body_html.count('/brand/logo/'), 1)

    def test_unfollow_link_kept_for_odoo_to_personalise(self):
        html = self.env['mail.render.mixin']._render_encapsulate(LIGHT, Markup('<p>x</p>'), add_context={
            'message': self.env['mail.message'].sudo().new(dict(body=Markup('<p>x</p>'), record_name='X')),
            'model_description': 'Account', 'company': self.company})
        self.assertIn('<span id="mail_unfollow"><a href="', html)
