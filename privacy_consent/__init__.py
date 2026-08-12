from . import models
from . import wizards
from . import controllers


def post_init_hook(env):
    """Set the default privacy framework (Loi 25) on companies lacking one.

    Fresh installs do not run migrations, so this mirrors the 18.0.4.0.0
    migration for the `-i` path. The framework mixin's Loi 25 fallback remains
    the final guard if a company default is ever unset.
    """
    framework = env.ref("privacy_consent.framework_loi25", raise_if_not_found=False)
    if not framework:
        return
    companies = env["res.company"].search([("default_privacy_framework_id", "=", False)])
    if companies:
        companies.write({"default_privacy_framework_id": framework.id})
