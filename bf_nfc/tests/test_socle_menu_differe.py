"""Le socle 18.0.2.0.0 : questions, menus, envoi différé, étiquette.

Ce que ces essais gardent surtout : une question n'écrit jamais rien, un envoi
rejoué ne refait jamais le geste, et une heure venue du téléphone est bornée.
"""
import json
import re
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.bf_nfc.models.bf_nfc_gesture import BfNfcGesture


@tagged("post_install", "-at_install")
class TestQuestionsEtMenus(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        cls.redacteur = new_test_user(cls.env, login="socle-redacteur",
                                      groups="base.group_user,base.group_partner_manager")
        cls.partenaire = cls.env["res.partner"].create({"name": "Immeuble du socle"})
        cls.menu = cls.env["bf.nfc.tag"].create({
            "name": "Porte du socle",
            "gesture_id": cls.env.ref("bf_nfc.gesture_menu").id,
            "res_model": "res.partner", "res_id": cls.partenaire.id,
        })
        cls.ligne_passage = cls.env["bf.nfc.tag.choice"].create({
            "tag_id": cls.menu.id, "name": "J'arrive", "sequence": 1, "style": "principal",
            "gesture_id": cls.env.ref("bf_nfc.gesture_note").id,
            "params": '{"texte": "Arrivée"}',
        })
        cls.ligne_cron = cls.env["bf.nfc.tag.choice"].create({
            "tag_id": cls.menu.id, "name": "Relancer la tâche", "sequence": 2,
            "gesture_id": cls.env.ref("bf_nfc.gesture_cron").id,
        })

    def _notes(self):
        return self.env["mail.message"].search_count(
            [("model", "=", "res.partner"), ("res_id", "=", self.partenaire.id)])

    def test_sans_choix_le_menu_rend_ses_boutons_et_n_ecrit_rien(self):
        avant_notes, avant_taps = self._notes(), self.menu.tap_count
        r = self.menu.with_user(self.redacteur).taper("app")
        self.assertEqual(r["statut"], "choice")
        self.assertEqual([c["libelle"] for c in r["choix"]], ["J'arrive"],
                         "Un choix réservé à la gestion ne s'affiche pas à un interne.")
        self.assertEqual(self._notes(), avant_notes)
        self.assertFalse(self.env["bf.nfc.tap"].search([("tag_id", "=", self.menu.id)]),
                         "Une question ne laisse pas de ligne de journal.")
        self.assertEqual(self.menu.tap_count, avant_taps)

    def test_le_choix_joue_le_geste_de_sa_ligne(self):
        avant = self._notes()
        r = self.menu.with_user(self.redacteur).taper("app", choix=str(self.ligne_passage.id))
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self._notes(), avant + 1)
        tap = self.env["bf.nfc.tap"].browse(r["tap_id"])
        self.assertEqual(tap.choice_key, str(self.ligne_passage.id))

    def test_forcer_la_cle_d_un_choix_reserve_est_refuse(self):
        """🔴 Un menu ne doit pas servir à contourner la garde de son geste."""
        r = self.menu.with_user(self.redacteur).taper("app", choix=str(self.ligne_cron.id))
        self.assertEqual(r["statut"], "refused")
        self.assertIn("réservé à la gestion", r["message"])

    def test_un_choix_disparu_est_refuse_proprement(self):
        r = self.menu.with_user(self.redacteur).taper("app", choix="999999")
        self.assertEqual(r["statut"], "refused")
        self.assertIn("n'existe plus", r["message"])

    def test_une_question_defait_ce_que_le_geste_a_touche(self):
        """🔴 Le contrat dit « demander avant d'agir ». Le socle garantit quand
        même qu'un geste qui aurait écrit AVANT de poser sa question ne laisse
        rien : c'est ce qui rend la question sûre même mal écrite."""
        tag = self.env["bf.nfc.tag"].create({
            "name": "Geste bavard", "gesture_id": self.env.ref("bf_nfc.gesture_note").id,
            "res_model": "res.partner", "res_id": self.partenaire.id,
        })

        def ecrit_puis_demande(geste, tag, tap, params):
            tag._cible().sudo().ref = "ECRIT-AVANT"
            return {"titre": "?", "message": "Vraiment ?",
                    "choix": [{"cle": "oui", "libelle": "Oui", "style": "principal", "saisie": None}]}

        with patch.object(BfNfcGesture, "_executer_note", ecrit_puis_demande):
            r = tag.with_user(self.redacteur).taper("app")
        self.assertEqual(r["statut"], "choice")
        self.partenaire.invalidate_recordset(["ref"])
        self.assertFalse(self.partenaire.ref)

    def test_une_question_sans_choix_est_une_information(self):
        tag = self.env["bf.nfc.tag"].create({
            "name": "Salle occupée", "gesture_id": self.env.ref("bf_nfc.gesture_note").id,
            "res_model": "res.partner", "res_id": self.partenaire.id,
        })

        def informe(geste, tag, tap, params):
            return {"titre": "Salle", "message": "Occupée jusqu'à 15 h", "choix": []}

        with patch.object(BfNfcGesture, "_executer_note", informe):
            r = tag.with_user(self.redacteur).taper("app")
        self.assertEqual(r["statut"], "info")
        self.assertEqual(r["message"], "Occupée jusqu'à 15 h")
        self.assertFalse(tag.tap_ids)


