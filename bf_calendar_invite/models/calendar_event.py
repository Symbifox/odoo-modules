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

import json
import logging
import uuid
from datetime import timedelta
from email.utils import parseaddr
from urllib.parse import unquote

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext
from odoo.tools.misc import format_date, format_datetime, format_time

# Core's public invitation page. It authenticates the visitor as one specific
# attendee (`auth="calendar"` resolves the token to a calendar.attendee), which
# is why it is only ever safe to put in a message with a single recipient.
_INVITATION_PATH = "/calendar/meeting/view"

_logger = logging.getLogger(__name__)


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

    # ------------------------------------------------------------
    # Event status
    # ------------------------------------------------------------

    bf_event_status = fields.Selection(
        [
            ("tentative", "Tentative"),
            ("confirmed", "Confirmed"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        index=True,
        tracking=True,
        help="Whether the meeting itself is going ahead. Distinct from "
             "\"Attending?\", which is one attendee's answer to the "
             "invitation: a confirmed meeting can have guests who declined, "
             "and a cancelled one can have guests who had accepted.",
    )

    # RFC 5545 §3.8.1.11. Only these three values exist for a VEVENT, which is
    # why the field carries exactly them and not a wider workflow: anything
    # else would not survive a round trip through a calendar client.
    _BF_ICS_STATUS = {
        "tentative": "TENTATIVE",
        "confirmed": "CONFIRMED",
        "cancelled": "CANCELLED",
    }

    @api.model_create_multi
    def create(self, vals_list):
        """New meetings are confirmed; old ones are left alone.

        ⚠️ Posed here and NOT as `default="confirmed"` on the field, and the
        difference is the entire history of this database. A field default is
        written into every existing row when the column is created: measured on
        a copy of a real calendar, `default=` stamped **every meeting ever
        held** as "confirmed". That is precisely the claim
        `_bf_ics_status` refuses to make, and the first re-push of the calendar
        would have carried `STATUS:CONFIRMED` to Nextcloud for all of them.

        What we want is the distinction: an event written before this field
        existed has **no** status, because nobody ruled on it; an event created
        from now on is confirmed unless someone says otherwise.
        """
        for vals in vals_list:
            vals.setdefault("bf_event_status", "confirmed")
        return super().create(vals_list)

    def _bf_ics_status(self):
        """ICS `STATUS` value, or False when the event should not carry one.

        Returning False rather than defaulting to CONFIRMED matters on the
        push side: a VEVENT with no STATUS is "unspecified", which is what an
        event written before this field existed actually is. Writing CONFIRMED
        for it would claim a confirmation nobody gave.
        """
        self.ensure_one()
        return self._BF_ICS_STATUS.get(self.bf_event_status, False)

    @api.model
    def _bf_status_from_ics(self, value):
        """Odoo value for an ICS `STATUS`, or False when we cannot map it.

        Unknown values are dropped rather than guessed. A client is free to
        send an x-name here, and coercing it to `confirmed` would silently
        promote a status we did not understand.
        """
        reverse = {v: k for k, v in self._BF_ICS_STATUS.items()}
        return reverse.get((value or "").strip().upper(), False)

    # ------------------------------------------------------------
    # Cancelling a meeting, without erasing it
    # ------------------------------------------------------------

    bf_cancellation_reason = fields.Text(
        string="Cancellation reason",
        copy=False,
        tracking=True,
        help="Why the meeting is not happening. Optional, and shown to the "
             "guests only when a cancellation notice is actually sent.",
    )

    def action_bf_cancel(self):
        """Open the cancellation dialog on this meeting.

        A dialog rather than an immediate write, because two of the three
        decisions a cancellation carries cannot be guessed: whether anyone is
        told, and what they are told. Doing it silently and offering to notify
        afterwards would lose the notice the moment the tab is closed, and
        leave no room for a reason.
        """
        return {
            "type": "ir.actions.act_window",
            "name": _("Cancel the meeting"),
            "res_model": "bf.calendar.event.cancel",
            "view_mode": "form",
            # 🔴 `views` et pas seulement `view_mode`. Le client web
            # préprocesse toute action par `action.views.map(...)` :
            # un dictionnaire bâti à la main qui n'a que `view_mode`
            # fait « Cannot read properties of undefined (reading
            # 'map') » et rend la boîte « Oups ! » à la place. Aucun
            # test Python ne peut le voir — l'action est bien formée
            # côté serveur, elle meurt à l'ouverture.
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_event_ids": [(6, 0, self.ids)]},
        }

    def _bf_cancel_blocker(self):
        """Why this meeting cannot be cancelled from here, or False.

        ⚠️ A recurring meeting is refused rather than handled. Writing on one
        occurrence of a series raises core's "this event / this and following /
        all events" question, and a dialog that does not show that choice would
        answer it silently — on a whole series of meetings, and by email. The
        popover hides the button for the same reason; this is the guard for
        every other way in.
        """
        self.ensure_one()
        if self.recurrency or self.recurrence_id:
            return _(
                "This meeting is part of a series. Cancel it from the form, "
                "where Odoo can ask whether you mean this occurrence, the "
                "following ones, or the whole series."
            )
        return False

    def _bf_cancel(self, reason=None, notify=False):
        """Mark these meetings cancelled, free their slot, keep them in place.

        The three halves of what "cancel" means here, and why each one:

        * `bf_event_status = "cancelled"` is what the grid reads to strike the
          title out, and what the CalDAV push turns into `STATUS:CANCELLED`.
        * `show_as = "free"` is what actually gives the time back. It is not
          cosmetic: `resource_calendar._get_bookable_intervals` counts an event
          as busy only when `show_as == "busy"`, so a cancelled meeting left at
          "busy" keeps blocking the slot picker for a meeting that is not
          happening. Measured on a real calendar before writing this: the
          handful of events already carrying the cancelled status were all
          still `busy`.
        * the event is NOT unlinked. The slot was held, and an agenda that
          silently loses the entry cannot answer "what was I supposed to be
          doing at 10?" a week later.

        Idempotent: a meeting already cancelled is left alone and no second
        notice goes out. Cancelling twice is a double booking of the guests'
        attention, and the commonest way to do it is a double click.
        """
        to_cancel = self.filtered(lambda e: e.bf_event_status != "cancelled")
        for event in to_cancel:
            blocker = event._bf_cancel_blocker()
            if blocker:
                raise UserError(blocker)
        if not to_cancel:
            return self.env["calendar.event"]

        vals = {"bf_event_status": "cancelled", "show_as": "free"}
        if reason:
            vals["bf_cancellation_reason"] = reason
        to_cancel.write(vals)

        # RFC 5545 §3.8.7.4. A `METHOD:CANCEL` carrying the same SEQUENCE as
        # the invitation the client already holds is, by the letter of the
        # spec, not newer than it — and clients do drop it. The material-field
        # bump in `write()` cannot cover this: nothing material changed.
        for event in to_cancel:
            super(CalendarEvent, event.sudo()).write(
                {"bf_ics_sequence": (event.bf_ics_sequence or 0) + 1}
            )

        # A meeting that belongs to a booking is the booking. Leaving the
        # booking live under a cancelled meeting would keep it in the "to
        # confirm" lists and let a reminder go out for a meeting that is
        # struck out on the calendar.
        to_cancel._bf_cancel_linked_bookings()

        if notify:
            to_cancel._bf_send_cancellation_notice()
        return to_cancel

    def _bf_cancel_linked_bookings(self):
        """Cancel the `resource.booking` behind these meetings, if any.

        Guarded on the model being present: `bf_appointment` is an optional
        neighbour, and this module is installed on databases that do not carry
        it.

        ⚠️ No flag needed to stop the two modules from undoing each other.
        `resource.booking.action_cancel` marks its own meeting cancelled and
        keeps it, which lands on the same values this method has just written;
        writing them twice is a wasted UPDATE, not a loop. Cancelling a meeting
        is not itself a trigger for anything.
        """
        if "resource.booking" not in self.env:
            return False
        bookings = self.env["resource.booking"].sudo().browse()
        for event in self:
            bookings |= event.sudo().resource_booking_ids.filtered("active")
        if bookings:
            bookings.with_context(
                no_mail_to_attendees=True,
                tracking_disable=True,
                mail_notrack=True,
            ).action_cancel()
        return bookings

    def _bf_cancellation_recipients(self):
        """Partners a cancellation notice would go to.

        The same list as any other notice about the meeting, and deliberately
        the same code: two copies of "everyone but the organiser" drift, and
        the day they do, one of them leaves a guest out of a cancellation.
        """
        self.ensure_one()
        return self._bf_notice_recipients()

    def _bf_send_cancellation_notice(self):
        """Send the branded cancellation notice, in the guests' language.

        One message to the whole list, like the invitation: the `.ics` it
        carries is the cancellation, and a per-guest send would put one
        `METHOD:CANCEL` per recipient on the same UID for no gain.

        ⚠️ `force_send=False`. The notice goes through the outgoing queue like
        every other message, so a mail server that is down delays it instead of
        raising inside the dialog and rolling the cancellation back with it.
        """
        template = self.env.ref(
            "bf_calendar_invite.mail_template_calendar_cancellation",
            raise_if_not_found=False,
        )
        if not template:
            _logger.warning(
                "bf_calendar_invite: cancellation template missing; "
                "no notice sent for %s.", self.ids,
            )
            return False
        sent = self.env["mail.mail"].sudo().browse()
        for event in self:
            recipients = event._bf_cancellation_recipients()
            if not recipients:
                continue
            mail_id = template.sudo().send_mail(
                event.id,
                force_send=False,
                email_values={"recipient_ids": [(6, 0, recipients.ids)]},
            )
            sent |= self.env["mail.mail"].sudo().browse(mail_id)
        return sent

    # ------------------------------------------------------------
    # Poke
    # ------------------------------------------------------------

    def action_bf_poke(self):
        """Composer on a short "are we still meeting?" note to the guests.

        Same shape as the EMAIL button — a draft the user reads and sends —
        and the same language rule, so the person who is late gets asked in
        their own language rather than the organiser's.

        No `.ics`: the event is unchanged and re-attaching it would read as a
        reschedule. The message repeats where to join instead, because the
        commonest reason someone is missing is that they cannot find the link,
        not that they forgot.
        """
        self.ensure_one()
        action = self.action_open_composer()
        template_id = self.env["ir.model.data"]._xmlid_to_res_id(
            "bf_calendar_invite.mail_template_calendar_poke", raise_if_not_found=False,
        )
        if template_id:
            action.setdefault("context", {})["default_template_id"] = template_id
        return action

    def _bf_poke_join_url(self):
        """Where to tell a missing guest to go.

        The video-call link first: someone who is not in the room needs the
        room, not the invitation page. `location` only when it is not that same
        link repeated, which is what an event created from a call room stores.
        """
        self.ensure_one()
        if self.videocall_location:
            return self.videocall_location
        if self.location and self.location.startswith(("http://", "https://")):
            return self.location
        return False


    # ------------------------------------------------------------
    # Telling the guests that a meeting moved
    # ------------------------------------------------------------
    #
    # Core has exactly one rule here, and it is `'start' in values`. A meeting
    # that is lengthened, moved to another room or given a new joining link
    # tells nobody, and the notice it does send is core's own template, the
    # only one in this family that was never dressed.
    #
    # Measured on a real calendar before writing this: a meeting with outside
    # guests went from thirty to sixty minutes and no one was written to, by
    # Odoo or by anything else. Over the days before that, meetings carrying a
    # material change far outnumbered the notices that went out.
    #
    # The revision counter below already knew about all eight material fields,
    # so what was missing was not knowledge. It was three things: a message, a
    # record of what the guests were last told, and a rule about when it leaves
    # on its own.

    bf_ics_sequence_notified = fields.Integer(
        string="Revision announced",
        default=0,
        copy=False,
        help="The revision of this meeting the guests were last told about. "
             "Lower than the ICS revision means a change nobody has heard of.",
    )
    bf_change_baseline = fields.Text(
        string="What the guests know",
        copy=False,
        help="The values of the meeting as of the last notice, in JSON. It is "
             "what a change is described against, because the guest compares "
             "the message to what is in their calendar, not to the value the "
             "meeting held five minutes ago.",
    )
    bf_change_since = fields.Datetime(
        string="Unannounced since",
        copy=False,
        help="When the first unannounced change was made. A change made five "
             "minutes ago is waiting for someone; a change made yesterday that "
             "nobody announced is a failure, and that is the difference the "
             "daily watchdog needs.",
    )
    bf_change_notice_due = fields.Boolean(
        string="Guests not told",
        compute="_compute_bf_change_notice_due",
        search="_search_bf_change_notice_due",
        help="This meeting carries a change that has not been announced.",
    )

    # What each material field is called in a message to a guest. Keys are the
    # fields of `_BF_ICS_MATERIAL_FIELDS`; `allday` is deliberately absent,
    # because a meeting that becomes all-day also changes its dates and would
    # otherwise be reported twice.
    def _bf_change_labels(self):
        return {
            "start": _("Start"),
            "stop": _("End"),
            "start_date": _("Start"),
            "stop_date": _("End"),
            "name": _("Title"),
            "location": _("Place"),
            "videocall_location": _("Joining link"),
        }

    def _bf_change_format(self, fname, value):
        """One side of one line, written the way a guest reads it.

        Datetimes go through the same timezone as the rest of the message, and
        say so. A bare "18:30" in a notice whose whole point is a time is the
        one place where the reader must not have to guess.
        """
        self.ensure_one()
        if value in (None, False, ""):
            return _("(none)")
        if fname in self._BF_ICS_DATETIME_FIELDS:
            moment = fields.Datetime.to_datetime(value)
            tz = self._bf_mail_tz()
            return "%s, %s (%s)" % (
                format_datetime(
                    self.env, moment, tz=tz, dt_format="EEEE d MMMM y",
                    lang_code=self.env.lang,
                ),
                format_time(
                    self.env, moment, tz=tz, time_format="short",
                    lang_code=self.env.lang,
                ),
                tz,
            )
        if fname in self._BF_ICS_DATE_FIELDS:
            return format_date(
                self.env, fields.Date.to_date(value), lang_code=self.env.lang,
            )
        return str(value)

    def bf_change_lines(self):
        """What changed since the guests were last told: [(label, before, after)].

        ⚠️ **Public on purpose**, unlike its neighbours. The QWeb of a
        `mail.template` is sandboxed and refuses any method whose name starts
        with an underscore, rendering `'NoneType' object is not callable` and
        sending nothing at all, to anyone. The rule cost a whole family of
        appointment emails once; it is cheaper to obey it than to
        re-establish where its edge is.

        Empty when nothing differs any more. A meeting moved and moved back has
        a higher revision and nothing to say: the baseline is what the guests
        hold, and it matches again.
        """
        self.ensure_one()
        baseline = self._bf_change_baseline_read()
        if not baseline:
            return []
        labels = self._bf_change_labels()
        lines = []
        for fname in self._BF_ICS_MATERIAL_FIELDS:
            if fname not in baseline or fname not in labels:
                continue
            if not self._bf_ics_changed(fname, baseline[fname]):
                continue
            lines.append({
                "label": labels[fname],
                "before": self._bf_change_format(fname, baseline[fname]),
                "after": self._bf_change_format(fname, self[fname]),
            })
        return lines

    def _bf_change_baseline_read(self):
        self.ensure_one()
        if not self.bf_change_baseline:
            return {}
        try:
            return json.loads(self.bf_change_baseline) or {}
        except ValueError:
            # A baseline we cannot read is a baseline we do not have. Losing
            # the "before" column of one notice is not a reason to lose the
            # notice, and it is certainly not a reason to raise inside a write.
            _logger.warning(
                "bf_calendar_invite: unreadable change baseline on event %s.",
                self.id,
            )
            return {}

    def _bf_change_baseline_capture(self, fields_touched):
        """Remember the values the guests hold, once per field.

        Called from `_bf_ics_bump`, i.e. **before** the write, and it only ever
        writes a field it does not already hold. That is the whole rule: after
        two successive moves the guest must be shown the slot they had in their
        calendar, not the intermediate one they were never told about.
        """
        self.ensure_one()
        baseline = self._bf_change_baseline_read()
        added = False
        for fname in fields_touched:
            if fname in baseline:
                continue
            value = self[fname]
            if fname in self._BF_ICS_DATETIME_FIELDS:
                value = fields.Datetime.to_string(value) if value else False
            elif fname in self._BF_ICS_DATE_FIELDS:
                value = fields.Date.to_string(value) if value else False
            baseline[fname] = value
            added = True
        if added:
            patch = {"bf_change_baseline": json.dumps(baseline)}
            # ⚠️ Posé seulement au PREMIER changement non annoncé. Le repousser
            # à chaque déplacement suivant remettrait le compteur à zéro, et une
            # rencontre bousculée tous les matins ne franchirait jamais le délai
            # de grâce : le seul cas où le contrôle doit crier serait le seul
            # qu'il ne verrait pas.
            if not self.bf_change_since:
                patch["bf_change_since"] = fields.Datetime.now()
            super(CalendarEvent, self.sudo()).write(patch)

    @api.depends("bf_ics_sequence", "bf_ics_sequence_notified", "start",
                 "bf_event_status", "partner_ids", "bf_change_baseline")
    def _compute_bf_change_notice_due(self):
        for event in self:
            event.bf_change_notice_due = event._bf_change_notice_due()

    def _bf_change_notice_due(self):
        """Is a notice owed to the guests of this meeting, right now?

        Five conditions, and each one has cost something somewhere:

        * the meeting is **not cancelled**, since a cancellation has its own
          notice and sending both would announce the new time of a meeting that
          is off;
        * it is **in the future**, the rule core applies too, because telling
          someone that last Tuesday moved is noise;
        * the revision **announced** is behind the revision **counted**;
        * somebody would actually be written to;
        * something still differs from what they hold.
        """
        self.ensure_one()
        if self.bf_event_status == "cancelled":
            return False
        if not self.start or self.start <= fields.Datetime.now():
            return False
        if (self.bf_ics_sequence or 0) <= (self.bf_ics_sequence_notified or 0):
            return False
        if not self._bf_notice_recipients():
            return False
        return bool(self.bf_change_lines())

    def _bf_notice_overdue(self, grace_hours=None):
        """Les rencontres dont l'avis est dû ET qui ont dépassé le délai.

        La différence avec `_bf_change_notice_due` est tout le sujet du filet :
        un changement fait il y a cinq minutes attend légitimement que quelqu'un
        presse le bouton, un changement d'hier que personne n'a annoncé est une
        panne. Sans ce délai, le contrôle crierait sur le fonctionnement normal
        et finirait désarmé.

        ⚠️ `get_param` d'une clé absente rend `False`, et `float(False)` vaut
        `0.0` sans lever : le `try/except` ne verrait rien et le délai
        retomberait à zéro chez tous les locataires, exactement le piège qui a
        failli faire taire les rappels de rendez-vous. D'où la garde avant le
        `float()`, et « non réglé » qui retombe sur le défaut plutôt que sur
        zéro.
        """
        if grace_hours is None:
            brut = self.env["ir.config_parameter"].sudo().get_param(
                "bf_calendar_invite.notice_grace_hours"
            )
            if brut in (None, False, ""):
                grace_hours = 24.0
            else:
                try:
                    grace_hours = float(brut)
                except (TypeError, ValueError):
                    grace_hours = 24.0
        limite = fields.Datetime.now() - timedelta(hours=grace_hours)
        candidats = self.sudo().search([
            ("bf_ics_sequence", ">", 0),
            ("start", ">", fields.Datetime.now()),
            ("bf_event_status", "!=", "cancelled"),
            ("bf_change_since", "!=", False),
            ("bf_change_since", "<=", limite),
        ])
        return candidats.filtered(lambda e: e._bf_change_notice_due())

    def _search_bf_change_notice_due(self, operator, value):
        """Domain side of the same question, for the views and the watchdog.

        ⚠️ Built by reading rather than by a domain, because the comparison is
        between two columns and a domain cannot express one. The pre-filter on
        the two counters keeps the read small: on a real calendar it takes the
        candidate set from every meeting ever held to a handful.
        """
        if operator not in ("=", "!="):
            raise UserError(_("Unsupported operator on this field."))
        wanted = bool(value) if operator == "=" else not bool(value)
        candidates = self.sudo().search([
            ("bf_ics_sequence", ">", 0),
            ("start", ">", fields.Datetime.now()),
            ("bf_event_status", "!=", "cancelled"),
        ])
        due = candidates.filtered(lambda e: e._bf_change_notice_due())
        if wanted:
            return [("id", "in", due.ids)]
        return [("id", "not in", due.ids)]

    def _bf_auto_change_notice_enabled(self):
        """Does a change learned from elsewhere leave on its own?

        🔴 Off unless the parameter is explicitly true, and the shape matters:
        `get_param` of a key nobody has set returns `False`, so "not configured"
        and "turned off" land on the same safe answer. A module that starts
        sending mail to clients the minute it is installed has no safe first
        day.
        """
        value = self.env["ir.config_parameter"].sudo().get_param(
            "bf_calendar_invite.auto_change_notice"
        )
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _bf_announce_remote_change(self):
        """Tell the guests about a change that came from outside Odoo.

        This is the half of the problem that matters most. A meeting
        moved in Thunderbird, on a phone or in the Nextcloud web calendar
        reaches Odoo through the pull, and the pull writes under
        `no_mail_to_attendees`, `tracking_disable` and `dont_notify`: it is
        silent by construction. Silence was right while Nextcloud was the one
        writing to the guests. It stops being right the moment Odoo takes that
        job over, and a system that is quiet on both sides is worse than the
        one we started from.
        """
        if not self._bf_auto_change_notice_enabled():
            return self.env["mail.mail"]
        due = self.filtered(lambda e: e._bf_change_notice_due())
        if not due:
            return self.env["mail.mail"]
        return due._bf_send_change_notice()

    def _bf_send_change_notice(self):
        """Send the branded notice and record that the guests now know.

        ⚠️ One savepoint per meeting. This runs inside the calendar pull, which
        walks every changed event of a calendar in one transaction: a single
        meeting whose rendering raises must not take the other meetings' new
        times down with it. The same shape, for the same reason, as the
        appointment reminder cron after it spent a day sending nothing because
        of one bad booking.

        `force_send=False`: the notice joins the outgoing queue like every
        other message, so a mail server that is down delays it instead of
        rolling back the change it was announcing.
        """
        template = self.env.ref(
            "bf_calendar_invite.mail_template_calendar_change",
            raise_if_not_found=False,
        )
        if not template:
            _logger.warning(
                "bf_calendar_invite: change template missing; no notice sent "
                "for %s.", self.ids,
            )
            return self.env["mail.mail"]
        sent = self.env["mail.mail"].sudo().browse()
        for event in self:
            recipients = event._bf_notice_recipients()
            if not recipients:
                continue
            try:
                with self.env.cr.savepoint():
                    mail_id = template.sudo().send_mail(
                        event.id,
                        force_send=False,
                        email_values={"recipient_ids": [(6, 0, recipients.ids)]},
                    )
                    sent |= self.env["mail.mail"].sudo().browse(mail_id)
                    # Written inside the same savepoint as the message: a
                    # notice that went out and a counter that did not would
                    # send it again at the next pull, and a counter that moved
                    # without a message would hide the meeting from the
                    # watchdog for good.
                    super(CalendarEvent, event.sudo()).write({
                        "bf_ics_sequence_notified": event.bf_ics_sequence or 0,
                        "bf_change_baseline": False,
                        "bf_change_since": False,
                    })
            except Exception:
                _logger.exception(
                    "bf_calendar_invite: could not send the change notice for "
                    "event %s; the change stands and the meeting stays on the "
                    "watchdog's list.", event.id,
                )
        return sent

    def action_bf_change_notice(self):
        """Open the dialog that proposes the notice.

        A dialog rather than an automatic send, on this side, because someone
        is at the keyboard: they have just moved the meeting and they are the
        only one who knows whether the guests already heard it in the call
        where it was decided. A notice that cannot be recalled is not a default.
        """
        return {
            "type": "ir.actions.act_window",
            "name": _("Tell the guests"),
            "res_model": "bf.calendar.event.change.notice",
            "view_mode": "form",
            # 🔴 `views` et pas seulement `view_mode` : le client web
            # préprocesse toute action par `action.views.map(...)`, et un
            # dictionnaire qui n'a que `view_mode` rend la boîte « Oups ! »
            # sans qu'aucun test Python ne puisse le voir.
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_event_ids": [(6, 0, self.ids)]},
        }

    def _bf_notice_recipients(self):
        """Partners any notice about this meeting would go to.

        Everyone invited except the organiser, who is the one making the
        change. Attendees with no address are dropped here rather than silently
        at send time, so a dialog can say how many people will actually hear
        about it.
        """
        self.ensure_one()
        # 🔴 `_origin` des deux côtés, et ce n'est pas une précaution de style.
        # Quand la boîte de dialogue n'est pas encore enregistrée, c'est-à-dire
        # TOUJOURS au moment où quelqu'un la lit, le client web calcule ce
        # champ sur un enregistrement neuf, et les participants remontent alors
        # en `NewId(origin=3)`. La soustraction compare les identifiants :
        # `NewId(origin=3) != 3`, donc elle ne retire rien, et l'organisateur
        # se retrouve dans la liste des gens à prévenir de son propre geste.
        #
        # ⚠️ Aucun test Python ne voyait ça : un assistant CRÉÉ dans un test
        # porte de vrais identifiants et la soustraction marche. C'est le banc
        # navigateur qui l'a sorti, en lisant la boîte telle qu'elle s'affiche.
        organiser = (self.user_id.partner_id | self.env.user.partner_id)._origin
        return (self.partner_ids._origin - organiser).filtered("email")

    # ------------------------------------------------------------
    # ICS identity — what makes an update an update
    # ------------------------------------------------------------

    bf_ics_uid = fields.Char(
        string="ICS UID",
        copy=False,
        index=True,
        help="Stable RFC 5545 identity of this meeting, reused by every .ics "
             "Odoo ever emits for it. Without one, each mail names a different "
             "meeting and the recipient's calendar gains a copy instead of "
             "moving the entry it already has.",
    )
    bf_ics_sequence = fields.Integer(
        string="ICS revision",
        default=0,
        copy=False,
        help="RFC 5545 SEQUENCE. Incremented whenever the time, place or title "
             "changes, so a calendar client can tell which .ics is the newer "
             "one when two arrive out of order.",
    )
    bf_ics_recurrence_id = fields.Datetime(
        string="Original occurrence start",
        copy=False,
        help="For an occurrence pulled out of its series, the slot it used to "
             "occupy. That instant is the RECURRENCE-ID: it is how a client "
             "knows WHICH occurrence moved, rather than being handed a second "
             "series.",
    )

    # Changing any of these is a new revision of the meeting in the eyes of a
    # calendar client. `description` is deliberately absent: a typo fixed in the
    # notes is not a reschedule, and bumping SEQUENCE for it would train clients
    # to re-prompt guests over nothing.
    _BF_ICS_MATERIAL_FIELDS = (
        "start", "stop", "allday", "start_date", "stop_date",
        "name", "location", "videocall_location",
    )

    def _bf_ics_domain(self):
        """Right-hand side of the UID. Cosmetic, but it must be stable.

        RFC 5545 §3.8.4.7 only asks for global uniqueness; the host part is
        convention. It is taken from `web.base.url` rather than the container
        hostname, which is what vobject uses when left to itself — a hostname
        that changes at every image rebuild, and did: the UIDs Odoo emitted
        would change at every image rebuild.
        """
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        host = (base or "").split("//")[-1].split("/")[0].split(":")[0]
        return host or "odoo"

    def _bf_ics_uid_get(self):
        """The stable UID of this event, minted and stored on first use.

        ⚠️ `x_nc_uid` wins when it exists. That field belongs to
        `calendar_nextcloud_sync` and holds the UID the remote calendar already
        knows; minting our own next to it would give one meeting two identities
        and put Odoo's mail and the CalDAV copy back in different worlds. The
        link is **soft** (`in self._fields`), because this module also runs on
        tenants with no calendar sync at all.
        """
        self.ensure_one()
        if "x_nc_uid" in self._fields and self.x_nc_uid:
            return self.x_nc_uid
        if self.bf_ics_uid:
            return self.bf_ics_uid
        uid = "%s@%s" % (uuid.uuid4(), self._bf_ics_domain())
        # sudo: the identity has to survive being read by someone who cannot
        # write the event — a portal guest opening the invitation page, the
        # mail layer rendering a template. It is bookkeeping, not content.
        self.sudo().write({"bf_ics_uid": uid})
        return uid

    def _bf_ics_identity(self):
        """(uid, recurrence_id, keep_rrule) for this event's VEVENT.

        Three shapes, and the third is the one core gets wrong:

        - a plain meeting: its own UID, no RECURRENCE-ID, no RRULE;
        - the **base** event of a series: the series UID and the RRULE;
        - **any other occurrence**: the series UID plus a RECURRENCE-ID naming
          the slot, and NO RRULE.

        Core emits the RRULE on every occurrence, so the `.ics` for one moved
        Thursday describes a whole weekly Thursday series. Measured on a weekly
        statutory meeting: the single occurrence moved to the Thursday went out
        carrying `RRULE:FREQ=WEEKLY;UNTIL=20261030T170000Z`.

        ⚠️ A detached occurrence with no recorded original slot falls back to
        an identity of its own. We know it left the series but not which slot
        it left, and claiming the series UID without a RECURRENCE-ID would
        rewrite the whole series in the guest's calendar. Standing alone is
        wrong in the small; rewriting the series is wrong in the large.
        """
        self.ensure_one()
        recurrence = self.recurrence_id
        if not recurrence:
            return self._bf_ics_uid_get(), None, bool(self.rrule)

        base = recurrence.base_event_id
        if base and base == self:
            return base._bf_ics_uid_get(), None, True

        anchor = self.bf_ics_recurrence_id or (
            self.start if self.follow_recurrence else None
        )
        if not anchor or not base:
            return self._bf_ics_uid_get(), None, False
        return base._bf_ics_uid_get(), anchor, False

    _BF_ICS_DATETIME_FIELDS = ("start", "stop")
    _BF_ICS_DATE_FIELDS = ("start_date", "stop_date")

    def _bf_ics_changed(self, fname, value):
        """Does writing `value` into `fname` actually change this event?

        ⚠️ Compared on normalised values, not raw ones. A write coming from the
        web client carries `start` as the string `"2026-09-17 18:00:00"` while
        the record holds a `datetime`; a raw `!=` finds them different every
        time and would bump the revision on every save, including saves that
        changed only a tag. A revision that moves for nothing teaches calendar
        clients to re-ask guests for nothing.
        """
        self.ensure_one()
        current = self[fname]
        if fname in self._BF_ICS_DATETIME_FIELDS:
            return fields.Datetime.to_datetime(current) != fields.Datetime.to_datetime(value)
        if fname in self._BF_ICS_DATE_FIELDS:
            return fields.Date.to_date(current) != fields.Date.to_date(value)
        return (current or False) != (value or False)

    def _bf_ics_bump(self, vals):
        """Record a new revision, the slot an occurrence is leaving, and what
        the guests still believe. Returns the meetings that actually moved.

        All three are captured **before** `super().write()`, because all three
        are statements about the value that is about to be replaced. The anchor
        in particular is only knowable now: once `start` is overwritten, the
        slot the occurrence used to hold is gone from the database, and so is
        the time the guests have in their own calendars.
        """
        touched = [f for f in self._BF_ICS_MATERIAL_FIELDS if f in vals]
        bumped = self.browse()
        if not touched:
            return bumped
        for event in self:
            really = [f for f in touched if event._bf_ics_changed(f, vals[f])]
            if not really:
                continue
            event._bf_change_baseline_capture(really)
            patch = {"bf_ics_sequence": (event.bf_ics_sequence or 0) + 1}
            leaving_series = (
                event.recurrence_id
                and event.follow_recurrence
                and not event.bf_ics_recurrence_id
                and ("start" in vals or "start_date" in vals)
                and event.recurrence_id.base_event_id != event
            )
            if leaving_series:
                patch["bf_ics_recurrence_id"] = event.start
            super(CalendarEvent, event.sudo()).write(patch)
            bumped |= event
        return bumped

    def write(self, vals):
        # ⚠️ Guarded against its own writes: `_bf_ics_bump` writes through
        # `super()` precisely so it cannot come back through here and count a
        # revision as a second revision.
        bumped = self.browse()
        if not self.env.context.get("bf_ics_skip_bump"):
            bumped = self._bf_ics_bump(vals)
        self._bf_couple_show_as(vals)
        result = super().write(vals)
        # A change learned from another calendar announces itself; a change
        # made here does not, and waits for the dialog. The flag is set by the
        # puller, and it is a plain context key rather than a field because the
        # module that sets it does not depend on this one.
        if bumped and self.env.context.get("bf_remote_change"):
            bumped._bf_announce_remote_change()
        return result

    def _bf_couple_show_as(self, vals):
        """Cancelling a meeting frees its time, whoever wrote the status.

        🔴 Written after seeing the two paths side by side in the popover: the
        footer offers "Cancel", which frees the slot and offers a notice, and
        the status group two inches away offers "Cancelled", which until now
        wrote the word and left the time booked. Two controls, the same claim,
        two different outcomes — and the wrong one is the one that looks like a
        plain field.

        Putting the rule in `write` rather than in the cancel path is what
        makes it true for every writer: the form's radio, the popover, a data
        import, and the CalDAV pull that turns an incoming `STATUS:CANCELLED`
        into this field. Someone who cancels a meeting in Nextcloud has freed
        their time there; Odoo now agrees.

        An explicit `show_as` in the same write always wins: the coupling is a
        default, not a lock.
        """
        if "bf_event_status" not in vals or "show_as" in vals:
            return
        cible = vals["bf_event_status"]
        if cible == "cancelled":
            vals["show_as"] = "free"
            return
        # Le retour en arrière, et il compte autant : décocher « annulée »
        # sur une rencontre qui redevient vraie doit lui rendre son créneau,
        # sinon elle reste invisible aux réservations pour toujours.
        if any(event.bf_event_status == "cancelled" for event in self):
            vals["show_as"] = "busy"

    def _bf_ics_organizer(self):
        """(address, display name) of the organiser, from a parsed address.

        ⚠️ `res.partner.email` is not guaranteed to hold a bare address. A
        partner holding a FORMATTED address (`"A display name"
        <mailbox@example.com>`) is enough: both core and `bf_appointment` build
        the ICS line as `"mailto:" + partner.email`, which then yields a
        `mailto:` URI carrying a display name and angle brackets inside it.
        That is not a valid URI, and a client that rejects it rejects the whole
        VEVENT. Measured on an automation user that organises most of a
        calendar.

        Parsing rather than repairing the field is deliberate: the value may be
        deliberate, and an ICS generator is not the place to decide.
        """
        self.ensure_one()
        partner = self.user_id.partner_id or self.partner_id
        if not partner:
            return None, None
        name, address = parseaddr(partner.email or "")
        if not address:
            return None, None
        return address, (partner.name or name or "").replace('"', "'")

    _BF_ICS_DATA_URI_PREFIX = "text/html,"

    def _bf_ics_description(self):
        """Plain-text description, with the data-URI wrapper peeled off.

        ⚠️ A large share of the events pulled in over CalDAV carry a
        description of the shape

            text/html,<percent-encoded html>":<the same text, in plain>

        — a `data:` URI that lost its scheme somewhere in the CalDAV ingestion,
        followed by the plain-text alternative the same producer wrote. Left
        alone it reaches the guest as `text/html,Lien%20pour%20la%20rencontre…`.

        The tail after `":` is taken when there is one, because it is the
        producer's own plain rendering — not our guess at one. Failing that the
        encoded half is decoded. Anything not starting with the marker is
        passed through untouched: reshaping a description someone actually
        wrote would be a worse defect than the one being fixed.

        ⚠️ This repairs the OUTGOING copy only. The field itself stays mangled,
        and so does the next event the sync pulls in; the ingestion is where
        that gets fixed.
        """
        self.ensure_one()
        raw = html2plaintext(self.description or "").strip()
        if not raw.startswith(self._BF_ICS_DATA_URI_PREFIX):
            return raw
        body = raw[len(self._BF_ICS_DATA_URI_PREFIX):]
        head, sep, tail = body.partition('":')
        if sep and tail.strip():
            return tail.strip()
        return html2plaintext(unquote(head)).strip()

    def _get_ics_file(self):
        """Give every emitted `.ics` an identity, a revision and a slot.

        Post-processing rather than a rewrite, for the same reason
        `bf_appointment` post-processes: core's builder carries rules we do not
        want to restate. What is added here is what core never writes at all.

        ⚠️ Core sets **no UID**. `vobject.iCalendar()` then invents one at each
        serialization, from the timestamp, a random number and the container
        hostname. Two serializations of the same event fifty minutes apart gave
        `20260909T220108Z - 57646@<container>` and
        `20260909T225231Z - 96877@<container>`. A client receiving
        `METHOD:REQUEST` under an unknown UID **adds a second entry**; it cannot
        move the one it holds. That is the whole reason a meeting moved in Odoo
        only ever landed in a guest's calendar through Nextcloud.
        """
        result = super()._get_ics_file()
        try:
            import vobject
        except ImportError:  # pragma: no cover - vobject ships with Odoo
            return result

        for event in self:
            ics = result.get(event.id)
            if not ics:
                continue
            try:
                cal = vobject.readOne(ics.decode("utf-8"))
                vevent = cal.vevent

                uid, recurrence_id, keep_rrule = event._bf_ics_identity()
                if hasattr(vevent, "uid"):
                    del vevent.contents["uid"]
                vevent.add("uid").value = uid

                if hasattr(vevent, "sequence"):
                    del vevent.contents["sequence"]
                vevent.add("sequence").value = str(event.bf_ics_sequence or 0)

                # RFC 5545 §3.8.1.11. Written here rather than only on the
                # CalDAV push, because a cancellation notice whose attachment
                # does not say CANCELLED is a paragraph of text: the guest
                # reads it, and the entry stays in their calendar.
                status = event._bf_ics_status()
                if hasattr(vevent, "status"):
                    del vevent.contents["status"]
                if status:
                    vevent.add("status").value = status

                # ⚠️ `METHOD` is set for a cancellation and left alone
                # otherwise. `METHOD:CANCEL` is what makes a client REMOVE the
                # entry instead of redrawing it; on every other path the value
                # core (or `bf_appointment`) already chose is none of this
                # method's business, and overwriting it would change what an
                # ordinary invitation does on databases that never asked.
                if event.bf_event_status == "cancelled":
                    if hasattr(cal, "method"):
                        del cal.contents["method"]
                    cal.add("method").value = "CANCEL"

                if not keep_rrule and hasattr(vevent, "rrule"):
                    del vevent.contents["rrule"]
                if recurrence_id:
                    if hasattr(vevent, "recurrence_id"):
                        del vevent.contents["recurrence-id"]
                    vevent.add("recurrence-id").value = pytz.utc.localize(
                        fields.Datetime.to_datetime(recurrence_id)
                    )

                address, cn = event._bf_ics_organizer()
                if address:
                    if hasattr(vevent, "organizer"):
                        del vevent.contents["organizer"]
                    organizer = vevent.add("organizer")
                    organizer.value = "mailto:" + address
                    if cn:
                        organizer.params["CN"] = [cn]

                description = event._bf_ics_description()
                if hasattr(vevent, "description"):
                    del vevent.contents["description"]
                if description:
                    vevent.add("description").value = description

                result[event.id] = cal.serialize().encode("utf-8")
            except Exception:  # pragma: no cover - never lose the invitation
                _logger.exception(
                    "bf_calendar_invite: could not stamp the ICS of event %s; "
                    "the unstamped file is sent as-is.", event.id,
                )
        return result
