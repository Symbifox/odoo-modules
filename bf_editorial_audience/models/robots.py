# -*- coding: utf-8 -*-
"""Reconnaître un robot à ce qu'il déclare, et rien de plus.

Ce fichier ne touche ni à la base ni à une requête : il prend une chaîne
d'agent utilisateur et rend un verdict. C'est ce qui le rend testable sans
locataire, et c'est ce qui permet de reclasser un historique quand un robot
nouveau apparaît, sans rejouer une seule visite.

⚠️ La portée du verdict. Un agent utilisateur se falsifie en une ligne de
code : ce qu'on mesure ici, ce sont les robots qui se DÉCLARENT. C'est une
mesure utile parce que les gros indexeurs et les moissonneurs d'entraînement
se déclarent tous, et c'est une mesure honnête tant qu'on ne la présente pas
comme un décompte de personnes.
"""

# ⚠️ L'ordre compte. « Robot générique » cherche le mot « bot » n'importe où :
# il attraperait « Googlebot » et « Bytespider » avant leur propre entrée, et
# le palmarès des familles se réduirait à une seule ligne. Il reste dernier.
SIGNATURES = (
    # Moissonneurs de modèles de langue
    ("GPTBot", ("gptbot",)),
    ("ChatGPT-User", ("chatgpt-user",)),
    ("OAI-SearchBot", ("oai-searchbot",)),
    ("ClaudeBot", ("claudebot", "claude-web", "anthropic-ai")),
    ("PerplexityBot", ("perplexitybot", "perplexity-user")),
    ("Google-Extended", ("google-extended",)),
    ("Bytespider", ("bytespider",)),
    ("Amazonbot", ("amazonbot",)),
    ("Applebot-Extended", ("applebot-extended",)),
    ("CCBot", ("ccbot",)),
    ("Diffbot", ("diffbot",)),
    ("Omgili", ("omgili",)),
    # Indexeurs
    ("Googlebot", ("googlebot", "google-inspectiontool", "storebot-google")),
    ("Bingbot", ("bingbot", "adidxbot", "msnbot")),
    ("Applebot", ("applebot",)),
    ("DuckDuckBot", ("duckduckbot", "duckassistbot")),
    ("YandexBot", ("yandex",)),
    ("Baiduspider", ("baiduspider",)),
    ("PetalBot", ("petalbot",)),
    ("Seznam", ("seznambot",)),
    # Réseaux sociaux et aperçus de lien
    ("Meta", ("meta-externalagent", "facebookexternalhit", "facebookbot")),
    ("LinkedIn", ("linkedinbot",)),
    ("Bluesky", ("bluesky", "atproto")),
    ("Slack", ("slackbot", "slack-imgproxy")),
    ("Discord", ("discordbot",)),
    ("Telegram", ("telegrambot",)),
    ("WhatsApp", ("whatsapp",)),
    # Référencement et veille commerciale
    ("SemrushBot", ("semrushbot",)),
    ("AhrefsBot", ("ahrefsbot",)),
    ("MJ12bot", ("mj12bot",)),
    ("DotBot", ("dotbot",)),
    ("DataForSeo", ("dataforseo",)),
    ("Barkrowler", ("barkrowler",)),
    ("Screaming Frog", ("screaming frog",)),
    ("Bright Data", ("brightbot", "luminati")),
    # Sondes de disponibilité
    # ⚠️ « health-check » avec le trait d'union autant que sans : la sonde
    # maison d'hébergement s'annonce « Odoo-Hosting-Health-Check » et tombait
    # dans « Indéterminé », où elle aurait pollué le seau des agents non lus
    # à chaque passage, tous les jours, pour toujours.
    ("Sonde", ("uptimerobot", "pingdom", "statuscake", "site24x7",
               "healthcheck", "health-check", "better uptime", "betteruptime",
               "hetrixtool", "nagios", "zabbix", "prometheus")),
    # Bibliothèques clientes : ni un navigateur, ni un robot nommé
    ("Bibliothèque cliente", (
        "python-requests", "python-urllib", "aiohttp", "httpx", "curl/",
        "wget/", "go-http-client", "okhttp", "axios/", "libwww-perl",
        "java/", "jakarta", "scrapy", "guzzlehttp", "node-fetch",
        "http_request2", "postmanruntime", "restsharp", "apache-httpclient",
    )),
    # Le filet, toujours en dernier
    ("Robot générique", (
        "bot", "crawler", "spider", "crawl", "slurp", "fetcher", "scraper",
        "archiver", "monitoring", "validator", "feed",
    )),
)

# Un navigateur se reconnaît à ce qu'il traîne depuis trente ans.
MARQUES_DE_NAVIGATEUR = ("mozilla", "applewebkit", "gecko", "webkit", "opera")

INCONNU = "Agent non relevé"
NAVIGATEUR = "Navigateur"
INDETERMINE = "Indéterminé"


def classer(user_agent):
    """Rendre ``(is_bot, famille)`` pour une chaîne d'agent utilisateur.

    Trois issues, et pas deux : robot déclaré, navigateur, et le reste. Le
    reste existe et il compte — une chaîne absente, un client maison, un agent
    tronqué. Le confondre avec « humain » gonflerait la série filtrée de tout
    ce qu'on n'a pas su lire.
    """
    if not user_agent or not user_agent.strip():
        return False, INCONNU
    agent = user_agent.lower()
    for famille, motifs in SIGNATURES:
        if any(motif in agent for motif in motifs):
            return True, famille
    if any(marque in agent for marque in MARQUES_DE_NAVIGATEUR):
        return False, NAVIGATEUR
    return False, INDETERMINE


# ⚠️ Les familles qui comptent comme lecteur, et la seule définition de cette
# règle dans tout le module. Le relevé quotidien agrège en SQL pour tenir sur
# des dizaines de milliers de traces : il lit cette liste, il ne la recopie
# pas. Une règle écrite à deux endroits est une règle qui diverge.
#
# Ce n'est PAS « tout ce qui n'est pas un robot ». Un agent absent ou illisible
# n'est pas un robot déclaré, et ce n'est pas non plus un lecteur : c'est un
# inconnu. Le ranger d'office du côté humain ferait exactement ce que ce module
# existe pour éviter, et il le ferait dans le sens flatteur.
FAMILLES_HUMAINES = (NAVIGATEUR,)


def est_humain(is_bot, famille):
    """Ce visiteur compte-t-il comme lecteur ?"""
    return not is_bot and famille in FAMILLES_HUMAINES
