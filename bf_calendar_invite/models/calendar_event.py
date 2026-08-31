"""Make the calendar's EMAIL and SMS buttons produce something usable.

Core's two "contact the attendees" buttons both stop short:

- EMAIL (`action_open_composer`) loads `calendar.calendar_template_meeting_update`,
  which has neither a link to the event nor an `.ics` attachment. The recipient
  gets a nicely formatted description of a meeting they cannot add to their
  calendar. The `.ics` is only ever attached by `_send_mail_to_attendees`, i.e.
  by "Send Invitations" — a button core hides behind the developer group.
- SMS (`calendar_sms.action_send_sms`) opens the composer with an empty body,
  so every reminder is retyped by hand.

This module points EMAIL at a template that carries both the link and the
`.ics`, and prefills the SMS body.
"""

import pytz

from odoo import _, api, fields, models
from odoo.tools.misc import format_time

# Core's public invitation page. It authenticates the visitor as one specific
# attendee (`auth="calendar"` resolves the token to a calendar.attendee), which
# is why it is only ever safe to put in a message with a single recipient.
_INVITATION_PATH = "/calendar/meeting/view"


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    bf_invitation_url = fields.Char(
        string="Invitation page",
        compute="_compute_bf_invitation_url",
        help="Public page where the attendee can see the event and accept or "
             "decline it. Only set when exactly one attendee is an outside "
             "guest, because the link identifies whoever opens it as that "
             "attendee.",
    )

    @api.depends("attendee_ids.access_token", "attendee_ids.partner_id",
                 "attendee_ids.partner_id.user_ids.share")
    def _compute_bf_invitation_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for event in self:
            event.bf_invitation_url = event._bf_guest_attendee_url(base_url)

    def _bf_outside_guests(self):
        """Attendees for whom the invitation token would be a real capability.

        Colleagues do not count. `/calendar/meeting/view` redirects a logged-in
        internal user to the backend form instead of showing them the guest
        page, and an internal user with access to the event can already set any
        attendee's status from the Invitations tab — so a link that also lands
        in a colleague's inbox hands them nothing they did not already have.

        Deliberately not keyed on the organiser: most events here are written
        by a sync, which leaves `user_id` as OdooBot. Counting "attendees other
        than the organiser" then counts everybody, and the link never appears
        on precisely the one-to-one meetings it is meant for.
        """
        self.ensure_one()
        return self.attendee_ids.filtered(
            lambda a: not any(not user.share for user in a.partner_id.user_ids)
        )

    def _bf_guest_attendee_url(self, base_url=None):
        """URL of the invitation page, or False when it cannot be shared.

        The token in this URL *is* the attendee's identity: whoever opens it is
        treated as that attendee and can accept or decline in their name. With
        two outside guests on the same message, either could answer for the
        other, so we return False rather than pick one.
        """
        self.ensure_one()
        guests = self._bf_outside_guests()
        if len(guests) != 1 or not guests.access_token:
            return False
        if base_url is None:
            base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return "%s%s?token=%s&id=%s" % (
            base_url.rstrip("/"), _INVITATION_PATH, guests.access_token, self.id,
        )

    # ------------------------------------------------------------
    # How the message is written
    # ------------------------------------------------------------

    bf_mail_lang = fields.Char(
        string="Invitation language",
        compute="_compute_bf_mail_lang",
        help="Language the EMAIL button writes in: the guests' own when they "
             "share one, the organiser's otherwise.",
    )

    bf_mail_lang_fr = fields.Char(
        string="Invitation language (French)",
        compute="_compute_bf_mail_lang",
        help="French variant this database actually has, for the template that "
             "forces French.",
    )
    bf_mail_lang_en = fields.Char(
        string="Invitation language (English)",
        compute="_compute_bf_mail_lang",
        help="English variant this database actually has, for the template that "
             "forces English.",
    )

    @api.depends("attendee_ids.partner_id.lang", "partner_id.lang",
                 "attendee_ids.partner_id.user_ids.share")
    def _compute_bf_mail_lang(self):
        for event in self:
            event.bf_mail_lang = event._bf_mail_lang()
            event.bf_mail_lang_fr = event._bf_mail_lang_family("fr")
            event.bf_mail_lang_en = event._bf_mail_lang_family("en")

    def _bf_mail_lang_family(self, prefix):
        """An *active* language of that family, because an inactive one lies.

        A `mail.template.lang` pointing at a deactivated code half works, which
        is what makes it hard to see: the prose comes out right, because an
        untranslated term falls back to the English source, while
        `format_datetime` resolves its locale through `get_lang`, which only
        looks at installed languages and quietly drops back to the first one.

        On this database — `en_CA` and `fr_CA` active, `en_US` not — forcing
        `en_US` produced English sentences under French dates. Ask `res.lang`
        which variants exist instead of naming one.
        """
        self.ensure_one()
        codes = [code for code, _name in self.env["res.lang"].get_installed()]
        return next((code for code in codes if code.split("_")[0] == prefix),
                    self.env.lang)

    def _bf_mail_lang(self):
        """Language one message addressed to every attendee should use.

        Core has no such question to answer: "Send Invitations" renders one
        message per attendee, so `calendar.attendee.partner_id.lang` decides
        each of them separately. The EMAIL button renders once for the whole
        list, and a single language has to be picked for it.

        Take the guests' when they agree on one — the ordinary shape here is a
        single client on the invitation — and the organiser's when they do not,
        rather than writing to a French guest in English because an English one
        is also on the thread.
        """
        self.ensure_one()
        langs = set(self._bf_outside_guests().partner_id.mapped("lang")) - {False}
        if len(langs) == 1:
            return langs.pop()
        return self.partner_id.lang or self.env.lang

    def _bf_mail_tz(self):
        """Timezone the message should name its hours in.

        Not core's `_get_mail_tz()`, which ends at `self.env.user.tz` — the
        timezone of whoever pressed the button. An organiser writing from
        Auckland then tells a Montreal client the meeting is at 7 a.m.

        The per-attendee templates do not have this problem (`mail_tz` reads
        each attendee's own `partner_id.tz`), but one message to a whole list
        carries one label, so it falls back to the company's: its working hours
        already record the timezone the meeting was scheduled in.
        """
        self.ensure_one()
        company = self.env.company
        candidates = [self.event_tz]
        if "resource_calendar_id" in company._fields:
            candidates.append(company.resource_calendar_id.tz)
        candidates.append(company.partner_id.tz)
        candidates.append(self.env.user.tz)
        return next((tz for tz in candidates if tz), "UTC")

    def _bf_mail_brand(self):
        """Company name, logo and colours for the message shell.

        Read straight off `res.company`, where `bf_onboarding_base` keeps the
        brand fields for the whole suite. `bluefox_branding` only surfaces them
        in Settings — depending on it would tie a branded invitation to the
        optional white-label panel being installed.

        `report_brand_logo` rather than `logo`: the header is dark, and the
        standard company logo is the one drawn for light backgrounds. Tested on
        the field, not on the URL — `/web/image` answers 200 with a placeholder
        for an empty one, so a fallback keyed on the request would never fire.
        """
        self.ensure_one()
        company = self.env.company
        field = "report_brand_logo" if company.report_brand_logo else "logo"
        return {
            "company": company,
            "primary": company.report_brand_primary or "#714B67",
            "dark": company.report_brand_dark or "#212529",
            "logo_src": "/web/image/res.company/%s/%s" % (company.id, field),
        }

    def _bf_guest_name(self):
        """Name to greet, when there is exactly one guest to greet.

        With two guests on the same message a salutation would name one of them
        and ignore the other, so it is left off entirely.
        """
        self.ensure_one()
        guests = self._bf_outside_guests()
        return guests.common_name if len(guests) == 1 else False

    # ------------------------------------------------------------
    # EMAIL
    # ------------------------------------------------------------

    def action_open_composer(self):
        """Same composer as core, but on a template that carries the .ics."""
        action = super().action_open_composer()
        template_id = self.env["ir.model.data"]._xmlid_to_res_id(
            "bf_calendar_invite.mail_template_calendar_invite", raise_if_not_found=False,
        )
        if template_id:
            action.setdefault("context", {})["default_template_id"] = template_id
        return action

    # ------------------------------------------------------------
    # SMS
    # ------------------------------------------------------------

    def _bf_sms_when(self):
        """Start and end of the event, written for a text message.

        Not `display_time`, which renders as "09/10/2026 at (14:00:00 To
        15:00:00) (UTC)" — brackets, seconds and a timezone label, on a line
        where every character is billed. Not the locale's `short` date either:
        "9/10/26" is read as 9 October by half the people who get it.

        The date is written numerically rather than spelled out, because the
        spelled-out French month is what breaks the encoding: "août" carries a
        û, which is not in GSM-7, and a single character outside GSM-7 re-encodes
        the entire message as UCS-2 — turning a 146-character reminder from one
        billed segment into three. ISO order is unambiguous in any locale.
        """
        self.ensure_one()
        tz = self.env.user.tz or self.env.context.get("tz") or "UTC"
        lang = self.env.lang
        if self.allday:
            return self.start_date.strftime("%Y-%m-%d")

        # The date has to be converted before formatting: a 9 p.m. Montreal
        # meeting is stored on the next UTC day, and `strftime` on the raw
        # value would name that day.
        #
        # Note `odoo.tools.misc.format_date` cannot do this job here — it
        # applies the user's timezone itself, so handing it an already
        # converted value shifts the date a second time. That double
        # conversion is what printed "27 août" for a meeting held on the 26th.
        tzinfo = pytz.timezone(tz)
        start_local = pytz.utc.localize(self.start).astimezone(tzinfo)
        stop_local = pytz.utc.localize(self.stop).astimezone(tzinfo)

        def _date(value):
            return value.strftime("%Y-%m-%d")

        def _time(value):
            return format_time(self.env, value, tz=tz, time_format="short",
                               lang_code=lang)

        if start_local.date() != stop_local.date():
            return "%s %s - %s %s" % (
                _date(start_local), _time(self.start),
                _date(stop_local), _time(self.stop),
            )
        return "%s %s - %s" % (
            _date(start_local), _time(self.start), _time(self.stop),
        )

    def _bf_sms_body(self):
        """Prefilled reminder text for the SMS composer.

        Deliberately short: title, when, link. The location is left out even
        though it is useful — the invitation page carries it, and the link
        alone already costs a hundred characters, so adding an address is what
        tips a one-segment reminder into being billed as two.

        Kept inside the GSM-7 alphabet for the same reason: one character
        outside it re-encodes the whole message as UCS-2 and drops the limit
        from 160 to 70. Accented French letters used here (é, è, à) are in
        GSM-7; em dashes and typographic quotes are not.
        """
        self.ensure_one()
        lines = [self.name or _("Meeting")]
        when = self._bf_sms_when()
        if when:
            lines.append(when)
        link = self._bf_guest_attendee_url() or self.videocall_location
        if link:
            lines.append(link)
        return "\n".join(lines)

    def action_send_sms(self):
        """Same composer as calendar_sms, opened on a drafted message."""
        action = super().action_send_sms()
        if len(self) == 1:
            action.setdefault("context", {})["default_body"] = self._bf_sms_body()
        return action
