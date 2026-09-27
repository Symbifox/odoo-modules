from datetime import timedelta

from odoo import fields, http
from odoo.http import request
from odoo.tools import html2plaintext

from odoo.addons.portal.controllers.portal import CustomerPortal


def _ics_escape(text):
    """RFC 5545 TEXT: backslash, semicolon, comma and newlines escaped."""
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace(
        "\r\n", "\\n").replace("\n", "\\n")


def _ics_fold(line):
    """RFC 5545: lines of at most 75 octets, continued with a leading space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, chunk = [], b""
    for char in line:
        encoded = char.encode("utf-8")
        if len(chunk) + len(encoded) > (75 if not parts else 74):
            parts.append(chunk.decode("utf-8"))
            chunk = b""
        chunk += encoded
    parts.append(chunk.decode("utf-8"))
    return "\r\n ".join(parts)


class SchoolHomeworkPortal(CustomerPortal):

    @http.route("/my/school/homework", type="http", auth="user", website=True)
    def portal_school_homework(self, **kw):
        partner = request.env.user.partner_id
        today = fields.Date.context_today(request.env.user)
        pairs = request.env["bf.school.homework"]._school_for_partner(partner, since=today - timedelta(days=1))
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "school_homework", "pairs": pairs,
            "ical_url": "%s/school/homework/%s/%s.ics" % (
                request.env["ir.config_parameter"].sudo().get_param("web.base.url"),
                partner.id, partner._school_ical_token()),
        })
        return request.render("bf_school_homework.portal_school_homework", values)

    @http.route("/my/school/homework/new-link", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_homework_new_link(self, **kw):
        request.env.user.partner_id._school_ical_reset()
        return request.redirect("/my/school/homework")

    @http.route("/school/homework/<int:partner_id>/<string:token>.ics", type="http", auth="public")
    def school_homework_ical(self, partner_id, token, **kw):
        partner = request.env["res.partner"].sudo().browse(partner_id).exists()
        if not partner or not partner._school_ical_check(token):
            raise request.not_found()
        since = fields.Date.context_today(partner) - timedelta(days=60)
        host = request.httprequest.host or "symbifox"
        stamp = fields.Datetime.now().strftime("%Y%m%dT%H%M%SZ")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Symbifox//Ecole//FR",
                 "CALSCALE:GREGORIAN", "X-WR-CALNAME:%s" % _ics_escape(partner.env._("School homework"))]
        for homework, child in request.env["bf.school.homework"]._school_for_partner(partner, since=since):
            kind = dict(homework._fields["kind"]._description_selection(homework.env))[homework.kind]
            first_name = (child.name or "").split(" ")[0]
            lines += [
                "BEGIN:VEVENT",
                "UID:homework-%s-%s@%s" % (homework.id, child.id, host),
                "DTSTAMP:%s" % stamp,
                "DTSTART;VALUE=DATE:%s" % homework.date_due.strftime("%Y%m%d"),
                "DTEND;VALUE=DATE:%s" % (homework.date_due + timedelta(days=1)).strftime("%Y%m%d"),
                "SUMMARY:%s" % _ics_escape("%s : %s, %s" % (first_name, kind, homework.name)),
                "DESCRIPTION:%s" % _ics_escape(
                    "%s\n%s" % (homework.group_id.name, html2plaintext(homework.description or "").strip())),
                "END:VEVENT",
            ]
        lines.append("END:VCALENDAR")
        body = "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"
        return request.make_response(body, headers=[
            ("Content-Type", "text/calendar; charset=utf-8"),
            ("Content-Disposition", "inline; filename=devoirs.ics"),
            ("Cache-Control", "private, max-age=900")])
