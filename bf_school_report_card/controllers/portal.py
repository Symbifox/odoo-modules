import base64

from odoo import _, fields, http
from odoo.http import content_disposition, request

from odoo.addons.portal.controllers.portal import CustomerPortal

#: A report card is a child's results: nothing in a cache, a proxy or a history.
NO_STORE = ("Cache-Control", "no-store, private")


class SchoolReportCardPortal(CustomerPortal):

    def _readable_cards(self):
        """Issued, shown, current report cards of the children this adult holds parental authority over.

        🔴 Report cards are for the holders of parental authority and, once the student is of age,
        for the student only: the same door as the rest of the portal, the adult's own links.
        """
        partner = request.env.user.partner_id
        students = partner._school_authority_links().student_id
        # The one shown is the current version, and the one it replaces until the corrected one is shown in
        # its turn: between the issue of a correction and its publication the family keeps the report card it
        # had, it does not lose it.
        cards = request.env["bf.school.report.card"].sudo().search([
            ("student_id", "in", (students | partner).ids), ("published_on", "!=", False),
            "|", ("state", "=", "issued"),
            "&", ("state", "=", "superseded"), ("replaced_by_id.published_on", "=", False)],
            order="year_id desc, term_id desc")
        # The parents read the cards of their minor children; a student of age reads their own.
        return cards.filtered(lambda c: (c.recipient == "guardians" and c.student_id in students)
                              or (c.recipient == "student" and c.student_id == partner))

    @http.route("/my/school/report-cards", type="http", auth="user", website=True)
    def portal_report_cards(self, **kw):
        cards = self._readable_cards()
        partner = request.env.user.partner_id
        read = request.env["bf.school.report.card.read"].sudo().search(
            [("card_id", "in", cards.ids), ("guardian_id", "=", partner.id)]).card_id
        values = self._prepare_portal_layout_values()
        values.update({"page_name": "school_report_cards", "cards": cards, "read": read})
        return request.render("bf_school_report_card.portal_school_report_cards", values)

    @http.route("/school/report-card/<int:card_id>/pdf", type="http", auth="user")
    def report_card_pdf(self, card_id, **kw):
        user = request.env.user
        if user.has_group("base.group_user"):
            card = request.env["bf.school.report.card"].browse(card_id).exists()
            if not card or not card.has_access("read") or card.state == "draft":
                raise request.not_found()
            # 🔴 The whole report card, every subject and every comment: the office and the teachers of the
            # group. A teacher of one subject reads their own lines on screen, not the whole PDF.
            if not (user.has_group("bf_school_core.group_school_manager") or user in card.group_id.sudo().teacher_ids):
                raise request.not_found()
            card = card.sudo()
            guardian = None
        else:
            card = self._readable_cards().filtered(lambda c: c.id == card_id)
            if not card:
                raise request.not_found()
            guardian = user.partner_id
        if not card.pdf:
            cron = request.env.ref("bf_school_report_card.ir_cron_render_pdfs").sudo()
            # One waiting trigger is enough: a page reloaded again and again adds no row.
            if not request.env["ir.cron.trigger"].sudo().search_count([("cron_id", "=", cron.id)]):
                cron._trigger()
            return request.make_response(
                _("The report card is being prepared: try again in a few minutes."), status=202,
                headers=[("Content-Type", "text/plain; charset=utf-8"), ("Retry-After", "60"), NO_STORE])
        if not card._pdf_matches_seal():
            # 🔴 What the family receives is the file that was sealed when it was rendered, or nothing: a file
            # replaced behind the application is not given out, and the access log says so.
            card._log_in_record("seal_mismatch", _("Report card, %(term)s", term=card._record_note()))
            return request.make_response(
                _("This report card cannot be given out for now: the school has to check it."), status=409,
                headers=[("Content-Type", "text/plain; charset=utf-8"), NO_STORE])
        card._log_in_record("piece_downloaded", _("Report card, %(term)s", term=card._record_note()))
        if guardian:
            Read = request.env["bf.school.report.card.read"].sudo()
            if not Read.search_count([("card_id", "=", card.id), ("guardian_id", "=", guardian.id)]):
                Read.create({"card_id": card.id, "guardian_id": guardian.id, "read_on": fields.Datetime.now()})
        return request.make_response(base64.b64decode(card.pdf), headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Disposition", content_disposition(card.pdf_name or "report-card.pdf")),
            ("X-Content-Type-Options", "nosniff"), NO_STORE])
