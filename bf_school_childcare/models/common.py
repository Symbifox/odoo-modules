import re
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: Child care runs Monday to Friday; index = date.weekday().
DAY_FIELDS = ("mon", "tue", "wed", "thu", "fri")
#: Who brought a record to the school.
CHANNELS = [("portal", "Family portal"), ("office", "School office")]


def forged(vals, defaults):
    """The reserved fields of `vals` that carry a value a record is not born with.

    🔴 Refuse the forged VALUE, never the key: the web client sends the defaults of every
    field of the form when it saves a new record (only a browser walk sees it), and an empty
    or default value is harmless since the system writes its own right after.
    """
    return sorted(name for name, default in defaults.items()
                  if vals.get(name) and vals[name] != default)


def normalise_name(name):
    """A name as compared with a pick-up restriction: no accents, no case, no punctuation
    ("L'oncle Jean-Pierre," reads "l oncle jean pierre"), single spaces."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def time_label(hour):
    """15.5 -> "15:30"."""
    minutes = int(round((hour or 0.0) * 60))
    return "%02d:%02d" % divmod(minutes, 60)


class ChildcareWeekdays(models.AbstractModel):
    """The days of the week a period, a registration line or a permission covers."""

    _name = "bf.school.childcare.weekdays"
    _description = "Days of the week (child care)"

    mon = fields.Boolean("Mon.")
    tue = fields.Boolean("Tue.")
    wed = fields.Boolean("Wed.")
    thu = fields.Boolean("Thu.")
    fri = fields.Boolean("Fri.")

    def _weekday_ticked(self, day):
        self.ensure_one()
        return day.weekday() < 5 and bool(self[DAY_FIELDS[day.weekday()]])

    def _weekday_count(self):
        self.ensure_one()
        return sum(bool(self[name]) for name in DAY_FIELDS)

    def _weekday_label(self):
        """The days ticked, in the reader's language: "Monday, Wednesday"."""
        self.ensure_one()
        names = {"mon": _("Monday"), "tue": _("Tuesday"), "wed": _("Wednesday"), "thu": _("Thursday"),
                 "fri": _("Friday")}
        return ", ".join(names[name] for name in DAY_FIELDS if self[name])


class ChildcareNotify(models.AbstractModel):
    """Tells every adult with parental authority over the child, each in their language."""

    _name = "bf.school.childcare.notify"
    _description = "Child care notices to the families"

    def _school_notify_authority(self, xmlid, **context):
        """One email per adult with parental authority over the record's student.

        🔴 Every one of them, not only those who receive the school's announcements: who may
        pick up a child concerns each holder of parental authority (shared custody included).
        """
        template = self.env.ref(xmlid, raise_if_not_found=False)
        if not template:
            return
        layout = self.env["bf.school"]._school_mail_layout()
        for record in self.sudo():
            adults = record.student_id.student_guardian_link_ids.filtered(
                "has_parental_authority").guardian_id.filtered("email")
            for adult in adults:
                lang = adult.lang or record.company_id.partner_id.lang or "fr_CA"
                template.sudo().with_context(lang=lang, school_lang=lang, **context).send_mail(
                    record.id, force_send=False, email_layout_xmlid=layout,
                    email_values={"recipient_ids": [(6, 0, adult.ids)], "email_to": False})
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()


class ChildcareGuarded(models.AbstractModel):
    """Reserved fields: moved by the methods of the model, never typed or sent by RPC."""

    _name = "bf.school.childcare.guarded"
    _description = "Child care record with reserved fields"

    #: {field: value a record is born with}. The system writes them in sudo.
    _school_system_defaults = {}

    @api.model
    def _school_guard_create(self, vals_list):
        """🔴 At creation too: `readonly` guards the screen only, and a record born "active" or
        "signed" skipped every check of the button that should have put it there."""
        if self.env.su:
            return
        for vals in vals_list:
            bad = forged(vals, self._school_system_defaults)
            if bad:
                raise UserError(_("These fields are set by the school's process, not by hand: %s.",
                                  ", ".join(self._fields[name].string for name in bad)))
            vals.update(self._school_system_defaults)

    def _school_guard_write(self, vals):
        if not self.env.su and set(self._school_system_defaults) & set(vals):
            raise UserError(_("These fields are set by the school's process, not by hand: %s.",
                              ", ".join(self._fields[name].string
                                        for name in sorted(set(self._school_system_defaults) & set(vals)))))

    @api.model
    def _school_today(self, school):
        return school._calendar_today()

    @api.model
    def _school_now_utc(self):
        return fields.Datetime.now()
