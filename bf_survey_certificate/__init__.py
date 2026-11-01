from . import controllers
from . import models


def post_init_hook(env):
    """Give a certificate number to certifications passed BEFORE the module
    was installed, so their re-printed PDF and their verification page work
    without waiting for a new attempt."""
    env["survey.user_input"].sudo()._bf_ensure_certificate_code(
        env["survey.user_input"].sudo().search([
            ("survey_id.certification", "=", True),
            ("scoring_success", "=", True),
            ("test_entry", "=", False),
            ("bf_certificate_code", "=", False),
        ])
    )
