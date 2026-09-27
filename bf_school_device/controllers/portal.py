from odoo.http import request

from odoo.addons.bf_school_portal.controllers.portal import SchoolPortal


class SchoolDevicePortal(SchoolPortal):

    def _school_child_cards(self, links):
        cards = super()._school_child_cards(links)
        loans = request.env["bf.school.device.loan"]._school_for_guardian(
            request.env.user.partner_id)
        for card in cards:
            card["loans"] = loans.filtered(lambda l, s=card["student"]: l.student_id == s)
        return cards
