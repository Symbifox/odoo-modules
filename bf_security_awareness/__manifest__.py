{
    "name": "Security Awareness",
    "summary": "Simulated phishing campaigns, per-person risk profiles and "
               "cybersecurity eLearning — a KnowBe4 / Terranova-style offering.",
    "version": "18.0.1.9.2",
    "category": "Marketing/Security Awareness",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "depends": [
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
