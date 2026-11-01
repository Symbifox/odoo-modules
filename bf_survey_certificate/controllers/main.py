from odoo import http
from odoo.http import request


class CertificateVerify(http.Controller):
    """Public verification of a certificate number.

    The page is the counterpart of the number printed on the PDF: whoever
    holds a number can confirm the certificate is genuine. It shows the
    holder's name (never an email address), the course, the completion date
    and the issuing company, nothing else - and answers the same way for a
    wrong number and for a malformed one, so it confirms numbers without
    helping to guess them (the number space, 25^12, makes guessing
    pointless anyway).
    """

    @http.route(
        ["/certificat/<string:code>", "/certificate/<string:code>"],
        type="http", auth="public", methods=["GET"], website=False,
    )
    def verify(self, code, **kwargs):
        # The whole page follows the language of the route: the static
        # terms through the request context, the computed values (date,
        # course title) through _bf_certificate_values(lang=...).
        lang = self._page_lang()
        request.update_context(lang=lang)
        env = request.env
        normalized = env["survey.user_input"]._bf_normalize_certificate_code(code)
        user_input = env["survey.user_input"].sudo().search(
            [("bf_certificate_code", "=", normalized),
             ("scoring_success", "=", True),
             ("test_entry", "=", False)],
            limit=1,
        ) if normalized else env["survey.user_input"].sudo()

        if not user_input:
            values = {
                "found": False,
                "page_title": env._("Certificate verification"),
                "lang_short": lang.split("_")[0],
            }
            return self._respond(values, status=404)

        values = {
            "found": True,
            "page_title": env._("Certificate verification"),
            "lang_short": lang.split("_")[0],
            "v": user_input._bf_certificate_values(lang=lang),
        }
        return self._respond(values)

    def _respond(self, values, status=200):
        response = request.render(
            "bf_survey_certificate.certificate_verify_page", values,
            status=status,
        )
        # The answer names a person: no shared or disk cache may keep it.
        response.headers["Cache-Control"] = "no-store"
        return response

    def _page_lang(self):
        """/certificat answers in French, /certificate in English, each
        falling back to whatever matching language the database has, then to
        the database default. The page addresses the holder of a paper
        document: the URL is the only language hint there is."""
        wanted = "fr" if request.httprequest.path.startswith("/certificat/") else "en"
        installed = [code for code, _name in request.env["res.lang"].sudo().get_installed()]
        preferred = {"fr": ["fr_CA", "fr_FR"], "en": ["en_US", "en_CA", "en_GB"]}[wanted]
        for lang in preferred:
            if lang in installed:
                return lang
        for lang in installed:
            if lang.startswith(wanted):
                return lang
        return installed[0] if installed else "en_US"
