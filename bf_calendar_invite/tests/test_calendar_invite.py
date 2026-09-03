"""Tests for the EMAIL / SMS invitation helpers.

These walk the buttons the way a user does — open the composer from the action
the button returns, and look at what the composer actually ended up holding —
rather than asserting on the values we passed in. The .ics went missing in the
obvious implementation precisely because a compute overwrote a context default,
and a test that only checked the context would have stayed green.
"""

from odoo import Command
from odoo.tests import TransactionCase, tagged

# GSM-7 default alphabet plus the escaped extension characters. Anything else
# re-encodes the whole SMS as UCS-2, dropping the limit from 160 to 70.
GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
) | set("^{}\\[~]|€")


@tagged("post_install", "-at_install")
class TestCalendarInvite(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ `_activate_lang` bascule le drapeau `active` du `res.lang` et
        # RIEN d'autre : les `.po` d'un module ne sont importés qu'à son
        # installation, pour les langues actives à ce moment-là. Sur une base
        # neuve où seul l'anglais l'est — la CI en fabrique une par lot — le
        # corps sortait en anglais et les assertions de langue tombaient, alors
        # que le code visé était bon. Sur les bases où le français était déjà
        # installé, personne ne le voyait.
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_calendar_invite"], ["fr_CA"])
        cls.env.registry.clear_cache()
        cls.organiser = cls.env["res.users"].create({
            "name": "Organiser",
            "login": "bf_invite_organiser",
            "email": "organiser@example.com",
        })
        cls.guest = cls.env["res.partner"].create({
            "name": "Guest One",
            "email": "guest.one@example.com",
            "phone": "+15145550101",
        })
        cls.other_guest = cls.env["res.partner"].create({
            "name": "Guest Two",
            "email": "guest.two@example.com",
            "phone": "+15145550102",
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://odoo.example.com")

    def _make_event(self, partners, **values):
        vals = {
            "name": "Project kickoff",
            "start": "2026-09-10 14:00:00",
            "stop": "2026-09-10 15:00:00",
            "user_id": self.organiser.id,
            "partner_ids": [Command.set([p.id for p in partners])],
        }
        vals.update(values)
        return self.env["calendar.event"].create(vals)

    # --- the .ics ------------------------------------------------------

    def test_ics_report_renders_a_parsable_calendar(self):
        event = self._make_event([self.guest])
        content, extension = self.env["ir.actions.report"]._render(
            "bf_calendar_invite.calendar_event_ics", event.ids)
        self.assertEqual(extension, "ics")
        text = content.decode()
        self.assertTrue(text.startswith("BEGIN:VCALENDAR"))
        self.assertIn("BEGIN:VEVENT", text)
        self.assertIn("Project kickoff", text)

    def test_email_button_attaches_the_ics_to_the_composer(self):
        """The whole point: the recipient can add the meeting to their calendar."""
        event = self._make_event([self.guest])
        action = event.action_open_composer()
        composer = self.env["mail.compose.message"].with_context(
            **action["context"]).create({})

        names = composer.attachment_ids.mapped("name")
        self.assertIn("invitation.ics", names,
                      "the composer opened without the .ics attached")
        ics = composer.attachment_ids.filtered(lambda a: a.name == "invitation.ics")
        self.assertEqual(ics.mimetype, "text/calendar",
                         "a wrong mimetype stops mail clients offering 'add to calendar'")

    def test_email_button_uses_our_template(self):
        event = self._make_event([self.guest])
        action = event.action_open_composer()
        self.assertEqual(
            action["context"]["default_template_id"],
            self.env.ref("bf_calendar_invite.mail_template_calendar_invite").id)

    def test_composer_body_carries_the_invitation_link(self):
        event = self._make_event([self.guest])
        action = event.action_open_composer()
        composer = self.env["mail.compose.message"].with_context(
            **action["context"]).create({})
        self.assertIn("/calendar/meeting/view", composer.body or "",
                      "the rendered body has no link to the invitation page")

    # --- the language and the hours the message is written in ----------

    def _composer_body(self, event, template=None):
        """The body as the composer holds it, which is what gets sent."""
        action = event.action_open_composer()
        context = dict(action["context"])
        if template is not None:
            context["default_template_id"] = template.id
        return self.env["mail.compose.message"].with_context(**context).create({}).body or ""

    def test_message_is_written_in_the_guests_language(self):
        """The defect this replaced: a French client, an English invitation.

        The body used to be English prose held in the template record, so the
        language of the reader changed nothing — only the dates came out in
        French, which is what made it look like a formatting problem.
        """
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        # The organiser is pinned to the *other* language on purpose: on a
        # French database both would be French, and the assertion would pass
        # just as well against code that reads the organiser.
        self.organiser.partner_id.lang = "en_US"
        self.guest.lang = "fr_CA"
        body = self._composer_body(self._make_event([self.guest]))
        self.assertIn("Bonjour", body, "a French guest was written to in English")
        self.assertNotIn("Here are the details", body)

    def test_message_follows_an_english_guest(self):
        """The other direction, so the assertion above cannot pass on a
        database that simply renders everything in French."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        self.organiser.partner_id.lang = "fr_CA"
        self.guest.lang = "en_US"
        body = self._composer_body(self._make_event([self.guest]))
        self.assertIn("Here are the details", body)
        self.assertNotIn("Bonjour", body)

    def test_guests_who_disagree_fall_back_to_the_organiser(self):
        """One message, two languages asked for: neither guest decides."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        self.guest.lang = "fr_CA"
        self.other_guest.lang = "en_US"
        self.organiser.partner_id.lang = "en_US"
        event = self._make_event([self.guest, self.other_guest])
        self.assertEqual(event.bf_mail_lang, "en_US")

    def test_a_colleagues_language_does_not_decide(self):
        """`_bf_outside_guests` already ignores colleagues for the link; the
        language has to ignore them too, or the one internal French user on a
        thread overrides the English client it is addressed to."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        self.organiser.partner_id.lang = "fr_CA"
        self.guest.lang = "en_US"
        event = self._make_event([self.organiser.partner_id, self.guest])
        self.assertEqual(event.bf_mail_lang, "en_US")

    def test_the_forced_templates_ignore_the_guest(self):
        """What makes the language switchable once the composer is open."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.guest.lang = "fr_CA"
        event = self._make_event([self.guest])
        english = self.env.ref("bf_calendar_invite.mail_template_calendar_invite_en")
        body = self._composer_body(event, template=english)
        self.assertIn("Here are the details", body,
                      "picking the English template did not switch the language")

    def test_a_forced_template_switches_the_dates_too(self):
        """The half-switch that hid behind a deactivated language.

        Forcing a `lang` that is not active renders the prose in English —
        untranslated terms fall back to the source — while `format_datetime`
        resolves its locale through the *installed* languages and drops back to
        the first one. The message then reads "Here are the details" above
        "jeudi 10 septembre". 2026-09-10 is a Thursday.
        """
        self.env["res.lang"]._activate_lang("fr_CA")
        self.guest.lang = "fr_CA"
        event = self._make_event([self.guest])
        english = self.env.ref("bf_calendar_invite.mail_template_calendar_invite_en")
        body = self._composer_body(event, template=english)
        self.assertIn("Thursday", body, "the date stayed in the reader's locale")
        self.assertNotIn("jeudi", body)

    def test_a_forced_template_only_ever_names_an_active_language(self):
        """Asking `res.lang` beats naming a code the database may not have."""
        self.env["res.lang"]._activate_lang("fr_CA")
        event = self._make_event([self.guest])
        installed = [code for code, _name in self.env["res.lang"].get_installed()]
        self.assertIn(event.bf_mail_lang_en, installed)
        self.assertIn(event.bf_mail_lang_fr, installed)
        self.assertTrue(event.bf_mail_lang_fr.startswith("fr"))

    def test_hours_are_not_written_in_the_senders_timezone(self):
        """The organiser's timezone is not the meeting's.

        One message goes to every attendee, so it carries one timezone label.
        Core's `_get_mail_tz()` ends at the sending user's, which told a
        Montreal client that a 3 p.m. meeting was at 7 a.m. because the
        organiser pressed the button from New Zealand.
        """
        self.env.company.resource_calendar_id.tz = "America/Toronto"
        self.organiser.tz = "Pacific/Auckland"
        event = self._make_event([self.guest])
        self.assertEqual(event.with_user(self.organiser)._bf_mail_tz(),
                         "America/Toronto")
        body = self._composer_body(event.with_user(self.organiser))
        self.assertIn("America/Toronto", body)
        self.assertNotIn("Pacific/Auckland", body)

    def test_an_event_carrying_its_own_timezone_keeps_it(self):
        """A meeting explicitly set elsewhere is not the company's mistake.

        Recurring, because that is the only kind of event that can carry a
        timezone: `event_tz` is computed from the recurrence, and
        `_compute_recurrence` clears it on everything else. A first draft of
        this test passed `event_tz` to a one-off event and asserted on a value
        Odoo had already wiped.
        """
        self.env.company.resource_calendar_id.tz = "America/Toronto"
        # 2026-09-10 is a Thursday.
        event = self._make_event(
            [self.guest], recurrency=True, rrule_type="weekly", thu=True,
            end_type="count", count=2, event_tz="Europe/Paris")
        self.assertEqual(event.event_tz, "Europe/Paris",
                         "the recurrence did not keep the timezone")
        self.assertEqual(event._bf_mail_tz(), "Europe/Paris")

    def test_a_call_room_is_not_also_announced_as_a_place(self):
        """Odoo stores the call URL in `location` too when the meeting is made
        from a room, so the message said "Where: https://…" above "Video call:
        https://…" — the same link, twice, one of them labelled as an address.
        """
        url = "https://nextcloud.example.com/call/abc123"
        event = self._make_event([self.guest], location=url, videocall_location=url)
        # Forced to English so the labels are the same whatever the database's
        # own language is. Counting occurrences of the URL would not do: the
        # video-call row prints it twice by design, once as href and once as
        # the link text.
        english = self.env.ref("bf_calendar_invite.mail_template_calendar_invite_en")
        body = self._composer_body(event, template=english)
        self.assertNotIn("Where:", body, "the call link was also labelled as a place")
        self.assertIn("Video call:", body, "the call link went missing entirely")
        self.assertNotIn("maps.google.com", body,
                         "a map link on a URL sends the reader to an empty map")

    def test_a_real_address_still_gets_its_map_link(self):
        event = self._make_event(
            [self.guest], location="450 rue Sainte-Hélène, Montréal")
        english = self.env.ref("bf_calendar_invite.mail_template_calendar_invite_en")
        body = self._composer_body(event, template=english)
        self.assertIn("Where:", body)
        self.assertIn("maps.google.com", body)

    # --- who may be sent the invitation link ---------------------------

    def test_invitation_link_for_a_single_guest(self):
        event = self._make_event([self.guest])
        attendee = event.attendee_ids.filtered(lambda a: a.partner_id == self.guest)
        self.assertEqual(
            event.bf_invitation_url,
            "https://odoo.example.com/calendar/meeting/view"
            "?token=%s&id=%s" % (attendee.access_token, event.id))

    def test_no_invitation_link_when_several_outside_guests(self):
        """The token is an identity: one link cannot be handed to two guests."""
        event = self._make_event([self.guest, self.other_guest])
        self.assertFalse(
            event.bf_invitation_url,
            "sending one guest's token to both would let either answer in the "
            "other's name")

    def test_no_invitation_link_without_guests(self):
        event = self._make_event([])
        self.assertFalse(event.bf_invitation_url)

    def test_an_internal_colleague_does_not_suppress_the_link(self):
        """The real shape of nearly every meeting: one colleague, one client.

        A colleague on the thread is not a second identity to protect — the
        invitation page redirects a logged-in internal user to the backend, and
        they can already set any attendee's status from the event form.
        """
        event = self._make_event([self.organiser.partner_id, self.guest])
        self.assertTrue(
            event.bf_invitation_url,
            "a colleague on the invitation should not hide the guest's link")

    def test_link_survives_an_event_written_by_a_sync(self):
        """`user_id` is OdooBot on synced events, so the organiser is no guide.

        Keying the rule on "attendees other than the organiser" suppressed the
        link on 397 of the last 400 real events.
        """
        event = self._make_event([self.organiser.partner_id, self.guest])
        event.user_id = self.env.ref("base.user_root")
        self.assertTrue(
            event.bf_invitation_url,
            "an event whose organiser is a bot still has exactly one guest")

    def test_a_portal_user_still_counts_as_a_guest(self):
        """Portal access is not internal access: the token still matters."""
        portal_partner = self.env["res.partner"].create({
            "name": "Portal guest", "email": "portal.guest@example.com"})
        self.env["res.users"].create({
            "name": "Portal guest",
            "login": "bf_invite_portal_guest",
            "partner_id": portal_partner.id,
            "groups_id": [Command.set([self.env.ref("base.group_portal").id])],
        })
        event = self._make_event([portal_partner, self.guest])
        self.assertFalse(
            event.bf_invitation_url,
            "two outside guests, one of them on the portal, is still two")

    # --- SMS -----------------------------------------------------------

    def test_sms_button_prefills_the_body(self):
        event = self._make_event([self.guest])
        action = event.action_send_sms()
        body = action["context"]["default_body"]
        self.assertIn("Project kickoff", body)
        self.assertIn("/calendar/meeting/view", body)

    def test_sms_falls_back_to_the_video_call_link(self):
        event = self._make_event(
            [self.guest, self.other_guest],
            videocall_location="https://nextcloud.example.com/call/abc123")
        body = event.action_send_sms()["context"]["default_body"]
        self.assertIn("https://nextcloud.example.com/call/abc123", body)

    def test_sms_body_stays_in_the_gsm7_alphabet(self):
        """Anything outside GSM-7 switches the whole SMS to UCS-2 (70 chars)."""
        event = self._make_event([self.guest])
        body = event.action_send_sms()["context"]["default_body"]
        self.assertFalse(
            set(body) - GSM7,
            "characters outside GSM-7 more than halve the usable length")

    def test_sms_body_stays_in_gsm7_in_french(self):
        """The locale that actually broke it: "août" carries a û.

        A spelled-out French month is one character outside GSM-7, which
        re-encodes the whole message and turned a 146-character reminder into
        three billed segments instead of one.
        """
        self.env["res.lang"]._activate_lang("fr_CA")
        french_user = self.env["res.users"].create({
            "name": "Lectrice", "login": "bf_invite_fr", "lang": "fr_CA",
            "email": "lectrice@example.com", "tz": "America/Toronto",
        })
        # August, so the month name is the one that breaks.
        event = self._make_event(
            [self.guest], start="2026-08-27 18:15:00", stop="2026-08-27 19:00:00")
        body = event.with_user(french_user).with_context(
            lang="fr_CA").action_send_sms()["context"]["default_body"]
        offenders = sorted(set(body) - GSM7)
        self.assertFalse(
            offenders,
            "outside GSM-7: %s — body was:\n%s" % (offenders, body))

    def test_sms_time_is_written_for_a_text_message(self):
        """`display_time` reads "09/10/2026 at (14:00:00 To 15:00:00) (UTC)"."""
        event = self._make_event([self.guest])
        body = event.action_send_sms()["context"]["default_body"]
        self.assertNotIn("(", body, "brackets and a timezone label waste a scarce 160 characters")
        self.assertNotIn(":00:00", body, "seconds do not belong in a meeting reminder")

    def test_sms_body_fits_one_segment(self):
        """A typical reminder should not silently cost two messages.

        Includes a location, because leaving it out is exactly what makes this
        fit: an assertion on an event with no address would pass whatever we do.
        """
        event = self._make_event(
            [self.guest], location="450 rue Sainte-Helene, Montreal")
        body = event.action_send_sms()["context"]["default_body"]
        self.assertLessEqual(
            len(body), 160,
            "a GSM-7 SMS is 160 characters; past that it is split and billed "
            "twice. Body was:\n%s" % body)

    def test_sms_names_the_day_in_the_users_timezone(self):
        """An evening meeting in Montreal is stored on the next UTC day."""
        montreal_user = self.env["res.users"].create({
            "name": "Montrealer",
            "login": "bf_invite_montrealer",
            "email": "montrealer@example.com",
            "tz": "America/Toronto",
        })
        # 2026-09-11 01:00 UTC is 2026-09-10 at 9 p.m. in Montreal.
        event = self._make_event(
            [self.guest], start="2026-09-11 01:00:00", stop="2026-09-11 02:00:00")
        body = event.with_user(montreal_user).action_send_sms()["context"]["default_body"]
        self.assertIn("10", body.split("\n")[1],
                      "the reminder names the UTC day, not the reader's day: %s" % body)
        self.assertNotIn("11", body.split("\n")[1])

    def test_sms_composer_keeps_our_body(self):
        """`sms.composer.body` is computed — check it does not overwrite us."""
        event = self._make_event([self.guest])
        action = event.action_send_sms()
        composer = self.env["sms.composer"].with_context(**action["context"]).create({})
        self.assertIn("Project kickoff", composer.body or "")
