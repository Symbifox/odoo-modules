FIELDS_TO_OPT_IN = [
    "name",
    "description",
    "partner_name",
    "partner_email",
    "team_id",
    "channel_id",
    "attachment_ids",
]


def opt_in_helpdesk_ticket_form_fields(env):
    env["ir.model.fields"].sudo().search([
        ("model", "=", "helpdesk.ticket"),
        ("name", "in", FIELDS_TO_OPT_IN),
        ("website_form_blacklisted", "=", True),
    ]).write({"website_form_blacklisted": False})


def post_init_hook(env):
    opt_in_helpdesk_ticket_form_fields(env)
