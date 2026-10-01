{
    "name": "BF BI",
    "version": "18.0.1.6.1",
    "category": "Productivity/Dashboard",
    "summary": "Design and edit dashboards on screen, straight from Odoo's views",
    "description": """
Symbifox BI
===========

Makes the dashboards of Odoo's Dashboards app editable in Community:

- spreadsheet editor (Odoo's o-spreadsheet engine) that saves itself, and
  refuses to overwrite a version someone else saved in the meantime;
- "Add to a dashboard" button in pivot and graph views, as a pivot table,
  a chart or an indicator (one big number compared with the previous period);
- "Period" and "Customer" filters created and linked automatically;
- "BI Designer" group; a new dashboard is visible to designers only until it
  is opened to other groups;
- version history whose access follows the dashboard's;
- dashboards shipped by Odoo are duplicated before being edited (an Odoo
  update would rewrite them);
- the public "Share" link is reserved to the "Share a dashboard by public
  link" group.

Figures drawn from Odoo are re-read under the rights of each person who
opens the dashboard.

Named measures: "Margin", "Billable hours"... defined once, used everywhere with
=BF.MEASURE("code"), always computed for the person looking, under their own
rights, following the dashboard's Period and Customer filters.

External sources (Grist): a "data connection" closed by default, opened to
groups or people. The formula =BF.SOURCE("code", "Table") spills the table in
the spreadsheet; every read checks the rights of the person looking, and the
API key is only ever seen by administrators:
it is never sent to the people reading dashboards.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["spreadsheet_dashboard"],
    "data": [
        "security/bf_bi_security.xml",
        "security/ir.model.access.csv",
        "data/spreadsheet_dashboard_group.xml",
        "views/spreadsheet_dashboard_views.xml",
        "views/bf_bi_dashboard_version_views.xml",
        "views/bf_bi_connection_views.xml",
        "views/bf_bi_measure_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_bi/static/src/backend/**/*",
        ],
        # Les vues tableau croisé et graphique d'Odoo 18 vivent dans ce paquet chargé
        # à la demande : le bouton « Add to a dashboard » doit y être aussi.
        "web.assets_backend_lazy": [
            "bf_bi/static/src/lazy/**/*",
        ],
        "spreadsheet.o_spreadsheet": [
            "bf_bi/static/src/bundle/**/*.js",
            "bf_bi/static/src/bundle/**/*.xml",
        ],
    },
    "installable": True,
    "application": False,
}
