from odoo import http
from odoo.http import request

from odoo.addons.bf_school_portal.controllers.portal import SchoolPortal


class SchoolRl24Portal(SchoolPortal):

    def _school_rl24_statements(self):
        """The issued statements of the adult signed in: theirs only, never their co-payer's."""
        return request.env["bf.school.rl24"].sudo().search([
            ("partner_id", "=", request.env.user.partner_id.id), ("state", "=", "issued")])

    @http.route()
    def portal_my_school(self, **kw):
        # The button shows to whoever has a statement: child care bills the first adult with parental
        # authority when nobody is ticked « Pays », and that adult had no way to their statement.
        response = super().portal_my_school(**kw)
        if getattr(response, "qcontext", None) is not None:
            response.qcontext["school_rl24_any"] = bool(self._school_rl24_statements())
        return response

    @http.route("/my/school/rl24", type="http", auth="user", website=True)
    def portal_school_rl24(self, **kw):
        values = self._prepare_portal_layout_values()
        values.update({"page_name": "school_rl24", "statements": self._school_rl24_statements()})
        return request.render("bf_school_rl24.portal_school_rl24", values)

    @http.route("/my/school/rl24/<int:statement_id>/pdf", type="http", auth="user", website=True)
    def portal_school_rl24_pdf(self, statement_id, **kw):
        statement = self._school_rl24_statements().filtered(lambda s: s.id == statement_id)
        if not statement:
            raise request.not_found()
        pdf, _type = request.env["ir.actions.report"].sudo()._render_qweb_pdf(
            "bf_school_rl24.action_report_school_rl24", statement.ids)
        name = "releve24-%s.pdf" % statement.year
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"), ("Content-Length", len(pdf)),
            ("Content-Disposition", 'attachment; filename="%s"' % name)])
