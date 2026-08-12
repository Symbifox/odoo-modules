from odoo.addons.web.controllers.dataset import DataSet
from odoo.http import route


class DataSetSmsArchive(DataSet):
    """Override call_kw for sms.archive.import.wizard to allow large uploads.

    ⚠ ``auth="bf_sms_import"`` (models/ir_http.py), not ``auth="user"``: the 1 GB
    body is read in ``pre_dispatch``, before any model ACL is consulted, so
    ``user`` let ANY authenticated account — portal included — push a gigabyte
    per worker without rights on the wizard. Authentication is the only hook
    early enough to refuse it, hence the group check living there.
    """

    @route(
        "/web/dataset/call_kw/sms.archive.import.wizard",
        type="json",
        auth="bf_sms_import",
        max_content_length=1024 * 1024 * 1024,  # 1 GB
    )
    def call_kw_sms_import(self, model, method, args, kwargs):
        return self._call_kw(model, method, args, kwargs)