@tagged("post_install", "-at_install")
class TestDiffere(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "20")
        cls.redacteur = new_test_user(cls.env, login="differe-redacteur",
                                      groups="base.group_user,base.group_partner_manager")
        cls.partenaire = cls.env["res.partner"].create({"name": "Sous-sol du parcours"})
        cls.ronde = cls.env["bf.nfc.tag"].create({
            "name": "Sous-sol", "place": "Local technique",
            "gesture_id": cls.env.ref("bf_nfc.gesture_note").id,
            "res_model": "res.partner", "res_id": cls.partenaire.id,
        })
        cls.fiche = cls.env["bf.nfc.tag"].create({
            "name": "Fiche", "gesture_id": cls.env.ref("bf_nfc.gesture_open").id,
            "res_model": "res.partner", "res_id": cls.partenaire.id,
        })

    def _iso(self, delta):
        return (fields.Datetime.now() - delta).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_le_meme_envoi_rejoue_ne_refait_pas_le_geste(self):
        """🔴 La file hors ligne renvoie tant qu'elle n'a pas lu de réponse."""
        notes = lambda: self.env["mail.message"].search_count(  # noqa: E731
            [("model", "=", "res.partner"), ("res_id", "=", self.partenaire.id)])
        avant = notes()
        premier = self.ronde.with_user(self.redacteur).taper("app", nonce="n-1")
        second = self.ronde.with_user(self.redacteur).taper("app", nonce="n-1")
        self.assertEqual(premier["statut"], "ok")
        self.assertEqual(second["tap_id"], premier["tap_id"])
        self.assertEqual(notes(), avant + 1)

    def test_le_passage_differe_garde_son_heure(self):
        quand = self._iso(timedelta(hours=6))
        r = self.ronde.with_user(self.redacteur).taper("app", quand=quand, nonce="n-2")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        tap = self.env["bf.nfc.tap"].browse(r["tap_id"])
        self.assertTrue(tap.offline)
        self.assertEqual(tap.tapped_at.strftime("%Y-%m-%dT%H:%M:%SZ"), quand)
        note = self.env["mail.message"].search(
            [("model", "=", "res.partner"), ("res_id", "=", self.partenaire.id)],
            order="id desc", limit=1)
        self.assertIn("envoyé en différé", note.body)

    def test_ouvrir_une_fiche_ne_se_joue_pas_en_differe(self):
        r = self.fiche.with_user(self.redacteur).taper("app", quand=self._iso(timedelta(hours=1)))
        self.assertEqual(r["statut"], "refused")
        self.assertIn("différé", r["message"])
        self.assertTrue(self.env["bf.nfc.tap"].browse(r["tap_id"]).offline)

    def test_trop_ancien_ou_dans_le_futur_refuse(self):
        vieux = self.ronde.with_user(self.redacteur).taper("app", quand=self._iso(timedelta(days=4)))
        self.assertEqual(vieux["statut"], "refused")
        self.assertIn("plus de 72 heures", vieux["message"])
        futur = self.ronde.with_user(self.redacteur).taper("app", quand=self._iso(-timedelta(hours=1)))
        self.assertEqual(futur["statut"], "refused")
        self.assertIn("futur", futur["message"])

    def test_la_derive_d_horloge_n_est_pas_un_differe(self):
        r = self.ronde.with_user(self.redacteur).taper("app", quand=self._iso(timedelta(seconds=40)))
        self.assertEqual(r["statut"], "ok")
        self.assertFalse(self.env["bf.nfc.tap"].browse(r["tap_id"]).offline)

    def test_deux_passages_hors_ligne_rapproches_font_un_doublon(self):
        """⚠️ Le doublon se juge à l'heure du tapotement : reçus ensemble une heure
        plus tard, deux lectures à cinq secondes d'écart restent un seul passage."""
        t0 = fields.Datetime.now() - timedelta(hours=1)
        a = self.ronde.with_user(self.redacteur).taper(
            "app", quand=t0.strftime("%Y-%m-%dT%H:%M:%SZ"), nonce="n-3")
        b = self.ronde.with_user(self.redacteur).taper(
            "app", quand=(t0 + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ"), nonce="n-4")
        self.assertEqual(a["statut"], "ok")
        self.assertEqual(b["statut"], "duplicate")

    def test_un_menu_ne_pose_pas_de_question_en_differe(self):
        menu = self.env["bf.nfc.tag"].create({
            "name": "Menu du sous-sol", "gesture_id": self.env.ref("bf_nfc.gesture_menu").id,
            "res_model": "res.partner", "res_id": self.partenaire.id,
        })
        self.env["bf.nfc.tag.choice"].create({
            "tag_id": menu.id, "name": "Passage", "gesture_id": self.env.ref("bf_nfc.gesture_note").id})
        r = menu.with_user(self.redacteur).taper("app", quand=self._iso(timedelta(hours=2)))
        self.assertEqual(r["statut"], "refused")
        self.assertIn("demande un choix", r["message"])


@tagged("post_install", "-at_install")
class TestEtiquette(TransactionCase):

    def test_l_etiquette_porte_le_qr_de_l_adresse_gravee(self):
        partenaire = self.env["res.partner"].create({"name": "Étiquette"})
        tag = self.env["bf.nfc.tag"].create({
            "name": "Poste d'accueil", "place": "Comptoir",
            "gesture_id": self.env.ref("bf_nfc.gesture_open").id,
            "res_model": "res.partner", "res_id": partenaire.id,
        })
        signee = self.env["bf.nfc.tag"].create({
            "name": "Pastille signée", "gesture_id": self.env.ref("bf_nfc.gesture_open").id,
            "res_model": "res.partner", "res_id": partenaire.id,
            "sdm_enabled": True, "sdm_uid": "04AABBCCDDEEFF", "user_id": self.env.uid,
        })
        self.assertTrue(tag.url.endswith("/nfc/%s" % tag.code))
        self.assertFalse(signee.url)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "bf_nfc.action_report_tag_label", (tag | signee).ids)[0].decode()
        self.assertIn("data:image/png;base64,", html)
        self.assertIn(tag.code, html)
        self.assertIn("Pastille signée : pas de code QR", html)


@tagged("post_install", "-at_install")
class TestPortesV2(HttpCase):

    def test_le_menu_s_affiche_au_navigateur_et_le_choix_agit(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        partenaire = self.env["res.partner"].create({"name": "Immeuble du navigateur"})
        menu = self.env["bf.nfc.tag"].create({
            "name": "Menu du navigateur", "gesture_id": self.env.ref("bf_nfc.gesture_menu").id,
            "res_model": "res.partner", "res_id": partenaire.id,
        })
        ligne = self.env["bf.nfc.tag.choice"].create({
            "tag_id": menu.id, "name": "Je suis passé",
            "gesture_id": self.env.ref("bf_nfc.gesture_note").id})
        self.authenticate("admin", "admin")
        page = self.url_open("/nfc/%s" % menu.code).text
        self.assertIn("Je suis passé", page)
        self.assertFalse(menu.tap_ids, "Afficher le menu ne fait rien.")
        jeton = re.search(r'<input[^>]*name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
        avant = self.env["mail.message"].search_count(
            [("model", "=", "res.partner"), ("res_id", "=", partenaire.id)])
        reponse = self.url_open("/nfc/%s/agir" % menu.code,
                                data={"csrf_token": jeton, "choix": str(ligne.id)})
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(self.env["mail.message"].search_count(
            [("model", "=", "res.partner"), ("res_id", "=", partenaire.id)]), avant + 1)

    def test_catalogue_sans_menu_et_liste_avec_adresse(self):
        import base64
        import hashlib
        personne = new_test_user(self.env, login="socle-api",
                                 groups="base.group_user,bf_nfc.group_nfc_manager")
        verif = "verificateur-socle-assez-long-pour-etre-serieux"
        defi = base64.urlsafe_b64encode(hashlib.sha256(verif.encode()).digest()).decode().rstrip("=")
        code = self.env["bf.nfc.device"]._issue_pending(personne.id, challenge=defi)
        _a, jeton = self.env["bf.nfc.device"]._exchange(code, verif)
        entetes = {"Authorization": "Bearer %s" % jeton}
        partenaire = self.env["res.partner"].create({"name": "Liste"})
        tag = self.env["bf.nfc.tag"].create({
            "name": "Liste", "gesture_id": self.env.ref("bf_nfc.gesture_open").id,
            "res_model": "res.partner", "res_id": partenaire.id})
        # 🔴 Au moins deux pastilles : l'adresse calculée sur une LISTE levait avec
        # le module Site web, et un essai à une seule pastille ne le voyait pas.
        self.env["bf.nfc.tag"].create({
            "name": "Liste bis", "gesture_id": self.env.ref("bf_nfc.gesture_open").id,
            "res_model": "res.partner", "res_id": partenaire.id})
        self.assertEqual(len(self.env["bf.nfc.tag"].search([]).mapped("url")) >= 2, True)
        catalogue = self.url_open("/bf_nfc/mobile/v1/catalogue", headers=entetes).json()
        self.assertNotIn("menu", [g["code"] for g in catalogue["gestes"]])
        self.assertIn("accepte_differe", catalogue["gestes"][0])
        lignes = self.url_open("/bf_nfc/mobile/v1/pastilles", headers=entetes).json()["pastilles"]
        self.assertEqual(next(l for l in lignes if l["code"] == tag.code)["url"], tag.url)


@tagged("post_install", "-at_install")
class TestLangueDeLAppareil(HttpCase):

    def test_les_phrases_suivent_la_langue_de_la_personne(self):
        """🔴 L'API basculait sur l'utilisateur sans sa langue : une personne en
        anglais lisait les phrases des gestes en français."""
        import base64
        import hashlib
        self.env["res.lang"]._activate_lang("en_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_nfc"], ["en_CA"], overwrite=True)
        personne = new_test_user(self.env, login="langue-en", groups="base.group_user,base.group_partner_manager",
                                 lang="en_CA")
        verif = "verificateur-langue-assez-long-pour-etre-serieux"
        defi = base64.urlsafe_b64encode(hashlib.sha256(verif.encode()).digest()).decode().rstrip("=")
        _a, jeton = self.env["bf.nfc.device"]._exchange(
            self.env["bf.nfc.device"]._issue_pending(personne.id, challenge=defi), verif)
        partenaire = self.env["res.partner"].create({"name": "Language check"})
        tag = self.env["bf.nfc.tag"].create({
            "name": "Language", "gesture_id": self.env.ref("bf_nfc.gesture_note").id,
            "res_model": "res.partner", "res_id": partenaire.id})
        reponse = self.url_open("/bf_nfc/mobile/v1/tap", data=json.dumps({"code": tag.code}),
                                headers={"Authorization": "Bearer %s" % jeton, "Content-Type": "application/json"})
        self.assertEqual(reponse.json()["message"], "Visit logged.")
