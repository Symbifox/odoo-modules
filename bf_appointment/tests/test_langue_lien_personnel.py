# -*- coding: utf-8 -*-
"""La langue des pages de lien personnel (routes à jeton).

🔴 **Ce que la plateforme fait déjà, et qu'il ne faut pas refaire.** Odoo résout
la langue d'une requête de site dans cet ordre (`http_routing/models/ir_http.py`,
`_match`) : préfixe de l'URL, puis témoin `frontend_lang`, puis langue de la
SESSION, puis langue par défaut du site. La langue de session vient de
`Request.default_lang()`, c'est-à-dire de l'en-tête `Accept-Language`, ou de
`en_US` quand l'en-tête manque. Et quand la langue résolue n'est pas celle par
défaut sur une URL sans préfixe, Odoo **redirige** vers `/en/…` de lui-même.

Donc un navigateur anglais arrive déjà en anglais sans que le module s'en mêle,
et un témoin anglais est déjà respecté par cette redirection. Le seul cas qui
appartient au module est celui du témoin qui nomme la langue PAR DÉFAUT : Odoo
ne redirige pas (l'URL nue EST le français), et c'est là que
`_apply_locale_from_request()` écrasait le choix avec l'en-tête du navigateur.

⚠️ Un essai qui ne pose pas d'`Accept-Language` ne mesure pas ce qu'il croit :
sans en-tête, la session part sur `en_US`. Chaque cas ci-dessous nomme donc
l'en-tête ET le témoin, et ils se contredisent exprès.

⚠️ Et le PARCOURS ne se mesure pas au `curl` : son agent est reconnu comme un
robot (`is_a_bot`), et Odoo ne REDIRIGE jamais un robot — il lui impose la langue
par défaut en résolvant la requête. Le témoin et l'en-tête, eux, restent
pleinement honorés sur ces routes-ci, puisque c'est le contrôleur qui les lit,
APRÈS cette résolution : un robot à navigateur français portant un témoin `en_CA`
reçoit bien une page anglaise, et sans témoin il reçoit le français de son
en-tête. Ce qu'un robot ne voit pas, c'est le 303 d'Odoo, et rien d'autre.
"""

from odoo import Command
from odoo.tests import HttpCase, tagged

FR = "fr-CA,fr;q=0.9"
EN = "en-CA,en;q=0.9"


def _type_rdv(env, nom, slug):
    presences = [
        Command.create({
            "name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
            "hour_to": 24.0, "day_period": "morning",
        })
        for d in range(7)
    ]
    calendrier = env["resource.calendar"].create({
        "name": "24/7 %s" % slug, "attendance_ids": presences, "tz": "UTC"})
    ressource = env["resource.resource"].create({
        "name": "%s material" % slug, "calendar_id": calendrier.id,
        "resource_type": "material", "tz": "UTC"})
    combinaison = env["resource.booking.combination"].create({
        "resource_ids": [Command.set([ressource.id])]})
    return env["resource.booking.type"].create({
        "name": nom, "slug": slug,
        "duration": 1.0, "slot_duration": 1.0,
        "modifications_deadline": 0.0, "combination_assignment": "sorted",
        "resource_calendar_id": calendrier.id, "video_provider": "none",
        "requires_recording_consent": False,
        "sends_intake_acknowledgement": False,
        "is_public": True, "listed_on_landing": False,
        "combination_rel_ids": [
            Command.create({"sequence": 0, "combination_id": combinaison.id})],
    })


