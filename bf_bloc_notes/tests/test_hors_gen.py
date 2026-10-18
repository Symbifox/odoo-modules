"""« Hors Gen » : la case, sa bascule et le contrat mobile.

Les passes de Gen qui la respectent sont éprouvées dans leur propre module. Ici : la
case elle-même, qui peut la basculer, les deux portes du téléphone (page ``/notes`` et API
à jeton) et ce que montrent les vues.

🔴 Chaque refus a son TÉMOIN : le même geste, permis, aboutit.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged

BASE = "/bf_bloc_notes/mobile/v1"


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestHorsGen(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = "base.group_user,project.group_project_user"
        cls.alice = new_test_user(cls.env, login="alice_26683", groups=groupes,
                                  email="alice.26683@example.com")
        cls.bruno = new_test_user(cls.env, login="bruno_26683", groups=groupes,
                                  email="bruno.26683@example.com")
        cls.Note = cls.env["bf.note"].with_user(cls.alice)

    def _creer_mobile(self, **vals):
        vals.setdefault("client_uuid", str(uuid.uuid4()))
        vals.setdefault("text", "Idée cadeau")
        resultat = self.Note._mobile_create(vals)
        return self.Note.browse(resultat["note"]["id"]), resultat["note"]

    def test_decochee_par_defaut_et_les_boutons_font_l_aller_retour(self):
        note = self.Note.create({"body": "<p>x</p>"})
        self.assertFalse(note.gen_excluded)
        note.action_gen_exclude()
        self.assertTrue(note.gen_excluded)
        note.action_gen_include()
        self.assertFalse(note.gen_excluded)

    def test_les_boutons_posent_la_valeur_sans_l_inverser(self):
        """Tour 1, constat 5 : un écran périmé (la note cochée ailleurs entre-temps) clique
        « exclure » : la note reste cochée. Même chose dans l'autre sens."""
        note = self.Note.create({"body": "<p>x</p>"})
        note.action_gen_exclude()
        note.action_gen_exclude()
        self.assertTrue(note.gen_excluded, "exclure deux fois : toujours exclue")
        note.action_gen_include()
        note.action_gen_include()
        self.assertFalse(note.gen_excluded, "rendre deux fois : toujours rendue")
        # Sur plusieurs notes d'états différents, chaque bouton aligne tout le monde.
        autre = self.Note.create({"body": "<p>y</p>", "gen_excluded": True})
        (note | autre).action_gen_exclude()
        self.assertEqual((note.gen_excluded, autre.gen_excluded), (True, True))
        (note | autre).action_gen_include()
        self.assertEqual((note.gen_excluded, autre.gen_excluded), (False, False))
        self.assertFalse(hasattr(type(note), "action_toggle_gen_excluded"),
                         "plus aucune bascule qui inverse")

    def test_seul_l_auteur_la_bascule(self):
        """Une note PARTAGÉE se lit par un collègue, mais il ne la coche pas."""
        note = self.Note.create({"body": "<p>x</p>", "is_shared": True})
        de_bruno = note.with_user(self.bruno)
        self.assertEqual(de_bruno.read(["gen_excluded"])[0]["gen_excluded"], False)
        with self.assertRaises(AccessError):
            de_bruno.action_gen_exclude()
        with self.assertRaises(AccessError):
            de_bruno._mobile_action("gen_exclude")
        note.invalidate_recordset()
        self.assertFalse(note.gen_excluded)
        # Témoin : l'autrice, elle, la coche.
        note._mobile_action("gen_exclude")
        self.assertTrue(note.gen_excluded)
        # Ni ne la décoche.
        with self.assertRaises(AccessError):
            de_bruno.action_gen_include()
        note.invalidate_recordset()
        self.assertTrue(note.gen_excluded)

    def test_independante_de_l_epingle_et_du_partage(self):
        note = self.Note.create({"body": "<p>x</p>", "pinned": True, "is_shared": True})
        note.action_gen_exclude()
        self.assertEqual((note.pinned, note.is_shared, note.gen_excluded), (True, True, True))
        note.action_toggle_pinned()
        note.is_shared = False
        self.assertEqual((note.pinned, note.is_shared, note.gen_excluded), (False, False, True))

    def test_la_charge_mobile_porte_la_case(self):
        note, charge = self._creer_mobile()
        self.assertIs(charge["gen_excluded"], False)
        note.gen_excluded = True
        self.assertIs(note._mobile_payload()["gen_excluded"], True)

    def test_creer_cochee_depuis_le_telephone(self):
        note, charge = self._creer_mobile(gen_excluded=True)
        self.assertTrue(note.gen_excluded)
        self.assertIs(charge["gen_excluded"], True)
        # Témoin : sans la clé, décochée.
        autre, _charge = self._creer_mobile()
        self.assertFalse(autre.gen_excluded)

    def test_modifier_coche_et_decoche(self):
        note, _charge = self._creer_mobile()
        self.assertIs(note._mobile_update({"gen_excluded": True})["note"]["gen_excluded"], True)
        self.assertIs(note._mobile_update({"gen_excluded": False})["note"]["gen_excluded"], False)

    def test_seul_un_vrai_booleen_json_est_accepte(self):
        """Tour 1, constat 6 : ``bool("false")`` vaut ``True``. Une chaîne, un nombre ou ``null``
        est refusé, à la création comme à la modification, sans rien écrire. Témoin : les vrais
        booléens passent (essais ci-dessus)."""
        for valeur in ("false", "true", "", 0, 1, None):
            with self.assertRaises(UserError, msg=repr(valeur)):
                self._creer_mobile(gen_excluded=valeur)
        note, _charge = self._creer_mobile(gen_excluded=True)
        for valeur in ("false", 0, None):
            with self.assertRaises(UserError, msg=repr(valeur)):
                note._mobile_update({"gen_excluded": valeur})
        note.invalidate_recordset()
        self.assertTrue(note.gen_excluded, "rien d'écrit")
        self.assertIs(note._mobile_update({"gen_excluded": False})["note"]["gen_excluded"], False)

    def test_un_telephone_qui_ignore_la_case_ne_la_decoche_pas(self):
        """La 3.22.0 publiée n'envoie que ce qu'elle modifie : texte, titre, épingle."""
        note, _charge = self._creer_mobile(gen_excluded=True)
        note._mobile_update({"text": "Idée cadeau, version 2", "pinned": True})
        self.assertTrue(note.gen_excluded)
        self.assertTrue(note.pinned)

    def test_les_gestes_cochent_decochent_et_le_disent(self):
        note, _charge = self._creer_mobile()
        coche = note._mobile_action("gen_exclude")
        self.assertTrue(note.gen_excluded)
        self.assertIs(coche["note"]["gen_excluded"], True)
        decoche = note._mobile_action("gen_include")
        self.assertFalse(note.gen_excluded)
        self.assertIs(decoche["note"]["gen_excluded"], False)
        # Une phrase pour chaque sens, et pas la même (quelle que soit la langue de l'usager).
        self.assertTrue(coche["message"] and decoche["message"])
        self.assertNotEqual(coche["message"], decoche["message"])

    def test_les_vues_montrent_la_case(self):
        vues = self.Note.get_views([(False, "kanban"), (False, "form"), (False, "list"),
                                    (False, "search")])["views"]
        kanban = vues["kanban"]["arch"]
        self.assertNotIn("action_toggle_gen_excluded", kanban)
        self.assertIn("fa-eye-slash", kanban)
        # Deux boutons qui POSENT : le pâle exclut (case décochée), le plein rend à Gen.
        from lxml import etree
        arbre = etree.fromstring(kanban)
        exclure = arbre.xpath("//button[@name='action_gen_exclude']")
        rendre = arbre.xpath("//button[@name='action_gen_include']")
        self.assertEqual((len(exclure), len(rendre)), (1, 1))
        self.assertEqual(exclure[0].get("t-if"), "!record.gen_excluded.raw_value")
        self.assertIn("0.35", exclure[0].get("t-attf-style"))
        self.assertIsNotNone(rendre[0].get("t-else"))
        self.assertIs(rendre[0].getprevious(), exclure[0])
        # L'œil AVANT l'épingle (maquette retenue : « ⊘ 📌 »).
        self.assertLess(kanban.index("o_bf_note_gen_btn"), kanban.index("o_bf_note_pin_btn"))
        self.assertIn('name="gen_excluded"', vues["form"]["arch"])
        self.assertIn('name="gen_excluded"', vues["list"]["arch"])
        self.assertIn("('gen_excluded', '=', True)", vues["search"]["arch"])


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestHorsGenHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="alice_http_26683",
                                  password="alice_http_26683",
                                  groups="base.group_user,project.group_project_user",
                                  email="alice.http.26683@example.com")

    def _rpc(self, route, **params):
        reponse = self.url_open(route, data=json.dumps({
            "jsonrpc": "2.0", "method": "call", "params": params,
        }), headers={"Content-Type": "application/json"})
        self.assertEqual(reponse.status_code, 200)
        return reponse.json()["result"]

    def _post(self, route, charge):
        return self.url_open(route, data=json.dumps(charge),
                             headers={"Content-Type": "application/json"})

    def test_le_ping_annonce_la_case_et_ses_gestes(self):
        """Symbifox Mobile ne montre la case que si le serveur l'annonce ici."""
        charge = self.url_open(f"{BASE}/ping").json()
        self.assertIn("gen_excluded", charge["features"])
        self.assertIn("gen_exclude", charge["actions"])
        self.assertIn("gen_include", charge["actions"])
        # Témoin : les capacités d'avant restent.
        self.assertIn("color_hex", charge["features"])

    def test_api_a_jeton_cree_cochee_puis_decoche(self):
        with patch("odoo.addons.bf_bloc_notes.controllers.mobile_api._device",
                   lambda: SimpleNamespace(user_id=self.alice, _fields={})):
            cree = self._post(f"{BASE}/notes", {"client_uuid": str(uuid.uuid4()), "text": "x",
                                                "gen_excluded": True})
            self.assertEqual(cree.status_code, 201)
            note = cree.json()["note"]
            self.assertIs(note["gen_excluded"], True)
            geste = self._post(f"{BASE}/notes/{note['id']}/action", {"action": "gen_include"})
        self.assertEqual(geste.status_code, 200)
        self.assertIs(geste.json()["note"]["gen_excluded"], False)
        self.assertTrue(geste.json()["message"])

    def test_api_a_jeton_refuse_une_chaine(self):
        """Une chaîne « false » rend un 400, et la note n'est pas créée cochée."""
        with patch("odoo.addons.bf_bloc_notes.controllers.mobile_api._device",
                   lambda: SimpleNamespace(user_id=self.alice, _fields={})):
            refus = self._post(f"{BASE}/notes", {"client_uuid": str(uuid.uuid4()), "text": "x",
                                                 "gen_excluded": "false"})
            self.assertEqual(refus.status_code, 400)
            self.assertEqual(refus.json()["error"], "bad_request")
            # Témoin : le vrai booléen passe.
            cree = self._post(f"{BASE}/notes", {"client_uuid": str(uuid.uuid4()), "text": "x",
                                                "gen_excluded": False})
        self.assertEqual(cree.status_code, 201)
        self.assertIs(cree.json()["note"]["gen_excluded"], False)

    def test_la_page_notes_coche_par_son_geste_et_traduit_ses_mots(self):
        self.authenticate("alice_http_26683", "alice_http_26683")
        texte = self.url_open("/notes").text
        debut = texte.index('id="i18n">') + len('id="i18n">')
        mots = json.loads(texte[debut:texte.index("</script>", debut)])
        for cle in ("gen_exclude", "gen_include", "gen_excluded_hint"):
            self.assertTrue(mots.get(cle), cle)
        cree = self._rpc("/notes/api/creer", client_uuid=str(uuid.uuid4()), text="x")
        resultat = self._rpc("/notes/api/geste", id=cree["note"]["id"], action="gen_exclude")
        self.assertIs(resultat["note"]["gen_excluded"], True)
        note = self.env["bf.note"].browse(cree["note"]["id"])
        self.assertTrue(note.gen_excluded)
