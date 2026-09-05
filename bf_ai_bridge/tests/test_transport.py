"""Le transport est écrit à la main : il est éprouvé contre une vraie socket.

Un faux bridge écoute sur une socket ``AF_UNIX`` jetable et rend les trames
qu'on lui dicte. Rien ici ne touche au vrai service.
"""
import json
import os
import shutil
import socket
import tempfile
import threading

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from ..models.bf_ai_bridge import (
    LEGACY_PARAMS, LEGACY_TENANT_PARAMS, SOCKET_PARAM, TENANT_PARAM,
)
from ..tools import transport


class _FauxBridge:
    """Serveur d'un seul tour : accepte une connexion, rend une trame figée."""

    def __init__(self, reponse_brute, decoupe=None):
        self.reponse = reponse_brute
        self.decoupe = decoupe or [len(reponse_brute)]
        self.repertoire = tempfile.mkdtemp(prefix="bf_ai_bridge_")
        self.chemin = os.path.join(self.repertoire, "b.sock")
        self.requete_recue = b""
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.chemin)
        self._srv.listen(1)
        self._fil = threading.Thread(target=self._servir, daemon=True)
        self._fil.start()

    def _servir(self):
        try:
            conn, _adr = self._srv.accept()
        except OSError:
            return
        try:
            conn.settimeout(5)
            # Lire la requête jusqu'à disposer du corps annoncé.
            tampon = b""
            while b"\r\n\r\n" not in tampon:
                bout = conn.recv(4096)
                if not bout:
                    break
                tampon += bout
            entetes, _sep, corps = tampon.partition(b"\r\n\r\n")
            longueur = 0
            for ligne in entetes.split(b"\r\n"):
                if ligne.lower().startswith(b"content-length:"):
                    longueur = int(ligne.split(b":", 1)[1])
            while len(corps) < longueur:
                bout = conn.recv(4096)
                if not bout:
                    break
                corps += bout
            self.requete_recue = entetes + b"\r\n\r\n" + corps

            pos = 0
            for taille in self.decoupe:
                conn.sendall(self.reponse[pos:pos + taille])
                pos += taille
            if pos < len(self.reponse):
                conn.sendall(self.reponse[pos:])
        finally:
            conn.close()

    def fermer(self):
        self._srv.close()
        shutil.rmtree(self.repertoire, ignore_errors=True)


