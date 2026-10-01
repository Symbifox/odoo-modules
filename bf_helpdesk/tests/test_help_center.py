import json

from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


class HelpCommon:
    @classmethod
    def _setup_help(cls):
        cls.Article = cls.env["helpdesk.article"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Aide essai",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "aide-essai", "alias_model_id": model.id,
            }).id,
            "public_form_enabled": True,
            "slug": "aide-essai",
            "ack_channel_ids": [(5, 0, 0)],
            "csat_mode": "none",
        })
        cls.other_team = cls.env["helpdesk.ticket.team"].create({
            "name": "Autre équipe",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "autre-equipe", "alias_model_id": model.id,
            }).id,
            "public_form_enabled": True,
            "slug": "autre-equipe",
        })
        cls.printer = cls.Article.create({
            "name": "Mon imprimante n'imprime plus",
            "summary": "Vérifier la file d'impression et le pilote.",
            "keywords": "imprimante, impression, bourrage",
            "body": "<p>Ouvrez la file d'impression, puis redémarrez le service.</p>",
            "is_published": True,
        })
        cls.vpn = cls.Article.create({
            "name": "Se connecter au VPN",
            "summary": "Réseau privé depuis la maison.",
            "keywords": "vpn, réseau, télétravail",
            "body": "<p>Installez le client, puis entrez votre code.</p>",
            "is_published": True,
            "team_ids": [(6, 0, cls.other_team.ids)],
        })
        cls.draft = cls.Article.create({
            "name": "Imprimante couleur (brouillon)",
            "keywords": "imprimante",
            "is_published": False,
        })


@tagged("bf_helpdesk", "bf_helpdesk_help")
class TestHelpArticles(HelpCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls._setup_help()

    def test_slug_from_title_unique_and_reserved(self):
        self.assertEqual(self.printer.slug, "mon-imprimante-n-imprime-plus")
        twin = self.Article.create({"name": "Mon imprimante n'imprime plus"})
        self.assertEqual(twin.slug, "mon-imprimante-n-imprime-plus-2")
        reserved = self.Article.create({"name": "Suggestions"})
        self.assertEqual(reserved.slug, "suggestions-article")
        self.assertEqual(self.printer.website_url, f"/aide/{self.printer.slug}")

    def test_search_ranks_and_skips_drafts(self):
        found = self.Article._search_public("L'imprimante fait un bourrage")
        self.assertEqual(found[:1], self.printer)
        self.assertNotIn(self.draft, found)

    def test_search_folds_accents(self):
        self.assertIn(self.vpn, self.Article._search_public("probleme de reseau"))

    def test_search_without_useful_terms_is_empty(self):
        self.assertFalse(self.Article._search_public("le la de"))
        self.assertFalse(self.Article._search_public("zzzz qqqq"))

    def test_team_filter(self):
        self.assertNotIn(self.vpn, self.Article._search_public("vpn", team=self.team))
        self.assertIn(self.vpn, self.Article._search_public("vpn", team=self.other_team))
        # Un article sans équipe vaut pour toutes.
        self.assertIn(self.printer,
                      self.Article._search_public("imprimante", team=self.other_team))

    def test_translated_search(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        self.printer.with_context(lang="fr_FR").write({
            "name": "Mon imprimante n'imprime plus",
        })
        self.printer.with_context(lang="en_US").write({
            "name": "My printer stopped printing", "keywords": "printer, print",
        })
        en = self.Article.with_context(lang="en_US")._search_public("printer jammed")
        self.assertIn(self.printer, en)

    def test_create_article_from_ticket(self):
        agent = new_test_user(
            self.env, login="agent-aide",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user",
        )
        ticket = self.env["helpdesk.ticket"].create({
            "name": "Bourrage au 3e", "description": "<p>Papier coincé.</p>",
            "team_id": self.team.id,
        })
        ticket.with_user(agent).message_post(
            body="Ouvrez le capot arrière et tirez doucement.",
            message_type="comment", subtype_xmlid="mail.mt_comment",
        )
        action = ticket.with_user(agent).action_create_article()
        article = self.Article.browse(action["res_id"])
        self.assertFalse(article.is_published)
        self.assertIn("Papier coincé", article.body)
        self.assertIn("capot arrière", article.body)
        self.assertEqual(article.source_ticket_id, ticket)
        self.assertIn(article, ticket.article_ids)
        self.assertEqual(article.team_ids, self.team)


@tagged("post_install", "-at_install", "bf_helpdesk", "bf_helpdesk_help")
class TestHelpCenterPages(HelpCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_help()

    def test_index_lists_published_only(self):
        html = self.url_open("/aide").text
        self.assertIn("Mon imprimante n", html)
        self.assertNotIn("brouillon", html)
        html = self.url_open("/aide?q=imprimante").text
        self.assertIn("Mon imprimante n", html)
        self.assertNotIn("Se connecter au VPN", html)

    def test_article_page_and_views(self):
        resp = self.url_open(f"/aide/{self.printer.slug}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("file d'impression", resp.text)
        self.assertIn('<meta name="description" content="Vérifier la file d&#39;impression', resp.text)
        self.printer.invalidate_recordset(["view_count"])
        self.assertEqual(self.printer.view_count, 1)
        self.url_open(f"/aide/{self.printer.slug}",
                      headers={"User-Agent": "Googlebot/2.1"})
        self.printer.invalidate_recordset(["view_count"])
        self.assertEqual(self.printer.view_count, 1)

    def test_draft_is_not_found(self):
        resp = self.url_open(f"/aide/{self.draft.slug}")
        self.assertEqual(resp.status_code, 404)

    def test_vote_once_per_session(self):
        html = self.url_open(f"/aide/{self.printer.slug}").text
        token = html.split('name="csrf_token" value="')[1].split('"')[0]
        for _i in range(2):
            self.url_open(f"/aide/{self.printer.slug}/vote",
                          data={"csrf_token": token, "vote": "yes"})
        self.printer.invalidate_recordset(["helpful_yes"])
        self.assertEqual(self.printer.helpful_yes, 1)

    def test_suggestions_json(self):
        data = json.loads(self.url_open(
            "/aide/suggestions?q=bourrage+imprimante&team=aide-essai").text)
        self.assertEqual([d["title"] for d in data], [self.printer.name])
        self.assertTrue(data[0]["url"].endswith(f"/aide/{self.printer.slug}"))
        data = json.loads(self.url_open("/aide/suggestions?q=vpn&team=aide-essai").text)
        self.assertEqual(data, [])

    def test_title_is_escaped(self):
        self.printer.name = "<script>alert(1)</script> imprimante"
        html = self.url_open(f"/aide/{self.printer.slug}").text
        self.assertNotIn("<script>alert(1)</script>", html)
        raw = self.url_open("/aide/suggestions?q=imprimante").text
        self.assertIn("<script>", json.loads(raw)[0]["title"])  # donnée brute, montée en textContent

    def test_forms_carry_suggestion_hook(self):
        html = self.url_open("/support/aide-essai").text
        self.assertIn('data-bf-help-suggest="1"', html)
        self.assertIn('data-bf-help-team="aide-essai"', html)
