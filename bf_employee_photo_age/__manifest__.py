# -*- coding: utf-8 -*-
{
    "name": "Symbifox Âge de la photo des employés",
    # 18.0.1.0.1: first public release. The date of the photo on an
    #   employee's file, and a reminder once it is old.
    "version": "18.0.1.0.1",
    "category": "Human Resources/Employees",
    "summary": "Know how old an employee's photo is, and remind them to update it",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "depends": ["hr", "mail"],
    "description": """
Symbifox Employee Photo Age
===========================

* **Photo taken on**: the date the current photo was put on file. A drawn
  avatar (an SVG) is not a photo.
* Two filters: "Photo to update" and "No photo on file".
* A weekly reminder on the person's own contact once the photo is older than
  the set number of months (24 by default, 0 turns it off). It closes by
  itself when a new photo is on file.
""",
    "data": [
        "data/photo_age_data.xml",
        "views/hr_employee_views.xml",
    ],
    "post_init_hook": "post_init_hook",
}
