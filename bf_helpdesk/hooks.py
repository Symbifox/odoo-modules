FIELDS_TO_OPT_IN = [
    "name",
    "description",
    "partner_name",
    "partner_email",
    "team_id",
    "channel_id",
    "attachment_ids",
]


def post_init_hook(env):
    """Opt helpdesk.ticket fields into the website form builder allow-list,
    and backfill slugs on pre-existing teams.

    Replaces standalone module bf_helpdesk_website_form (now folded in).
    """
    env["ir.model.fields"].sudo().search([
        ("model", "=", "helpdesk.ticket"),
        ("name", "in", FIELDS_TO_OPT_IN),
        ("website_form_blacklisted", "=", True),
    ]).write({"website_form_blacklisted": False})

    Team = env["helpdesk.ticket.team"].sudo()
    teams_without_slug = Team.search([("slug", "in", [False, ""])])
    for team in teams_without_slug:
        team.slug = Team._slugify(team.name)
    teams_without_slug._ensure_unique_slugs()
