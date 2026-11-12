"""Les portes de la captation, et la preuve qu'elles sont fermées.

🔴 Une relecture adverse a trouvé que toutes les méthodes publiques de
``bf.capture`` s'appelaient par ``/web/dataset/call_kw``, route ouverte à tout
usager authentifié, portail compris. Un ``AbstractModel`` n'a pas de table :
aucun contrôle d'accès ne se déclenche au passage. ``deposer_rencontre`` posait
ainsi, au nom du compte de service, le fichier et le titre qu'un portail
choisissait dans le dossier surveillé du processeur de rencontres, en
contournant la garde « usager interne » des routes mobiles.

Chaque essai vérifie une moitié du correctif : la porte RPC est fermée, la garde
du modèle refuse un portail, et le chemin des routes marche toujours.

Les bornes du disque sont ici aussi, et depuis la 18.0.1.4.3 elles sont
éprouvées sur ce qui les faisait céder (remarques de relecture) : une
fiche sans ses octets, un réglage illisible, deux téléversements qui se
promettent le même disque, un refus d'espace qui coûtait un enregistrement.
"""
import inspect
import os
import time
from collections import namedtuple
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.service.model import get_public_method
from odoo.tests import new_test_user
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.base.models.ir_autovacuum import is_autovacuum

from ..models import televersement as televersement_module
from ..models.bf_capture import ServiceIndisponible

# Les dix méthodes publiques connues au 18.0.1.4.2. Elles ne servent plus de
# liste à vérifier (l'énumération le fait) mais de TÉMOIN : une énumération qui
# ne les voit plus ne mesure plus rien.
PUBLIQUES = ("is_configured", "transcription_disponible", "cibles",
             "deposer_rencontre", "deposer_memo", "televersement_ouvrir",
             "televersement_morceau", "televersement_etat",
             "televersement_terminer", "televersement_abandonner")

GIO = 1024 * 1024 * 1024

Disque = namedtuple("Disque", "total used free")


def _methodes_publiques(modele):
    """Les méthodes publiques que CE module pose sur le modèle, énumérées.

    🔴 Une liste en dur ne protège que les méthodes qu'on a déjà regardées
    (remarque de relecture). La relecture du 2026-10-08 en a trouvé dix
    ouvertes par RPC ; la onzième, écrite plus tard, aurait passé un essai qui
    récite une liste. L'énumération part donc des classes du module, et la liste
    connue ne sert plus qu'à prouver que l'énumération voit ce qu'on sait déjà.

    Les classes d'ailleurs (``models.AbstractModel`` et ses parents) sont hors
    sujet : leurs méthodes publiques sont celles de l'ORM, et c'est à Odoo de
    dire lesquelles s'appellent à distance.
    """
    noms = set()
    for classe in type(modele).__mro__:
        if not (classe.__module__ or "").startswith("odoo.addons.bf_capture"):
            continue
        for nom, attribut in vars(classe).items():
            if nom.startswith("_") or not callable(attribut):
                continue
            noms.add(nom)
    return noms


