"""Modèles de rapports livrés : un par pont de bf_bi, et un rapport de direction qui les réunit.

Un modèle n'est proposé que si ses ponts sont installés et ses mesures trouvées. Les noms
(rapport, description, pages, titres) sont des textes source en anglais ; leurs traductions
vivent dans les catalogues du module, et le rapport créé reçoit son nom dans chaque langue
installée. Les visuels n'ont pas de titre saisi (il ne vaudrait que dans une langue) : ils
prennent le nom de leur mesure, traduit pour chaque lecteur ; une cascade dit en plus « ce qui a
changé ».
"""
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)


def _v(vid, type_, measures, dimension=None, x=0, y=0, w=3, h=2, **extra):
    visual = {"id": vid, "type": type_, "measures": measures, "x": x, "y": y, "w": w, "h": h, **extra}
    if dimension:
        visual["dimension"] = dimension
    return visual


def _kpis(codes, y=0, prefix="k"):
    return [_v("%s%s" % (prefix, n + 1), "kpi", [code], None, 3 * n, y, 3, 2) for n, code in enumerate(codes)]


CUSTOMER_DETAIL = _lt("Customer detail")

TEMPLATES = [
    {
        "key": "time",
        "name": _lt("Time and margin"),
        "description": _lt("Hours, revenue and margin by month and by customer, what changed in revenue since "
                           "the previous period, and a detail page for each customer."),
        "modules": ["bf_bi_timesheet"],
        "pages": [
            {"name": _lt("Overview"), "visuals": [
                *_kpis(["hours", "revenue", "margin", "margin_rate"]),
                _v("c1", "column", ["hours"], "month", 0, 2, 8, 4),
                _v("d1", "donut", ["hours"], "customer", 8, 2, 4, 4, limit=6),
                _v("w1", "waterfall", ["revenue"], "customer", 0, 6, 6, 4, limit=8),
                _v("t1", "table", ["hours", "revenue", "margin", "margin_rate"], "customer", 6, 6, 6, 4, limit=10),
                _v("h1", "heatmap", ["hours"], "customer", 0, 10, 12, 5, limit=10, columns="month"),
            ]},
            {"name": CUSTOMER_DETAIL, "drill": "customer", "visuals": [
                *_kpis(["hours", "revenue", "margin"]),
                _v("g1", "gauge", ["margin_rate"], None, 9, 0, 3, 4, max=1),
                _v("c1", "column", ["hours"], "month", 0, 2, 9, 4),
                _v("l1", "line", ["revenue"], "month", 0, 6, 12, 4),
            ]},
        ],
    },
    {
        "key": "cx",
        "name": _lt("Customer experience"),
        "description": _lt("NPS and complaints by month and by customer, with a detail page for each customer."),
        "modules": ["bf_bi_cx"],
        "pages": [
            {"name": _lt("Overview"), "visuals": [
                *_kpis(["nps", "nps_responses", "open_complaints", "resolution_days"]),
                _v("l1", "line", ["nps"], "month", 0, 2, 8, 4),
                _v("b1", "bar", ["open_complaints"], "customer", 8, 2, 4, 4, limit=8),
                _v("t1", "table", ["nps_responses", "promoters", "detractors", "nps"], "customer", 0, 6, 12, 4, limit=10),
            ]},
            {"name": CUSTOMER_DETAIL, "drill": "customer", "visuals": [
                *_kpis(["nps", "nps_responses", "open_complaints"]),
                _v("l1", "line", ["nps"], "month", 0, 2, 12, 4),
            ]},
        ],
    },
    {
        "key": "hosting",
        "name": _lt("Hosting"),
        "description": _lt("Services, backups and domains: backup success against a 95 % target, backups by "
                           "month, and failed backups by customer and by month."),
        "modules": ["bf_bi_hosting"],
        "pages": [
            {"name": _lt("Overview"), "visuals": [
                *_kpis(["active_services", "services_down", "backup_success_rate", "domains_expiring"]),
                _v("g1", "gauge", ["backup_success_rate"], None, 0, 2, 4, 4, max=1, target=0.95),
                _v("c1", "column", ["backup_runs"], "month", 4, 2, 8, 4),
                _v("t1", "table", ["active_services", "services_down", "failed_backups", "average_storage_used"],
                   "customer", 0, 6, 12, 4, limit=10),
                _v("h1", "heatmap", ["failed_backups"], "customer", 0, 10, 12, 4, limit=10, columns="month"),
            ]},
        ],
    },
    {
        "key": "hour_bank",
        "name": _lt("Hour banks"),
        "description": _lt("Hours bought and adjusted in hour banks, by month and by customer, and what changed "
                           "since the previous period."),
        "modules": ["bf_bi_hour_bank"],
        "pages": [
            {"name": _lt("Overview"), "visuals": [
                *_kpis(["hour_bank_adjustments"]),
                _v("c1", "column", ["hour_bank_adjustments"], "month", 3, 0, 9, 4),
                _v("b1", "bar", ["hour_bank_adjustments"], "customer", 0, 4, 6, 4, limit=10),
                _v("w1", "waterfall", ["hour_bank_adjustments"], "customer", 6, 4, 6, 4, limit=8),
            ]},
        ],
    },
    {
        "key": "management",
        "name": _lt("Management report"),
        "description": _lt("The figures of time, customer experience, hosting and hour banks on one page, what "
                           "changed in revenue, hours by customer and by month, and a detail page for each customer."),
        "modules": ["bf_bi_timesheet", "bf_bi_cx", "bf_bi_hosting", "bf_bi_hour_bank"],
        "pages": [
            {"name": _lt("Overview"), "visuals": [
                *_kpis(["hours", "revenue", "margin_rate", "nps"]),
                *_kpis(["backup_success_rate", "open_complaints", "active_services", "hour_bank_adjustments"], y=2, prefix="m"),
                _v("c1", "column", ["hours"], "month", 0, 4, 6, 4),
                _v("w1", "waterfall", ["revenue"], "customer", 6, 4, 6, 4, limit=8),
                _v("h1", "heatmap", ["hours"], "customer", 0, 8, 12, 5, limit=10, columns="month"),
            ]},
            {"name": CUSTOMER_DETAIL, "drill": "customer", "visuals": [
                *_kpis(["hours", "revenue", "nps", "open_complaints"]),
                _v("c1", "column", ["hours"], "month", 0, 2, 6, 4),
                _v("l1", "line", ["revenue"], "month", 6, 2, 6, 4),
            ]},
        ],
    },
]

TEMPLATE_KEYS = [t["key"] for t in TEMPLATES]


def template_codes(template):
    return sorted({code for page in template["pages"] for v in page["visuals"] for code in v["measures"]})
