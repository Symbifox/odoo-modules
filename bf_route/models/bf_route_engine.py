"""Client of the self-hosted engines: OSRM (roads, distances, times), VROOM (order of the
stops) and Nominatim (coordinates of an address).

⚠️ The addresses of the engines come from the system parameters only, set by an
administrator. Nothing the worker or a customer types reaches a URL here.
⚠️ No third-party service: without an engine configured, the buttons say so and the
order stays manual. OSRM and VROOM get coordinates only. Nominatim gets the street,
city, postal code, province and country of a contact without coordinates, never a
name, a phone or an email.
"""
import logging
import time

import requests
from markupsafe import Markup

from odoo import _, api, models
from odoo.tools.misc import formatLang
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TIMEOUT = 20
OSRM_PARAM = "bf_route.osrm_url"
VROOM_PARAM = "bf_route.vroom_url"
NOMINATIM_PARAM = "bf_route.nominatim_url"
# Nominatim's rank of a result: 30 a building, 28 a house number placed along its street,
# 26 and 27 a street, 16 a town. Coarser than a street, the point can be kilometres from the
# door: refused. A street is good enough to plan the kilometres, not to send the worker to:
# the phone navigates to the contact's coordinates, so only a house number is written there
# (a rural road can run for kilometres).
MIN_PLACE_RANK = 26
SAVE_PLACE_RANK = 28
# Geocoding runs in the user's request: past this, the rest waits for the next click.
GEOCODE_SECONDS = 20
GEOCODE_TIMEOUT = 5