@tagged("post_install", "-at_install", "bf_capture")
class TestGardesRpc(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.interne = new_test_user(cls.env, login="capture-gardes-interne",
                                    groups="base.group_user")
        cls.portail = new_test_user(cls.env, login="capture-gardes-portail",
                                    groups="base.group_portal")
        cls.Capture = cls.env["bf.capture"]

    def setUp(self):
        super().setUp()
        # La préparation touche Nextcloud et le calendrier : hors sujet ici.
        rustine = patch.object(type(self.Capture), "_preparer_rencontre",
                               lambda self, *a, **k: {})
        rustine.start()
        self.addCleanup(rustine.stop)
        self.ouverts = []
        self.addCleanup(self._nettoyer)

    def _nettoyer(self):
        for upload_id in self.ouverts:
            for chemin in self.Capture._chemins(upload_id):
                if os.path.exists(chemin):
                    os.remove(chemin)

    def _ouvrir(self, usager, taille=1000):
        reponse = self.Capture.with_user(usager).televersement_ouvrir(
            "rencontre.m4a", taille, titre="Essai")
        self.ouverts.append(reponse["upload_id"])
        return reponse["upload_id"]

    # ── La porte RPC ──────────────────────────────────────────────────

    def test_aucune_methode_ne_s_appelle_par_rpc(self):
        methodes = _methodes_publiques(self.Capture)
        manquantes = sorted(set(PUBLIQUES) - methodes)
        self.assertFalse(
            manquantes,
            f"l'énumération ne voit plus {manquantes} : elle ne mesure rien")
        for methode in sorted(methodes):
            with self.subTest(methode=methode), self.assertRaises(AccessError):
                get_public_method(self.Capture, methode)

    # ── La garde du modèle ────────────────────────────────────────────

    def test_un_portail_ne_depose_pas_de_rencontre(self):
        with self.assertRaises(AccessError):
            self.Capture.with_user(self.portail).deposer_rencontre(b"son", titre="Essai")

    def test_un_portail_ne_fait_pas_tourner_la_dictee(self):
        Modele = type(self.Capture)
        with patch.object(Modele, "transcription_disponible", lambda self: True), \
                patch.object(Modele, "_transcrire") as dictee, \
                self.assertRaises(AccessError):
            self.Capture.with_user(self.portail).deposer_memo(b"son", nom_source="memo.m4a")
        dictee.assert_not_called()

    def test_un_portail_n_ouvre_pas_de_televersement(self):
        with self.assertRaises(AccessError):
            self.Capture.with_user(self.portail).televersement_ouvrir(
                "rencontre.m4a", 1000, titre="Essai")

    def test_un_interne_ouvre_toujours(self):
        upload_id = self._ouvrir(self.interne)
        etat = self.Capture.with_user(self.interne).televersement_etat(upload_id)
        self.assertEqual(etat["recus"], 0)

    # ── Les bornes du disque ──────────────────────────────────────────

    def test_le_plus_ancien_cede_la_place(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_capture.televersement_max_ouverts", "3")
        premiers = [self._ouvrir(self.interne) for _i in range(3)]
        # L'ordre se lit à la date des fiches : on vieillit la première.
        _part, meta = self.Capture._chemins(premiers[0])
        os.utime(meta, (time.time() - 3600, time.time() - 3600))
        self._ouvrir(self.interne)
        ouverts = self.Capture._ouverts_de(self.interne.id)
        self.assertEqual(len(ouverts), 3)
        self.assertNotIn(premiers[0], ouverts)

    def test_un_disque_plein_est_une_panne_pas_un_refus(self):
        with patch.object(televersement_module.shutil, "disk_usage",
                          lambda chemin: Disque(10, 10, 1000)), \
                self.assertRaises(ServiceIndisponible):
            self._ouvrir(self.interne)

    def test_un_refus_d_espace_ne_coute_pas_le_plus_ancien(self):
        """🔴 L'éviction se faisait AVANT le contrôle du disque : un 503 coûtait
        quand même l'enregistrement le plus ancien de la personne."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_capture.televersement_max_ouverts", "1")
        premier = self._ouvrir(self.interne)
        with patch.object(televersement_module.shutil, "disk_usage",
                          lambda chemin: Disque(10, 10, 1000)), \
                self.assertRaises(ServiceIndisponible):
            self._ouvrir(self.interne)
        self.assertIn(premier, self.Capture._ouverts_de(self.interne.id))
        for chemin in self.Capture._chemins(premier):
            self.assertTrue(os.path.exists(chemin), "le plus ancien a payé le refus")

    def test_l_espace_promis_est_reserve(self):
        """🔴 Deux ouvertures promettaient le même disque : le contrôle ne
        réservait rien, et ``televersement_morceau`` ne revérifiait pas."""
        def disque(chemin):
            return Disque(4 * GIO, GIO, 3 * GIO)
        # ⚠️ Le dossier des morceaux est PARTAGÉ par toute la base : les essais
        # HTTP d'à côté y laissent leurs téléversements. Le compte se lit donc
        # en écart, jamais en valeur absolue.
        with patch.object(televersement_module.shutil, "disk_usage", disque):
            avant = self.Capture._octets_promis()
            self._ouvrir(self.interne, taille=int(1.5 * GIO))
            self.assertEqual(self.Capture._octets_promis() - avant, int(1.5 * GIO))
            with self.assertRaises(ServiceIndisponible):
                self._ouvrir(self.interne, taille=int(1.5 * GIO))
            # Le témoin : sans la réserve, l'espace LIBRE couvrait le second.
            with patch.object(type(self.Capture), "_octets_promis",
                              lambda self, ignorer=(): 0):
                self._ouvrir(self.interne, taille=int(1.5 * GIO))

    def test_un_morceau_refuse_quand_le_disque_a_fondu(self):
        upload_id = self._ouvrir(self.interne)
        with patch.object(televersement_module.shutil, "disk_usage",
                          lambda chemin: Disque(10, 10, 1024)), \
                self.assertRaises(ServiceIndisponible):
            self.Capture.with_user(self.interne).televersement_morceau(
                upload_id, 0, b"x" * 10)
        etat = self.Capture.with_user(self.interne).televersement_etat(upload_id)
        self.assertEqual(etat["recus"], 0, "le morceau refusé a quand même été écrit")

    # ── La fiche et ses octets ────────────────────────────────────────

    def test_une_fiche_sans_ses_octets_est_un_refus(self):
        """🔴 C'était un 500 à chaque morceau, et le téléversement restait
        bloqué jusqu'à la purge : ``_lire_meta`` ne regardait que la fiche."""
        upload_id = self._ouvrir(self.interne)
        part, _meta = self.Capture._chemins(upload_id)
        os.remove(part)
        Capture = self.Capture.with_user(self.interne)
        with self.assertRaises(UserError):
            Capture.televersement_etat(upload_id)
        with self.assertRaises(UserError):
            Capture.televersement_morceau(upload_id, 0, b"x")
        with self.assertRaises(UserError):
            Capture.televersement_terminer(upload_id)

    def test_l_effacement_retire_la_fiche_d_abord(self):
        """L'ordre EST la correction : sans fiche, c'est « introuvable » ; avec
        la fiche et sans les octets, c'était un 500."""
        upload_id = self._ouvrir(self.interne)
        part, meta = self.Capture._chemins(upload_id)
        ordre = []
        vrai_remove = os.remove

        def espion(chemin):
            ordre.append(os.path.basename(chemin))
            return vrai_remove(chemin)

        with patch.object(televersement_module.os, "remove", espion):
            self.Capture.with_user(self.interne).televersement_abandonner(upload_id)
        self.assertEqual(ordre, [os.path.basename(meta), os.path.basename(part)])

    def test_un_part_orphelin_finit_par_partir(self):
        """Une ouverture coupée entre ses deux écritures laisse des octets que
        personne ne peut reprendre."""
        upload_id = self._ouvrir(self.interne)
        part, meta = self.Capture._chemins(upload_id)
        os.remove(meta)
        vieux = time.time() - 49 * 3600
        os.utime(part, (vieux, vieux))
        self.Capture._purger_abandonnes()
        self.assertFalse(os.path.exists(part))

    # ── Le ménage quotidien ───────────────────────────────────────────

    def test_le_menage_quotidien_trouve_la_purge(self):
        """🔴 Appeler la purge soi-même ne prouve pas qu'Odoo l'appelle.

        ``ir.autovacuum._run_vacuum_cleaner`` énumère les membres de la CLASSE
        du registre avec ``is_autovacuum`` — c'est cette énumération-là qu'on
        interroge, pas la nôtre. Un décorateur perdu dans un remaniement
        passerait l'essai d'à côté sans que plus rien ne purge.
        """
        classe = self.env.registry["bf.capture"]
        trouvees = [nom for nom, _f in inspect.getmembers(classe, is_autovacuum)]
        self.assertIn("_gc_televersements_abandonnes", trouvees)

    def test_le_menage_quotidien_purge_l_abandonne(self):
        upload_id = self._ouvrir(self.interne)
        vieux = time.time() - 49 * 3600
        for chemin in self.Capture._chemins(upload_id):
            os.utime(chemin, (vieux, vieux))
        self.Capture._gc_televersements_abandonnes()
        for chemin in self.Capture._chemins(upload_id):
            self.assertFalse(os.path.exists(chemin))

    def test_un_morceau_refuse_ne_rajeunit_pas_la_fiche(self):
        """🔴 Un refus n'est pas une activité, et c'était le seul mode de panne
        NEUF du lot 18.0.1.4.3 (relecture adverse du 2026-11-12).

        Le rajeunissement se faisait avant le contrôle d'espace. Sous la marge,
        chaque morceau refusait en 503, l'app rejouait sans plafond, la fiche ne
        vieillissait donc jamais, la purge de 48 h ne la ramassait jamais, et ses
        octets non écrits restaient promis — ce qui refusait l'ouverture de TOUT
        LE MONDE, ``_octets_promis`` comptant tous les usagers.
        """
        upload_id = self._ouvrir(self.interne)
        _part, meta = self.Capture._chemins(upload_id)
        vieux = time.time() - 47 * 3600
        os.utime(meta, (vieux, vieux))
        with patch.object(televersement_module.shutil, "disk_usage",
                          lambda chemin: Disque(10, 10, 1024)), \
                self.assertRaises(ServiceIndisponible):
            self.Capture.with_user(self.interne).televersement_morceau(
                upload_id, 0, b"x" * 10)
        self.assertLess(os.path.getmtime(meta), vieux + 60,
                        "un morceau refusé a rajeuni la fiche")

    def test_abandonner_nettoie_une_fiche_orpheline(self):
        """Exiger les deux fichiers ne doit pas empêcher de NETTOYER.

        Une fiche sans ses octets tiendrait sinon un des trois créneaux de la
        personne jusqu'à la purge de 48 h, sans que rien ne puisse l'effacer.
        """
        upload_id = self._ouvrir(self.interne)
        part, meta = self.Capture._chemins(upload_id)
        os.remove(part)
        self.assertTrue(
            self.Capture.with_user(self.interne).televersement_abandonner(upload_id))
        self.assertFalse(os.path.exists(meta))
        self.assertNotIn(upload_id, self.Capture._ouverts_de(self.interne.id))

    def test_un_abandon_ne_touche_pas_celui_d_un_autre(self):
        """Le témoin de l'essai d'à côté : tolérer l'absence des octets ne
        desserre pas la garde d'identité."""
        upload_id = self._ouvrir(self.interne)
        part, _meta = self.Capture._chemins(upload_id)
        os.remove(part)
        with self.assertRaises(AccessError):
            self.Capture.with_user(self.portail).televersement_abandonner(upload_id)

    def test_un_fichier_etranger_ne_bloque_pas_l_ouverture(self):
        """``_ouverts_de`` rendait son nom à ``_chemins``, qui REFUSE tout ce qui
        n'est pas un identifiant.

        Le déclencheur n'est pas le fichier seul : il faut que l'éviction aille
        le chercher, donc qu'il soit dans les plus anciens quand le plafond est
        atteint. Un reste d'une version antérieure, ou une copie de secours
        posée là, refusait alors l'ouverture de tout le monde.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_capture.televersement_max_ouverts", "3")
        dossier = self.Capture._dossier_televersements()
        intrus = os.path.join(dossier, "sauvegarde.json")
        with open(intrus, "w", encoding="utf-8") as f:
            f.write('{"uid": %s, "taille": 1}' % self.interne.id)
        self.addCleanup(lambda: os.path.exists(intrus) and os.remove(intrus))
        vieux = time.time() - 7200
        os.utime(intrus, (vieux, vieux))
        premiers = [self._ouvrir(self.interne) for _i in range(3)]
        quatrieme = self._ouvrir(self.interne)
        ouverts = self.Capture._ouverts_de(self.interne.id)
        self.assertIn(quatrieme, ouverts)
        self.assertEqual(len(ouverts), 3)
        self.assertNotIn(premiers[0], ouverts, "l'éviction a visé l'intrus")
        self.assertTrue(os.path.exists(intrus), "l'intrus a été effacé")

    def test_une_ouverture_impossible_est_une_panne(self):
        """Un dossier qui n'accepte plus l'écriture est une panne du serveur, pas
        un refus de ce que le téléphone a envoyé : en 500, l'app retirerait
        l'enregistrement de sa file."""
        avant = set(os.listdir(self.Capture._dossier_televersements()))
        with patch.object(televersement_module.json, "dump",
                          side_effect=OSError("No space left on device")), \
                self.assertRaises(ServiceIndisponible):
            self.Capture.with_user(self.interne).televersement_ouvrir(
                "rencontre.m4a", 1000, titre="Essai")
        apres = set(os.listdir(self.Capture._dossier_televersements()))
        self.assertEqual(apres, avant, "l'ouverture ratée a laissé ses fichiers")

    def test_l_ecran_des_reglages_survit_a_un_reglage_illisible(self):
        """🔴 ``default_get`` finit par ``res.update(self.get_values())`` : un
        ``int()`` nu dans notre façade faisait lever TOUT l'écran des Paramètres
        généraux, tous modules confondus, c'est-à-dire le seul endroit où un
        administrateur irait réparer la valeur."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_capture.max_bytes", "cent vingt-huit")
        Reglages = self.env["res.config.settings"]
        self.assertEqual(Reglages.get_values()["bf_capture_max_bytes"],
                         128 * 1024 * 1024)
        defauts = Reglages.default_get(["bf_capture_max_bytes"])
        self.assertEqual(defauts["bf_capture_max_bytes"], 128 * 1024 * 1024)
        self.assertTrue(Reglages.create({}))

    def test_un_morceau_rajeunit_la_fiche(self):
        upload_id = self._ouvrir(self.interne)
        _part, meta = self.Capture._chemins(upload_id)
        vieux = time.time() - 47 * 3600
        os.utime(meta, (vieux, vieux))
        self.Capture.with_user(self.interne).televersement_morceau(upload_id, 0, b"x" * 10)
        self.assertGreater(os.path.getmtime(meta), vieux + 3600)

    def test_un_identifiant_non_textuel_est_un_refus(self):
        for brut in (123, None, "a" * 32 + "\n", "../" + "a" * 29):
            with self.subTest(brut=brut), self.assertRaises(UserError):
                self.Capture.with_user(self.interne).televersement_etat(brut)

    # ── Les réglages ──────────────────────────────────────────────────

    def test_un_reglage_illisible_n_est_pas_une_panne(self):
        """🔴 Une valeur non numérique rendait un 500 à CHAQUE ouverture."""
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_capture.televersement_max_ouverts", "trois")
        ICP.set_param("bf_capture.televersement_max_bytes", "4 Gio")
        ICP.set_param("bf_capture.max_bytes", "cent vingt-huit")
        upload_id = self._ouvrir(self.interne)
        etat = self.Capture.with_user(self.interne).televersement_etat(upload_id)
        self.assertEqual(etat["recus"], 0)
        self.assertEqual(self.Capture._televersement_max(),
                         televersement_module._DEFAULT_TELEVERSEMENT_MAX)
        self.assertEqual(
            self.Capture._param_entier("bf_capture.televersement_max_ouverts", 3), 3)
        self.assertEqual(self.Capture._settings()["max_bytes"], 128 * 1024 * 1024)

    def test_un_plafond_a_zero_ne_ferme_pas_la_porte(self):
        """Un réglage à 0 ou négatif se lit comme le minimum, pas comme « rien
        n'est permis » : un zéro posé par erreur fermerait le magnétophone."""
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_capture.televersement_max_ouverts", "0")
        self.assertEqual(
            self.Capture._param_entier("bf_capture.televersement_max_ouverts", 3), 1)
        premier = self._ouvrir(self.interne)
        self._ouvrir(self.interne)
        self.assertNotIn(premier, self.Capture._ouverts_de(self.interne.id))
