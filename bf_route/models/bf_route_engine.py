"""Client of the self-hosted routing engines: OSRM (roads, distances, times) and VROOM
(order of the stops).

⚠️ The addresses of the engines come from the system parameters only, set by an
administrator. Nothing the worker or a customer types reaches a URL here.
⚠️ No third-party service: without an engine configured, the buttons say so and the
order stays manual. Coordinates are sent; names and addresses never are.
"""
import logging

import requests
from markupsafe import Markup

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TIMEOUT = 20
OSRM_PARAM = "bf_route.osrm_url"
VROOM_PARAM = "bf_route.vroom_url"


class BfRouteEngine(models.AbstractModel):
    _name = "bf.route.engine"
    _description = "Routing engine (OSRM and VROOM)"

    @api.model
    def _url(self, param):
        return (self.env["ir.config_parameter"].sudo().get_param(param) or "").strip().rstrip("/")

    @api.model
    def _points(self, start, partners, end):
        """[lon, lat] of the depot, each customer, and the return point.
        Refuses when one has no coordinates, naming who."""
        everyone = [start] + list(partners) + [end]
        missing = [p.display_name for p in everyone
                   if not (p.partner_latitude or p.partner_longitude)]
        if missing:
            raise UserError(_("These places have no coordinates yet: %s.",
                              ", ".join(dict.fromkeys(missing))))
        return [[p.partner_longitude, p.partner_latitude] for p in everyone]

    @api.model
    def _call(self, method, url, **kwargs):
        try:
            response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
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
        fit (kept at the end, never dropped), "summary": a line for the chatter}.
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
            summary = data.get("summary") or {}
            km = self._number(summary.get("distance") or 0) / 1000.0
            minutes = round(self._number(summary.get("duration") or 0) / 60)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            raise UserError(_("The optimizer could not order the stops (%s).", "?"))
        unassigned |= stops - ordered - unassigned
        line = _("Order optimized: %(km).1f km, %(min)s min of driving.", km=km, min=minutes)
        if unassigned:
            line += " " + _("Could not fit in their time window, left at the end: %s.",
                            ", ".join(unassigned.mapped("partner_id.display_name")))
        return {"ordered": ordered | unassigned, "unassigned": unassigned,
                "summary": Markup("<p>%s</p>") % line}