@tagged("post_install", "-at_install", "bf_appointment", "bf_appointment_langue")
class TestLangueLienPersonnel(HttpCase):

    def setUp(self):
        super().setUp()
        if not self.env["ir.module.module"].sudo().search_count(
                [("name", "=", "website"), ("state", "=", "installed")]):
            self.skipTest("module `website` absent : les routes `website=True` ne sont pas routées")
        # ⚠️ La question est « que PUBLIE ce site », pas « quelles langues la base
        # a-t-elle d'actives ». Les deux divergent, et pas en théorie :
        # `symbifox-demo` et `symbifox-essai` ont `en_CA` actif en base et ne
        # publient que le français. Une garde posée sur `res.lang.get_installed()`
        # ne sautait donc pas chez eux et six de ces neuf essais exigeaient une
        # page anglaise que le site ne route pas (mesuré : `6 failed of 9`).
        # C'est aussi la question que pose le correctif (`res.lang._get_frontend()`
        # par `ir.http.get_nearest_lang()`), et une recette qui en pose une autre
        # ne mesure pas ce qu'elle croit.
        site = self.env["website"].sudo().search([], limit=1)
        publiees = site.language_ids.mapped("code")
        defaut = site.default_lang_id.code
        self.fr = defaut if defaut in publiees else None
        self.en = next((c for c in publiees if c.startswith("en") and c != defaut), None)
        if not (self.fr and self.en):
            # Site d'une seule langue : il n'y a pas de sélecteur, donc pas de
            # bascule à tenir, et le correctif y est inerte.
            self.skipTest("site sans couple défaut/anglais publié : publiées = %s, défaut = %s"
                          % (publiees, defaut))
        self.type_rdv = _type_rdv(self.env, "Rencontre langue", "langue-lien-personnel")
        self.personne = self.env["res.partner"].create({
            "name": "Personne Langue", "email": "personne-langue@test.invalid"})
        self.lien = self.type_rdv._bf_create_onetime_link(self.personne)
        self.env.cr.flush()
        self.url = "/appointment/b/%d/%s/schedule" % (self.lien.id, self.lien.access_token)

    # -- lecture de la langue rendue ------------------------------------------

    # Un couple de libellés par page mesurée : même gabarit, même place. Le
    # correctif vit dans `_apply_locale_from_request()`, que les QUATRE routes à
    # jeton partagent ; le mesurer sur la seule page du créneau laisserait croire
    # qu'il est attaché à ce gabarit-là.
    TITRES_CRENEAU = ("Choisissez votre créneau", "Pick your time")
    TITRES_CONFIRMATION = ("En attente de confirmation", "Pending confirmation")

    def _langue(self, reponse, titres=None):
        """La langue du CORPS, pas le seul attribut `html lang`.

        Les deux libellés viennent du même gabarit, à la même place : la page du
        créneau titre « Choisissez votre créneau » ou « Pick your time », la page
        de confirmation « En attente de confirmation » ou « Pending confirmation ».
        Un verdict pris sur `html lang` seul ne verrait pas un corps parti dans
        l'autre langue, et c'est exactement la forme du défaut cherché ici.
        """
        self.assertEqual(reponse.status_code, 200, reponse.text[:200])
        texte = reponse.text
        libelle_fr, libelle_en = titres or self.TITRES_CRENEAU
        fr = libelle_fr in texte
        en = libelle_en in texte
        self.assertNotEqual(fr, en, "la page doit titrer dans une seule langue")
        rendue = "fr" if fr else "en"
        self.assertIn('<html lang="%s' % rendue, texte,
                      "l'attribut `html lang` contredit le corps")
        return rendue

    def _oublier_les_temoins(self):
        """Repartir sans témoin de langue ni session, SANS toucher au reste.

        ⚠️ `self.opener.cookies.clear()` retirerait aussi le témoin de curseur
        d'essai posé par `HttpCase.setUp` (`TEST_CURSOR_COOKIE_NAME`) : les
        requêtes sortiraient du curseur de l'essai et ne verraient plus les
        fiches qu'il vient de créer.

        ⚠️ Et pas de `except KeyError` autour du `del` : `__delitem__` d'un
        `RequestsCookieJar` passe par `remove_cookie_by_name()`, qui balaie le
        bocal et ne signale pas une absence (requests 2.31). La garde serait du
        code mort, c'est-à-dire une précaution qui ne protège de rien.
        """
        for nom in ("frontend_lang", "session_id"):
            del self.opener.cookies[nom]

    def _page(self, url=None, entete=None, temoin=None):
        self._oublier_les_temoins()
        if temoin:
            self.opener.cookies.set("frontend_lang", temoin)
        entetes = {"Accept-Language": entete} if entete else {}
        return self.url_open(url or self.url, headers=entetes)

    # -- le défaut corrigé ici --------------------------------------------------

    def test_le_temoin_de_la_langue_par_defaut_gagne_sur_un_navigateur_anglais(self):
        """🔴 Le cas signalé par un prospect le 2026-11-16 : il basculait vers
        Français sur la page de son lien et elle revenait en anglais à chaque
        fois. Odoo ne redirige pas ici (le témoin nomme la langue par défaut,
        dont l'URL nue est déjà la bonne), et le module écrasait le choix avec
        l'en-tête du navigateur.
        """
        self.assertEqual(self._langue(self._page(entete=EN, temoin=self.fr)), "fr")

    def test_la_bascule_du_selecteur_tient_sur_le_parcours_reel(self):
        """Le parcours entier d'un visiteur à navigateur anglais, dans l'ordre.

        1. il ouvre son lien : Odoo le renvoie sur `/en/…` et pose le témoin
           `en_CA` de lui-même ;
        2. il clique « Français » : le sélecteur appelle
           `/website/lang/<code>?r=<url>` (`website_root.js::_onLangChangeClick`),
           qui pose le témoin et renvoie sur l'URL SANS préfixe, la langue par
           défaut n'en ayant pas ;
        3. la page d'arrivée doit être française, et le rester au retour.

        🔴 Avant le correctif, l'étape 3 revenait en anglais, à chaque clic et à
        chaque retour : le témoin était écrit, puis jeté par l'en-tête du
        navigateur. Mesuré en production le 2026-11-16 (réservation 758).

        ⚠️ L'ordre compte, et ce n'est pas une coquetterie d'essai : à la toute
        première requête, sans aucun témoin, la réponse de `/website/lang/`
        porte DEUX `Set-Cookie: frontend_lang` — celui du contrôleur, puis celui
        que le rafraîchissement de fin de requête écrit depuis la langue de la
        session (`http_routing/models/ir_http.py`). Le client garde le dernier,
        donc l'anglais. Dès la deuxième requête, le témoin présent égale la
        langue de session et ce doublon disparaît. Un essai qui cliquerait la
        bascule sans avoir d'abord ouvert une page mesurerait ce décalage de
        l'amorce, pas le défaut du module.
        """
        self._oublier_les_temoins()
        entetes = {"Accept-Language": EN}
        saut = self.url_open(self.url, headers=entetes, allow_redirects=False)
        self.assertIn(saut.status_code, (301, 302, 303),
                      "Odoo envoie un navigateur anglais sur sa langue")
        self.assertEqual(self._langue(self.url_open(saut.headers["Location"],
                                                    headers=entetes)), "en")
        self.assertEqual(self.opener.cookies.get("frontend_lang"), self.en)

        url_fr = self.env["res.lang"]._lang_get(self.fr).url_code
        bascule = self.url_open("/website/lang/%s?r=%s" % (url_fr, self.url),
                                headers=entetes, allow_redirects=False)
        self.assertIn(bascule.status_code, (301, 302, 303))
        self.assertEqual(self.opener.cookies.get("frontend_lang"), self.fr,
                         "le clic doit laisser le choix dans le témoin")
        arrivee = self.url_open(bascule.headers["Location"], headers=entetes)
        self.assertEqual(self._langue(arrivee), "fr")
        # Et le choix tient au retour, sans repasser par le sélecteur.
        self.assertEqual(self._langue(self.url_open(self.url, headers=entetes)), "fr")

    # -- ce que la plateforme décide, et qui doit continuer de marcher --------

    def test_sans_temoin_l_en_tete_du_navigateur_decide(self):
        self.assertEqual(self._langue(self._page(entete=FR)), "fr")
        self.assertEqual(self._langue(self._page(entete=EN)), "en")

    def test_un_temoin_anglais_passe_par_la_redirection_d_odoo(self):
        """L'autre sens n'a jamais dépendu du module : une langue résolue qui
        n'est pas celle par défaut est renvoyée dans l'URL par Odoo lui-même.
        """
        self._oublier_les_temoins()
        self.opener.cookies.set("frontend_lang", self.en)
        saut = self.url_open(self.url, headers={"Accept-Language": FR},
                             allow_redirects=False)
        self.assertIn(saut.status_code, (301, 302, 303))
        self.assertIn("/%s/" % self.env["res.lang"]._lang_get(self.en).url_code,
                      saut.headers["Location"])
        self.assertEqual(self._langue(self._page(entete=FR, temoin=self.en)), "en")

    def test_le_prefixe_de_l_url_prime_sur_le_temoin_et_sur_l_en_tete(self):
        """Le préfixe est le choix le plus explicite : il gagne contre les deux.

        ⚠️ Odoo retire ce préfixe avant le routage, donc le contrôleur ne peut
        pas le lire dans le chemin : c'est la langue résolue du contexte qui en
        porte la trace.
        """
        url_en = "/%s%s" % (self.env["res.lang"]._lang_get(self.en).url_code, self.url)
        self.assertEqual(self._langue(self._page(url=url_en, entete=FR, temoin=self.fr)), "en")

    def test_un_temoin_qui_nomme_une_langue_absente_ne_sort_pas_des_langues_actives(self):
        """Un témoin bricolé est ignoré, et la page reste dans une langue servie."""
        self.assertEqual(self._langue(self._page(entete=EN, temoin="de_DE")), "en")
        self.assertEqual(
            self._langue(self._page(entete="de-DE,de;q=0.9", temoin="de_DE")), "fr")

    def test_un_temoin_illisible_est_traite_comme_absent(self):
        """🔴 Un témoin portant un octet NUL faisait tomber une page publique.

        `frontend_lang` est du texte fourni par le visiteur, et il partait tel
        quel dans `res.lang.search([("code", "=", ...)])` : psycopg2 refuse un
        NUL (« A string literal cannot contain NUL (0x00) characters »). Mesuré
        au socket brut, version par version : **`/appointment` et
        `/appointment/<slug>` rendaient 500** (le témoin y est lu par
        `_visitor_lang()` et `_maybe_redirect_to_visitor_lang()`), et aucun jeton
        valide n'était nécessaire. Les quatre routes à jeton, elles, tenaient :
        c'est un premier état de ce correctif qui les a ouvertes, avant que la
        garde ne referme les six. Un témoin illisible doit valoir un témoin
        absent : 200, dans la langue que le reste de la requête demande, et pas
        une trace au journal.
        """
        self.assertEqual(self._langue(self._page(entete=FR, temoin="fr\x00CA")), "fr")
        self.assertEqual(self._langue(self._page(entete=EN, temoin="fr\x00CA")), "en")
        # ⚠️ Et les deux routes PUBLIQUES, qui sont les seules que la version
        # d'avant faisait tomber : `_apply_locale_from_request()` sort tôt sur
        # elles, mais `_visitor_lang()` et `_maybe_redirect_to_visitor_lang()` y
        # lisent le même témoin. Sans ces deux lignes, l'essai reste vert contre
        # la version d'avant et ne mesure donc rien de ce que son titre annonce.
        for url in ("/appointment", "/appointment/%s" % self.type_rdv.slug):
            for entete in (FR, EN):
                with self.subTest(route=url, entete=entete):
                    self.assertEqual(
                        self._page(url=url, entete=entete, temoin="fr\x00CA").status_code,
                        200, "un témoin illisible ne doit pas casser une page publique")

    def test_le_temoin_decide_aussi_sur_la_page_de_confirmation(self):
        """La même mesure sur une DEUXIÈME route à jeton.

        Le correctif vit dans `_apply_locale_from_request()`, que les quatre
        routes à jeton appellent. Mesuré sur la seule page du créneau, il
        passerait pour une propriété de ce gabarit ; la page de confirmation,
        celle que les courriels ouvrent, porte le même défaut et le même
        correctif.
        """
        # La même route à jeton, sans son dernier segment : la page que les
        # courriels de confirmation ouvrent.
        url = self.url.removesuffix("/schedule")
        self.assertEqual(
            self._langue(self._page(url=url, entete=EN, temoin=self.fr),
                         self.TITRES_CONFIRMATION), "fr")
        self.assertEqual(
            self._langue(self._page(url=url, entete=FR, temoin="fr\x00CA"),
                         self.TITRES_CONFIRMATION), "fr")

    def test_un_site_d_une_seule_langue_reste_inerte(self):
        """Là où il n'y a pas de bascule à tenir, le module ne doit RIEN faire.

        Affirmé, et non sauté : le site qui sert la requête est ramené à sa seule
        langue par défaut — dans la transaction de l'essai, donc rendu tel quel
        à la fin — et la page doit rester dans cette langue alors que l'en-tête
        ET le témoin réclament l'autre.

        🔴 Le témoin était validé contre `res.lang.active`, qui est GLOBAL : un
        témoin `en_CA` servait donc une page anglaise sur un site qui ne publie
        pas l'anglais, sans sélecteur pour en sortir. Odoo, lui, valide contre
        `res.lang._get_frontend()` — les langues du SITE.
        """
        site = self.env["website"].sudo().search([], limit=1)
        site.language_ids = [Command.set([site.default_lang_id.id])]
        # Précondition MESURÉE, pas supposée : le site ne route plus l'anglais.
        # Sans elle, un essai vert ne prouverait que l'inertie de sa propre mise
        # en scène ([[reference_un_zero_constat_qui_ne_mesure_rien]]).
        prefixe = "/" + self.env["res.lang"]._lang_get(self.en).url_code + self.url
        self.assertEqual(
            self.url_open(prefixe, headers={"Accept-Language": EN}).status_code, 404,
            "le site publie encore l'anglais : la mise en scène n'a pas pris")
        self.assertEqual(self._langue(self._page(entete=EN, temoin=self.en)), "fr")
        self.assertEqual(self._langue(self._page(entete=EN, temoin="fr\x00CA")), "fr")
