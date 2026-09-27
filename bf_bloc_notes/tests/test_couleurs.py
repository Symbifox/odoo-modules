"""Couleurs libres et préférences d'affichage du bloc-notes."""

import uuid

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestNoteCouleurs(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = "base.group_user,project.group_project_user"
        cls.alice = new_test_user(cls.env, login="alice_couleurs", groups=groupes)
        cls.bruno = new_test_user(cls.env, login="bruno_couleurs", groups=groupes)
        cls.Note = cls.env["bf.note"].with_user(cls.alice)

    def _creer(self, **vals):
        vals = dict({"client_uuid": str(uuid.uuid4()), "text": "Une note"}, **vals)
        resultat = self.Note._mobile_create(vals)
        return self.Note.browse(resultat["note"]["id"]), resultat["note"]

    def test_un_ancien_index_se_lit_en_pastel_sans_rien_reecrire(self):
        note, payload = self._creer(color=3)
        self.assertEqual(note.color, 3)
        self.assertFalse(note.color_hex)
        self.assertEqual(payload["color"], 3)
        self.assertEqual(payload["color_hex"], "#FCE89A")
        self.assertEqual(payload["text_color"], "#000000")
        self.assertEqual(payload["color_source"], "record")

    def test_sans_couleur(self):
        _note, payload = self._creer()
        self.assertEqual(payload["color"], 0)
        self.assertIsNone(payload["color_hex"])
        self.assertIsNone(payload["text_color"])

    def test_couleur_libre_a_la_creation_et_index_le_plus_proche(self):
        note, payload = self._creer(color_hex="#f0a0a0", color=7)
        self.assertEqual(note.color_hex, "#F0A0A0")
        self.assertEqual(payload["color_hex"], "#F0A0A0")
        # L'index suit la couleur libre (rouge pastel), pas le `color` envoyé en même temps.
        self.assertEqual(payload["color"], 1)

    def test_modifier_la_couleur_libre_puis_l_effacer(self):
        note, payload = self._creer(color=2)
        res = note.with_user(self.alice)._mobile_update(
            {"color_hex": "#1f2a44", "write_date": payload["write_date"]})
        self.assertFalse(res["conflict"])
        self.assertEqual(res["note"]["color_hex"], "#1F2A44")
        self.assertEqual(res["note"]["text_color"], "#FFFFFF")
        res = note.with_user(self.alice)._mobile_update({"color_hex": ""})
        self.assertIsNone(res["note"]["color_hex"])
        self.assertEqual(res["note"]["color"], 0)

    def test_une_ancienne_application_qui_envoie_un_index_efface_la_couleur_libre(self):
        note, _payload = self._creer(color_hex="#123456")
        res = note.with_user(self.alice)._mobile_update({"color": 4})
        self.assertEqual(res["note"]["color"], 4)
        self.assertEqual(res["note"]["color_hex"], "#B6D7F2")

    def test_couleur_invalide_refusee(self):
        with self.assertRaises(UserError):
            self._creer(color_hex="rouge")
        with self.assertRaises(UserError):
            self._creer(color_hex=12)

    def test_etiquettes_envoyees_avec_leur_couleur(self):
        tag = self.env["bf.note.tag"].create({"name": "Clinique essai", "color": 1})
        note, _payload = self._creer()
        note.sudo().tag_ids = tag
        payload = note._mobile_payload()
        self.assertEqual(payload["tags"], [{
            "id": tag.id, "name": "Clinique essai",
            "color_hex": "#EE2D2D", "text_color": "#000000",
        }])

    def test_ma_couleur_d_etiquette_ne_se_voit_que_chez_moi(self):
        tag = self.env["bf.note.tag"].create({"name": "Perso essai", "color": 1})
        tag.with_user(self.alice).bf_color_set_mine("#00AA55")
        note_a, _p = self._creer()
        note_a.sudo().tag_ids = tag
        self.assertEqual(note_a._mobile_payload()["tags"][0]["color_hex"], "#00AA55")
        note_b = self.env["bf.note"].with_user(self.bruno)._mobile_create(
            {"client_uuid": str(uuid.uuid4()), "text": "B"})["note"]
        note_b = self.env["bf.note"].with_user(self.bruno).browse(note_b["id"])
        note_b.sudo().tag_ids = tag
        self.assertEqual(note_b._mobile_payload()["tags"][0]["color_hex"], "#EE2D2D")

    def test_preferences_par_defaut(self):
        prefs = self.Note._mobile_prefs()
        self.assertEqual(prefs["layout"], "cards")
        self.assertEqual(prefs["density"], "comfortable")
        self.assertEqual(prefs["color_style"], "fill")
        self.assertIs(prefs["group_by_color"], False)
        self.assertEqual(len(prefs["palette"]), 12)
        self.assertEqual(prefs["palette"][3], "#FCE89A")
        self.assertFalse([sw for sw in prefs["swatches"] if not sw["shared"]])

    def test_preferences_ecrites_par_l_usager_pour_lui_seul(self):
        prefs = self.Note._mobile_set_prefs({"layout": "minimal", "group_by_color": 1,
                                             "color_style": "stripe"})
        self.assertEqual(prefs["layout"], "minimal")
        self.assertIs(prefs["group_by_color"], True)
        self.assertEqual(self.alice.bf_note_layout, "minimal")
        self.assertEqual(self.bruno.bf_note_layout, "cards")

    def test_preference_hors_liste_refusee(self):
        with self.assertRaises(UserError):
            self.Note._mobile_set_prefs({"layout": "carrousel"})
        self.assertEqual(self.alice.bf_note_layout, "cards")

    def test_nuanciers_dans_les_preferences(self):
        self.env["bf.color.swatch"].with_user(self.alice).bf_add_to_mine("#abcdef")
        prefs = self.Note._mobile_prefs()
        # Les siens d'abord, puis ceux du locataire.
        self.assertFalse(prefs["swatches"][0]["shared"])
        self.assertEqual(prefs["swatches"][0]["colors"], ["#ABCDEF"])
        chez_bruno = self.env["bf.note"].with_user(self.bruno)._mobile_prefs()["swatches"]
        self.assertFalse([sw for sw in chez_bruno if not sw["shared"]])
