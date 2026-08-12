def migrate(cr, version):
    """Backfill slugs on pre-existing teams when bf_helpdesk is upgraded."""
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    Team = env["helpdesk.ticket.team"].sudo()
    teams_without_slug = Team.search([("slug", "in", [False, ""])])
    for team in teams_without_slug:
        team.slug = Team._slugify(team.name)
    teams_without_slug._ensure_unique_slugs()
