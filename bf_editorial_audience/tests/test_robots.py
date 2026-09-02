# -*- coding: utf-8 -*-
"""Le classement des agents décide de tout le reste : une famille mal rangée
et la série filtrée ment. On l'éprouve donc sur des chaînes réelles, pas sur
des chaînes inventées pour passer."""

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_editorial_audience.models import robots

# Des agents réellement vus dans les journaux, recopiés tels quels.
REELS = {
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.1;"
    " +https://openai.com/gptbot": ("GPTBot", True),
    "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)":
        ("ClaudeBot", True),
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)":
        ("Googlebot", True),
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)":
        ("Bingbot", True),
    "Mozilla/5.0 (compatible; SemrushBot/7~bl;"
    " +http://www.semrush.com/bot.html)": ("SemrushBot", True),
    "Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Mobile Safari/537.36 (compatible; Bytespider;"
    " spider-feedback@bytedance.com)": ("Bytespider", True),
    "facebookexternalhit/1.1": ("Meta", True),
    "python-requests/2.31.0": ("Bibliothèque cliente", True),
    "curl/8.5.0": ("Bibliothèque cliente", True),
    # La sonde d'hébergement maison, relevée en production le 2026-08-29.
    "Odoo-Hosting-Health-Check/1.0": ("Sonde", True),
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36":
        (robots.NAVIGATEUR, False),
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0":
        (robots.NAVIGATEUR, False),
}


@tagged("post_install", "-at_install")
class TestClassementDesAgents(TransactionCase):

    def test_agents_reels(self):
        for agent, (famille_attendue, robot_attendu) in REELS.items():
            is_bot, famille = robots.classer(agent)
            self.assertEqual(
                (is_bot, famille), (robot_attendu, famille_attendue),
                "mal classé : %s" % agent[:60],
            )

    def test_le_filet_ne_passe_pas_devant_un_nom(self):
        """« bot » est contenu dans « Googlebot ». Si le filet passait en
        premier, le palmarès des familles se réduirait à une seule ligne."""
        _is_bot, famille = robots.classer(
            "Mozilla/5.0 (compatible; Googlebot/2.1)"
        )
        self.assertEqual(famille, "Googlebot")

    def test_un_robot_inconnu_tombe_dans_le_filet(self):
        is_bot, famille = robots.classer("SuperNouveauCrawler/0.1")
        self.assertTrue(is_bot)
        self.assertEqual(famille, "Robot générique")

    def test_agent_absent(self):
        for vide in ("", "   ", None, False):
            is_bot, famille = robots.classer(vide)
            self.assertFalse(is_bot)
            self.assertEqual(famille, robots.INCONNU)

    def test_agent_illisible_nest_ni_robot_ni_lecteur(self):
        is_bot, famille = robots.classer("xyzzy/1")
        self.assertFalse(is_bot)
        self.assertEqual(famille, robots.INDETERMINE)

    def test_seul_un_navigateur_compte_comme_lecteur(self):
        """⚠️ Le piège central du module : « pas un robot » n'est pas
        « un humain ». Un agent absent ou illisible n'est ni l'un ni l'autre,
        et le ranger du côté humain gonflerait la série filtrée de tout ce
        qu'on n'a pas su lire."""
        self.assertTrue(robots.est_humain(False, robots.NAVIGATEUR))
        self.assertFalse(robots.est_humain(False, robots.INCONNU))
        self.assertFalse(robots.est_humain(False, robots.INDETERMINE))
        self.assertFalse(robots.est_humain(True, "GPTBot"))

    def test_la_liste_des_familles_humaines_est_la_regle(self):
        """Le relevé quotidien agrège en SQL et lit cette liste. Si elle
        s'ouvrait à l'inconnu ou à l'indéterminé, la série filtrée cesserait
        d'être filtrée sans qu'aucun autre test ne s'en aperçoive."""
        self.assertIn(robots.NAVIGATEUR, robots.FAMILLES_HUMAINES)
        self.assertNotIn(robots.INCONNU, robots.FAMILLES_HUMAINES)
        self.assertNotIn(robots.INDETERMINE, robots.FAMILLES_HUMAINES)
        for famille, _motifs in robots.SIGNATURES:
            self.assertNotIn(
                famille, robots.FAMILLES_HUMAINES,
                "une famille de robots ne peut pas compter comme lecteur",
            )

    def test_les_robots_qu_odoo_laisse_passer(self):
        """La raison d'être du module, écrite en test.

        Odoo refuse de tracer un visiteur dont l'agent contient une de ses
        treize sous-chaînes. Sa liste attrape tout ce qui se NOMME robot ; ce
        module existe pour les autres. Si un agent de cette liste cessait
        d'être reconnu, le module perdrait sa raison d'être en silence.
        """
        liste_odoo = [
            "bot", "crawl", "slurp", "spider", "curl", "wget",
            "facebookexternalhit", "whatsapp", "trendsmapresolver",
            "pinterest", "instagram", "google-pagerenderer", "preview",
        ]
        passent_au_travers = [
            "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
            "Barkrowler/0.9 (+https://babbar.tech/crawler)",
            "python-requests/2.31.0",
            "Go-http-client/2.0",
            "Scrapy/2.11 (+https://scrapy.org)",
            "node-fetch/1.0",
        ]
        for agent in passent_au_travers:
            minuscule = agent.lower()
            if any(motif in minuscule for motif in liste_odoo):
                # Barkrowler contient « crawler », donc Odoo l'attrape :
                # on ne le compte pas comme un manque de sa liste.
                continue
            is_bot, famille = robots.classer(agent)
            self.assertTrue(
                is_bot,
                "Odoo laisse passer %s : c'est à ce module de le reconnaître"
                % agent[:50],
            )
            self.assertNotEqual(famille, robots.NAVIGATEUR)
