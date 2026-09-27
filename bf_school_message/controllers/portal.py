from odoo import http
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class SchoolNewsPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "school_news_count" in counters:
            values["school_news_count"] = len(
                request.env["bf.babillard.post"]._school_news_for(request.env.user))
        return values

    def _school_news_or_404(self, post_id):
        """The announcement, if this user may read it. Anything else is a 404, never a 403:
        a 403 would confirm that the announcement exists."""
        user = request.env.user
        post = request.env["bf.babillard.post"].sudo().browse(post_id).exists()
        if not post or post not in request.env["bf.babillard.post"]._school_news_for(user):
            return None
        return post

    @http.route("/my/school/news", type="http", auth="user", website=True)
    def portal_school_news(self, **kw):
        user = request.env.user
        posts = request.env["bf.babillard.post"]._school_news_for(user)
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "school_news",
            "posts": posts,
            "read_on": {p.id: p._school_read_on(user) for p in posts},
        })
        return request.render("bf_school_message.portal_school_news", values)

    @http.route("/my/school/news/<int:post_id>", type="http", auth="user", website=True)
    def portal_school_news_detail(self, post_id, **kw):
        post = self._school_news_or_404(post_id)
        if not post:
            raise request.not_found()
        user = request.env.user
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "school_news_detail",
            "post": post,
            "children": post._school_recipient_map().get(user.id),
            "read_on": post._school_read_on(user),
            "first_names": post._school_first_names,
        })
        return request.render("bf_school_message.portal_school_news_detail", values)

    @http.route("/my/school/news/<int:post_id>/read", type="http", auth="user",
                methods=["POST"], website=True)
    def portal_school_news_read(self, post_id, **kw):
        post = self._school_news_or_404(post_id)
        if not post:
            raise request.not_found()
        # 🔴 sudo keeps the portal user's uid: `action_marquer_lu` checks the
        # audience against that user and writes the receipt in their name.
        if post.lecture_requise:
            post.action_marquer_lu()
        return request.redirect("/my/school/news/%s" % post.id)

    @http.route("/my/school/news/<int:post_id>/image", type="http", auth="user")
    def portal_school_news_image(self, post_id, **kw):
        post = self._school_news_or_404(post_id)
        if not post or not post.image_couverture:
            raise request.not_found()
        return request.env["ir.binary"]._get_image_stream_from(
            post, "image_couverture").get_response()
