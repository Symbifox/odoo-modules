{
    "name": "Symbifox — Activity Cancel Note",
    # 18.0.1.0.1: archiving a project archives its tasks with
    #   active_test=False; done keep_done activities (archived) were then
    #   noted as cancelled. Only active activities are noted now.
    "version": "18.0.1.0.1",
    "category": "Productivity/Discuss",
    "summary": "Cancelling an activity can leave a note in the chatter, like marking it done",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["mail"],
    "data": [
        "data/mail_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_activity_cancel_note/static/src/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
