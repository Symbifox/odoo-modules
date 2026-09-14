"""Per-person Nextcloud identity for the embedded browser.

The browser used to speak to Nextcloud with the storage configuration's single
account, so every member of the browser group acted as that one account and
Nextcloud's own permissions were never consulted. Each person now connects
their own account through Nextcloud's Login Flow v2 and the browser presents
that person's app password instead.

This file holds what the model files share: the exceptions the browser maps to
a "connect your Nextcloud" state, and the requests auth object that turns the
HTTP status of a per-person call into one of them.
"""

from requests.auth import HTTPBasicAuth

from odoo.exceptions import UserError


class NcNotConnected(UserError):
    """The current person has no Nextcloud connection for this configuration."""


class NcTokenRejected(UserError):
    """Nextcloud refused the person's app password (revoked, or account renamed)."""


class NcAccountUnavailable(UserError):
    """Nextcloud answered 503 for the person: account disabled, or maintenance."""


class NcPathNotFound(UserError):
    """PROPFIND 404: the folder does not exist in this person's Nextcloud."""


class NcPersonAuth(HTTPBasicAuth):
    """Basic auth with a person's app password, reading the response status.

    The WebDAV/OCS helpers of bf_document_nextcloud_sync turn every failure
    into one generic UserError, so the browser could not tell "your token was
    revoked" from "the server is down". A response hook sees the status before
    those helpers do and raises the precise exception instead. None of these
    exceptions derives from requests.RequestException, so the helpers'
    `except requests.RequestException` blocks let them through untouched.

    Only PROPFIND turns a 404 into an exception: DELETE treats 404 as success
    (already gone) and GET already has its own "file not found" message.

    Messages are built by the caller, in a frame that has an environment:
    a hook has none, and `_()` would silently fall back to the source string.
    """

    def __init__(self, username, password, messages):
        super().__init__(username, password)
        self.messages = messages

    def __call__(self, request):
        request = super().__call__(request)
        request.register_hook("response", self._check_status)
        return request

    def _check_status(self, response, **kwargs):
        method = (response.request.method or "").upper() if response.request else ""
        if response.status_code == 401:
            raise NcTokenRejected(self.messages["rejected"])
        if response.status_code == 503:
            raise NcAccountUnavailable(self.messages["unavailable"])
        if response.status_code == 404 and method == "PROPFIND":
            raise NcPathNotFound(self.messages["not_found"])
        return response
