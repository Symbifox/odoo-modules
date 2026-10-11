{
    "name": "Symbifox Work Routes",
    "version": "18.0.1.0.5",
    "category": "Services/Field Service",
    "summary": "Recurring routes for people on the road: stops in order, proof at each stop, "
               "mileage, alerts, and a phone screen for the worker",
    "description": """
Work routes
===========

Routes for the people who spend their day on the road: delivery drivers,
technicians, sales representatives.

- a route is a template: its stops in order (customer, time window, time on
  site, access instructions), its default worker and vehicle, and the days it
  runs (every week or every N weeks);
- route days are created a few days ahead from the template; a stop can be
  added to one day without touching the template;
- the worker follows the day on the phone ("My route"): the stops in order, a
  link to the phone's maps app, one tap to mark a stop done, absent or
  postponed, with a note. Marks made without network are sent later and keep
  the phone's time;
- proof at the stop: the time, and the position read once when the stop is
  marked, only if the company turned it on and the worker has read the notice
  (Quebec Law 25, s. 8.1). Positions are erased after the retention period.
  There is no continuous tracking;
- mileage: odometer at the start and end of the day, logged to the vehicle;
  planned distance from a self-hosted OSRM;
- "Optimize the order" sends the stops to a self-hosted VROOM. No address
  leaves for a third party. Without the service, the order stays manual;
- a heavy vehicle (gross weight of 4,500 kg or more) asks for the pre-trip
  inspection before the day starts;
- the person responsible gets one activity per problem: a day not started on
  time, a day left open, stops missed.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["fleet", "mail"],
    "external_dependencies": {"python": ["pytz", "requests"]},
    "data": [
        "security/bf_route_security.xml",
        "security/ir.model.access.csv",
        "data/bf_route_data.xml",
        "views/bf_route_views.xml",
        "views/bf_route_day_views.xml",
        "views/fleet_vehicle_views.xml",
        "views/res_config_settings_views.xml",
        "views/bf_route_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_route/static/src/window_time/window_time_field.js",
            "bf_route/static/src/ma_route/ma_route.js",
            "bf_route/static/src/ma_route/ma_route.xml",
            "bf_route/static/src/ma_route/ma_route.scss",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
