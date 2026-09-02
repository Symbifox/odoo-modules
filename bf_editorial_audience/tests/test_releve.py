# -*- coding: utf-8 -*-
"""Le relevé doit rattacher la bonne visite au bon article, et ses quatre
compteurs doivent s'additionner sans jamais se contredire."""

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestReleveDAudience(TransactionCase):

    def setUp(self):
        super().setUp()
        self.site = self.env["website"].search([], limit=1)
        blogue = self.env["blog.blog"].create({"name": "Banc audience"})
        self.billet = self.env["blog.post"].create({
            "name": "Un billet mesuré", "blog_id": blogue.id,
        })
        calendrier = self.env["bf.editorial.calendar"].create({
            "name": "Audience", "require_all_langs": "no",
        })
        self.entree = self.env["bf.editorial.entry"].create({
            "name": "Entrée mesurée", "calendar_id": calendrier.id,
            "post_id": self.billet.id,
        })
        self.hier = fields.Date.subtract(fields.Date.context_today(self.entree), days=1)
        self.langue = self.env["res.lang"].search([("active", "=", True)], limit=1)

    def _visiteur(self, agent, famille, is_bot):
        return self.env["website.visitor"].create({
            "access_token": "%032x" % (abs(hash(agent + famille)) % (16 ** 32)),
            "website_id": self.site.id,
            "lang_id": self.langue.id,
            "user_agent": agent,
            "agent_family": famille,
            "is_bot": is_bot,
        })

    def _trace(self, visiteur, url=None, jour=None):
        url = url or (
            "https://exemple.test/blog/banc-audience-1/un-billet-mesure-%s"
            % self.billet.id
        )
        quand = fields.Datetime.to_datetime(
            "%s 12:00:00" % (jour or self.hier)
        )
        return self.env["website.track"].create({
            "visitor_id": visiteur.id, "url": url, "visit_datetime": quand,
        })

    def test_les_trois_seaux_font_le_brut(self):
        self._trace(self._visiteur("Mozilla/5.0 Firefox", "Navigateur", False))
        self._trace(self._visiteur("GPTBot/1.1", "GPTBot", True))
        self._trace(self._visiteur("", "Agent non relevé", False))
        releves = self.env["bf.editorial.audience"]._capture_day(self.hier)
        releve = releves.filtered(lambda r: r.entry_id == self.entree)
        self.assertEqual(len(releve), 1)
        self.assertEqual(releve.tracked_views, 3)
        self.assertEqual(releve.human_views, 1)
        self.assertEqual(releve.bot_views, 1)
        self.assertEqual(releve.unknown_views, 1)
        self.assertEqual(
            releve.tracked_views,
            releve.human_views + releve.bot_views + releve.unknown_views,
        )

    def test_un_agent_indetermine_ne_compte_pas_comme_lecteur(self):
        """C'est la garantie centrale : « pas un robot » n'est pas « un
        humain ». Sans elle, tout ce qu'on ne sait pas lire gonfle la série
        filtrée, et la mesure ment dans le sens le plus flatteur."""
        self._trace(self._visiteur("xyzzy/1", "Indéterminé", False))
        releve = self.env["bf.editorial.audience"]._capture_day(self.hier)
        releve = releve.filtered(lambda r: r.entry_id == self.entree)
        self.assertEqual(releve.tracked_views, 1)
        self.assertEqual(releve.human_views, 0)
        self.assertEqual(releve.unknown_views, 1)

    def test_la_chaine_de_requete_ne_change_pas_l_article(self):
        """Une campagne UTM ne doit pas faire passer le même billet pour un
        autre : l'identifiant se lit après avoir retiré la requête."""
        visiteur = self._visiteur("Mozilla/5.0 Firefox", "Navigateur", False)
        self._trace(visiteur)
        self._trace(visiteur, url=(
            "https://exemple.test/blog/banc-audience-1/un-billet-mesure-%s"
            "?utm_source=linkedin" % self.billet.id
        ))
        releve = self.env["bf.editorial.audience"]._capture_day(self.hier)
        releve = releve.filtered(lambda r: r.entry_id == self.entree)
        self.assertEqual(releve.tracked_views, 2)

    def test_une_url_hors_blogue_est_ignoree(self):
        self._trace(
            self._visiteur("Mozilla/5.0 Firefox", "Navigateur", False),
            url="https://exemple.test/contactus",
        )
        releves = self.env["bf.editorial.audience"]._capture_day(self.hier)
        self.assertFalse(releves.filtered(lambda r: r.entry_id == self.entree))

    def test_un_billet_sans_entree_ne_fabrique_rien(self):
        autre = self.env["blog.post"].create({
            "name": "Sans entrée", "blog_id": self.billet.blog_id.id,
        })
        self._trace(
            self._visiteur("Mozilla/5.0 Firefox", "Navigateur", False),
            url="https://exemple.test/blog/banc-audience-1/sans-entree-%s" % autre.id,
        )
        avant = self.env["bf.editorial.audience"].search_count([])
        self.env["bf.editorial.audience"]._capture_day(self.hier)
        self.assertEqual(
            self.env["bf.editorial.audience"].search_count([]), avant,
            "un billet que personne ne pilote ne doit pas créer de relevé",
        )

    def test_rejouer_une_journee_ecrase_au_lieu_de_doubler(self):
        self._trace(self._visiteur("Mozilla/5.0 Firefox", "Navigateur", False))
        self.env["bf.editorial.audience"]._capture_day(self.hier)
        self.env["bf.editorial.audience"]._capture_day(self.hier)
        releves = self.env["bf.editorial.audience"].search([
            ("entry_id", "=", self.entree.id), ("capture_date", "=", self.hier),
        ])
        self.assertEqual(len(releves), 1)
        self.assertEqual(releves.tracked_views, 1)

    def test_l_entree_totalise_ses_releves(self):
        self._trace(self._visiteur("Mozilla/5.0 Firefox", "Navigateur", False))
        self._trace(self._visiteur("GPTBot/1.1", "GPTBot", True))
        self.env["bf.editorial.audience"]._capture_day(self.hier)
        self.entree.invalidate_recordset()
        self.assertEqual(self.entree.audience_tracked, 2)
        self.assertEqual(self.entree.audience_human, 1)
        self.assertEqual(self.entree.audience_bot, 1)
        self.assertEqual(self.entree.audience_bot_share, 50.0)
        self.assertEqual(self.entree.audience_first_day, self.hier)

    def test_une_journee_sans_trace_ne_cree_rien(self):
        releves = self.env["bf.editorial.audience"]._capture_day(
            fields.Date.subtract(self.hier, days=400)
        )
        self.assertFalse(releves)
