{
    "name": "Symbifox: food allergens",
    "version": "18.0.1.0.0",
    "category": "Inventory/Inventory",
    "summary": "One shared list of Health Canada's priority food allergens for the Symbifox food modules",
    "description": """
Food allergens
==============

The priority food allergens of Health Canada (peanuts, tree nuts, sesame, milk, eggs,
fish, crustaceans and molluscs, soy, wheat and gluten, sulphites, mustard), as one list
that every Symbifox food module shares: the kitchen's recipes, the school caterer's meals.

The list is data: a kitchen can add its own entries (celery, lupin) or archive one.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["product"],
    "data": [
        "security/ir.model.access.csv",
        "data/allergen_data.xml",
        "views/allergen_views.xml",
    ],
    "installable": True,
    "application": False,
}
