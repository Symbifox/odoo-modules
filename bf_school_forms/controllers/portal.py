from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class SchoolFormsPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "school_form_count" in counters:
            values["school_form_count"] = len(
                request.env["bf.school.form.answer"]._portal_answers_for(request.env.user.partner_id))
        return values

    # --- Signed in -------------------------------------------------------------

    @http.route("/my/school/forms", type="http", auth="user", website=True)
    def portal_school_forms(self, **kw):
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "school_forms",
            "answers": request.env["bf.school.form.answer"]._portal_answers_for(
                request.env.user.partner_id),
        })
        return request.render("bf_school_forms.portal_school_forms", values)

    def _own_answer(self, answer_id):
        """This adult's own answer, or None. Someone else's answers a 404."""
        answer = request.env["bf.school.form.answer"].sudo().browse(answer_id).exists()
        if not answer or answer.partner_id != request.env.user.partner_id or not answer._school_still_signs():
            return None
        return answer

    @http.route("/my/school/forms/<int:answer_id>", type="http", auth="user", website=True)
    def portal_school_form(self, answer_id, **kw):
        answer = self._own_answer(answer_id)
        if not answer:
            raise request.not_found()
        return self._render_answer(answer, action="/my/school/forms/%s/answer" % answer.id,
                                   error=kw.get("error"))

    @http.route("/my/school/forms/<int:answer_id>/answer", type="http", auth="user",
                methods=["POST"], website=True)
    def portal_school_form_answer(self, answer_id, decision=None, **kw):
        answer = self._own_answer(answer_id)
        if not answer:
            raise request.not_found()
        return self._decide(answer, decision, "portal", "/my/school/forms/%s" % answer.id)

    # --- Personal link, no account needed ----------------------------------------

    def _token_answer(self, answer_id, token):
        answer = request.env["bf.school.form.answer"].sudo().browse(answer_id).exists()
        if not answer or not answer._check_token(token) or not answer._school_still_signs():
            return None
        return answer

    @http.route("/school/form/<int:answer_id>/<string:token>", type="http", auth="public", website=True)
    def school_form_link(self, answer_id, token, **kw):
        answer = self._token_answer(answer_id, token)
        if not answer:
            raise request.not_found()
        return self._render_answer(answer, action="/school/form/%s/%s/answer" % (answer.id, token),
                                   error=kw.get("error"))

    @http.route("/school/form/<int:answer_id>/<string:token>/answer", type="http", auth="public",
                methods=["POST"], website=True)
    def school_form_link_answer(self, answer_id, token, decision=None, **kw):
        answer = self._token_answer(answer_id, token)
        if not answer:
            raise request.not_found()
        return self._decide(answer, decision, "link", "/school/form/%s/%s" % (answer.id, token))

    # --- Shared ------------------------------------------------------------------

    def _render_answer(self, answer, action, error=None):
        values = self._prepare_portal_layout_values() if not request.env.user._is_public() else {}
        values.update({"page_name": "school_form", "answer": answer, "form": answer.form_id,
                       "action": action, "error": error})
        return request.render("bf_school_forms.portal_school_form", values)

    def _decide(self, answer, decision, channel, back):
        try:
            answer._school_decide(
                decision, channel,
                ip=request.httprequest.remote_addr,
                user_agent=request.httprequest.headers.get("User-Agent"),
                answering_user=request.env.user)
        except UserError:
            return request.redirect(back + "?error=1")
        return request.redirect(back)