class BfRouteEngine(models.AbstractModel):
    _name = "bf.route.engine"
    _description = "Routing engine (OSRM and VROOM)"

    @api.model
    def _url(self, param):
        return (self.env["ir.config_parameter"].sudo().get_param(param) or "").strip().rstrip("/")

    @api.model
    def _points(self, start, partners, end):
        """[lon, lat] of the depot, each customer, and the return point: the contact's
        coordinates, or else the point of its street. Refuses when one has neither, naming
        who, and why (not found, or not reached in time)."""
        everyone = [start] + list(partners) + [end]
        untried = self._geocode(start | partners | end)["untried"]

        def point(p):
            if p.partner_latitude or p.partner_longitude:
                return [p.partner_longitude, p.partner_latitude]
            if p.bf_route_street_latitude or p.bf_route_street_longitude:
                return [p.bf_route_street_longitude, p.bf_route_street_latitude]
            return None

        missing = [p for p in everyone if point(p) is None]
        if not missing:
            return [point(p) for p in everyone]
        if not self._url(NOMINATIM_PARAM):
            raise UserError(_("These places have no coordinates yet: %s.",
                              ", ".join(dict.fromkeys(p.display_name for p in missing))))
        parts = []
        late = [p.display_name for p in missing if p.id in untried]
        lost = [p.display_name for p in missing if p.id not in untried]
        if lost:
            parts.append(_("No coordinates found for these addresses: %s. Check the street, "
                           "the city and the postal code on the contact.",
                           ", ".join(dict.fromkeys(lost))))
        if late:
            parts.append(_("Not located yet (the geocoder did not get to them in time): %s. Locate "
                           "them from the contacts (Action, Locate on the map (routes)), then try "
                           "again.", ", ".join(dict.fromkeys(late))))
        raise UserError(" ".join(parts))

    @api.model
    def _geocode(self, partners, retry_street=False):
        """Coordinates for the partners that have none, from the self-hosted Nominatim.

        A house number is written as the contact's coordinates; a street without its number
        only as the street point (bf_route_street_*), which plans the kilometres but never
        guides the phone, and is not asked again. Returns {"found": {partner id: (lat, lon,
        rank)}, "untried": partner ids not reached (time budget spent, or Nominatim down)}.
        retry_street: ask again for the contacts that only have a street point (the map may
        know the number by now); a plan never does, so it never waits on them.
        """
        todo = partners.filtered(lambda p: not (p.partner_latitude or p.partner_longitude or (
            not retry_street and (p.bf_route_street_latitude or p.bf_route_street_longitude))))
        base = self._url(NOMINATIM_PARAM)
        out = {"found": {}, "untried": set()}
        if not base or not todo:
            return out
        until = time.monotonic() + GEOCODE_SECONDS
        for index, partner in enumerate(todo):
            if time.monotonic() > until:
                out["untried"].update(todo[index:].ids)
                break
            try:
                result = self._lookup(base, partner)
            except UserError:
                # Unreachable: the next ones would wait as long for the same answer.
                out["untried"].update(todo[index:].ids)
                break
            if not result:
                continue
            out["found"][partner.id] = result
            if result[2] >= SAVE_PLACE_RANK:
                partner.sudo().write({"partner_latitude": result[0], "partner_longitude": result[1]})
            else:
                partner.sudo().write({"bf_route_street_latitude": result[0],
                                      "bf_route_street_longitude": result[1]})
        return out

    @api.model
    def _lookup(self, base, partner):
        """(lat, lon, rank) of a partner's address, or None. Tries with the postal code, then
        without: a postal code OSM does not know would otherwise hide a good street."""
        address = {
            "street": (partner.street or "").strip(),
            "city": (partner.city or "").strip(),
            "postalcode": (partner.zip or "").strip(),
            "state": (partner.state_id.name or "").strip(),
        }
        if not (address["street"] and (address["city"] or address["postalcode"])):
            return None
        # A contact without a country is looked up in the company's, not in the whole world.
        country = (partner.country_id.code or self.env.company.country_id.code or "").lower()
        tries = [address]
        if address["postalcode"] and address["city"]:
            tries.append(dict(address, postalcode=""))
        for query in tries:
            params = {key: value for key, value in query.items() if value}
            params.update({"format": "jsonv2", "limit": 1})
            if country:
                params["countrycodes"] = country
            result = self._read_point(self._call("GET", base + "/search", params=params,
                                                 timeout=GEOCODE_TIMEOUT))
            if result:
                return result
        return None

    @staticmethod
    def _read_point(data):
        """(lat, lon, rank) of Nominatim's best answer when it is precise enough, else None."""
        try:
            best = data[0]
            rank = int(best.get("place_rank") or 0)
            if rank < MIN_PLACE_RANK:
                return None
            lat, lon = float(best["lat"]), float(best["lon"])
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            return None
        return lat, lon, rank

    @api.model
    def _call(self, method, url, timeout=TIMEOUT, **kwargs):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            # The host only: the path carries customers' coordinates.
            _logger.warning("bf_route: routing engine %s unreachable: %s",
                            "/".join(url.split("/")[:3]), type(exc).__name__)
            raise UserError(_("The routing engine does not answer. The order stays as it is."))
        try:
            return response.json()
        except ValueError:
            raise UserError(_("The routing engine sent an unreadable answer (HTTP %s).",
                              response.status_code))

    @api.model
    def _route(self, points):
        """Distance (m) and duration (s) of the road through ``points``, and of each leg."""
        base = self._url(OSRM_PARAM)
        if not base:
            raise UserError(_("No routing engine: enter the OSRM address in the Routes settings."))
        coords = ";".join("%.6f,%.6f" % (lon, lat) for lon, lat in points)
        data = self._call("GET", "%s/route/v1/driving/%s" % (base, coords),
                          params={"overview": "false", "steps": "false"})
        if data.get("code") != "Ok" or not data.get("routes"):
            raise UserError(_("The routing engine found no road (%s).",
                              data.get("message") or data.get("code") or "-"))
        try:
            best = data["routes"][0]
            return {
                "distance": self._number(best["distance"]),
                "duration": self._number(best["duration"]),
                "legs": [{"distance": self._number(leg["distance"]),
                          "duration": self._number(leg["duration"])}
                         for leg in best.get("legs", [])],
            }
        except (KeyError, TypeError, ValueError, IndexError):
            raise UserError(_("The routing engine sent an unreadable answer (HTTP %s).", "200"))

    @staticmethod
    def _number(value):
        """A finite, non-negative number from the engine, or ValueError."""
        value = float(value)
        if value != value or value < 0 or value > 1e9:
            raise ValueError(value)
        return value

    @api.model
    def _optimize(self, start, end, stops, start_seconds=0):
        """The stops in VROOM's order. Time windows are seconds since local midnight.

        Returns {"ordered": stops in the new order, "unassigned": stops VROOM could not
        fit (kept at the end, never dropped)}. The note for the chatter is written by the
        caller once OSRM has measured the new order (`_optimized_note`): VROOM's own
        distance is an estimate, and the chatter must say what the form says.
        """
        base = self._url(VROOM_PARAM)
        if not base:
            raise UserError(_("No optimizer: enter the VROOM address in the Routes settings."))
        points = self._points(start, stops.mapped("partner_id"), end)
        jobs = []
        for index, (stop, location) in enumerate(zip(stops, points[1:-1]), 1):
            job = {"id": index, "location": location, "service": int(stop.service_minutes * 60)}
            if stop.window_start or stop.window_end:
                job["time_windows"] = [[int((stop.window_start or 0.0) * 3600),
                                        int((stop.window_end or 24.0) * 3600)]]
            jobs.append(job)
        payload = {
            "vehicles": [{"id": 1, "profile": "car", "start": points[0], "end": points[-1],
                          "time_window": [int(start_seconds), 48 * 3600]}],
            "jobs": jobs,
            # Without the geometry, VROOM leaves the distance of the summary empty.
            "options": {"g": True},
        }
        data = self._call("POST", base + "/", json=payload)
        if data.get("code") != 0:
            raise UserError(_("The optimizer could not order the stops (%s).",
                              data.get("error") or data.get("code")))
        by_index = dict(enumerate(stops, 1))
        try:
            steps = data["routes"][0]["steps"] if data.get("routes") else []
            ordered = stops.browse()
            for step in steps:
                if step.get("type") == "job":
                    ordered |= by_index[step.get("id", step.get("job"))]
            unassigned = stops.browse()
            for item in data.get("unassigned", []):
                unassigned |= by_index[item["id"]]
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            raise UserError(_("The optimizer could not order the stops (%s).", "?"))
        unassigned |= stops - ordered - unassigned
        return {"ordered": ordered | unassigned, "unassigned": unassigned}

    @api.model
    def _optimized_note(self, km, minutes, unassigned):
        """The chatter line after an optimization, numbers in the reader's language."""
        line = _("Order optimized: %(km)s km, %(min)s min of driving.",
                 km=formatLang(self.env, km, digits=1), min=minutes)
        if unassigned:
            line += " " + _("Could not fit in their time window, left at the end: %s.",
                            ", ".join(unassigned.mapped("partner_id.display_name")))
        return Markup("<p>%s</p>") % line
