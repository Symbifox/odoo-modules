# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Hébergement — Mises à jour système : section du digest",
    "summary": "L'état de mise à jour du parc dans le digest quotidien",
    "version": "18.0.1.1.0",
    "category": "Services",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "installable": True,
    # Satellite à part, comme `bf_cx_digest` : le relevé ne doit pas imposer le
    # digest, ni l'inverse.
    "depends": ["bf_hosting_patch", "daily_todo_digest"],
    "data": [],
}
