"""Chaque texte que ce module écrit pour quelqu'un d'autre est dans la langue de QUI LE LIRA
.

18.0.2.5.0 passe la source en anglais (le français vit dans ``i18n/fr_CA.po``). Odoo ne
traduit jamais vers en_US, et ``_()`` prend la langue de QUI ÉCRIT. La tâche planifiée
tourne sous un autre usager que le demandeur : le message d'état de la fiche, la
notification et la note au fil se lisent pourtant dans la langue du DEMANDEUR.

Les essais épinglent le demandeur dans une langue et font tourner la tâche dans
l'autre (``with_context(lang=…)``), pour que l'ancien code (``_()`` de l'écrivain) tombe
dans les deux sens. Les langues sont activées ici : une base neuve est en_US seule, une
base réelle (BF) est fr_CA seule.
"""
import json
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

BRIDGE = "odoo.addons.bf_contact_enrichment.tools.bridge.call_bridge"

TROUVE = {
    "company_name": "Ébénisterie Boréal-Orme",
    "website": "ebenisterie-boreal-orme.example.test",
    "phone": "819 555 0142",
    "street": "12 rue du Moulin",
    "city": "Racine",
    "state": "Québec",
    "country": "Canada",
    "sources": ["https://ebenisterie-boreal-orme.example.test/contact/",
                "javascript:alert(1)"],
    "confidence": 88,
}


