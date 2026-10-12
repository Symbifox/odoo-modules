"""The subtypes of a follower line, for someone who can see the line only.

The core route checks the read right on an empty set, opens the line in sudo,
then checks the followed record under the reader's name: the refusal named the
model of a line the reader cannot see, and in debug mode its id and rules. The
line is now checked first, under the reader's name.
"""
from odoo import http
from odoo.http import request

from odoo.addons.mail.controllers import thread


class ThreadController(thread.ThreadController):

    @http.route()
    def read_subscription_data(self, follower_id):
        request.env["mail.followers"].browse(follower_id).exists().check_access("read")
        return super().read_subscription_data(follower_id)
