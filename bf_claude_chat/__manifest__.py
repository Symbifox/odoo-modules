{
    "name": "Gen",
    "version": "18.0.1.15.4",
    "category": "Productivity",
    "summary": "Chat with Gen, the AI assistant, directly inside Odoo",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "Other proprietary",
    "depends": ["web", "base", "project", "mail"],
    "external_dependencies": {
        "python": ["cryptography"],
    },
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/menu.xml",
        "views/instruction_views.xml",
        "views/res_config_settings.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_claude_chat/static/src/scss/claude_chat.scss",
            "bf_claude_chat/static/src/js/claude_stream.js",
            "bf_claude_chat/static/src/js/claude_chat.js",
            "bf_claude_chat/static/src/js/claude_systray.js",
            "bf_claude_chat/static/src/xml/claude_chat.xml",
        ],
    },
    "installable": True,
    "application": True,
}
