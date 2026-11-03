{
    "name": "BF Project Number",
    "version": "18.0.1.1.0",
    "category": "Services/Project",
    "summary": "A project number assigned on its own, taking the smallest free number, editable by hand",
    "description": """
Project number
==============

Every project carries a three-digit number (001, 002, 003…) assigned when it is
created. The number taken is **the smallest one still free**: a number freed by
a deleted or renumbered project is given to the next project, instead of
leaving a hole.

The number stays **editable by hand**, as long as the target number is not
already taken by another project. A taken number is refused, naming the project
that holds it.

The number reads to the left of the name in the project list, as a badge on the
kanban card, and answers the search bar. It does not touch the project name:
nothing to rename.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["project"],
    "data": [
        "views/project_views.xml",
    ],
    "post_init_hook": "post_init_numeroter_existants",
    "installable": True,
    "application": False,
}
