"""Téléverser un fichier média par morceaux, joué par HTTP.

Ce qui est éprouvé : les morceaux s'ajoutent dans l'ordre, un morceau renvoyé
est ignoré, un trou est refusé avec la position où reprendre, et le fichier
complet part au dossier surveillé EN FLUX, sous le nom que le processeur sait
lire. Le WebDAV est un double : rien ne sort du banc.
"""
import json
import os
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import new_test_user
from odoo.tests.common import HttpCase, tagged
from odoo.tools import config as odoo_config

BASE = "/bf_capture/mobile/v1"
MO = 1024 * 1024


@tagged("post_install", "-at_install", "bf_capture")
class TestTeleversement(HttpCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("bf_capture.folder", "Transcriptions")
        self.env["nextcloud.document.config"].sudo().create({
            "name": "Banc téléversement",
            "nextcloud_base_url": "https://nextcloud.example.com",
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "jdoe",
            "company_id": self.env.company.id,
        })
        self.evenement = self.env["calendar.event"].create({
            "name": "Conseil du banc",
            "start": datetime(2026, 9, 21, 19, 0, 0),
            "stop": datetime(2026, 9, 21, 20, 0, 0),
        })
        self.usager = self.env.ref("base.user_admin")
        self.poses = []
        Config = type(self.env["nextcloud.document.config"])

        def faux_put(config, path, content, content_type=""):
            # En flux : la passerelle reçoit un fichier ouvert, pas des octets.
            octets = content.read() if hasattr(content, "read") else content
            # Un dépôt au même chemin ÉCRASE, comme le ferait WebDAV.
            for rang, (ancien, _o) in enumerate(self.poses):
                if ancien == path:
                    self.poses[rang] = (path, octets)
                    return
            self.poses.append((path, octets))

        def faux_propfind(config, path, depth="1", props=None, limit=None):
            # 🔴 Le double rendait toujours une liste VIDE, donc `_noms_pris`
            # était toujours vide et `_nom_libre` ne numérotait jamais : aucun
            # essai du module ne pouvait tomber sur un écrasement (constat du
            # tour final). Il reflète maintenant ce qui a été déposé.
            prefixe = path.rstrip("/") + "/"
            return [{"name": chemin[len(prefixe):], "size": len(octets),
                     "is_dir": False}
                    for chemin, octets in self.poses
                    if chemin.startswith(prefixe) and "/" not in chemin[len(prefixe):]]

        for nom, double in (("_webdav_propfind", faux_propfind),
                            ("_webdav_mkcol", lambda *a, **k: True),
                            ("_webdav_put", faux_put)):
            rustine = patch.object(Config, nom, double)
            rustine.start()
            self.addCleanup(rustine.stop)
        rustine = patch("odoo.addons.bf_capture.controllers.mobile_api._device",
                        lambda: SimpleNamespace(user_id=self.usager, _fields={}))
        rustine.start()
        self.addCleanup(rustine.stop)

    def _json(self, route, corps):
        return self.url_open(f"{BASE}{route}", data=json.dumps(corps),
                             headers={"Content-Type": "application/json"})

    def _morceau(self, upload_id, offset, octets):
        return self.url_open(f"{BASE}/televersement/morceau?upload_id={upload_id}&offset={offset}",
                             data=octets, headers={"Content-Type": "application/octet-stream"})

    def _ouvrir(self, nom="reunion.mp4", taille=int(2.5 * MO)):
        reponse = self._json("/televersement/ouvrir", {"nom": nom, "taille": taille,
                                                        "event_id": self.evenement.id})
        self.assertEqual(reponse.status_code, 200, reponse.text)
        upload_id = reponse.json()["upload_id"]
        # ⚠️ Le dossier des morceaux est partagé par toute la base, et une fiche
        # déposée n'est plus visible à l'éviction : sans ce nettoyage, la suite
        # laisse derrière elle des fiches que seule la purge des 48 h ramasse.
        self.addCleanup(self._nettoyer_televersement, upload_id)
        return upload_id

    def _nettoyer_televersement(self, upload_id):
        """🔴 Efface les fichiers SANS passer par le modèle.

        Un harnais qui appelle une méthode neuve du lot (``_effacer``) ne peut
        plus jouer contre la version d'AVANT : le nettoyage lève une
        ``AttributeError`` et toute la classe rougit, ce qui gonfle le décompte
        des essais qui « discriminent » avec des artefacts. Le harnais ne doit
        rien devoir à ce qu'il mesure.
        """
        dossier = os.path.join(odoo_config["data_dir"],
                               "bf_capture_televersements", self.env.cr.dbname)
        for suffixe in (".json", ".part", ".json.tmp", ".part.vide"):
            chemin = os.path.join(dossier, upload_id + suffixe)
            if os.path.exists(chemin):
                os.remove(chemin)

    def test_la_sonde_annonce_le_televersement(self):
        charge = self.url_open(f"{BASE}/ping").json()
        self.assertEqual(charge["televersement"], 1)
        self.assertGreater(charge["televersement_max_bytes"], 128 * MO)
        self.assertEqual(charge["morceau_bytes"], 8 * MO)

    def test_les_morceaux_se_reprennent_et_le_fichier_part_en_flux(self):
        contenu = bytes(range(256)) * (int(2.5 * MO) // 256)
        uid = self._ouvrir(taille=len(contenu))
        self.assertEqual(self._morceau(uid, 0, contenu[:MO]).json()["recus"], MO)
        self.assertEqual(self._morceau(uid, MO, contenu[MO:2 * MO]).json()["recus"], 2 * MO)
        # Renvoyé après une réponse perdue : ignoré, rien ne double.
        self.assertEqual(self._morceau(uid, 0, contenu[:MO]).json()["recus"], 2 * MO)
        # Trop loin : refusé, avec la position où reprendre.
        trou = self._morceau(uid, 3 * MO, b"x")
        self.assertEqual(trou.status_code, 409)
        self.assertEqual(trou.json()["recus"], 2 * MO)
        # Terminer trop tôt : même réponse, rien ne part.
        tot = self._json("/televersement/terminer", {"upload_id": uid})
        self.assertEqual(tot.status_code, 409)
        self.assertEqual(self.poses, [])
        # L'état dit où on en est, puis le dernier morceau.
        etat = self.url_open(f"{BASE}/televersement/etat?upload_id={uid}").json()
        self.assertEqual((etat["recus"], etat["taille"]), (2 * MO, len(contenu)))
        self._morceau(uid, 2 * MO, contenu[2 * MO:])

        fin = self._json("/televersement/terminer", {"upload_id": uid})
        self.assertEqual(fin.status_code, 200, fin.text)
        self.assertEqual(fin.json()["nom"], "2026-09-21 15-00-00 - Conseil du banc.mp4")
        self.assertEqual(len(self.poses), 1)
        chemin, octets = self.poses[0]
        self.assertEqual(chemin, "Transcriptions/2026-09-21 15-00-00 - Conseil du banc.mp4")
        self.assertEqual(octets, contenu, "le fichier arrive entier et dans l'ordre")
        # 🔴 La fiche SURVIT au dépôt (18.0.1.4.3) : elle annonce un
        # téléversement complet, pour qu'une app qui n'a pas reçu la réponse
        # n'aille pas rouvrir à zéro et redéposer. Ses octets, eux, sont libérés.
        etat_final = self.url_open(f"{BASE}/televersement/etat?upload_id={uid}").json()
        self.assertEqual((etat_final["recus"], etat_final["taille"]),
                         (len(contenu), len(contenu)))
        self.assertTrue(etat_final["depose"])
        part, _meta = self.env["bf.capture"]._chemins(uid)
        self.assertEqual(os.path.getsize(part), 0, "les octets n'ont pas été libérés")

    def test_terminer_deux_fois_ne_depose_qu_une_fois(self):
        uid = self._ouvrir(taille=10)
        self._morceau(uid, 0, b"0123456789")
        cle = str(uuid.uuid4())
        premier = self._json("/televersement/terminer", {"upload_id": uid, "client_uuid": cle})
        second = self._json("/televersement/terminer", {"upload_id": uid, "client_uuid": cle})
        self.assertEqual(premier.status_code, 200, premier.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json().get("replay"))
        self.assertEqual(len(self.poses), 1)

    def test_ce_que_le_processeur_ne_lit_pas_est_refuse_avant_le_premier_octet(self):
        reponse = self._json("/televersement/ouvrir", {"nom": "rapport.pdf", "taille": 10,
                                                        "event_id": self.evenement.id})
        self.assertEqual(reponse.status_code, 400)

    def test_au_dela_du_plafond_est_refuse(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_capture.televersement_max_bytes", "100")
        reponse = self._json("/televersement/ouvrir", {"nom": "a.mp3", "taille": 101,
                                                        "event_id": self.evenement.id})
        self.assertEqual(reponse.status_code, 400)

    def test_sans_rencontre_ni_titre_est_refuse(self):
        reponse = self._json("/televersement/ouvrir", {"nom": "a.mp3", "taille": 10})
        self.assertEqual(reponse.status_code, 400)

    def test_un_nombre_illisible_est_un_refus_pas_une_panne(self):
        uid = self._ouvrir(taille=10)
        self.assertEqual(self._morceau(uid, "abc", b"x").status_code, 400)
        reponse = self._json("/televersement/ouvrir", {"nom": "a.mp3", "taille": "beaucoup",
                                                        "event_id": self.evenement.id})
        self.assertEqual(reponse.status_code, 400)

    def test_un_identifiant_fabrique_ne_sort_pas_du_dossier(self):
        reponse = self.url_open(f"{BASE}/televersement/etat?upload_id=../../etc/passwd")
        self.assertEqual(reponse.status_code, 400)

    def test_un_refus_annule_ce_que_le_geste_avait_ecrit(self):
        """🔴 La route attrapait l'exception, rendait le refus, et Odoo validait
        quand même ce que le geste avait écrit avant de renoncer.

        Le témoin est posé DANS la requête, par la passerelle simulée, juste
        avant la panne : s'il survit au 503, c'est que la transaction a été
        validée malgré le refus. Il se relit par une recherche SQL, pas par
        ``get_param`` — un cache d'ORM ne se rejoue pas en arrière.
        """
        temoin = "bf_capture.essai_retour_en_arriere"

        def put_qui_tombe(config, path, content, content_type=""):
            config.env["ir.config_parameter"].sudo().set_param(temoin, path)
            raise UserError("WebDAV PUT error: HTTP 503")

        uid = self._ouvrir(taille=10)
        self._morceau(uid, 0, b"0123456789")
        with patch.object(type(self.env["nextcloud.document.config"]),
                          "_webdav_put", put_qui_tombe):
            reponse = self._json("/televersement/terminer", {"upload_id": uid})
        self.assertEqual(reponse.status_code, 503, reponse.text)
        self.assertEqual(self.poses, [])
        self.env.invalidate_all()
        reste = self.env["ir.config_parameter"].sudo().search_count(
            [("key", "=", temoin)])
        self.assertEqual(reste, 0, "le refus a laissé son écriture en base")
        # Le chemin du refus n'efface rien, par construction : le téléversement
        # reste repris par l'app. C'est le harnais qui nettoie (`_ouvrir`), et il
        # le fait sans rien devoir au lot qu'il mesure.

    def test_terminer_rejoue_ne_depose_pas_deux_fois(self):
        """🔴 Sans accusé, un ``terminer`` rejoué redéposait (constat d'une
        relecture adverse).

        C'est le cas où la réponse se perd avant que la transaction soit validée :
        l'accusé n'existe pas, et l'app rejoue. Elle recevait « introuvable »,
        rouvrait à zéro, redéposait sous « … (2).mp4 », et le processeur faisait
        DEUX comptes rendus de la même rencontre.
        """
        uid = self._ouvrir(taille=10)
        self._morceau(uid, 0, b"0123456789")
        # ⚠️ En écart : le dossier des morceaux est partagé par toute la base, et
        # les autres essais y laissent leurs téléversements.
        promis_avant = self.env["bf.capture"]._octets_promis()
        premier = self._json("/televersement/terminer", {"upload_id": uid})
        second = self._json("/televersement/terminer", {"upload_id": uid})
        self.assertEqual(premier.status_code, 200, premier.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(premier.json()["nom"], second.json()["nom"])
        self.assertEqual(len(self.poses), 1, "le rejeu a déposé une seconde fois")
        # La fiche gardée ne promet plus rien : ses octets sont libérés, et les
        # recompter réserverait le disque une seconde fois.
        self.assertEqual(self.env["bf.capture"]._octets_promis(), promis_avant,
                         "la fiche déposée promet encore des octets")

    def test_un_depot_coupe_avant_sa_confirmation_ecrase_le_meme_nom(self):
        """Le dépôt est un effet HORS transaction : s'il part et que tout tombe
        avant la confirmation, le rejeu doit écraser, pas numéroter."""
        uid = self._ouvrir(taille=10)
        self._morceau(uid, 0, b"0123456789")
        Modele = type(self.env["bf.capture"])
        vrai_ecrire = Modele._ecrire_fiche
        appels = []

        def ecrire_qui_tombe(self, meta, fiche):
            appels.append(meta)
            if len(appels) > 1:          # après le dépôt, avant la confirmation
                raise RuntimeError("coupé")
            return vrai_ecrire(self, meta, fiche)

        with patch.object(Modele, "_ecrire_fiche", ecrire_qui_tombe):
            coupe = self._json("/televersement/terminer", {"upload_id": uid})
        self.assertEqual(coupe.status_code, 500, coupe.text)
        self.assertEqual(len(self.poses), 1)
        premier = self.poses[0][0]
        # Les octets sont libérés AVANT la marque : coupé entre les deux, le
        # téléversement redemande ses octets plutôt que de laisser un `.part`
        # plein que plus aucune borne ne voit.
        etat = self.url_open(f"{BASE}/televersement/etat?upload_id={uid}").json()
        self.assertEqual((etat["recus"], etat["taille"]), (0, 10))
        self._morceau(uid, 0, b"0123456789")
        rejeu = self._json("/televersement/terminer", {"upload_id": uid})
        self.assertEqual(rejeu.status_code, 200, rejeu.text)
        self.assertEqual(len(self.poses), 1, "le rejeu a posé un SECOND fichier")
        self.assertEqual(self.poses[0][0], premier,
                         "le rejeu a numéroté au lieu d'écraser son propre nom")
        self.assertEqual(self.poses[0][1], b"0123456789")

    def test_un_nom_reserve_n_ecrase_pas_le_depot_d_un_autre(self):
        """🔴 Le constat bloquant du tour final : la réservation du nom était
        invisible aux autres, donc elle devenait une arme.

        Deux téléversements de la MÊME rencontre calculent le même nom. Si le
        premier réserve puis échoue, le second ne voyait rien et déposait sous ce
        nom ; le rejeu du premier écrasait alors son fichier. Un enregistrement
        détruit sans trace, là où la version d'avant se contentait de doubler le
        compte rendu.
        """
        a = self._ouvrir(taille=10)
        self._morceau(a, 0, b"AAAAAAAAAA")
        b = self._ouvrir(taille=10)
        self._morceau(b, 0, b"BBBBBBBBBB")

        Config = type(self.env["nextcloud.document.config"])

        def put_injoignable(config, path, content, content_type=""):
            raise UserError("Erreur WebDAV PUT: HTTP 503")

        with patch.object(Config, "_webdav_put", put_injoignable):
            echec = self._json("/televersement/terminer", {"upload_id": a})
        self.assertEqual(echec.status_code, 503, echec.text)
        self.assertEqual(self.poses, [], "rien n'aurait dû partir")

        depot_b = self._json("/televersement/terminer", {"upload_id": b})
        self.assertEqual(depot_b.status_code, 200, depot_b.text)
        rejeu_a = self._json("/televersement/terminer", {"upload_id": a})
        self.assertEqual(rejeu_a.status_code, 200, rejeu_a.text)

        self.assertNotEqual(depot_b.json()["nom"], rejeu_a.json()["nom"],
                            "le rejeu a repris le nom déjà posé par l'autre")
        self.assertEqual(len(self.poses), 2)
        contenus = {chemin.rsplit("/", 1)[-1]: octets for chemin, octets in self.poses}
        self.assertEqual(sorted(contenus.values()), [b"AAAAAAAAAA", b"BBBBBBBBBB"],
                         "un des deux enregistrements a été écrasé")

    def test_un_morceau_apres_le_depot_renvoie_la_position_finale(self):
        """Dire « tout est reçu » envoie l'app à ``terminer``, qui lui rendra la
        réponse d'origine, au lieu de la faire pousser pour rien."""
        uid = self._ouvrir(taille=10)
        self._morceau(uid, 0, b"0123456789")
        self._json("/televersement/terminer", {"upload_id": uid})
        tardif = self._morceau(uid, 0, b"0123456789")
        self.assertEqual(tardif.status_code, 409)
        self.assertEqual(tardif.json()["recus"], 10)
        self.assertEqual(len(self.poses), 1)

    def test_le_televersement_d_un_autre_est_introuvable(self):
        uid = self._ouvrir(taille=10)
        self.usager = new_test_user(self.env, login="capture_autre_morceaux", groups="base.group_user")
        self.assertEqual(self._morceau(uid, 0, b"0123456789").status_code, 403)
        self.assertEqual(self._json("/televersement/terminer", {"upload_id": uid}).status_code, 403)
