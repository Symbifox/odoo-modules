import json
import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

# ous_multi_company_email field -> our decoupled field, for one-time migration
# at install time (ous is still installed when this module is installed; it is
# uninstalled only after verification).
_OUS_COMPANY_FIELDS = {
    "catchall_domain": "bf_catchall_domain",
    "catchall_alias": "bf_catchall_alias",
    "bounce_alias": "bf_bounce_alias",
    "default_from": "bf_default_from",
}

# ---------------------------------------------------------------------------
# Tenant brand seed
#
# The five elements the rich signature template used to hardcode — pronouns,
# displayed job title, toll-free number, logo, footer shortlinks — are fields
# now. Somebody still has to put the first values in, and those values are
# nobody's business but the tenant's: a published module that ships one
# organisation's accent colour and one person's booking links is a module that
# signs every other company's mail wrong.
#
# So they live in a data file whose name carries « .private. », which the
# publication procedure excludes from the copy. The published module reads no
# seed, seeds nothing, and says so once in the log. Adding a company or a
# person is an edit to that JSON, not to this file.
# ---------------------------------------------------------------------------
BRAND_SEED_PATH = Path(__file__).resolve().parent / "data" / "brand_seed.private.json"


def load_brand_seed(path=None):
    """Read the tenant seed, or return None when there is none to read."""
    path = Path(path) if path else BRAND_SEED_PATH
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # A malformed seed must not stop an install. It IS worth shouting
        # about: the signatures will render generic and nobody would connect
        # that to a stray comma in a JSON file.
        _logger.error(
            "bf_multi_company_email: semis de marque illisible (%s) — les "
            "signatures seront rendues sans les éléments du locataire.",
            path, exc_info=True,
        )
        return None


def _untouched(record, field_name):
    """True when this field still holds what the field itself defaults to.

    « Only fill what is empty » is the right instinct and the wrong test for a
    field that ships a default: Odoo writes that default into every existing
    row the moment it creates the column, so nothing is ever empty and the
    seeding silently does nothing. A second company inherited the first
    one's accent colour and font that way, and the signatures of everybody in
    it came out in the wrong brand.

    A value equal to the field's own default was never chosen by a human, so
    it is safe to replace. Anything else is somebody's decision and is left
    alone, which is what keeps this re-runnable.
    """
    current = record[field_name]
    if not current:
        return True
    default = record._fields[field_name].default
    if callable(default):
        try:
            default = default(record)
        except Exception:
            return False
    return current == default


def _seed_links(env, owner_field, owner, specs):
    """Create the quick links this owner is missing, matched on URL."""
    Link = env["bf.signature.link"].sudo()
    existing = set(Link.with_context(active_test=False).search(
        [(owner_field, "=", owner.id)]).mapped("target_url"))
    pending = [
        # `url` est devenu calculé en 18.0.4.0.0 : la destination s'écrit dans
        # `target_url`. Le semis garde la clé « url » pour ne pas invalider les
        # fichiers déjà rédigés.
        dict({k: v for k, v in spec.items() if k != "url"},
             target_url=spec["url"], **{owner_field: owner.id})
        for spec in specs if spec.get("url") and spec["url"] not in existing
    ]
    if pending:
        Link.create(pending)
        _logger.info(
            "bf_multi_company_email: %s lien(s) rapide(s) créé(s) pour %s",
            len(pending), owner.display_name,
        )


def _seed_fields(record, values, label):
    """Write the values this record has not had chosen for it."""
    pending = {
        field: value for field, value in values.items()
        if field in record._fields and _untouched(record, field)
    }
    unknown = [f for f in values if f not in record._fields]
    if unknown:
        _logger.warning(
            "bf_multi_company_email: champ(s) inconnu(s) dans le semis pour "
            "%s, ignoré(s) : %s", label, ", ".join(sorted(unknown)),
        )
    if pending:
        record.write(pending)
        _logger.info(
            "bf_multi_company_email: semis appliqué sur %s : %s",
            label, ", ".join(sorted(pending)),
        )


def seed_signature_brand(env, seed=None):
    """Apply the tenant brand seed. A no-op when there is no seed file."""
    seed = seed if seed is not None else load_brand_seed()
    if not seed:
        _logger.info(
            "bf_multi_company_email: aucun semis de marque (%s absent) — les "
            "sociétés gardent les valeurs par défaut du module.",
            BRAND_SEED_PATH.name,
        )
        return

    Company = env["res.company"].sudo()
    Users = env["res.users"].sudo()

    for name, spec in (seed.get("companies") or {}).items():
        company = Company.search([("name", "=", name)], limit=1)
        if not company:
            _logger.info(
                "bf_multi_company_email: semis ignoré, aucune société nommée "
                "« %s » sur cette base.", name,
            )
            continue
        _seed_fields(company, spec.get("fields") or {}, "société %s" % name)
        if spec.get("links"):
            _seed_links(env, "company_id", company, spec["links"])

    for login, spec in (seed.get("users") or {}).items():
        user = Users.search([("login", "=", login)], limit=1)
        if not user:
            _logger.info(
                "bf_multi_company_email: semis ignoré, aucun compte « %s » "
                "sur cette base.", login,
            )
            continue
        _seed_fields(user, spec.get("fields") or {}, "compte %s" % login)
        if spec.get("links"):
            _seed_links(env, "user_id", user, spec["links"])


def post_init_hook(env):
    """Migrate ous_multi_company_email settings, then apply signatures.

    Idempotent: only fills empty target fields, so re-running is safe.
    """
    Company = env["res.company"]
    Users = env["res.users"]

    # 1. Per-company catchall/bounce/default-from (company fields).
    company_fields = Company._fields
    if any(src in company_fields for src in _OUS_COMPANY_FIELDS):
        for company in Company.search([]):
            vals = {}
            for src, dst in _OUS_COMPANY_FIELDS.items():
                if src in company_fields and company[src] and not company[dst]:
                    vals[dst] = company[src]
            if vals:
                company.write(vals)
                _logger.info(
                    "bf_multi_company_email: migrated catchall fields for "
                    "company %s: %s", company.name, list(vals),
                )

    # 2. Per-user per-company sender address (ous temp_email -> bf_company_email).
    if "temp_email" in Users._fields:
        for user in Users.search([("share", "=", False)]):
            for company in user.company_ids:
                user_c = user.with_company(company.id)
                src_val = user_c.temp_email
                if src_val and not user_c.bf_company_email:
                    user_c.bf_company_email = src_val
                    _logger.info(
                        "bf_multi_company_email: migrated sender email for "
                        "%s @ %s -> %s", user.login, company.name, src_val,
                    )

    # 3. Give every company without a template the default one, so a fresh
    #    install signs correctly out of the box instead of rendering nothing.
    # Never rename this xmlid by simply changing it here: the data file is NOT
    # noupdate, so Odoo would create the new record, delete the old one as
    # stale, and take every company's template assignment with it
    # (ondelete="set null"). A rename goes through ir_model_data first.
    default_template = env.ref(
        "bf_multi_company_email.bf_signature_card",
        raise_if_not_found=False,
    )
    if default_template:
        for company in Company.search([("bf_signature_template_id", "=", False)]):
            company.bf_signature_template_id = default_template.id

    # 4. Tenant brand elements, if this deployment carries a seed.
    seed_signature_brand(env)

    # 5. Render signatures now for every internal user whose company has a template.
    Users.search([("share", "=", False)])._bf_apply_company_signature()
