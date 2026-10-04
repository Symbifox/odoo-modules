from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class School(models.Model):
    _inherit = "bf.school"

    # 🔴 groups= like the rest of the directory configuration: whoever chooses the provider
    # chooses which sign-ins open a student's portal.
    student_oauth_provider_id = fields.Many2one(
        "auth.oauth.provider", "Student sign-in", groups="base.group_system",
        help="The OpenID application of the school's directory, whose subject is the user's id "
             "(Authentik: subject mode « Based on the User's ID »). Students sign in to the "
             "portal through it with the account that opens the school's computers.")

    @api.constrains("student_oauth_provider_id", "directory_url")
    def _check_student_oauth_provider(self):
        for school in self.sudo():
            provider = school.student_oauth_provider_id
            if not provider:
                continue
            directory = urlparse(school.directory_url or "").netloc
            urls = [provider.auth_endpoint, provider.validation_endpoint]
            # With OCA auth_oidc (the code flow Authentik requires since 2026), the token and the
            # keys come from the provider too: same directory.
            urls += [provider[f] for f in ("token_endpoint", "jwks_uri", "data_endpoint")
                     if f in provider._fields and provider[f]]
            # An address left empty (auth_oidc makes the userinfo one optional) is not a host.
            hosts = {urlparse(u).netloc for u in urls if u}
            # A subject is the user's id in ONE directory: the same number in another
            # Authentik is somebody else.
            if not directory or hosts != {directory}:
                raise ValidationError(_(
                    "The student sign-in must go through the school's own directory (%s).",
                    directory or _("not configured")))


class AuthOAuthProvider(models.Model):
    _inherit = "auth.oauth.provider"

    _SCHOOL_ADDRESS_FIELDS = {"auth_endpoint", "validation_endpoint", "token_endpoint", "jwks_uri",
                              "data_endpoint"}

    def write(self, vals):
        res = super().write(vals)
        # The school checks the provider's addresses when it chooses it; a provider pointed
        # elsewhere afterwards is checked as well (found in review). Only on an address: a
        # provider already wrong can still be disabled.
        if self._SCHOOL_ADDRESS_FIELDS & set(vals):
            self.env["bf.school"].sudo().search(
                [("student_oauth_provider_id", "in", self.ids)])._check_student_oauth_provider()
        return res