def _reponse_simple(charge, statut=200):
    corps = json.dumps(charge).encode()
    return (
        f"HTTP/1.1 {statut} OK\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(corps)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode() + corps


def _reponse_par_morceaux(morceaux):
    tete = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/event-stream\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    corps = b""
    for m in morceaux:
        corps += f"{len(m):x}\r\n".encode() + m + b"\r\n"
    return tete + corps + b"0\r\n\r\n"


@tagged("post_install", "-at_install")
class TestTransport(TransactionCase):
    """La trame HTTP elle-même, sans passer par l'ORM."""

    def test_un_aller_retour_json_simple(self):
        faux = _FauxBridge(_reponse_simple({"response": "salut"}))
        self.addCleanup(faux.fermer)
        recu = transport.post(faux.chemin, "/chat", {"message": "allo"}, 5)
        self.assertEqual(recu, {"response": "salut"})
        self.assertIn(b"POST /chat HTTP/1.1", faux.requete_recue)
        self.assertIn(b'{"message": "allo"}', faux.requete_recue)

    def test_une_reponse_par_morceaux_est_recomposee(self):
        faux = _FauxBridge(_reponse_par_morceaux([b'{"a":', b'1}']))
        self.addCleanup(faux.fermer)
        self.assertEqual(transport.post(faux.chemin, "/x", {}, 5), {"a": 1})

    def test_un_statut_d_erreur_leve_une_ValueError(self):
        faux = _FauxBridge(_reponse_simple({"error": "non"}, statut=500))
        self.addCleanup(faux.fermer)
        with self.assertRaises(ValueError):
            transport.post(faux.chemin, "/x", {}, 5)

    def test_une_socket_absente_leve_avant_tout(self):
        # ⚠️ assertRaises d'Odoo n'accepte PAS un tuple d'exceptions : son
        # surchargeur fait issubclass(exc, AccessError) et lève un TypeError.
        with self.assertRaises(FileNotFoundError):
            transport.post("/tmp/bf_ai_bridge_absente.sock", "/x", {}, 1)

    # ── L'injection d'en-têtes ────────────────────────────────────────────
    #
    # La requête est bâtie à la main : un CR/LF dans un en-tête laisserait un
    # appelant en ajouter d'autres, voire un second corps. Le refus doit tomber
    # AVANT que la socket ne soit ouverte — d'où un chemin qui n'existe pas.

    def test_une_valeur_d_entete_avec_coupure_de_ligne_est_refusee(self):
        for mauvais in ("Bearer x\r\nX-Injecte: 1", "Bearer x\nX-Injecte: 1"):
            with self.assertRaises(ValueError):
                transport.post("/tmp/bf_ai_bridge_absente.sock", "/assist",
                               {"text": "allo"}, 1,
                               headers={"Authorization": mauvais})

    def test_un_nom_d_entete_avec_coupure_de_ligne_est_refuse(self):
        with self.assertRaises(ValueError):
            transport.post("/tmp/bf_ai_bridge_absente.sock", "/assist",
                           {"text": "allo"}, 1,
                           headers={"X-Mauvais\r\nInjecte": "1"})

    def test_un_entete_valide_est_bien_transmis(self):
        faux = _FauxBridge(_reponse_simple({"ok": True}))
        self.addCleanup(faux.fermer)
        transport.post(faux.chemin, "/assist", {}, 5,
                       headers={"Authorization": "Bearer jeton"})
        self.assertIn(b"Authorization: Bearer jeton", faux.requete_recue)

    # ── Le flux ───────────────────────────────────────────────────────────

    def test_le_flux_rend_les_morceaux_un_a_un(self):
        """Le découpage réseau ne doit pas décider du découpage rendu : la
        réponse arrive en trois paquets TCP et ressort en deux morceaux HTTP."""
        brut = _reponse_par_morceaux([b"event: token\n", b"data: salut\n\n"])
        coupe = len(brut) // 3
        faux = _FauxBridge(brut, decoupe=[coupe, coupe])
        self.addCleanup(faux.fermer)
        morceaux = list(transport.stream(faux.chemin, "/chat-stream", {}, 5))
        self.assertEqual(morceaux, [b"event: token\n", b"data: salut\n\n"])

    def test_le_flux_leve_sur_un_statut_d_erreur(self):
        faux = _FauxBridge(_reponse_simple({"error": "non"}, statut=502))
        self.addCleanup(faux.fermer)
        with self.assertRaises(ValueError):
            list(transport.stream(faux.chemin, "/chat-stream", {}, 5))


@tagged("post_install", "-at_install")
class TestModeleAbstrait(TransactionCase):
    """Le paramètre unique et ce que le modèle en fait."""

    def setUp(self):
        super().setUp()
        self.pont = self.env["bf.ai.bridge"]
        self.ICP = self.env["ir.config_parameter"].sudo()

    def test_sans_reglage_le_defaut_est_le_chemin_du_conteneur(self):
        self.ICP.set_param(SOCKET_PARAM, False)
        self.assertEqual(self.pont.socket_path(), transport.DEFAULT_SOCKET)

    def test_le_reglage_prime_sur_le_defaut(self):
        self.ICP.set_param(SOCKET_PARAM, "/ailleurs/b.sock")
        self.assertEqual(self.pont.socket_path(), "/ailleurs/b.sock")

    def test_disponible_suit_l_existence_de_la_socket(self):
        faux = _FauxBridge(_reponse_simple({}))
        self.addCleanup(faux.fermer)
        self.ICP.set_param(SOCKET_PARAM, faux.chemin)
        self.assertTrue(self.pont.available())
        self.ICP.set_param(SOCKET_PARAM, "/tmp/bf_ai_bridge_absente.sock")
        self.assertFalse(self.pont.available())

    def test_le_message_d_absence_nomme_le_parametre_a_corriger(self):
        """Sinon on cherche le mauvais réglage : c'est exactement le scénario
        qui a motivé l'unification."""
        self.ICP.set_param(SOCKET_PARAM, "/tmp/bf_ai_bridge_absente.sock")
        with self.assertRaises(UserError) as piege:
            self.pont.check_available()
        self.assertIn(SOCKET_PARAM, str(piege.exception))

    def test_call_passe_par_le_parametre(self):
        faux = _FauxBridge(_reponse_simple({"response": "ok"}))
        self.addCleanup(faux.fermer)
        self.ICP.set_param(SOCKET_PARAM, faux.chemin)
        self.assertEqual(self.pont.call("/chat", {"m": 1}, timeout=5),
                         {"response": "ok"})

    # ── Reprise des anciens paramètres ────────────────────────────────────

    def test_l_ancien_reglage_est_repris(self):
        self.ICP.set_param(SOCKET_PARAM, False)
        self.ICP.set_param(LEGACY_PARAMS[0], "/ancien/b.sock")
        self.assertTrue(self.pont._adopt_legacy_param())
        self.assertEqual(self.pont.socket_path(), "/ancien/b.sock")

    def test_le_second_ancien_reglage_sert_de_repli(self):
        self.ICP.set_param(SOCKET_PARAM, False)
        self.ICP.set_param(LEGACY_PARAMS[0], False)
        self.ICP.set_param(LEGACY_PARAMS[1], "/meeting/b.sock")
        self.assertTrue(self.pont._adopt_legacy_param())
        self.assertEqual(self.pont.socket_path(), "/meeting/b.sock")

    def test_une_reprise_n_ecrase_jamais_un_reglage_deja_pose(self):
        self.ICP.set_param(SOCKET_PARAM, "/choisi/b.sock")
        self.ICP.set_param(LEGACY_PARAMS[0], "/ancien/b.sock")
        self.assertFalse(self.pont._adopt_legacy_param())
        self.assertEqual(self.pont.socket_path(), "/choisi/b.sock")

    def test_sans_ancien_reglage_rien_n_est_pose(self):
        self.ICP.set_param(SOCKET_PARAM, False)
        for ancien in LEGACY_PARAMS:
            self.ICP.set_param(ancien, False)
        self.assertFalse(self.pont._adopt_legacy_param())
        self.assertEqual(self.pont.socket_path(), transport.DEFAULT_SOCKET)

    # ── Le locataire ──────────────────────────────────────────────────────

    def _oublier_le_locataire(self):
        self.ICP.set_param(TENANT_PARAM, False)
        for ancien in LEGACY_TENANT_PARAMS:
            self.ICP.set_param(ancien, False)

    def test_le_locataire_pose_est_rendu(self):
        self.ICP.set_param(TENANT_PARAM, "pme")
        self.assertEqual(self.pont.tenant(), "pme")

    def test_sans_locataire_l_appel_leve_au_lieu_de_deviner(self):
        """Le coeur de la chose : pas de défaut. Un défaut serait juste chez
        celui qui l'a écrit et servirait les données d'un autre client
        ailleurs, sans que rien ne le signale."""
        self._oublier_le_locataire()
        with self.assertRaises(UserError) as piege:
            self.pont.tenant()
        self.assertIn(TENANT_PARAM, str(piege.exception))

    def test_l_ancien_parametre_est_relu_a_chaud(self):
        """Ce module s'installe AVANT celui qui portait la valeur : l'ordre
        d'installation ne doit pas décider si un appel part bien locataire."""
        self.ICP.set_param(TENANT_PARAM, False)
        self.ICP.set_param(LEGACY_TENANT_PARAMS[0], "pme")
        self.assertEqual(self.pont.tenant(), "pme")

    def test_le_nouveau_parametre_prime_sur_l_ancien(self):
        self.ICP.set_param(LEGACY_TENANT_PARAMS[0], "bf")
        self.ICP.set_param(TENANT_PARAM, "pme")
        self.assertEqual(self.pont.tenant(), "pme")

    def test_un_locataire_tout_en_espaces_ne_compte_pas(self):
        self._oublier_le_locataire()
        self.ICP.set_param(TENANT_PARAM, "   ")
        with self.assertRaises(UserError):
            self.pont.tenant()

    def test_la_reprise_ecrit_le_locataire_sous_le_nouveau_nom(self):
        self.ICP.set_param(TENANT_PARAM, False)
        self.ICP.set_param(LEGACY_TENANT_PARAMS[0], "pme")
        self.assertTrue(self.pont._adopt_legacy_tenant())
        self.assertEqual(self.ICP.get_param(TENANT_PARAM), "pme")

    def test_la_reprise_n_ecrase_jamais_un_locataire_deja_pose(self):
        self.ICP.set_param(TENANT_PARAM, "pme")
        self.ICP.set_param(LEGACY_TENANT_PARAMS[0], "bf")
        self.assertFalse(self.pont._adopt_legacy_tenant())
        self.assertEqual(self.pont.tenant(), "pme")

    def test_sans_rien_a_reprendre_la_reprise_ne_pose_rien(self):
        self._oublier_le_locataire()
        self.assertFalse(self.pont._adopt_legacy_tenant())
        self.assertFalse(self.ICP.get_param(TENANT_PARAM))
