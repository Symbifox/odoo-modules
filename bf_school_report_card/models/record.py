import json

from odoo import fields, models


class SchoolRecord(models.Model):
    _inherit = "bf.school.record"

    def _issued_report_cards(self):
        """The report cards that were issued, those a correction replaced too: what the family received and read
        first is what the school remitted, and the version that replaced it does not erase it."""
        self.ensure_one()
        return self.env["bf.school.report.card"].sudo().search([
            ("student_id", "=", self.student_id.id), ("school_id", "=", self.school_id.id),
            ("state", "in", ("issued", "superseded"))], order="year_id, term_id, version")

    def _results_payload(self, with_receipts=False):
        """The issued report cards, as sealed, each version with its state; with the receipts of reading for the
        copies that stay with the person and the school (not for a transfer: the receiving school has no use of
        who read what)."""
        out = []
        for card in self._issued_report_cards():
            item = dict(json.loads(card.snapshot), seal_sha256=card.snapshot_sha256,
                        status="current" if card.state == "issued" else "replaced",
                        issued_on=card.issued_on and fields.Datetime.to_string(card.issued_on) or None,
                        shown_on=card.published_on and fields.Datetime.to_string(card.published_on) or None,
                        correction_reason=card.correction_reason or None)
            if with_receipts:
                item["read_receipts"] = [
                    {"read_by": receipt.guardian_id.name, "read_on": fields.Datetime.to_string(receipt.read_on)}
                    for receipt in card.read_ids.sorted("read_on")]
            out.append(item)
        return out

    def _extra_payload(self):
        data = super()._extra_payload()
        data["report_cards"] = self._results_payload(with_receipts=True)
        return data

    def _transfer_results_payload(self):
        data = super()._transfer_results_payload()
        data["report_cards"] = self._results_payload()
        return data
