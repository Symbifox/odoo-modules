from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user


class FakeRequest:
    """Just enough of odoo.http.request for ``acting_in_own_web_session``."""

    def __init__(self, uid, path="/web/dataset/call_kw", session=None):
        self.session = type("S", (dict,), {})(session or {})
        self.session.uid = uid
        self.httprequest = type("H", (), {"path": path})()


@contextmanager
def acting_as(uid, path="/web/dataset/call_kw", **session):
    """Pretend the call comes over HTTP: a web session, an API key path, an impersonation."""
    fake = FakeRequest(uid, path, session)
    with patch("odoo.addons.bf_work_hours.models.tools.request", fake), \
            patch("odoo.addons.bf_work_hours.models.work_signal.request", fake):
        yield fake


class WorkHoursCase(TransactionCase):
    """Two employees and an administrator, none of them activated."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, "wh_alice", groups="base.group_user", tz="America/Montreal")
        cls.bob = new_test_user(cls.env, "wh_bob", groups="base.group_user", tz="America/Montreal")
        cls.admin = new_test_user(cls.env, "wh_admin", groups="base.group_user,base.group_system,hr.group_hr_manager",
                                  tz="America/Montreal")
        cls.Consent = cls.env["bf.work.consent"]
        cls.Signal = cls.env["bf.work.signal"]
        cls.Day = cls.env["bf.work.day"]
        cls.Share = cls.env["bf.work.share"]

    def withdraw(self, user, scope="hours"):
        user.with_user(user).with_context(bf_work_scope=scope).action_bf_work_withdraw()

    def activate(self, user, scope="hours", granted_at=None):
        """Activate as the person, through the notice, like the interface does."""
        wizard = self.env["bf.work.activate.wizard"].with_user(user).create({"scope": scope, "understood": True})
        wizard.action_confirm()
        consent = self.Consent.sudo().search([("user_id", "=", user.id), ("scope", "=", scope),
                                              ("withdrawn_at", "=", False)])
        if granted_at:
            consent.sudo().write({"granted_at": granted_at})
        return consent

    def signal(self, user, start, minutes=0, source="presence"):
        return self.Signal.sudo().create({"user_id": user.id, "source": source, "date_start": start,
                                          "date_stop": start + timedelta(minutes=minutes)})

    @staticmethod
    def utc(*args):
        return datetime(*args)
