{
    "name": "Symbifox Work Routes: Time Sheets",
    "version": "18.0.1.0.0",
    "category": "Services/Timesheets",
    "summary": "A finished route day logs the worker's time, from the start of the day to its end, "
               "on the route's project",
    "description": """
Time sheets for work routes
===========================

Installs itself when both Work routes and Timesheets are installed.

- a route names the project (and the task, if any) its time goes to; the
  settings give a default project for the routes that name none;
- when the worker finishes the day on the phone, one time sheet line is
  logged for them: the time between "Start the day" and "Finish the day",
  with the route and the stops;
- without a project, nothing is logged. A worker without an employee in the
  company gets a note on the day instead.

Quebec's Act respecting labour standards (s. 57) counts the travel the employer
requires as work time: the drive between customers is paid time. The day runs
from the moment the worker starts it on the phone.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_route", "hr_timesheet"],
    "data": [
        "views/bf_route_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": True,
}
