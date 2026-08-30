"""Le registre de consommation, du côté des passes sans personne au clavier.

Ce que ces tests protègent n'est pas l'arithmétique des jetons — elle est déjà
couverte par le calcul de `net_tokens` — mais les trois choses qui rendraient le
registre faux sans lever la moindre erreur : un fil par enregistrement (sinon le
rattachement au projet devient impossible), le filtre des clés que le pont rend
en trop (sinon une passe qui a bien travaillé disparaît de l'addition), et le
fait de ne jamais bloquer l'appelant.
"""

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRegistrePasses(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Message = self.env["claude.chat.message"]
        self.Session = self.env["claude.chat.session"]

    # ── Le fil ────────────────────────────────────────────────────────
    def test_une_passe_ouvre_un_fil_portant_l_enregistrement(self):
        ligne_id = self.Message.journaliser_passe(
            "refine_meeting", {"input_tokens": 900, "output_tokens": 100},
            resume="Raffinage", res_model="meeting.record", res_id=341)
        self.assertTrue(ligne_id)
        ligne = self.Message.browse(ligne_id)
        self.assertEqual(ligne.session_id.origin, "refine_meeting")
        self.assertEqual(ligne.session_id.res_model, "meeting.record")
        self.assertEqual(ligne.session_id.res_id, 341)
        # Le modèle est recopié sur la ligne : c'est lui que le cockpit groupe.
        self.assertEqual(ligne.res_model, "meeting.record")

    def test_deux_passes_sur_le_meme_enregistrement_partagent_le_fil(self):
        for _i in range(2):
            self.Message.journaliser_passe(
                "refine_meeting", {"output_tokens": 10},
                res_model="meeting.record", res_id=341)
        self.assertEqual(
            self.Session.search_count([("origin", "=", "refine_meeting"),
                                       ("res_id", "=", 341)]), 1)

    def test_deux_enregistrements_ne_partagent_pas_le_fil(self):
        """Le piège que ce test garde fermé.

        Un fil unique par fonction coûterait le même nombre de lignes et
        semblerait plus propre, mais il perdrait `res_id` — donc tout espoir de
        dire plus tard quel mandat a payé quelle passe.
        """
        for enr in (341, 342):
            self.Message.journaliser_passe(
                "refine_meeting", {"output_tokens": 10},
                res_model="meeting.record", res_id=enr)
        self.assertEqual(
            self.Session.search_count([("origin", "=", "refine_meeting")]), 2)

    def test_une_passe_sans_enregistrement_retombe_sur_un_fil_unique(self):
        for _i in range(3):
            self.Message.journaliser_passe("title", {"output_tokens": 5})
        self.assertEqual(
            self.Session.search_count([("origin", "=", "title")]), 1)

    # ── Ce que le pont rend en trop ───────────────────────────────────
    def test_les_cles_sans_colonne_sont_filtrees(self):
        """`num_turns` n'a pas de colonne et les deux totaux sont calculés.

        Laisser passer l'une des trois ferait échouer la création, donc perdre
        la mesure d'une passe qui, elle, a bien tourné.
        """
        ligne_id = self.Message.journaliser_passe(
            "carto",
            {"input_tokens": 9000, "output_tokens": 1200,
             "cache_read_tokens": 40000, "cache_write_tokens": 300,
             "net_tokens": 999, "total_tokens": 999, "num_turns": 7,
             "cost_usd": 0.42, "duration_ms": 8100},
            res_model="bf.process", res_id=12)
        ligne = self.Message.browse(ligne_id)
        # Les totaux viennent du calcul, jamais de ce que le pont a envoyé.
        self.assertEqual(ligne.net_tokens, 9000 + 1200 + 300)
        self.assertEqual(ligne.total_tokens, 9000 + 1200 + 300 + 40000)
        self.assertAlmostEqual(ligne.cost_usd, 0.42, places=4)

    # ── Ne jamais bloquer l'appelant ──────────────────────────────────
    def test_une_provenance_inconnue_se_range_sous_autre(self):
        """Le pont peut nommer une fonction que ce module ne connaît pas encore.

        ⚠️ Le piège que ce test a levé le 2026-08-30 : laisser passer la valeur
        en comptant sur le `try` ne marche PAS. Odoo valide un sélecteur au
        flush, donc l'erreur tombe après la sortie du bloc protégé, dans la
        transaction de l'appelant. Il faut normaliser avant d'écrire.

        Et on range plutôt que de refuser : une passe a bien dépensé, la perdre
        fausserait l'addition dans le sens qui rassure.
        """
        ligne_id = self.Message.journaliser_passe(
            "fonction_inventee", {"output_tokens": 7})
        self.assertTrue(ligne_id)
        fil = self.Message.browse(ligne_id).session_id
        self.assertEqual(fil.origin, "autre")
        # Le nom garde ce que le pont a dit : sans ça, personne ne saurait
        # quelle fonction ajouter au sélecteur.
        self.assertIn("fonction_inventee", fil.name)
        self.Message.flush_model()

    def test_la_ligne_est_interne(self):
        """Une passe automatique n'est pas une prise de parole.

        Sans ce drapeau, le raffinage d'un compte rendu apparaîtrait dans le
        panneau de clavardage comme un message de l'assistant.
        """
        ligne = self.Message.browse(
            self.Message.journaliser_passe("editorial", {"output_tokens": 1}))
        self.assertTrue(ligne.internal)
        self.assertEqual(ligne.role, "assistant")
        self.assertEqual(ligne.state, "done")
