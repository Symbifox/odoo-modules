"""Aggregate campaign report: the figures a client can hand to a supplier.

The nominative campaign report lists every person and their click. This one
never names anyone. Two levels:

- proof of completion, always: lure, status, send date, number of recipients;
- rates (open, click, submit, report, completed trainings, change since the
  previous campaign), only from a minimum number of recipients.

Campaign names are free text ("Targeted test (Finance)") and are never
printed: the lure and the date identify a campaign well enough.

Trainings are assigned only to people who failed, so their completion rate
needs its own threshold: "1 training out of 1" would say whether the one
person who clicked did the course. Only trainings assigned BY this campaign
count; a person who fails again reuses an older assignment.

Unfavourable rates are shown as a range (18.0.2.3.2): above the threshold, "100 %
clicked" still says what each person did. The band is counted, not computed on
the rate, so that it always holds at least two possible counts: clicks and
submissions from min(ceil(90 % of n), n - 1), reports and completed trainings up to
max(floor(10 % of n), 1). From 10 people the band reads "90% or more" or "10% or
less"; under 10 (threshold lowered towards its floor of 5) such a label would be
false, and the rate reads "Not published". The change since the previous campaign
is not shown for a bounded rate (here or there): the previous report plus the
change would give the exact value back.

The completed-training rate needs more recipients than the threshold, and a click
rate that is not bounded: a training is created only by a click, one per result,
so its denominator is the number of clicks (or close to it). "At least `threshold`
trainings" says everyone clicked when there are exactly `threshold` recipients, and
a rate such as 41.7 % (only 5 / 12) gives the count back when clicks are bounded.

Each campaign carries a neutral reference ("SEN-0003"), numbered per company by
a sequence (never reused, even after a deletion), so that two campaigns on the
same lure can be told apart without a name. Only the system assigns it.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import SQL

MIN_RECIPIENTS_PARAM = "bf_security_awareness.aggregate_min_recipients"
MIN_RECIPIENTS_DEFAULT = 10
MIN_RECIPIENTS_FLOOR = 5
BAND_HIGH = 90  # percent: click, submission from here up read as a range
BAND_LOW = 10   # percent: report, completed trainings from here down read as a range
BAND_LABEL_MIN = 10  # under this many, "90% or more" / "10% or less" would be false
REFERENCE_PREFIX = "SEN-"
REFERENCE_SEQUENCE = "bf.phishing.campaign.report_reference"


class BfPhishingCampaign(models.Model):
    _inherit = "bf.phishing.campaign"

    report_reference = fields.Char(
        "Report reference", readonly=True, copy=False, index=True,
        help="Neutral reference printed on the aggregate report, numbered per company.")

    _sql_constraints = [
        ("report_reference_company_uniq", "unique(company_id, report_reference)",
         "Two campaigns of the same company cannot share a report reference."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                vals.pop("report_reference", None)
            # The default_* door: a context key would set it before the system.
            contexte = dict(self.env.context)
            contexte.pop("default_report_reference", None)
            self = self.with_context(contexte)
        campaigns = super().create(vals_list)
        campaigns._assign_report_reference()
        return campaigns

    def write(self, vals):
        # readonly=True holds in the form only: RPC could print free text on a
        # document handed outside. A context key would be bypassable by RPC too.
        # An export re-imported unchanged writes the same value: let it through.
        if ("report_reference" in vals and not self.env.su
                and any(c.report_reference != vals["report_reference"] for c in self)):
            raise AccessError(_("The report reference is assigned automatically."))
        moved = self.browse()
        if "company_id" in vals:
            moved = self.filtered(lambda c: c.company_id.id != vals["company_id"])
        if moved:
            # Numbered per company: cleared BEFORE the move (its number may
            # exist in the new company), then the new company's next number.
            moved.sudo().write({"report_reference": False})
        res = super().write(vals)
        if moved:
            moved._assign_report_reference()
        return res

    @api.model
    def _report_reference_sequence(self, company):
        """The company's sequence, created on first use after the highest
        reference already given (campaigns numbered before it existed)."""
        Sequence = self.env["ir.sequence"].sudo()
        domaine = [("code", "=", REFERENCE_SEQUENCE), ("company_id", "=", company.id)]
        sequence = Sequence.search(domaine, limit=1)
        if not sequence:
            # Two first campaigns of a company created at once would each create
            # a sequence and take SEN-0001: the company row serialises them.
            self.env.cr.execute(SQL(
                "SELECT id FROM res_company WHERE id = %s FOR UPDATE", company.id))
            sequence = Sequence.search(domaine, limit=1)
        if not sequence:
            taken = self.sudo().with_context(active_test=False).search([
                ("company_id", "=", company.id),
                ("report_reference", "=like", REFERENCE_PREFIX + "%"),
            ]).mapped("report_reference")
            numbers = [int(r[len(REFERENCE_PREFIX):]) for r in taken
                       if r[len(REFERENCE_PREFIX):].isdigit()]
            sequence = Sequence.create({
                "name": "Phishing report reference (%s)" % company.name,
                "code": REFERENCE_SEQUENCE,
                "company_id": company.id,
                "prefix": REFERENCE_PREFIX,
                "padding": 4,
                "number_next": max(numbers, default=0) + 1,
            })
        return sequence

    def _assign_report_reference(self):
        """Next number of the campaign's company, in creation order. Also run
        by the 18.0.2.3.2 migration for existing campaigns."""
        for camp in self.sudo().filtered(lambda c: not c.report_reference).sorted(
                lambda c: (c.create_date or fields.Datetime.now(), c.id)):
            camp.report_reference = self._report_reference_sequence(
                camp.company_id).next_by_id()

    def _aggregate_min_recipients(self):
        """Threshold from which rates are published: default 10, never below 5."""
        raw = self.env["ir.config_parameter"].sudo().get_param(
            MIN_RECIPIENTS_PARAM, str(MIN_RECIPIENTS_DEFAULT))
        try:
            value = int(float(raw))
        except (TypeError, ValueError, OverflowError):
            value = MIN_RECIPIENTS_DEFAULT
        return max(value, MIN_RECIPIENTS_FLOOR)

    def _aggregate_sent_results(self):
        """Results that reached someone: sent, and not bounced."""
        self.ensure_one()
        return self.result_ids.filtered(
            lambda r: r.state != "pending" and not r.bounced)

    def _aggregate_send_date(self):
        """First real send, else the scheduled date."""
        self.ensure_one()
        sent = self._aggregate_sent_results().filtered("sent_datetime")
        if sent:
            return min(sent.mapped("sent_datetime"))
        return self.scheduled_date

    def _aggregate_rates(self, results, threshold):
        """Rates in percent over the sent results (caller checks the threshold
        on recipients; the training rate checks its own)."""
        total = len(results)
        opened = len(results.filtered(
            lambda r: r.state in ("opened", "clicked", "submitted")))
        clicked = len(results.filtered(
            lambda r: r.state in ("clicked", "submitted")))
        submitted = len(results.filtered(lambda r: r.state == "submitted"))
        reported = len(results.filtered("reported"))
        # Trainings assigned BY these results (a reused older assignment has
        # its source elsewhere). Counted in sudo: an assignment created by the
        # public click may carry another company than the reader's, and only
        # a count comes out.
        trainings = self.env["bf.training.assignment"].sudo().search(
            [("source_result_id", "in", results.ids)])
        pct = lambda n, d: round(100.0 * n / d, 1) if d else None  # noqa: E731
        counts = {"clicked": (clicked, total, True), "submitted": (submitted, total, True),
                  "reported": (reported, total, False)}
        bounded = self._aggregate_bounded(counts)
        # The trainings' denominator is about the number of clicks: never at
        # exactly `threshold` recipients (everyone clicked), never when clicks
        # are bounded (the rate would give their count back).
        training_hidden = "clicked" in bounded
        training_publishable = (len(trainings) >= threshold and total > threshold
                                and not training_hidden)
        completed = len(trainings.filtered("completed"))
        # "No training" only when nothing could have assigned one: saying it
        # after clicks would tell that whoever clicked already had the course.
        campaign = results.campaign_id[:1]
        aucune_attendue = not clicked or not (
            campaign.auto_assign_training
            and campaign.template_id.training_channel_id)
        rates = {
            "opened": pct(opened, total),
            "clicked": pct(clicked, total),
            "submitted": pct(submitted, total),
            "reported": pct(reported, total),
            "training_none_expected": aucune_attendue,
            "training_hidden": training_hidden,
            "training_publishable": training_publishable,
            "training_completed": pct(completed, len(trainings))
            if training_publishable else None,
        }
        if training_publishable:
            bounded.update(self._aggregate_bounded(
                {"training_completed": (completed, len(trainings), False)}))
        rates["bounded"] = bounded
        return rates

    @api.model
    def _aggregate_bounded(self, counts):
        """{rate: "range" | "hidden"} for the unfavourable rates that fall in
        their band: who failed, or who did not report, would follow. `counts`
        maps a rate to (count, total, high_is_unfavourable)."""
        bounded = {}
        for key, (count, total, high) in counts.items():
            if not total:
                continue
            if high:
                # ceil(BAND_HIGH % of total), in integers, but never above total - 1.
                inside = count >= min((BAND_HIGH * total + 99) // 100, total - 1)
            else:
                inside = count <= max(BAND_LOW * total // 100, 1)
            if inside:
                bounded[key] = "range" if total >= BAND_LABEL_MIN else "hidden"
        return bounded

    def _aggregate_previous(self):
        """The latest earlier campaign of the same company that also passes
        the threshold, or an empty recordset."""
        self.ensure_one()
        threshold = self._aggregate_min_recipients()
        own_date = self._aggregate_send_date()
        if not own_date:
            return self.browse()
        candidates = self.search([
            ("id", "!=", self.id),
            ("company_id", "=", self.company_id.id),
            ("state", "in", ("running", "done")),
        ])
        best, best_date = self.browse(), None
        for camp in candidates:
            when = camp._aggregate_send_date()
            if not when or when >= own_date:
                continue
            if len(camp._aggregate_sent_results()) < threshold:
                continue
            if best_date is None or when > best_date:
                best, best_date = camp, when
        return best

    def _aggregate_values(self):
        """Everything the aggregate report shows. No person, no campaign name,
        no department, no risk profile, no time of click, at any size."""
        self.ensure_one()
        threshold = self._aggregate_min_recipients()
        results = self._aggregate_sent_results()
        values = {
            "reference": self.report_reference,
            "lure": self.template_id.name,
            "state": self.state,
            "send_date": self._aggregate_send_date(),
            "recipients": len(results),
            "threshold": threshold,
            "publishable": len(results) >= threshold,
            "rates": None,
            "previous": None,
        }
        if not values["publishable"]:
            return values
        values["rates"] = self._aggregate_rates(results, threshold)
        previous = self._aggregate_previous()
        if previous:
            prev_results = previous._aggregate_sent_results()
            prev_rates = previous._aggregate_rates(prev_results, threshold)
            rates = values["rates"]

            def delta(key):
                # A bounded rate on either side: the previous report and the
                # change would give the exact value back.
                if key in rates["bounded"] or key in prev_rates["bounded"]:
                    return None
                return round(rates[key] - prev_rates[key], 1)

            values["previous"] = {
                "send_date": previous._aggregate_send_date(),
                # Its size tells whether the comparison holds (a targeted test
                # of 12 against the whole company). It passed the threshold.
                "recipients": len(prev_results),
                "clicked_delta": delta("clicked"),
                "reported_delta": delta("reported"),
            }
        return values
