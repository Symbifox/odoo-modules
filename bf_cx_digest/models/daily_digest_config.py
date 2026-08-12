"""« Expérience client » section in the daily digest.

Exact same injection pattern as bf_subscription_daily_digest: build the
section HTML, splice it in front of the "<!-- Divider -->" marker emitted
by the base template. The section only shows when something is actionable
(open follow-ups, complaints or fresh testimonial opt-ins) so quiet days
stay quiet. The NPS shown in the header uses the shared honest-window
helper (bf.cx.feedback._nps_summary)."""
from markupsafe import escape as _esc

from odoo import _, fields, models


class DailyDigestConfig(models.Model):
    _inherit = "daily.digest.config"

    include_cx = fields.Boolean(
        string="Inclure l'expérience client", default=True,
    )

    def _render_cx_section(self, user):
        """Build the CX HTML section, or '' when nothing is actionable."""
        self.ensure_one()
        if not self.include_cx:
            return ""
        Feedback = self.env["bf.cx.feedback"].sudo()
        Complaint = self.env["bf.cx.complaint"].sudo()
        company = user.company_id or self.env.company
        now = fields.Datetime.now()

        # "À rappeler" mirrors the closed loop: NPS detractors AND
        # dissatisfied CSAT, not yet handled.
        followups = Feedback.search(
            [
                ("needs_followup", "=", True),
                ("state", "!=", "done"),
                ("company_id", "=", company.id),
            ],
            order="date desc",
            limit=10,
        )
        complaints = Complaint.search(
            [
                ("state", "not in", ("resolved", "closed")),
                ("company_id", "=", company.id),
            ],
            order="date_received desc",
            limit=10,
        )
        candidates = Feedback.search(
            [
                ("is_testimonial_candidate", "=", True),
                ("testimonial_id", "=", False),
                ("company_id", "=", company.id),
            ],
            order="date desc",
            limit=5,
        )
        if not followups and not complaints and not candidates:
            return ""

        summary = Feedback._nps_summary([("company_id", "=", company.id)])
        nps_text = _("NPS %(days)s j : %(display)s (n=%(n)s)") % {
            "days": summary["days"],
            "display": summary["display"],
            "n": summary["n"],
        }

        accent = company.report_brand_primary or "#29ABE1"
        dark = company.report_brand_dark or "#2D3031"
        cell = (
            "padding:8px 10px;border-bottom:1px solid #e5e7eb;"
            "font-family:'Lexend',Arial,sans-serif;font-size:13px;"
        )
        rows = ""
        kind_labels = dict(
            Feedback._fields["kind"]._description_selection(self.env)
        )
        for feedback in followups:
            rows += (
                f"<tr>"
                f"<td style=\"{cell}color:#dc3545;font-weight:600;\">"
                f"{_esc(_('À rappeler'))}</td>"
                f"<td style=\"{cell}color:#111827;\">"
                f"{_esc(feedback.partner_id.display_name or _('Anonyme'))}</td>"
                f"<td style=\"{cell}color:#374151;\">"
                f"{_esc('%s %s/%s' % (kind_labels.get(feedback.kind, ''), feedback.score, int(feedback.score_max)))}</td>"
                f"<td style=\"{cell}color:#6B7280;\">"
                f"{_esc((feedback.comment or '')[:80])}</td>"
                f"</tr>"
            )
        for complaint in complaints:
            late = (
                complaint.state == "received"
                and complaint.ack_deadline
                and complaint.ack_deadline < now
            )
            label = _("Plainte (AR en retard)") if late else _("Plainte")
            rows += (
                f"<tr>"
                f"<td style=\"{cell}color:{'#dc3545' if late else '#f59e0b'};font-weight:600;\">"
                f"{_esc(label)}</td>"
                f"<td style=\"{cell}color:#111827;\">"
                f"{_esc(complaint.partner_id.display_name or complaint.contact_name or '')}</td>"
                f"<td style=\"{cell}color:#374151;\">{_esc(complaint.number)}</td>"
                f"<td style=\"{cell}color:#6B7280;\">{_esc(complaint.name[:80])}</td>"
                f"</tr>"
            )
        for feedback in candidates:
            rows += (
                f"<tr>"
                f"<td style=\"{cell}color:#16a34a;font-weight:600;\">"
                f"{_esc(_('Candidat témoignage'))}</td>"
                f"<td style=\"{cell}color:#111827;\">"
                f"{_esc(feedback.partner_id.display_name or _('Anonyme'))}</td>"
                f"<td style=\"{cell}color:#374151;\">"
                f"{_esc(str(feedback.date))}</td>"
                f"<td style=\"{cell}color:#6B7280;\">"
                f"{_esc((feedback.comment or '')[:80])}</td>"
                f"</tr>"
            )

        return (
            f"<h3 style=\"font-family:'Lexend','Segoe UI',Arial,sans-serif;"
            f"font-size:16px;font-weight:600;color:{dark};margin:24px 0 8px 0;\">"
            f"💬 {_esc(_('Expérience client'))}"
            f"<span style=\"font-weight:400;font-size:13px;color:#6B7280;\"> - "
            f"{_esc(nps_text)}</span></h3>"
            f"<table role=\"presentation\" width=\"100%\" "
            f"style=\"border:1px solid #e5e7eb;border-radius:8px;"
            f"border-collapse:separate;overflow:hidden;margin-bottom:8px;\">"
            f"<thead><tr>"
            f"<th style=\"padding:8px 10px;text-align:left;background:{accent};color:#fff;font-family:'Lexend',Arial,sans-serif;font-size:12px;\">{_esc(_('Type'))}</th>"
            f"<th style=\"padding:8px 10px;text-align:left;background:{accent};color:#fff;font-family:'Lexend',Arial,sans-serif;font-size:12px;\">{_esc(_('Contact'))}</th>"
            f"<th style=\"padding:8px 10px;text-align:left;background:{accent};color:#fff;font-family:'Lexend',Arial,sans-serif;font-size:12px;\">{_esc(_('Note / N°'))}</th>"
            f"<th style=\"padding:8px 10px;text-align:left;background:{accent};color:#fff;font-family:'Lexend',Arial,sans-serif;font-size:12px;\">{_esc(_('Détail'))}</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )

    def _generate_html(self, data, user):
        html = super()._generate_html(data, user)
        section = self._render_cx_section(user)
        if section and "<!-- Divider -->" in html:
            html = html.replace("<!-- Divider -->", section + "<!-- Divider -->", 1)
        return html
