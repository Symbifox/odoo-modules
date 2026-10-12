from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

from .bf_route_engine import NOMINATIM_PARAM, SAVE_PLACE_RANK

# One click, one request each, in the user's request: past this, select fewer.
MAX_TO_LOCATE = 200
# A change to one of these moves the contact: its coordinates no longer hold. Not street2:
# another unit in the same building is the same door for the truck.
ADDRESS_FIELDS = ("street", "city", "zip", "state_id", "country_id")
COORDINATE_FIELDS = ("partner_latitude", "partner_longitude",
                     "bf_route_street_latitude", "bf_route_street_longitude")


def _same(old, new):
    """Equal once spaces and case are set aside: "H7A1A1" is "h7a 1a1"."""
    if hasattr(old, "id"):
        return old.id == (new or False)
    def norm(value):
        return "".join(str(value or "").split()).casefold()
    return norm(old) == norm(new)


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_route_street_latitude = fields.Float(
        string="Street latitude (routes)", digits=(10, 7), copy=False,
        help="Point of the street when the geocoder found the street but not the number: it plans "
             "the kilometres, it never guides the phone.")
    bf_route_street_longitude = fields.Float(string="Street longitude (routes)", digits=(10, 7), copy=False)

    def write(self, vals):
        """Coordinates follow the address: a contact that moves loses the old ones, so the
        worker is not sent to the former door (Nominatim locates the new address when a
        route needs it). Writing coordinates with the address keeps them."""
        if (any(field in vals for field in COORDINATE_FIELDS)
                or not any(field in vals for field in ADDRESS_FIELDS)):
            return super().write(vals)
        moved = self.filtered(lambda p: any(p[f] for f in COORDINATE_FIELDS)
                              and any(not _same(p[f], vals[f]) for f in ADDRESS_FIELDS if f in vals))
        result = super().write(vals)
        if moved:
            super(ResPartner, moved).write(dict.fromkeys(COORDINATE_FIELDS, 0.0))
        return result

    def action_bf_route_locate(self):
        """Coordinates of the selected contacts, from the self-hosted Nominatim (Routes).

        ⚠️ Public method: reachable over RPC by any user who can read a contact. Route
        managers only.
        """
        if not (self.env.su or self.env.user.has_group("bf_route.group_route_manager")):
            raise AccessError(_("Only a route manager can do this."))
        Engine = self.env["bf.route.engine"]
        if not Engine._url(NOMINATIM_PARAM):
            raise UserError(_("No geocoder: enter the Nominatim address in the Routes settings."))
        # A street point is asked again: the map may know the number by now.
        todo = self.filtered(lambda p: not (p.partner_latitude or p.partner_longitude))
        if len(todo) > MAX_TO_LOCATE:
            raise UserError(_("Select at most %s contacts without coordinates at a time.", MAX_TO_LOCATE))
        if not todo:
            message, kind = _("Nothing to locate: these contacts already have coordinates."), "info"
        else:
            result = Engine._geocode(todo, retry_street=True)
            found, untried = result["found"], result["untried"]
            saved = todo.filtered(lambda p: p.id in found and found[p.id][2] >= SAVE_PLACE_RANK)
            street_only = todo.filtered(lambda p: p.id in found) - saved
            late = todo.filtered(lambda p: p.id in untried)
            missing = todo - saved - street_only - late
            message = _("%(located)s of %(total)s contacts located.", located=len(saved), total=len(todo))
            if street_only:
                message += " " + _("Street found, but not the number (kept for the plans only): %s.",
                                   self._bf_route_names(street_only))
            if missing:
                message += " " + _("Not found (check the street, city and postal code): %s.",
                                   self._bf_route_names(missing))
            if late:
                message += " " + _("Not tried yet (the geocoder ran out of time or did not answer): %s. "
                                   "Run this again.", self._bf_route_names(late))
            kind = "warning" if (missing or street_only or late) else "success"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Locate on the map"), "message": message, "type": kind,
                       "sticky": kind == "warning"},
        }

    @staticmethod
    def _bf_route_names(partners):
        names = partners[:20].mapped("display_name")
        return ", ".join(names) + (", …" if len(partners) > 20 else "")
