{
    "name": "BF BI Reports",
    "version": "18.0.1.0.1",
    "category": "Productivity/Dashboard",
    "summary": "Power BI style reports on named measures: slicers, cross-filtering, drill-through, rich visuals",
    "description": """
Symbifox BI reports
===================

Reports laid out on a canvas, read like a Power BI report: period and customer
slicers at the top, every visual filtering the others when clicked, pages as tabs,
customer detail pages opened by a right-click, and pages that keep their own period
or customers.

Tiles, column, bar, line and donut charts, tables, gauges, waterfalls and heat maps,
designed on screen by BI designers (panes, drag and drop, history of 30 versions),
in the Symbifox palette or the company's colors. Five ready-made reports for the
time, customer experience, hosting and hour bank bridges of bf_bi.

The values are the named measures of bf_bi, always computed for the person looking,
under their own access rights. Charts use Chart.js, which ships with Odoo.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_bi"],
    "data": [
        "security/bf_bi_report_security.xml",
        "security/ir.model.access.csv",
        "views/bf_bi_report_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_bi_report/static/src/report/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
