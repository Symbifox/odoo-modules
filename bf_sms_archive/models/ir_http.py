"""Auth method gating the oversized SMS-import upload route.

``controllers/main.py`` raises ``max_content_length`` to 1 GB on
``/web/dataset/call_kw/sms.archive.import.wizard`` — a real need, since SMS
Backup & Restore exports run past 600 MB (see ``wizard/import_wizard.py``,
``_MAX_FILE_SIZE``). The problem was ``auth="user"``: Odoo applies the route's
``max_content_length`` and reads the body in ``pre_dispatch``, which runs AFTER
``_authenticate`` but BEFORE the model ACL is ever consulted
(``odoo/http.py``: ``_serve_ir_http`` → ``_authenticate`` → ``_pre_dispatch``).
Any authenticated account — a portal user included — could therefore push 1 GB
into memory per HTTP worker, repeatedly, without ever being entitled to the
wizard.

Authentication is the only hook that fires early enough, so the group check
moves there: ``auth="bf_sms_import"`` refuses the request before a single byte
of the body is read, and the cap stays where the feature needs it.
"""

from odoo import models
from odoo.exceptions import AccessDenied
from odoo.http import request

SMS_USER_GROUP = "bf_sms_archive.group_sms_user"


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _auth_method_bf_sms_import(cls):
        """``user`` plus membership of the SMS group, enforced pre-body-read."""
        cls._auth_method_user()
        if not request.env.user.has_group(SMS_USER_GROUP):
            raise AccessDenied()
