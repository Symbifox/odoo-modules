"""La recherche voit le corps ENTIER, et comprend des opérateurs.

Avant, quatre champs seulement : objet, expéditeur, destinataire et
`body_preview`, coupé à 300 caractères. Mesuré sur une base réelle le
2026-09-13 : **11,2 % du texte reçu était indexé**, 92,5 % des corps
dépassaient la coupe.

⚠️ Le contrôle qui tranche est `test_un_mot_du_deuxieme_paragraphe_se_trouve` :
tant que la recherche lisait l'aperçu, il échouait, et aucun autre ne le
voyait.
"""
from odoo.tests import tagged

from .common import MobileApiCase

LONG = ("Bonjour, " + ("blabla de remplissage. " * 40)
        + " Le mot rarissime est ornithorynque. " + ("suite. " * 20))


@tagged("post_install", "-at_install")
class TestRecherchePleinTexte(MobileApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.long = cls.env["bf.email"].with_user(cls.owner).create({
            "subject": "Rapport trimestriel",
            "email_from": "analyste@acme.test",
            "email_to": "owner@test.invalid",
            "direction": "in", "status": "new", "source": "imap",
            "account_id": cls.account.id, "user_id": cls.owner.id,
            "imap_in_inbox": True, "imap_folder": "INBOX", "imap_uid": "990",
            "message_id_header": "<long@test.invalid>",
            "date": "2026-08-05 15:00:00",
            "body_html": "<p>%s</p>" % LONG,
        })

    def _chercher(self, terme, folder="all"):
        data = self.env["bf.email"].with_user(self.owner).inbox_get_messages(
            folder=folder, search=terme, limit=100)
        return {m["id"] for m in data["messages"]}

    # -- le corps entier -------------------------------------------------
    def test_le_texte_entier_est_extrait(self):
        self.assertIn("ornithorynque", self.long.body_text)
        self.assertNotIn("ornithorynque", self.long.body_preview or "",
                         "l'aperçu s'arrête bien avant")

    def test_un_mot_du_deuxieme_paragraphe_se_trouve(self):
        self.assertIn(self.long.id, self._chercher("ornithorynque"))

    def test_les_balises_ne_sont_pas_du_texte(self):
        """Chercher dans `body_html` aurait rendu des faux positifs."""
        rec = self.env["bf.email"].with_user(self.owner).create({
            "subject": "Avec du style", "email_from": "x@acme.test",
            "email_to": "owner@test.invalid", "direction": "in",
            "status": "new", "source": "imap", "user_id": self.owner.id,
            "message_id_header": "<style@test.invalid>",
            "date": "2026-08-05 15:00:00",
            "body_html": '<p style="font-family: Helvetica">Bonjour</p>',
        })
        self.assertNotIn("Helvetica", rec.body_text or "")
        self.assertNotIn(rec.id, self._chercher("Helvetica"))

    def test_le_script_et_le_style_sont_jetes(self):
        rec = self.env["bf.email"].with_user(self.owner).create({
            "subject": "Avec un script", "email_from": "x@acme.test",
            "email_to": "owner@test.invalid", "direction": "in",
            "status": "new", "source": "imap", "user_id": self.owner.id,
            "message_id_header": "<script@test.invalid>",
            "date": "2026-08-05 15:00:00",
            "body_html": "<p>Visible</p><script>var secret = 1;</script>",
        })
        self.assertIn("Visible", rec.body_text)
        self.assertNotIn("secret", rec.body_text)

    # -- la grammaire ----------------------------------------------------
    def test_de_restreint_a_l_expediteur(self):
        trouves = self._chercher("de:analyste@acme.test")
        self.assertIn(self.long.id, trouves)
        self.assertNotIn(self.inbound.id, trouves)

    def test_objet_restreint_a_l_objet(self):
        trouves = self._chercher("objet:trimestriel")
        self.assertEqual(trouves, {self.long.id})

    def test_les_termes_s_additionnent(self):
        """Ajouter un mot rétrécit, il n'élargit pas."""
        self.assertEqual(
            self._chercher("de:analyste objet:trimestriel"), {self.long.id})
        self.assertEqual(
            self._chercher("de:analyste objet:introuvable"), set())

    def test_est_recu_et_est_envoye(self):
        recus = self._chercher("est:reçu")
        self.assertIn(self.inbound.id, recus)
        self.assertNotIn(self.outbound.id, recus)
        self.assertIn(self.outbound.id, self._chercher("est:envoyé"))

    def test_pj_oui(self):
        self.assertIn(self.with_attachment.id, self._chercher("pj:oui"))
        self.assertNotIn(self.inbound.id, self._chercher("pj:oui"))

    def test_avant_et_apres(self):
        self.assertIn(self.long.id, self._chercher("avant:2026-08-06"))
        self.assertNotIn(self.long.id, self._chercher("après:2026-08-06"))

    def test_une_phrase_entre_guillemets(self):
        self.assertIn(self.long.id, self._chercher('"mot rarissime est"'))
        self.assertEqual(self._chercher('"mot qui n\'y est pas"'), set())

    def test_une_cle_inconnue_redevient_un_mot(self):
        """⚠️ Refuser la ligne apprendrait à ne plus écrire de deux-points.

        Un objet qui contient « Re: suivi » doit rester trouvable tel quel.
        """
        rec = self.env["bf.email"].with_user(self.owner).create({
            "subject": "Truc:machin", "email_from": "y@acme.test",
            "email_to": "owner@test.invalid", "direction": "in",
            "status": "new", "source": "imap", "user_id": self.owner.id,
            "message_id_header": "<cleinconnue@test.invalid>",
            "date": "2026-08-05 15:00:00",
            "body_html": "<p>corps</p>",
        })
        self.assertIn(rec.id, self._chercher("truc:machin"))

    def test_une_recherche_vide_ne_filtre_rien(self):
        self.assertEqual(
            self.env["bf.email"].with_user(self.owner)._search_domain_from_query(""),
            [])

    def test_une_regle_lit_le_corps_entier(self):
        """Le moteur de règles profite du même champ."""
        regle = self.env["bf.email.rule"].with_user(self.owner).create({
            "name": "Ornithorynques",
            "user_id": self.owner.id,
            "condition_ids": [(0, 0, {
                "field_name": "body", "operator": "contains",
                "value": "ornithorynque",
            })],
            "set_category": "internal",
        })
        self.assertTrue(regle._match(self.long))
        self.assertFalse(regle._match(self.inbound))
