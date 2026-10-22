{
    "name": "Security Awareness",
    "summary": "Simulated phishing campaigns, per-person risk profiles and "
               "cybersecurity eLearning — a KnowBe4 / Terranova-style offering.",
    # 18.0.2.2.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.2.3.2: « Rapport agrégé », the campaign figures a
    #   client can hand to a supplier, nobody named, rates only from
    #   bf_security_awareness.aggregate_min_recipients (10, never below 5),
    #   unfavourable rates as a range, neutral reference per company;
    #   nominative report renamed « Named report (confidential) ».
    "version": "18.0.2.3.2",
    "category": "Marketing/Security Awareness",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "depends": [
        "bf_training_slides",
        "mail",
        "utm",
        "mass_mailing",
        "link_tracker",
        "website",
        "website_slides",
    ],
    "data": [
        # Security
        "security/bf_security_awareness_security.xml",
        "security/ir.model.access.csv",
        # Data
        "data/bf_security_awareness_data.xml",
        "data/mail_template_data.xml",
        "data/bf_security_awareness_cron.xml",
        # Website / landing templates
        "views/phishing_landing_templates.xml",
        # Backend views (actions before menus)
        "views/bf_phishing_template_views.xml",
        "views/bf_phishing_campaign_views.xml",
        "views/bf_phishing_result_views.xml",
        "views/bf_security_profile_views.xml",
        "views/bf_training_assignment_views.xml",
        "views/res_partner_views.xml",
        "report/campaign_report.xml",
        "report/aggregate_report.xml",
        "views/bf_security_awareness_menus.xml",
        "views/bf_security_dashboard_views.xml",
        "views/bf_reported_phish_views.xml",
        "views/bf_clawback_views.xml",
    ],
    "demo": [
        "demo/bf_security_awareness_demo.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_security_awareness/static/src/js/bf_security_dashboard.js",
            "bf_security_awareness/static/src/xml/bf_security_dashboard.xml",
        ],
    },
}
