import werkzeug.urls

from odoo.http import request

from odoo.addons.auth_oauth.controllers.main import OAuthLogin


class SchoolStudentOAuthLogin(OAuthLogin):

    def get_state(self, provider):
        state = super().get_state(provider)
        # A student who signs in through the school's directory lands on their portal. Without
        # a redirect, auth_oauth returns to /web, which it turns into the website's home page
        # for a portal user. An explicit redirect (a portal link followed while signed out) is
        # kept.
        if not request.params.get("redirect") and request.env["bf.school"].sudo().search_count(
                [("student_oauth_provider_id", "=", provider["id"])], limit=1):
            state["r"] = werkzeug.urls.url_quote_plus(request.httprequest.url_root + "my/school")
        return state