@tagged("post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_US"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_contact_enrichment"], ["fr_CA"], overwrite=True)
        groupe = cls.env.ref("bf_contact_enrichment.group_bf_contact_enrich")
        droits = [cls.env.ref("base.group_user").id,
                  cls.env.ref("base.group_partner_manager").id, groupe.id]
        cls.lecteur = {}
        for lang in ("fr_CA", "en_US"):
            cls.lecteur[lang] = cls.env["res.users"].create({
                "name": "Lecteur " + lang,
                "login": "lecteur_%s_26848" % lang.lower(),
                "email": "lecteur_%s_26848@example.com" % lang.lower(),
                "lang": lang,
                "groups_id": [(6, 0, droits)],
            })

    # 🔴 `with_user` garde le contexte : sur le Web, le contexte porte la langue de
    # l'usager, pas ici. Les gestes interactifs passent donc `with_context(lang=…)`.
    def _autre(self, lang):
        return "en_US" if lang == "fr_CA" else "fr_CA"

    def _creer(self, lang, nom="Ébénisterie Boréal-Orme", **vals):
        """Une société créée par le lecteur de cette langue : elle part en file."""
        return self.env["res.partner"].with_user(self.lecteur[lang]).create(
            dict({"name": nom, "is_company": True, "city": "Racine"}, **vals))

    def _tourner(self, partner, reponse, lang_tache):
        """La tâche planifiée, dans la langue de l'autre."""
        with patch(BRIDGE, return_value=reponse):
            self.env["res.partner"].with_context(
                lang=lang_tache)._cron_bf_gen_companies(chunk=5)
        partner.invalidate_recordset()

    # ── Le message d'état de la fiche ───────────────────────────────

    def test_le_message_d_etat_suit_le_demandeur(self):
        attendu = {"fr_CA": "Rien de fiable trouvé en ligne (confiance 10)",
                   "en_US": "Nothing reliable found online (confidence 10)"}
        for lang in ("fr_CA", "en_US"):
            with self.subTest(demandeur=lang):
                p = self._creer(lang)
                self._tourner(p, {"data": {"confidence": 10}}, self._autre(lang))
                self.assertEqual(p.x_bf_gen_state, "nothing")
                self.assertIn(attendu[lang], p.x_bf_gen_message)

    def test_le_message_d_erreur_inattendue_suit_le_demandeur(self):
        attendu = {"fr_CA": "Erreur inattendue", "en_US": "Unexpected error"}
        for lang in ("fr_CA", "en_US"):
            with self.subTest(demandeur=lang):
                p = self._creer(lang)
                with patch(BRIDGE, return_value={"data": TROUVE}), patch.object(
                        type(p), "_bf_gen_appliquer_reponse",
                        side_effect=RuntimeError("panne d'essai")):
                    self.env["res.partner"].with_context(
                        lang=self._autre(lang))._cron_bf_gen_companies(chunk=5)
                p.invalidate_recordset()
                self.assertEqual(p.x_bf_gen_state, "error")
                self.assertIn(attendu[lang], p.x_bf_gen_message)

    # ── La notification passagère ───────────────────────────────────

    def test_la_notification_suit_le_demandeur(self):
        Partner = type(self.env["res.partner"])
        attendu = {"fr_CA": ("Complétion par Gen", "ouvrez la fiche"),
                   "en_US": ("Completion by Gen", "open the record")}
        for lang in ("fr_CA", "en_US"):
            with self.subTest(demandeur=lang):
                p = self._creer(lang)
                with patch.object(Partner, "_bus_send", autospec=True) as envoi:
                    self._tourner(p, {"data": TROUVE}, self._autre(lang))
                self.assertEqual(p.x_bf_gen_state, "proposed")
                charges = [c.args[2] for c in envoi.call_args_list
                           if c.args[1] == "simple_notification"]
                self.assertEqual(len(charges), 1)
                titre, morceau = attendu[lang]
                self.assertEqual(charges[0]["title"], titre)
                self.assertIn(morceau, charges[0]["message"])

    # ── La note au fil ──────────────────────────────────────────────

    def test_la_note_au_fil_suit_le_demandeur(self):
        attendu = {"fr_CA": "Recherche en ligne par Gen",
                   "en_US": "Online search by Gen"}
        for lang in ("fr_CA", "en_US"):
            with self.subTest(demandeur=lang):
                self.lecteur[lang].bf_gen_company_mode = "fill"
                p = self._creer(lang)
                self._tourner(p, {"data": TROUVE}, self._autre(lang))
                self.assertEqual(p.x_bf_gen_state, "done")
                fil = " ".join(p.message_ids.mapped("body"))
                self.assertIn(attendu[lang], fil)
                self.assertNotIn("javascript:", fil)

    # ── Le titre de la fenêtre de la proposition ────────────────────

    def test_la_fenetre_de_la_proposition_porte_un_titre(self):
        """🔴 Sans `name`, la fenêtre s'ouvrait intitulée « Odoo » (tournage du 2026-10-19)."""
        attendu = {"fr_CA": "Enrichir un contact", "en_US": "Enrich a contact"}
        for lang in ("fr_CA", "en_US"):
            with self.subTest(lecteur=lang):
                p = self._creer(lang)
                self._tourner(p, {"data": TROUVE}, lang)
                action = p.with_user(self.lecteur[lang]).with_context(
                    lang=lang).action_bf_gen_review()
                self.assertEqual(action["name"], attendu[lang])

    def test_la_fenetre_de_la_carte_porte_un_titre(self):
        attendu = {"fr_CA": "Numériser une carte d'affaires",
                   "en_US": "Scan a business card"}
        for lang in ("fr_CA", "en_US"):
            with self.subTest(lecteur=lang):
                wizard = self.env["bf.contact.card.wizard"].with_user(
                    self.lecteur[lang]).with_context(lang=lang).create({})
                self.assertEqual(wizard._reopen()["name"], attendu[lang])

    # ── Les sources lues par Gen ────────────────────────────────────

    def _assistant_gen(self, lang, sources):
        p = self._creer(lang)
        self._tourner(p, {"data": dict(TROUVE, sources=sources)}, lang)
        action = p.with_user(self.lecteur[lang]).action_bf_gen_review()
        return self.env["bf.contact.enrich.wizard"].with_user(
            self.lecteur[lang]).browse(action["res_id"])

    def test_les_sources_se_montrent_en_liens(self):
        """🔴 Au tournage, les sources n'avaient pas d'étiquette et une seule se voyait."""
        wizard = self._assistant_gen("en_US", [
            "https://un.example.test/a", "https://deux.example.test/b",
            "https://trois.example.test/c"])
        html = str(wizard.proposed_sources_html)
        for n in ("un", "deux", "trois"):
            self.assertIn('<a href="https://%s.example.test/' % n, html)
        self.assertEqual(html.count("<li>"), 3)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_les_sources_ne_laissent_passer_que_le_web(self):
        wizard = self._assistant_gen("en_US", [
            "javascript:alert(1)", "data:text/html,<b>x", "ftp://x.example.test/",
            "https://ok.example.test/?a=\"b\"&c=<i>",
            "https://ok.example.test/?a=\"b\"&c=<i>"])
        html = str(wizard.proposed_sources_html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("data:", html)
        self.assertNotIn("ftp:", html)
        self.assertEqual(html.count("<li>"), 1, "sans doublon")
        self.assertNotIn('"b"', html, "le guillemet est échappé dans l'attribut")
        self.assertNotIn("<i>", html)

    def test_sans_source_pas_d_intertitre(self):
        wizard = self._assistant_gen("en_US", [])
        self.assertFalse(wizard.proposed_sources_html)

    def test_l_intertitre_des_sources_suit_la_langue(self):
        attendu = {"fr_CA": "Sources lues par Gen", "en_US": "Sources read by Gen"}
        for lang in ("fr_CA", "en_US"):
            with self.subTest(lecteur=lang):
                arch = self.env["bf.contact.enrich.wizard"].with_context(
                    lang=lang).get_view(view_type="form")["arch"]
                self.assertIn(attendu[lang], arch)
                self.assertIn('name="proposed_sources_html"', arch)

    # ── Les motifs de doublon ───────────────────────────────────────

    def test_le_motif_de_doublon_suit_le_lecteur(self):
        attendu = {"fr_CA": "Même courriel", "en_US": "Same email"}
        for lang in ("fr_CA", "en_US"):
            with self.subTest(lecteur=lang):
                courriel = "double_%s_26848@example.com" % lang.lower()
                self.env["res.partner"].create([
                    {"name": "Double A " + lang, "email": courriel},
                    {"name": "Double B " + lang, "email": courriel}])
                wizard = self.env["bf.contact.dedup.wizard"].with_user(
                    self.lecteur[lang]).with_context(lang=lang).create({})
                wizard.action_scan()
                lignes = wizard.line_ids.filtered(lambda l: l.key == courriel)
                self.assertEqual(len(lignes), 1)
                self.assertEqual(lignes.reason, attendu[lang])

    # ── Épingler la langue du lecteur ne doit pas changer ce que l'on CHERCHE ──

    def test_pays_et_province_se_trouvent_quelle_que_soit_la_langue_du_demandeur(self):
        """🔴 ``with_context(lang=…)`` pilote aussi la recherche des champs traduits : le pont
        répond « United States » / « Vermont » en anglais, et pour un demandeur fr_CA en mode
        « Remplir » la tâche cherchait « États-Unis » (relecture adverse). Le pays
        se cherche en anglais d'abord, puis dans la langue du lecteur (le nom d'une province,
        lui, n'est pas un champ traduit)."""
        usa = self.env.ref("base.us")
        usa.update_field_translations("name", {"fr_CA": "États-Unis"})
        vermont = self.env["res.country.state"].search(
            [("country_id", "=", usa.id), ("code", "=", "VT")], limit=1)
        # « États-Unis » n'a de sens que pour un lecteur qui lit le français ; le code à deux lettres,
        # lui, se lit partout. (🔴 « États-Unis » trouvait l'Éthiopie : le code est tronqué à 2.)
        for lang, pays in (("fr_CA", "United States"), ("en_US", "United States"),
                           ("fr_CA", "États-Unis"), ("fr_CA", "us"), ("en_US", "US")):
            with self.subTest(demandeur=lang, pays=pays):
                self.lecteur[lang].bf_gen_company_mode = "fill"
                p = self._creer(lang, nom="Green Mountain Cheese %s %s" % (lang, pays))
                self._tourner(p, {"data": {
                    "website": "greenmountain.example.test", "phone": "802 555 0100",
                    "street": "1 Main Street", "city": "Burlington", "zip": "05401",
                    "state": "Vermont", "country": pays,
                    "sources": ["https://greenmountain.example.test/"],
                    "confidence": 88}}, "")
                self.assertEqual(p.x_bf_gen_state, "done")
                self.assertEqual(p.country_id, usa)
                self.assertEqual(p.state_id, vermont)

    def test_la_fenetre_du_detecteur_de_doublons_porte_un_titre(self):
        attendu = {"fr_CA": "Détecter les doublons", "en_US": "Find duplicates"}
        for lang in ("fr_CA", "en_US"):
            with self.subTest(lecteur=lang):
                wizard = self.env["bf.contact.dedup.wizard"].with_user(
                    self.lecteur[lang]).with_context(lang=lang).create({})
                self.assertEqual(wizard.action_scan()["name"], attendu[lang])

    def test_un_nom_long_ne_se_confond_pas_avec_un_code_de_pays(self):
        """🔴 ``res.country.code`` est un Char de 2 : la valeur cherchée est tronquée, et un nom
        de pays inconnu qui commence par « ét » trouvait l'Éthiopie (code ET)."""
        Partner = self.env["res.partner"]
        self.assertFalse(Partner._enrich_country_id("Étoile-Unie"))
        self.assertEqual(Partner._enrich_country_id("et"), self.env.ref("base.et").id)
        self.assertEqual(Partner._enrich_country_id("Canada"), self.env.ref("base.ca").id)

    def test_les_abreviations_de_pays_courantes_se_resolvent(self):
        """« USA » trouvait « US » par accident quand le code était tronqué à 2 ; sans table, plus
        rien ne résolvait « USA », « CAN », « UK » (relecture adverse du tour 2)."""
        Partner = self.env["res.partner"]
        for texte, xmlid in (("USA", "base.us"), ("U.S.A.", "base.us"), ("usa", "base.us"),
                             ("UK", "base.uk"), ("GBR", "base.uk"), ("CAN", "base.ca"),
                             ("FRA", "base.fr"), ("NZL", "base.nz"), ("US", "base.us")):
            with self.subTest(texte=texte):
                self.assertEqual(Partner._enrich_country_id(texte), self.env.ref(xmlid).id)

    def test_les_libelles_du_resume_au_fil_suivent_la_langue_du_lecteur(self):
        """🔴 ``field.string`` est le texte SOURCE : un lecteur français lisait « LinkedIn profile »."""
        p = self.env["res.partner"].create({"name": "Libellés du résumé"})
        attendu = {"fr_CA": "Profil LinkedIn", "en_US": "LinkedIn profile"}
        for lang, libelle in attendu.items():
            with self.subTest(lecteur=lang):
                corps = str(p.with_context(lang=lang)._enrichment_log_html(
                    "x", ["x_linkedin_url"]))
                self.assertIn("<li>%s</li>" % libelle, corps)

