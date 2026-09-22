"""Captation audio : le nom, le dépôt, et les deux portes.

Aucun test ne parle à un vrai Nextcloud ni à un vrai Whisper : les primitives
WebDAV et le transcripteur sont remplacés par des doubles. Ce qu'on éprouve
n'est pas que le téléversement marche, c'est que **le nom composé est celui que
le processeur de rencontres sait lire**, et qu'un mémo ne devient jamais un
compte rendu.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_capture")
class TestCapture(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Capture = self.env["bf.capture"]
        self.ICP = self.env["ir.config_parameter"].sudo()
        self.ICP.set_param("bf_capture.folder", "Transcriptions")
        self.config = self.env["nextcloud.document.config"].create({
            "name": "Banc",
            "nextcloud_base_url": "https://nextcloud.example.com",
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "jdoe",
            "company_id": self.env.company.id,
        })
        self.puts = []
        self.dans_le_dossier = []
        self.dans_les_traites = []

    # ── doubles ───────────────────────────────────────────────────────

    def _doubles(self, dans_le_dossier=None, dans_les_traites=None):
        """Remplace PUT / PROPFIND / MKCOL par des doubles qui enregistrent.

        Les deux emplacements sont distincts : c'est la seule façon d'éprouver
        que le balayage regarde AUSSI le sous-dossier des traités.
        """
        self.dans_le_dossier = dans_le_dossier or []
        self.dans_les_traites = dans_les_traites or []
        Config = type(self.env["nextcloud.document.config"])

        def faux_put(config_self, path, content, content_type="application/octet-stream"):
            self.puts.append((path, len(content), content_type))
            return True

        def faux_propfind(config_self, path, depth="1", props=None):
            noms = self.dans_les_traites if path.endswith("Traités") else self.dans_le_dossier
            return [{"name": n, "is_dir": False} for n in noms]

        def faux_mkcol(config_self, path):
            return True

        return (
            patch.object(Config, "_webdav_put", faux_put),
            patch.object(Config, "_webdav_propfind", faux_propfind),
            patch.object(Config, "_webdav_mkcol", faux_mkcol),
        )

    def _avec_doubles(self, dans_le_dossier=None, dans_les_traites=None):
        patches = self._doubles(dans_le_dossier, dans_les_traites)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    # ── Le nom ────────────────────────────────────────────────────────

    def test_horodatage_en_heure_de_montreal(self):
        """🔴 Le piège qui a motivé le module : un appareil en NZ date d'un jour d'avance.

        La rencontre du 2026-09-21 19:00 UTC est un 1-1 de 15 h à Montréal. Le
        téléphone d'ici l'aurait nommée « 2026-09-22 06-57-16 », et le processeur
        aurait daté le compte rendu du lendemain.
        """
        self.assertEqual(
            self.Capture._horodatage(datetime(2026, 9, 21, 19, 0, 0)),
            "2026-09-21 15-00-00")

    def test_horodatage_traverse_lheure_avancee(self):
        # 2026-01-15 19:00 UTC = 14 h à Montréal (UTC-5), pas 15 h.
        self.assertEqual(
            self.Capture._horodatage(datetime(2026, 1, 15, 19, 0, 0)),
            "2026-01-15 14-00-00")

    def test_titre_propre_retire_ce_qui_casse_un_nom(self):
        self.assertEqual(
            self.Capture._titre_propre('Statutaire / hebdomadaire : suivi'),
            "Statutaire _ hebdomadaire _ suivi")

    def test_titre_propre_retire_une_date_en_tete(self):
        """Le nettoyeur du processeur mange tout motif de date : ne pas lui en offrir deux."""
        self.assertEqual(
            self.Capture._titre_propre("2026-09-21 Rencontre hebdomadaire"),
            "Rencontre hebdomadaire")

    def test_titre_propre_retire_le_pour_cent(self):
        """🔴 `%XX` ferait REFUSER le chemin par la passerelle, en ValidationError."""
        self.assertEqual(
            self.Capture._titre_propre("Marge 100%B2 et suite"),
            "Marge 100B2 et suite")

    def test_titre_propre_retire_les_caracteres_de_controle(self):
        self.assertEqual(
            self.Capture._titre_propre("Statutaire\x07 hebdomadaire"),
            "Statutaire hebdomadaire")

    def test_titre_propre_borne_a_quatre_vingts(self):
        self.assertEqual(len(self.Capture._titre_propre("x" * 200)), 80)

    def test_extension_inconnue_retombe_sur_m4a(self):
        self.assertEqual(self.Capture._extension("memo.aiff"), ".m4a")
        self.assertEqual(self.Capture._extension("rencontre.MP3"), ".mp3")
        self.assertEqual(self.Capture._extension(None), ".m4a")

    def test_nom_libre_evite_ce_qui_attend_dans_le_dossier(self):
        self._avec_doubles(dans_le_dossier=["2026-09-21 15-00-00 - Statutaire.m4a"])
        nom = self.Capture._nom_libre(
            self.config, "Transcriptions", "2026-09-21 15-00-00 - Statutaire", ".m4a")
        self.assertEqual(nom, "2026-09-21 15-00-00 - Statutaire (2).m4a")

    def test_nom_libre_evite_aussi_ce_qui_est_deja_traite(self):
        """🔴 Le surveillant déduplique par NOM, en mémoire, même déplacé dans Traités.

        Le fichier n'est plus dans le dossier : rien, côté Nextcloud, ne dirait
        que le nom est pris. Le surveillant, lui, s'en souvient jusqu'au
        redémarrage de son conteneur, et ignorerait le second dépôt en silence.
        """
        self._avec_doubles(dans_les_traites=["2026-09-21 15-00-00 - Statutaire.m4a"])
        nom = self.Capture._nom_libre(
            self.config, "Transcriptions", "2026-09-21 15-00-00 - Statutaire", ".m4a")
        self.assertEqual(nom, "2026-09-21 15-00-00 - Statutaire (2).m4a")

    def test_nom_libre_numerote_jusqu_au_premier_libre(self):
        self._avec_doubles(
            dans_le_dossier=["2026-09-21 15-00-00 - Statutaire.m4a"],
            dans_les_traites=["2026-09-21 15-00-00 - Statutaire (2).m4a"])
        nom = self.Capture._nom_libre(
            self.config, "Transcriptions", "2026-09-21 15-00-00 - Statutaire", ".m4a")
        self.assertEqual(nom, "2026-09-21 15-00-00 - Statutaire (3).m4a")

    # ── La porte « rencontre » ────────────────────────────────────────

    def _evenement(self):
        return self.env["calendar.event"].create({
            "name": "Statutaire hebdomadaire",
            "start": datetime(2026, 9, 21, 19, 0, 0),
            "stop": datetime(2026, 9, 21, 20, 0, 0),
        })

    def test_depot_nomme_depuis_levenement(self):
        self._avec_doubles()
        evenement = self._evenement()
        res = self.Capture.deposer_rencontre(
            b"x" * 1000, nom_source="enregistrement.m4a", event_id=evenement.id)
        self.assertEqual(res["nom"], "2026-09-21 15-00-00 - Statutaire hebdomadaire.m4a")
        self.assertEqual(
            self.puts[0][0],
            "Transcriptions/2026-09-21 15-00-00 - Statutaire hebdomadaire.m4a")
        self.assertEqual(self.puts[0][1], 1000)

    def test_levenement_gagne_sur_ce_que_dit_le_telephone(self):
        """Le serveur nomme d'après l'événement, pas d'après le titre envoyé."""
        self._avec_doubles()
        evenement = self._evenement()
        res = self.Capture.deposer_rencontre(
            b"x" * 10, nom_source="a.m4a", event_id=evenement.id,
            titre="Autre chose", debut="2020-01-01 00:00:00")
        self.assertIn("Statutaire hebdomadaire", res["nom"])
        self.assertTrue(res["nom"].startswith("2026-09-21 15-00-00"))

    def test_depot_libre_accepte_un_titre_et_une_heure(self):
        self._avec_doubles()
        res = self.Capture.deposer_rencontre(
            b"x" * 10, nom_source="a.m4a",
            titre="Rencontre chez le notaire", debut="2026-09-21T19:00:00Z")
        self.assertEqual(res["nom"], "2026-09-21 15-00-00 - Rencontre chez le notaire.m4a")

    def test_depot_sans_titre_est_refuse(self):
        """Sans nom, le routage plafonne à 0,30 pour un seuil de 0,50."""
        self._avec_doubles()
        with self.assertRaises(UserError):
            self.Capture.deposer_rencontre(b"x" * 10, nom_source="a.m4a")
        self.assertEqual(self.puts, [])

    def test_depot_trop_gros_est_refuse_avant_le_put(self):
        self._avec_doubles()
        self.ICP.set_param("bf_capture.max_bytes", "100")
        with self.assertRaises(UserError):
            self.Capture.deposer_rencontre(
                b"x" * 101, nom_source="a.m4a", titre="Trop gros",
                debut="2026-09-21 19:00:00")
        self.assertEqual(self.puts, [])

    def test_depot_sans_configuration_nextcloud_est_refuse(self):
        self._avec_doubles()
        self.env["nextcloud.document.config"].search([]).write({"active": False})
        with self.assertRaises(UserError):
            self.Capture.deposer_rencontre(
                b"x" * 10, nom_source="a.m4a", titre="Sans dépôt",
                debut="2026-09-21 19:00:00")

    def test_debut_illisible_est_refuse(self):
        self._avec_doubles()
        with self.assertRaises(UserError):
            self.Capture.deposer_rencontre(
                b"x" * 10, nom_source="a.m4a", titre="Titre", debut="hier soir")

    def test_evenement_inexistant_est_refuse(self):
        self._avec_doubles()
        with self.assertRaises(UserError):
            self.Capture.deposer_rencontre(
                b"x" * 10, nom_source="a.m4a", event_id=999999999)

    # ── La porte « mémo » ─────────────────────────────────────────────

    def test_memo_sans_transcription_garde_laudio(self):
        with patch.object(type(self.Capture), "transcription_disponible",
                          lambda self: False):
            res = self.Capture.deposer_memo(b"x" * 500, nom_source="memo.m4a")
        note = self.env["bf.note"].browse(res["note_id"])
        self.assertTrue(note.exists())
        self.assertFalse(res["transcrit"])
        self.assertIn("sans transcription", note.body)
        pieces = self.env["ir.attachment"].search(
            [("res_model", "=", "bf.note"), ("res_id", "=", note.id)])
        self.assertEqual(len(pieces), 1)
        # ⚠️ Une pièce jointe posée avec res_id n'est pas dans le fil : c'est le
        # message qui la rend visible.
        self.assertTrue(any(pieces[0] in m.attachment_ids for m in note.message_ids))

    def test_memo_transcrit_porte_le_texte_et_son_titre(self):
        with patch.object(type(self.Capture), "transcription_disponible",
                          lambda self: True), \
             patch.object(type(self.env["bf.speech.transcriber"]), "transcribe",
                          lambda self, data, filename=None, language=None:
                          "Rappeler l'équipe demain. Voir le dossier du trimestre."):
            res = self.Capture.deposer_memo(b"x" * 500, nom_source="memo.m4a")
        note = self.env["bf.note"].browse(res["note_id"])
        self.assertTrue(res["transcrit"])
        self.assertEqual(note.name, "Rappeler l'équipe demain.")
        self.assertIn("Rappeler l'équipe demain", note.body)

    def test_le_memo_survit_a_un_fil_qui_refuse(self):
        """🔴 Relevé en production : `message_post` exige une adresse à l'auteur.

        Un compte sans courriel faisait échouer tout le dépôt, après la
        création de la note et de la pièce. Le mémo doit survivre : c'est le
        message qui est accessoire, pas l'audio.
        """
        from odoo.exceptions import UserError as Erreur

        def refuse(self, *a, **k):
            raise Erreur("Impossible d'envoyer le message")

        with patch.object(type(self.Capture), "transcription_disponible",
                          lambda self: False), \
             patch.object(type(self.env["bf.note"]), "message_post", refuse):
            res = self.Capture.deposer_memo(b"x" * 500, nom_source="memo.m4a")
        note = self.env["bf.note"].browse(res["note_id"])
        self.assertTrue(note.exists())
        pieces = self.env["ir.attachment"].search(
            [("res_model", "=", "bf.note"), ("res_id", "=", note.id)])
        self.assertEqual(len(pieces), 1, "l'audio doit rester attaché à la note")

    def test_memo_trop_long_renvoie_vers_lautre_porte(self):
        self.ICP.set_param("bf_capture.memo_max_bytes", "100")
        with self.assertRaises(UserError) as capture:
            self.Capture.deposer_memo(b"x" * 101, nom_source="memo.m4a")
        self.assertIn("rencontre", str(capture.exception))

    def test_memo_ne_cree_jamais_de_compte_rendu(self):
        """La porte « mémo » ne touche ni au dossier surveillé, ni aux rencontres."""
        self._avec_doubles()
        avant = self.env["bf.note"].search_count([])
        with patch.object(type(self.Capture), "transcription_disponible",
                          lambda self: False):
            self.Capture.deposer_memo(b"x" * 100, nom_source="memo.m4a")
        self.assertEqual(self.puts, [])
        self.assertEqual(self.env["bf.note"].search_count([]), avant + 1)

    # ── Les cibles ────────────────────────────────────────────────────

    def test_cibles_annoncent_le_nom_qui_sera_pose(self):
        depart = datetime.utcnow() + timedelta(hours=1)
        evenement = self.env["calendar.event"].create({
            "name": "Session de travail de l'équipe",
            "start": depart,
            "stop": depart + timedelta(hours=1),
        })
        cibles = self.Capture.cibles()
        trouvee = [c for c in cibles if c["id"] == evenement.id]
        self.assertTrue(trouvee, "la rencontre de la prochaine heure doit être offerte")
        self.assertEqual(
            trouvee[0]["nom_fichier"],
            "%s - Session de travail de l'équipe" % self.Capture._horodatage(depart))

    def test_cibles_ignorent_ce_qui_est_hors_fenetre(self):
        loin = datetime.utcnow() + timedelta(days=5)
        evenement = self.env["calendar.event"].create({
            "name": "Rencontre lointaine",
            "start": loin,
            "stop": loin + timedelta(hours=1),
        })
        self.assertFalse([c for c in self.Capture.cibles() if c["id"] == evenement.id])

    # ── Capacité ──────────────────────────────────────────────────────

    def test_capacite_suit_la_configuration(self):
        self.assertTrue(self.Capture.is_configured())
        self.env["nextcloud.document.config"].search([]).write({"active": False})
        self.assertFalse(self.Capture.is_configured())
