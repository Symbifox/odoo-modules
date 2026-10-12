"""A follower line is read only by someone who can read the record it follows.

``mail.followers`` is readable by every internal user and has no rule. A search on
``res_model`` listed the followers of records hidden from the reader: which emails,
private notes, expenses or invoices exist, and whose they are. Odoo already ties
messages and attachments to the access of their record; follower lines get the
same treatment. Two gates:

* ``_search``: the search, AND the read of a stored field by id (``fetch`` re-reads
  the ids through ``_search`` and refuses those that do not come back);
* ``_check_access``: ``check_access`` / ``has_access``, called by the controllers.

The superuser and the code that runs in sudo (notifications, subscriptions, the
chatter once the record is checked) are untouched.

A rule of a followed model may itself go through ``message_follower_ids``. While
the readable records are being computed, the search of follower lines that this
triggers is left to Odoo: without that, the computation would call itself. The
flag is a thread-local, not a context key, which a caller could pass.
"""
import threading

from odoo import _, api, models
from odoo.exceptions import AccessError
from odoo.osv import expression
from odoo.tools import SQL

_COMPUTING = threading.local()


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        if self.env.su or getattr(_COMPUTING, "active", False):
            return super()._search(domain, offset=offset, limit=limit, order=order)
        lines = super()._search(domain)
        if lines.is_empty():
            return lines
        self.flush_model(["res_model"])
        rows = self.env.execute_query(SQL("SELECT DISTINCT res_model FROM mail_followers WHERE id IN %s", lines.subselect()))
        readable = self._bf_readable_records_domain([name for name, in rows], lines)
        return super()._search(expression.AND([domain, readable]), offset=offset, limit=limit, order=order)

    @api.model
    def _bf_readable_records_domain(self, model_names, lines):
        """Domain of the lines whose record the user can read, among ``lines``.

        The rules are applied to the followed records only, through a subquery:
        over a whole model (8,000 tasks), they cost 65 ms for the chatter of a
        single task.
        """
        free, clauses = [], []
        before = getattr(_COMPUTING, "active", False)
        _COMPUTING.active = True
        try:
            for name in model_names:
                if name not in self.env:
                    continue
                records = self.env[name].with_context(active_test=False)
                if not records.has_access("read"):
                    continue
                if self.env["ir.rule"]._compute_domain(name, "read"):
                    followed = SQL(
                        "(SELECT res_id FROM mail_followers WHERE res_model = %s AND id IN %s)",
                        name, lines.subselect())
                    clauses.append([("res_model", "=", name), ("res_id", "in", records._search([("id", "in", followed)]))])
                else:
                    free.append(name)
        finally:
            _COMPUTING.active = before
        return expression.OR([[("res_model", "in", free)], *clauses])

    def _check_access(self, operation):
        result = super()._check_access(operation)
        # As the core: nothing to filter for new records, nor when the access right
        # already refuses every line (portal, public: has_access must not raise).
        if self.env.su or operation != "read" or not any(self._ids) or (result and not self - result[0]):
            return result
        # One source of truth: a line is readable when the filtered search returns it.
        forbidden = (self - self.search([("id", "in", self.ids)])).exists()
        if not forbidden:
            return result
        if result:
            forbidden |= result[0]
        error = AccessError(_("The followers of a record can only be seen by those who can read the record."))
        return forbidden, lambda: error
