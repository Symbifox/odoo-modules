import logging

from odoo import SUPERUSER_ID

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Redraw the stored avatar of the minor students that already have one.

    A contact's avatar is computed when it is read, so most students need
    nothing. Internal users and employees keep a stored SVG, and a student who
    has one would otherwise keep the beard the draw handed them before this
    module existed.

    ⚠️ Only the minors are redrawn, on purpose. `bf.avatar._regenerate_generated`
    would rewrite every picture in the base to produce the same bytes for
    everyone but them: the draw is seeded by the name, and an adult's pool has
    not changed.
    """
    avatars = env["bf.avatar"]
    if avatars._style() not in ("open_peeps", "notionists"):
        return 0
    students = env["res.partner"].sudo().with_context(active_test=False).search([
        ("is_student", "=", True), ("student_birthdate", "!=", False)])
    minors = students.filtered(lambda p: avatars._is_minor_student(p))
    if not minors:
        return 0
    count = 0
    partners = avatars._generated_targets("res.partner", minors.ids).get("res.partner", [])
    records = env["res.partner"].with_user(SUPERUSER_ID).with_context(
        tracking_disable=True, mail_notrack=True, active_test=False).browse(partners).exists()
    for record in records:
        if not record[record._avatar_name_field]:
            continue
        record.write({"image_1920": record._avatar_generate_svg()})
        count += 1
    _logger.info("bf_avatar_school: %d stored avatar(s) of minor students redrawn "
                 "(%d minor student(s) in all)", count, len(minors))
    return count
