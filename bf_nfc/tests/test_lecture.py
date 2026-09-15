"""Les routes de lecture de l'application : inspecter, lister, journal, chercher.

Toutes en lecture, toutes avec les droits de la personne. Ce que ces essais
gardent surtout, ce sont les trois choses qui fuiraient en silence si on les
écrivait naïvement.
"""
import base64
import hashlib
import json

from odoo.tests import HttpCase, new_test_user, tagged

API = "/bf_nfc/mobile/v1"


@tagged("post_install", "-at_install")
class TestLecture(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.geste_open = cls.env.ref("bf_nfc.gesture_open")
        cls.partenaire = cls.env["res.partner"].create({"name": "Fiche lisible du parcours"})
        cls.tag = cls.env["bf.nfc.tag"].create({
            "name": "Porte du bureau", "place": "Entrée",
            "gesture_id": cls.geste_open.id,
            "res_model": "res.partner", "res_id": cls.partenaire.id,
        })

    def _apparier(self, login, groupes="base.group_user"):
        personne = new_test_user(self.env, login=login, groups=groupes)
        verif = "verificateur-lecture-assez-long-pour-etre-serieux"
        defi = base64.urlsafe_b64encode(hashlib.sha256(verif.encode()).digest()).decode().rstrip("=")
        Device = self.env["bf.nfc.device"]
        code = Device._issue_pending(personne.id, challenge=defi)
        _appareil, jeton = Device._exchange(code, verif)
        return personne, {"Authorization": "Bearer %s" % jeton}

    def _get(self, chemin, entetes):
        return self.url_open(API + chemin, headers=entetes)

    # ------------------------------------------------------------------
    # Inspecter une pastille
    # ------------------------------------------------------------------
    def test_inspecter_rend_le_nom_d_une_fiche_lisible(self):
        """🔴 Garde le défaut corrigé avant même le premier essai.

        La pastille est retrouvée avant de basculer sur l'identité de la
        personne. Relue avec `tag.sudo()` au lieu d'un nouveau parcours, son
        contrôle de lecture se faisait en utilisateur PUBLIC, et le nom de toute
        fiche restait masqué, même lisible. Ceci aurait échoué.
        """
        _p, entetes = self._apparier("lecteur-lisible")
        charge = self._get("/pastille/infos?code=%s" % self.tag.code, entetes).json()
        self.assertEqual(charge["nom"], "Porte du bureau")
        self.assertEqual(charge["endroit"], "Entrée")
        self.assertTrue(charge["cible"]["lisible"])
        self.assertEqual(charge["cible"]["nom"], "Fiche lisible du parcours")
        self.assertTrue(charge["utilisable"])

    def test_inspecter_ne_nomme_pas_une_fiche_interdite(self):
        """🔴 Inspecter ne doit pas faire fuir ce que jouer refuserait."""
        parametre = self.env["ir.config_parameter"].create({
            "key": "bf_nfc.lecture_secret", "value": "ne doit pas fuir"})
        tag = self.env["bf.nfc.tag"].create({
            "name": "Vers un secret", "gesture_id": self.geste_open.id,
            "res_model": "ir.config_parameter", "res_id": parametre.id,
        })
        _p, entetes = self._apparier("lecteur-interdit")
        charge = self._get("/pastille/infos?code=%s" % tag.code, entetes).json()
        self.assertFalse(charge["cible"]["lisible"])
        self.assertIsNone(charge["cible"]["nom"])
        self.assertIsNone(charge["cible"]["id"])
        self.assertNotIn("bf_nfc.lecture_secret", json.dumps(charge))

    def test_inspecter_ne_joue_pas_le_geste(self):
        _p, entetes = self._apparier("lecteur-sans-jouer")
        avant = self.tag.tap_count
        self._get("/pastille/infos?code=%s" % self.tag.code, entetes)
        self.tag.invalidate_recordset(["tap_count"])
        self.assertEqual(self.tag.tap_count, avant, "Lire n'est pas taper.")
        self.assertFalse(self.env["bf.nfc.tap"].search([("tag_id", "=", self.tag.id)]))

    def test_inspecter_une_pastille_expiree_dit_pourquoi(self):
        tag = self.env["bf.nfc.tag"].create({
            "name": "Périmée", "gesture_id": self.geste_open.id,
            "res_model": "res.partner", "res_id": self.partenaire.id,
            "date_expiry": "2020-01-01",
        })
        _p, entetes = self._apparier("lecteur-perime")
        charge = self._get("/pastille/infos?code=%s" % tag.code, entetes).json()
        self.assertFalse(charge["utilisable"])
        self.assertIn("expiré", charge["raison"])

    def test_inspecter_code_inconnu_et_sans_jeton(self):
        _p, entetes = self._apparier("lecteur-inconnu")
        self.assertEqual(self._get("/pastille/infos?code=ZZZZZZZZ", entetes).status_code, 404)
        self.assertEqual(self.url_open(API + "/pastille/infos?code=%s" % self.tag.code).status_code, 401)

    # ------------------------------------------------------------------
    # Lister, journal
    # ------------------------------------------------------------------
    def test_mes_pastilles_liste_ce_que_la_personne_voit(self):
        _p, entetes = self._apparier("lister")
        codes = [t["code"] for t in self._get("/pastilles", entetes).json()["pastilles"]]
        self.assertIn(self.tag.code, codes)

    def test_le_journal_ne_rend_que_ses_propres_tapotements_meme_a_la_gestion(self):
        """🔴 Le téléphone est un outil personnel, pas une console de surveillance."""
        self.tag.taper("app")   # un tapotement de l'administrateur
        gestion, entetes = self._apparier(
            "gestionnaire-journal", groupes="base.group_user,bf_nfc.group_nfc_manager")
        self.tag.with_user(gestion).taper("app")
        lignes = self._get("/journal", entetes).json()["tapotements"]
        self.assertTrue(lignes)
        utilisateurs = set(self.env["bf.nfc.tap"].browse([l["id"] for l in lignes]).mapped("user_id").ids)
        self.assertEqual(utilisateurs, {gestion.id},
                         "Même un gestionnaire ne reçoit que ses tapotements sur le téléphone.")

    # ------------------------------------------------------------------
    # Chercher une fiche à viser
    # ------------------------------------------------------------------
    def test_chercher_une_fiche_par_son_nom(self):
        _p, entetes = self._apparier("chercheur")
        cibles = self._get("/cibles?modele=res.partner&q=lisible%20du%20parcours", entetes).json()["cibles"]
        self.assertIn(self.partenaire.id, [c["id"] for c in cibles])

    def test_la_recherche_refuse_un_modele_hors_liste(self):
        """🔴 Pas une API de lecture générale posée à côté des pastilles."""
        _p, entetes = self._apparier("chercheur-hors-liste")
        for modele in ("res.users", "ir.config_parameter", "account.move", "n.existe.pas"):
            reponse = self._get("/cibles?modele=%s&q=a" % modele, entetes)
            self.assertEqual(reponse.status_code, 400, "%s doit être refusé" % modele)

    def test_le_catalogue_annonce_les_modeles_cherchables(self):
        _p, entetes = self._apparier("catalogue-modeles")
        modeles = [m["modele"] for m in self._get("/catalogue", entetes).json()["modeles_cibles"]]
        self.assertIn("res.partner", modeles)
        self.assertNotIn("res.users", modeles)


@tagged("post_install", "-at_install")
class TestGravureBornee(HttpCase):
    """🔴 La route qui crée une pastille rejoue les filtres du catalogue.

    Sans ça, ils n'étaient que décoratifs : le catalogue cachait les gestes réservés
    et bornait les types de fiche, mais cette route acceptait n'importe lequel.
    """

    def _apparier(self, login, groupes="base.group_user"):
        personne = new_test_user(self.env, login=login, groups=groupes)
        verif = "verificateur-gravure-assez-long-pour-etre-serieux"
        defi = base64.urlsafe_b64encode(hashlib.sha256(verif.encode()).digest()).decode().rstrip("=")
        Device = self.env["bf.nfc.device"]
        code = Device._issue_pending(personne.id, challenge=defi)
        _appareil, jeton = Device._exchange(code, verif)
        return {"Authorization": "Bearer %s" % jeton, "Content-Type": "application/json"}

    def _creer(self, entetes, **charge):
        return self.url_open(API + "/pastille", data=json.dumps(charge), headers=entetes)

    def test_un_geste_reserve_a_la_gestion_est_refuse(self):
        entetes = self._apparier("gravure-interne", "base.group_user,bf_nfc.group_nfc_manager")
        # Gestionnaire des pastilles, mais le geste « cron » reste réservé : il l'est
        # pour la gestion, donc accepté ici. On éprouve l'inverse avec un interne.
        self.assertEqual(self._creer(entetes, geste="cron", nom="Sauvegarde").status_code, 200)
        entetes = self._apparier("gravure-ordinaire")
        reponse = self._creer(entetes, geste="cron", nom="Sauvegarde")
        self.assertEqual(reponse.status_code, 403)
        self.assertIn("réservé", reponse.json()["message"])

    def test_un_type_de_fiche_hors_liste_est_refuse(self):
        entetes = self._apparier("gravure-modele", "base.group_user,bf_nfc.group_nfc_manager")
        reponse = self._creer(entetes, geste="open", nom="Fuite", modele="res.users", fiche=2)
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(reponse.json()["error"], "model_not_allowed")

    def test_des_parametres_qui_ne_sont_pas_un_objet_json_sont_refuses(self):
        """Une pastille ainsi gravée lèverait au premier tapotement, sur le terrain."""
        entetes = self._apparier("gravure-params", "base.group_user,bf_nfc.group_nfc_manager")
        for mauvais in ("pas du json", "[1, 2]", '"texte"'):
            reponse = self._creer(entetes, geste="open", nom="Mauvaise", params=mauvais)
            self.assertEqual(reponse.status_code, 400, mauvais)
            self.assertEqual(reponse.json()["error"], "bad_params")
