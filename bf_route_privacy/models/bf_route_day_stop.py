"""Every position erased by bf_route's watcher leaves an entry in the register.

⚠️ This bridge erases nothing. The erasure belongs to `_purge_positions`, in `bf_route`,
and its term belongs to each company's setting. This module hooks the extension point the
purge calls once its write is done, and records what has just left.

🔴 **It refuses to attest to an emptiness it cannot see.** Before writing, it reads the row
back: if the position is still there, it raises, the transaction unwinds and the watcher
comes back in fifteen minutes. Lesson of an earlier bridge, where the register certified a
"CERTIFICATE OF DESTRUCTION" over anonymisations that had rewritten no field at all. A
register that can lie is worth nothing; an erasure postponed beats a false attestation of
one.

⚠️ Since bf_route 18.0.1.4.0 the purge runs each company inside its own savepoint, so this
refusal costs the erasure of ONE company's pass and not of all of them.
"""
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_route.models.tools import utc_to_local

_logger = logging.getLogger(__name__)

# The fields the purge sets back to zero, in the order the entry names them.
ERASED_FIELDS = ("latitude", "longitude", "accuracy")

CALENDAR = "bf_route_privacy.retention_route_position"


class BfRouteDayStop(models.Model):
    _inherit = "bf.route.day.stop"

    def _bf_route_positions_erased(self, company, days):
        """Record in the register the positions the purge has just erased."""
        result = super()._bf_route_positions_erased(company, days)
        if not self:
            return result
        stops = self.sudo()
        # The purge's write may still be in cache: the read-back has to reach what is really
        # in the table, or the check merely repeats what we just said.
        self.env.flush_all()
        stops.invalidate_recordset(list(ERASED_FIELDS) + ["position_erased"])
        for stop in stops:
            self._assert_position_really_gone(stop)
        calendar = self.env.ref(CALENDAR, raise_if_not_found=False)
        now = fields.Datetime.now()
        user = self.env.user
        vals_list = [self._position_register_vals(stop, company, days, calendar, now, user)
                     for stop in stops]
        entries = self.env["privacy.destruction.register"].sudo().create(vals_list)
        _logger.info("bf_route_privacy: %s erased position(s) recorded in the register for %s "
                     "(kept %s day(s))", len(entries), company.display_name, days)
        return result

    def _marked_label(self, stop):
        """When the stop was marked, in the time zone of the route, named.

        ⚠️ The entry is immutable: an unlabelled "2026-09-10 06:30:26" is UTC, and a stop
        marked at 14:00 Eastern seals as 18:00. The module is scrupulous about time zones
        everywhere else (a route runs in the time zone of the person responsible for it),
        and a sealed line is the last place to stop being. Named rather than converted
        silently, because a reader cannot tell one from the other afterwards.
        """
        if not stop.done_at:
            return _("unknown date")
        route = stop.day_id.route_id.sudo()
        tz = route._tz() if route else None
        if not tz:
            return "%s UTC" % fields.Datetime.to_string(stop.done_at)
        return "%s (%s)" % (utc_to_local(tz, stop.done_at).strftime("%Y-%m-%d %H:%M:%S"), tz)

    def _assert_position_really_gone(self, stop):
        """Raise rather than let an entry say "erased" of a position still in the table."""
        left = [name for name in ERASED_FIELDS if stop[name]]
        if left:
            raise UserError(_(
                "The register refuses to attest the erasure of the position at the stop of "
                "\"%(stop)s\": %(fields)s still hold a value. Nothing was recorded and the "
                "erasure is undone; bf_route's purge will come back on its next pass.",
                stop=stop.display_name, fields=", ".join(left)))
        if not stop.position_erased:
            raise UserError(_(
                "The register refuses to attest the erasure of the position at the stop of "
                "\"%(stop)s\": the \"position erased\" mark is not set, so nothing on the "
                "record says a position ever existed.", stop=stop.display_name))

    def _position_register_vals(self, stop, company, days, calendar, now, user):
        """The immutable entry of s. 3.2 of Law 25 for one erased position."""
        marked = self._marked_label(stop)
        lines = [
            _("Position read on the phone on %(marked)s, at the \"%(customer)s\" stop of the "
              "day \"%(day)s\", erased at the end of the company's %(days)s day(s) of "
              "retention.",
              marked=marked, customer=stop.partner_id.display_name or _("customer removed"),
              day=stop.day_id.display_name or "-", days=days),
            _("Fields set back to zero: %s.", ", ".join(ERASED_FIELDS)),
            # Say what SURVIVES as well: an entry that lists only what was destroyed reads as
            # though nothing was left of the person.
            _("Not covered, and still held: the stop itself, its arrival time, its marking "
              "time, the person who marked it, their note and the time on site, which are the "
              "proof of the visit and follow the project file; the position notice as it was "
              "read (bf.route.notice.ack), which is the proof of the prior information of "
              "s. 8.1 and must outlive the position it made lawful; the kilometres of the day "
              "measured from the readings before they were erased, which is a distance and "
              "not a place and which falls under rule ROU-002, six years; and, where an "
              "office person accepted one of these readings as a customer's place on the map, "
              "that copy, which has become the customer's own address and carries on the "
              "contact the date it was accepted."),
            _("Automatic erasure under rule %(rule)s. The approval is the rule itself, "
              "approved when the retention term was decided; there is no case-by-case "
              "approval, the erasure bearing on three fields and not being triggered by a "
              "person. The account named as having destroyed and approved is the one the "
              "watcher runs under.",
              rule=calendar.code if calendar else "ROU-001"),
        ]
        vals = {
            "destruction_date": now,
            "destroyed_by_id": user.id,
            "approved_by_id": user.id,
            "res_model": "bf.route.day.stop",
            "res_id": stop.id,
            "res_name": stop.display_name,
            "document_description": "\n".join(lines),
            "pi_categories": _(
                "Latitude, longitude and accuracy in metres of the person on the road, read "
                "once at the moment they marked the stop."),
            "subject_count": 1,
            # `delete` and not `secure_wipe`: the three columns are set back to zero in
            # PostgreSQL. The underlying blocks and the backups already taken are not
            # touched, and declaring a secure wipe would be a surclaim.
            "destruction_method": "delete",
            # 🔴 PARTIAL scope, and the field has to say so: the stop lives on, short of its
            # position. Declaring "complete" would assert the row no longer exists, and the
            # register's own guard would refuse the entry since it still does.
            "destruction_scope": "partial",
            "res_field": ", ".join(ERASED_FIELDS),
            "legal_basis": _(
                "Section 23 of Quebec's Law 25: personal information is destroyed when the "
                "purpose for which it was collected is accomplished. The purpose is proving "
                "the visit and settling a disagreement about a delivery. Section 8.1: the "
                "person had been informed beforehand of the reading and of the means offered "
                "to activate it. Section 3.2: framework for retention and destruction."),
            "company_id": company.id,
        }
        if calendar:
            vals["retention_calendar_id"] = calendar.id
        return vals
