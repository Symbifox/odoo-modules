from odoo import http
from odoo.http import request

from odoo.addons.bf_school_portal.controllers.portal import SchoolPortal
from odoo.addons.privacy_consent.controllers.portal import PrivacyPortal


class SchoolStudentPortal(SchoolPortal):

    @http.route()
    def account(self, redirect=None, **post):
        # The student's card is the school's record: name, birth date, address. A student
        # does not edit it from the portal; the office does.
        if request.env.user._school_is_student_user():
            return request.redirect("/my/school")
        return super().account(redirect=redirect, **post)

    @http.route()
    def portal_my_school(self, **kw):
        response = super().portal_my_school(**kw)
        response.qcontext["school_own_student"] = request.env.user._school_is_student_user()
        return response


class SchoolStudentPrivacy(PrivacyPortal):
    """A child under 14 does not give, refuse nor withdraw their own consents (Law 25, s. 4.1):
    the parental authority does. Signed in with their own account, an 8-year-old student saw
    their consents with the buttons to give and withdraw them, and their marketing
    preferences (found in review). A student of 14 or more keeps them: the law makes them
    the person who consents.
    """

    @staticmethod
    def _school_child_under_14(partner):
        partner = partner.sudo()
        return partner.is_student and partner.is_minor_child

    def _get_consent_domain_for_partner(self, partner):
        if self._school_child_under_14(partner):
            return [("id", "=", False)]
        return super()._get_consent_domain_for_partner(partner)

    def _can_access_consent(self, partner, consent):
        if self._school_child_under_14(partner):
            return False
        return super()._can_access_consent(partner, consent)

    @http.route()
    def portal_privacy_preferences(self, **kw):
        if self._school_child_under_14(request.env.user.partner_id):
            return request.redirect("/my/school")
        return super().portal_privacy_preferences(**kw)

    @http.route()
    def portal_save_preferences(self, **kw):
        if self._school_child_under_14(request.env.user.partner_id):
            return request.redirect("/my/school")
        return super().portal_save_preferences(**kw)
