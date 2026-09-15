"""La personnalité de Gen et le plafond des consignes.

Ce que ces tests protègent :

* un texte de personnalité qui partirait au pont sans être borné, ou qui
  serait coupé en silence dans les Paramètres au lieu d'être refusé ;
* un bloc de consignes tranché au milieu d'une phrase : la coupe doit tomber
  sur une frontière de consigne, être comptée et être dite ;
* une jauge qui ne compterait pas le brouillon qu'on est en train d'écrire,
  ou qui compterait deux fois la consigne qu'on modifie.
"""

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.bf_claude_chat.controllers.main import _attach_identity
from odoo.addons.bf_claude_chat.models.claude_chat_instruction import (
    STEERING_MAX_CHARS,
)
from odoo.addons.bf_claude_chat.models.res_config_settings import (
    PERSONALITY_MAX_CHARS,
)

JOURNAL = "odoo.addons.bf_claude_chat.models.claude_chat_instruction"


@tagged("post_install", "-at_install")
class TestPersonnalite(TransactionCase):

    def setUp(self):
        super().setUp()
        self.ICP = self.env["ir.config_parameter"].sudo()
        self.ICP.set_param("bf_claude_chat.personality", "")

    def test_sans_personnalite_rien_ne_part(self):
        payload = {}
        _attach_identity(self.env, payload)
        self.assertNotIn("context", payload)
        self.ICP.set_param("bf_claude_chat.personality", "   ")
        _attach_identity(self.env, payload)
        self.assertNotIn("context", payload)

    def test_la_personnalite_part_dans_le_contexte(self):
        self.ICP.set_param("bf_claude_chat.personality",
                           "  Tu t'appelles Gen. Tu tutoies.  ")
        payload = {"context": {"model": "project.task"}}
        _attach_identity(self.env, payload)
        self.assertEqual(payload["context"]["identity"],
                         "Tu t'appelles Gen. Tu tutoies.")
        self.assertEqual(payload["context"]["model"], "project.task")

    def test_le_pont_ne_recoit_jamais_plus_que_le_plafond(self):
        self.ICP.set_param("bf_claude_chat.personality",
                           "x" * (PERSONALITY_MAX_CHARS + 500))
        payload = {}
        _attach_identity(self.env, payload)
        self.assertEqual(len(payload["context"]["identity"]),
                         PERSONALITY_MAX_CHARS)

    def test_les_parametres_refusent_un_texte_trop_long(self):
        # Le refus tombe à l'enregistrement de la fiche des paramètres, avant
        # set_values() : c'est le geste que fait le bouton Enregistrer.
        Settings = self.env["res.config.settings"]
        with self.assertRaises(ValidationError):
            Settings.create(
                {"claude_personality": "y" * (PERSONALITY_MAX_CHARS + 1)})
        juste = Settings.create(
            {"claude_personality": "y" * PERSONALITY_MAX_CHARS})
        self.assertEqual(len(juste.claude_personality), PERSONALITY_MAX_CHARS)
        # Des espaces autour ne comptent pas : c'est le texte dépouillé que le
        # contrôleur envoie.
        Settings.create(
            {"claude_personality": "  " + "y" * PERSONALITY_MAX_CHARS + "\n"})


@tagged("post_install", "-at_install")
class TestPlafondConsignes(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Instr = self.env["claude.chat.instruction"]
        # Les consignes déjà en base fausseraient les comptes.
        self.Instr.search([]).write({"active": False})

    def _seme(self, n, taille, seq0=10):
        """n consignes de `taille` caractères de corps, donc taille + 2 par ligne."""
        return self.Instr.create([{
            "name": f"Consigne {i}",
            "sequence": seq0 + i,
            "body": f"C{i:02d} " + "m" * (taille - 4),
        } for i in range(n)])

    def test_la_coupe_tombe_sur_une_frontiere_et_se_dit(self):
        # 15 lignes de 302 caractères plus 14 sauts = 4 544, au-dessus du plafond.
        # 13 lignes tiennent (3 938) ; la 14e porterait le bloc à 4 241.
        self._seme(15, 300)
        with self.assertLogs(JOURNAL, level="WARNING") as journal:
            bloc = self.Instr._build_prompt_block()
        self.assertLessEqual(len(bloc), STEERING_MAX_CHARS)
        lignes = bloc.split("\n")
        self.assertEqual(len(lignes), 13)
        self.assertTrue(all(len(ligne) == 302 for ligne in lignes))
        self.assertEqual(lignes[0][:5], "- C00")
        self.assertEqual(lignes[-1][:5], "- C12")
        self.assertIn("2 instruction(s) left out", journal.output[0])

    def test_sous_le_plafond_rien_ne_manque_et_rien_ne_se_dit(self):
        self._seme(3, 100)
        with self.assertNoLogs(JOURNAL, level="WARNING"):
            bloc = self.Instr._build_prompt_block()
        self.assertEqual(len(bloc.split("\n")), 3)

    def test_la_jauge_compte_le_brouillon(self):
        self._seme(13, 300)  # 3 938 caractères
        brouillon = self.Instr.new({
            "name": "Neuve", "body": "n" * 300, "sequence": 999,
            "scope": "global",
        })
        self.assertEqual(brouillon.block_chars, 3938 + 1 + 302)
        avert = brouillon._onchange_block_ceiling()
        self.assertTrue(avert and "warning" in avert)
        self.assertIn("1 instruction", avert["warning"]["message"])
        self.assertIn("would be left out", brouillon.block_usage)

    def test_sous_le_plafond_aucun_avertissement(self):
        self._seme(2, 100)
        brouillon = self.Instr.new({"name": "Neuve", "body": "n" * 100})
        self.assertIsNone(brouillon._onchange_block_ceiling())
        self.assertEqual(brouillon.block_usage,
                         f"{3 * 102 + 2} / {STEERING_MAX_CHARS} characters")

    def test_la_jauge_ne_compte_pas_deux_fois_la_consigne_modifiee(self):
        recs = self._seme(2, 100)
        premier = recs[0]
        self.assertEqual(premier.block_chars, 2 * 102 + 1)
        premier.body = "z" * 50
        self.assertEqual(premier.block_chars, 52 + 1 + 102)

    def test_la_consigne_privee_d_un_autre_ne_compte_pas(self):
        autre = self.env["res.users"].create(
            {"name": "Autre", "login": "autre@essai.invalid"})
        self.Instr.create(
            {"name": "Sienne", "body": "p" * 100, "user_id": autre.id})
        mienne = self.Instr.create({"name": "Mienne", "body": "q" * 100})
        self.assertEqual(mienne.block_chars, 102)

    def test_une_consigne_par_modele_compte_avec_les_globales_seulement(self):
        self._seme(2, 100)
        modele = self.env["ir.model"]._get("project.task")
        tache = self.Instr.create({
            "name": "Sur les tâches", "body": "t" * 100,
            "scope": "model", "model_id": modele.id,
        })
        autre_modele = self.env["ir.model"]._get("res.partner")
        self.Instr.create({
            "name": "Sur les contacts", "body": "c" * 100,
            "scope": "model", "model_id": autre_modele.id,
        })
        # deux globales + la sienne, pas celle des contacts
        self.assertEqual(tache.block_chars, 3 * 102 + 2)

    def test_le_rapport_de_coherence_porte_la_jauge(self):
        self._seme(2, 100)
        action = self.Instr.search([]).action_check_coherence()
        rapport = self.env["claude.chat.coherence.report"].browse(
            action["res_id"])
        self.assertIn(f"{2 * 102 + 1} / {STEERING_MAX_CHARS}", rapport.body)
