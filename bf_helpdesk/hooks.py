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
    # ⚠️ En SQL, pas par l'ORM. `ir.model.fields.write` refuse toute écriture
    # non traduite sur un champ dont `state != 'manual'` (« Properties of base
    # fields cannot be altered in this manner ») : sur une base NEUVE, où les
    # sept champs visés sont encore à `blacklisted = True`, ce hook levait donc
    # une UserError et faisait échouer l'installation entière. En upgrade il
    # passait inaperçu, parce que le domaine ne ramenait plus rien.
    # Odoo fait exactement la même chose dans
    # `website/models/website_form.py::formbuilder_whitelist` — et pour la même
    # raison. On n'appelle pas ce helper directement : il exige le groupe
    # `website.group_website_designer`, que l'utilisateur du hook n'a pas.
    env.cr.execute(
        "UPDATE ir_model_fields SET website_form_blacklisted = false "
        "WHERE model = %s AND name IN %s",
        ("helpdesk.ticket", tuple(FIELDS_TO_OPT_IN)),
    )
    env["ir.model.fields"].invalidate_model(["website_form_blacklisted"])

    Team = env["helpdesk.ticket.team"].sudo()
    teams_without_slug = Team.search([("slug", "in", [False, ""])])
    for team in teams_without_slug:
        team.slug = Team._slugify(team.name)
    teams_without_slug._ensure_unique_slugs()
