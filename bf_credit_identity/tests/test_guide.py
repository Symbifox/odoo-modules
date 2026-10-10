import re

from odoo.tests import tagged

from .common import CreditCase


def _titres(html):
    return re.findall(r"<h2>(.*?)</h2>", str(html))


@tagged("post_install", "-at_install", "bf_credit_identity")
class TestGuide(CreditCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(["bf_credit_identity"], ["fr_CA"], overwrite=True)

    def _guide(self, lang, user=None):
        env = self.env(user=user or self.anne, context={"lang": lang})
        return str(env["bf.credit.guide"].get_guide_html())

    def test_le_guide_en_anglais_cite_ses_sources(self):
        guide = self._guide("en_US")
        for attendu in ("Identity: the guide", "Security freeze",
                        "Credit Assessment Agents Act", "read on October 2, 2026",
                        "https://www.legisquebec.gouv.qc.ca/fr/document/lc/A-8.2",
                        "https://lautorite.qc.ca/grand-public/dossier-de-credit",
                        "https://www.transunion.ca/fr/assistance/fraude-et-vol-didentite"):
            with self.subTest(attendu=attendu):
                self.assertIn(attendu, guide)
        self.assertEqual(len(_titres(guide)), 10)
        self.assertEqual(guide.count('target="_blank"'), 27)

    def test_le_guide_en_francais(self):
        guide = self._guide("fr_CA")
        for attendu in ("Crédit et identité : le guide", "Gel de sécurité",
                        "Loi sur les agents d'évaluation du crédit", "lues le 2 octobre 2026",
                        "https://www.legisquebec.gouv.qc.ca/fr/document/lc/A-8.2"):
            with self.subTest(attendu=attendu):
                self.assertIn(attendu, guide.replace("&#39;", "'"))
        self.assertNotIn("Security freeze", guide)
        # Chaque titre de section est traduit (sauf « Sources », identique).
        en, fr = _titres(self._guide("en_US")), _titres(guide)
        self.assertEqual(len(en), len(fr))
        pareils = [t for t, u in zip(en, fr) if t == u]
        self.assertEqual(pareils, ["Sources"])
        # Les liens survivent à la traduction : mêmes adresses, même nombre.
        self.assertEqual(re.findall(r'href="([^"]+)"', guide),
                         re.findall(r'href="([^"]+)"', self._guide("en_US")))

    def test_aucun_terme_du_guide_sans_traduction(self):
        """Le contrôle par termes : un paragraphe oublié dans fr_CA.po resterait anglais."""
        en = re.sub(r"<[^>]+>", "\n", self._guide("en_US"))
        fr = self._guide("fr_CA").replace("&#39;", "'")
        restes = [ligne.strip() for ligne in en.split("\n")
                  if len(ligne.strip()) > 40 and ligne.strip() in fr]
        self.assertEqual(restes, [])

    def test_les_libelles_sont_traduits(self):
        env_fr = self.env(context={"lang": "fr_CA"})
        genres = dict(env_fr["bf.credit.reminder"]._fields["kind"]._description_selection(env_fr))
        self.assertEqual(genres["report_equifax"], "Dossier de crédit Equifax")
        self.assertEqual(genres["freeze_back"], "Remettre le gel de sécurité")
        menu = self.env.ref("bf_credit_identity.menu_credit_root").with_context(lang="fr_CA")
        self.assertEqual(menu.name, "Crédit et identité")
        type_act = self.env.ref("bf_credit_identity.mail_activity_type_credit").with_context(lang="fr_CA")
        self.assertEqual(type_act.name, "Crédit et identité")

    def test_le_guide_se_lit_sans_droits_particuliers(self):
        self.assertIn("Security freeze", self._guide("en_US", user=self.bruno))
