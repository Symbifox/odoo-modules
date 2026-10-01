import json
import re

from odoo import http
from odoo.http import request

_BOT = re.compile(r"bot|crawl|spider|slurp|preview|fetch|monitor", re.I)
_SLUG = re.compile(r"^[a-z0-9-]{1,90}$")


def _sitemap_articles(env, rule, qs):
    Article = env["helpdesk.article"].sudo()
    for article in Article.search(Article._public_domain()):
        loc = f"/aide/{article.slug}"
        if not qs or qs.lower() in loc:
            yield {"loc": loc}


class HelpCenterController(http.Controller):
    """Centre d'aide public : /aide, /aide/<adresse>, suggestions.

    Tout passe en sudo avec le domaine public (publié, société courante) :
    le visiteur n'a aucun droit sur le modèle, et n'en a pas besoin.
    """

    def _articles(self):
        return request.env["helpdesk.article"].sudo()

    def _team(self, slug):
        if not slug:
            return None
        team = request.env["helpdesk.ticket.team"].sudo().search(
            [("slug", "=", slug), ("public_form_enabled", "=", True)], limit=1)
        return team or None

    @http.route("/aide", type="http", auth="public", website=True, sitemap=True)
    def help_index(self, q=None, **kw):
        Article = self._articles()
        q = (q or "").strip()[:200]
        if q:
            articles = Article._search_public(q, limit=30)
        else:
            articles = Article.search(Article._public_domain())
        return request.render("bf_helpdesk.help_center_index", {
            "articles": articles, "q": q,
        })

    @http.route("/aide/<string:slug>", type="http", auth="public",
                website=True, sitemap=_sitemap_articles)
    def help_article(self, slug, **kw):
        if not _SLUG.match(slug or ""):
            return request.not_found()
        Article = self._articles()
        article = Article.search(
            Article._public_domain() + [("slug", "=", slug)], limit=1)
        if not article:
            return request.not_found()
        agent = request.httprequest.user_agent.string or ""
        if not _BOT.search(agent):
            # Écriture SQL : un compteur ne mérite ni suivi ni write_date.
            request.env.cr.execute(
                "UPDATE helpdesk_article SET view_count = COALESCE(view_count, 0) + 1 "
                "WHERE id = %s", (article.id,))
        voted = article.id in (request.session.get("bf_help_votes") or [])
        return request.render("bf_helpdesk.help_center_article", {
            "article": article,
            "voted": voted,
            "main_object": article,
            # website.layout lit cette variable pour <meta name="description">.
            "website_meta_description": article.summary or "",
        })

    @http.route("/aide/<string:slug>/vote", type="http", auth="public",
                website=True, methods=["POST"], csrf=True, sitemap=False)
    def help_vote(self, slug, vote=None, **kw):
        if not _SLUG.match(slug or ""):
            return request.not_found()
        Article = self._articles()
        article = Article.search(
            Article._public_domain() + [("slug", "=", slug)], limit=1)
        if not article:
            return request.not_found()
        votes = list(request.session.get("bf_help_votes") or [])
        if vote in ("yes", "no") and article.id not in votes:
            column = "helpful_yes" if vote == "yes" else "helpful_no"
            request.env.cr.execute(
                f"UPDATE helpdesk_article SET {column} = COALESCE({column}, 0) + 1 "  # noqa: S608
                "WHERE id = %s", (article.id,))
            votes.append(article.id)
            request.session["bf_help_votes"] = votes[-200:]
        return request.redirect(f"/aide/{article.slug}?merci=1#bf-help-vote")

    @http.route("/aide/suggestions", type="http", auth="public",
                website=True, methods=["GET"], sitemap=False)
    def help_suggest(self, q=None, team=None, **kw):
        """Suggestions pendant la saisie du sujet d'une demande (JSON)."""
        q = (q or "").strip()[:200]
        articles = self._articles()._search_public(q, team=self._team(team), limit=5)
        payload = [
            {"title": a.name, "summary": a.summary or "",
             "url": request.env["ir.http"]._url_for(a.website_url)}
            for a in articles
        ]
        return request.make_response(
            json.dumps(payload),
            headers=[("Content-Type", "application/json"),
                     ("Cache-Control", "no-store")],
        )
