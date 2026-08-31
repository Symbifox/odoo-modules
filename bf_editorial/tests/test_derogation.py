# -*- coding: utf-8 -*-
"""Une dérogation doit être une décision, pas une case qui rend la garde
décorative.

Ce qu'on vérifie ici, dans l'ordre où ça compte : elle débloque vraiment, elle
ne débloque QUE ce qu'elle nomme, elle tombe toute seule dès que le texte
bouge, elle demande une raison écrite, et elle n'est pas à la portée du groupe
Rédaction.
"""

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDerogation(TransactionCase):

    def setUp(self):
        super().setUp()
        self.calendrier = self.env["bf.editorial.calendar"].create({
            "name": "Dérogation",
            "require_all_langs": "no",
            "word_floor": 0,
        })
        self.entree = self.env["bf.editorial.entry"].create({
            "name": "Entrée sous constats",
            "calendar_id": self.calendrier.id,
            "stage_id": self.env.ref("bf_editorial.stage_draft").id,
            "qa_state": "findings",
            "qa_findings": "[fr_CA] 2 tiret(s) cadratin.",
        })
        self.entree.checklist_ids.unlink()
        self.entree.invalidate_recordset()

    def _signer(self, raison="Les cadratins sont dans une citation."):
        fenetre = self.env["bf.editorial.waiver"].create({
            "entry_id": self.entree.id,
            "problems": "\n".join(self.entree._waivable_problems()),
            "reason": raison,
        })
        fenetre.action_sign()
        self.entree.invalidate_recordset()

    # --- elle débloque ----------------------------------------------------
    def test_les_constats_bloquent_avant_signature(self):
        self.assertFalse(self.entree.preflight_ok)
        self.assertTrue(
            any("QA" in p for p in self.entree._preflight_problems())
        )

    def test_la_signature_debloque(self):
        self._signer()
        self.assertTrue(self.entree.qa_waived)
        self.assertTrue(self.entree.preflight_ok)
        self.assertFalse(self.entree._preflight_problems())

    def test_le_constat_reste_lisible(self):
        """Une dérogation ne blanchit rien : la QA continue de dire « constats »
        et le texte des constats reste en place. C'est le refus qui cède."""
        self._signer()
        self.assertEqual(self.entree.qa_state, "findings")
        self.assertIn("cadratin", self.entree.qa_findings)
        self.assertIn("dérogation", self.entree.preflight_summary.lower())

    def test_le_motif_signe_est_conserve(self):
        self._signer(raison="Citation d'un texte de loi.")
        self.assertIn("Citation", self.entree.qa_waiver_reason)
        self.assertEqual(self.entree.qa_waived_by, self.env.user)
        self.assertTrue(self.entree.qa_waived_on)

    # --- elle ne déborde pas ---------------------------------------------
    def test_elle_ne_couvre_pas_un_motif_de_fait(self):
        """Un reste bloquant n'est pas un jugement : il se règle, il ne se
        signe pas. La signature ne doit pas l'emporter avec le reste."""
        self._signer()
        self.env["bf.editorial.checklist"].create({
            "entry_id": self.entree.id,
            "name": "Visuels à produire",
            "is_blocking": True,
        })
        self.entree.invalidate_recordset()
        self.assertFalse(self.entree.preflight_ok)
        self.assertTrue(
            any("reste" in p for p in self.entree._preflight_problems())
        )

    def test_rien_a_signer_leve_une_erreur(self):
        self.entree.qa_state = "clean"
        self.entree.invalidate_recordset()
        with self.assertRaises(UserError):
            self.entree.action_open_waiver()

    def test_le_plancher_de_mots_se_signe_avec_ses_chiffres(self):
        """Le motif du plancher porte les nombres. Un article qui raccourcit
        encore change de motif, donc sort de la signature tout seul."""
        self.calendrier.word_floor = 1000
        version = self.env["bf.editorial.version"].create({
            "entry_id": self.entree.id,
            "lang_id": self.env["res.lang"].search(
                [("active", "=", True)], limit=1
            ).id,
            "is_source": True,
            "word_count": 900,
        })
        self.entree.invalidate_recordset()
        self.assertIn(
            "Plancher de mots non atteint : 900 contre 1000.",
            self.entree._preflight_problems(),
        )
        self._signer()
        self.assertTrue(self.entree.preflight_ok)

        version.word_count = 800
        self.entree.invalidate_recordset()
        self.assertFalse(
            self.entree.preflight_ok,
            "un article encore plus court n'est plus l'article signé",
        )

    # --- elle tombe quand le texte bouge ---------------------------------
    def test_des_constats_neufs_perime_la_derogation(self):
        self._signer()
        self.entree.write({"qa_findings": "[fr_CA] Une image sans alt."})
        self.entree.invalidate_recordset()
        self.assertTrue(self.entree.qa_waiver_stale)
        self.assertFalse(self.entree.preflight_ok)

    def test_des_constats_identiques_ne_la_perime_pas(self):
        """Repasser la QA sans rien changer ne doit pas coûter une signature."""
        self._signer()
        self.entree.write({"qa_findings": "[fr_CA] 2 tiret(s) cadratin."})
        self.entree.invalidate_recordset()
        self.assertFalse(self.entree.qa_waiver_stale)
        self.assertTrue(self.entree.preflight_ok)

    def test_la_levee_rend_la_garde(self):
        self._signer()
        self.entree.action_revoke_waiver()
        self.entree.invalidate_recordset()
        self.assertFalse(self.entree.qa_waived)
        self.assertFalse(self.entree.preflight_ok)

    # --- elle demande une raison, et le bon groupe ------------------------
    def test_une_raison_vide_est_refusee(self):
        fenetre = self.env["bf.editorial.waiver"].create({
            "entry_id": self.entree.id, "reason": "   ",
        })
        with self.assertRaises(UserError):
            fenetre.action_sign()
        self.entree.invalidate_recordset()
        self.assertFalse(self.entree.qa_waived)

    def test_la_redaction_ne_signe_pas(self):
        """Signer une dérogation, c'est publier d'avance. Le groupe qui ne
        peut pas publier ne doit pas pouvoir signer."""
        redaction = self.env["res.users"].create({
            "name": "Rédactrice", "login": "qa_derogation_redaction",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_editorial.group_editorial_user").id,
            ])],
        })
        # ⚠️ Contrôler le TYPE d'erreur ne suffit pas : l'ACL du modèle de
        # fenêtre refuse elle aussi, et par une AccessError. Contrôler le nom
        # du groupe ne suffit pas non plus : le message d'Odoo NOMME les
        # groupes autorisés, donc « Direction éditoriale » s'y trouve déjà.
        # Les deux versions restaient vertes avec la garde retirée — vérifié
        # par mutation. Seule une phrase propre à la garde discrimine.
        with self.assertRaises(AccessError) as pris:
            self.entree.with_user(redaction).action_open_waiver()
        self.assertIn("publication d'avance", str(pris.exception))
        with self.assertRaises(AccessError) as pris:
            self.entree.with_user(redaction).action_revoke_waiver()
        self.assertIn("Lever une dérogation", str(pris.exception))

    # --- et la publication le dit ----------------------------------------
    def test_le_chatter_distingue_une_publication_sous_derogation(self):
        self._signer()
        self.entree._do_publish()
        corps = self.entree.message_ids.mapped("body")
        self.assertTrue(
            any("DÉROGATION" in (c or "") for c in corps),
            "le chatter doit distinguer une publication sous dérogation",
        )
