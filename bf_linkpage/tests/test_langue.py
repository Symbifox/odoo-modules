"""La langue des textes que le module écrit pour qui les lira.

La source est en anglais et le français vient de i18n/fr_CA.po. Le travail planifié n'a pas
d'usager : son avis suit la personne responsable de la page. Chaque essai fait tourner le code
dans la langue CONTRAIRE de celle attendue.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.tools.translate import code_translations

from odoo.addons.website.tools import MockRequest


@tagged("bf_linkpage", "post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.Page = cls.env["bf.linkpage"]
        cls.org = cls.env["res.partner"].create({
            "name": "Organisation de langue", "is_company": True,
        })

    def _conseil(self, lang):
        return self.env["res.users"].with_context(no_reset_password=True).create({
            "name": f"Conseil {lang}", "login": f"conseil.langue.{lang}@test.invalid",
            "email": f"conseil.langue.{lang}@test.invalid", "lang": lang,
        })

    def _page(self, conseil):
        return self.Page.create({
            "name": self.org.name, "kind": "org", "partner_id": self.org.id,
            "state": "published", "advisor_user_id": conseil.id,
            "date_expiry": fields.Datetime.now() + timedelta(days=5),
        })

    def test_avis_d_echeance_dans_la_langue_de_la_personne_responsable(self):
        resultats = {}
        for lang in ("fr_CA", "en_US"):
            page = self._page(self._conseil(lang))
            contraire = "en_US" if lang == "fr_CA" else "fr_CA"
            self.Page.with_context(lang=contraire)._cron_warn_expiring()
            activite = self.env["mail.activity"].search(
                [("res_model", "=", "bf.linkpage"), ("res_id", "=", page.id)])
            corps = page.message_ids.filtered(lambda m: "404" in (m.body or ""))[:1].body or ""
            date = page.date_expiry.date().strftime(self.env["res.lang"]._lang_get(lang).date_format)
            resultats[lang] = (activite.summary, corps, date)
        resume, corps, date = resultats["fr_CA"]
        self.assertEqual(resume, "Page d'organisation à reconduire ou à fermer")
        self.assertIn("Cette page ferme le %s" % date, corps)
        resume, corps, date = resultats["en_US"]
        self.assertEqual(resume, "Organization page to renew or close")
        self.assertIn("This page closes on %s" % date, corps, "date formatée dans sa langue")

    def test_libelles_des_sources_dans_la_langue_de_l_usager(self):
        Source = self.env["bf.linkpage.source"]
        fr = dict(Source.with_context(lang="fr_CA")._selection())
        en = dict(Source.with_context(lang="en_US")._selection())
        self.assertEqual(fr["manual"], "Adresse saisie")
        self.assertEqual(en["manual"], "Manual address")
        self.assertEqual(fr["appointment"], "Prise de rendez-vous")

    def test_le_bouton_de_theme_se_traduit_sur_la_page_publique(self):
        """Le site ne charge les traductions JS que des modules qui se déclarent."""
        # La méthode du site lit les modules de la requête : une requête simulée.
        with MockRequest(self.env, website=self.env["website"].get_current_website()):
            modules = self.env["ir.http"]._get_translation_frontend_modules_name()
        self.assertIn("bf_linkpage", modules)
        messages = {m["id"]: m["string"] for m in
                    code_translations.get_web_translations("bf_linkpage", "fr_CA")["messages"]}
        self.assertEqual(messages.get("Switch to the dark theme"), "Passer au thème sombre")
        self.assertEqual(messages.get("Switch to the light theme"), "Passer au thème clair")

    def test_un_second_gabarit_par_defaut_se_refuse_dans_la_langue_de_l_usager(self):
        from odoo.exceptions import ValidationError
        Gabarit = self.env["bf.linkpage.template"]
        Gabarit.search([("is_default", "=", True), ("target", "=", "person")]).write({"is_default": False})
        Gabarit.create({"name": "Premier", "target": "person", "is_default": True})
        for lang, libelle in (("fr_CA", "« Pages de personnes »"), ("en_US", "\"Person pages\"")):
            with self.assertRaises(ValidationError) as refus:
                Gabarit.with_context(lang=lang).create(
                    {"name": f"Second {lang}", "target": "person", "is_default": True})
            self.assertIn(libelle, str(refus.exception))
