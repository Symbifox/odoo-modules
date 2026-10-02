{
    "name": "BF Work Category",
    "version": "18.0.1.0.3",
    "category": "Services/Project",
    "summary": "Work categories on project labels, resolved and stored on projects, tasks and every bridged record, "
               "so work can be filtered, grouped and exported by category",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["project"],
    "data": [
        "data/ir_cron.xml",
        "views/project_tags_views.xml",
        "views/project_views.xml",
    ],
    "pre_init_hook": "pre_init_hook",
    "installable": True,
    "application": False,
}
