# -*- coding: utf-8 -*-
"""Un déplacement garde la salle Talk.

Le cas d'origine, vu sur une réservation réelle : le client confirme son
heure, la déplace, puis renvoie le même créneau une minute plus tard. Trois
POST sur `/confirm`, trois salles Talk pour une seule rencontre. Les courriels
successifs menaient chacun à une salle différente, et la rencontre a eu lieu
dans une salle que la réservation ne citait plus.

Ce que ces essais tiennent :

* la réservation qui a déjà SA salle la garde, renommée à la nouvelle heure ;
* elle en reçoit une neuve seulement si Nextcloud dit que l'ancienne n'existe
  plus (404), si elle était sur la salle de repli, ou sur le lien fixe du type
  (une salle PARTAGÉE, qu'on ne renomme jamais) ;
* un renommage qui échoue autrement ne change pas le lien ;
* le même créneau renvoyé ne fait rien : ni salle, ni courriel.

Et aussi : salle neuve quand l'hôte change ; jamais de reprise
à la première confirmation, ni du lien fixe d'un autre type, ni d'une salle
qu'une autre réservation porte écrite autrement (comparaison par jeton) ; un
lien court Shlink reste le lien et suit la salle neuve.
"""

import re
from datetime import timedelta
from unittest.mock import patch

import pytz

from odoo import Command, fields
from odoo.tests import HttpCase, TransactionCase, tagged

BASE = "https://nc.test.invalid"


class _Reponse:
    def __init__(self, status_code=200, charge=None):
        self.status_code = status_code
        self._charge = charge if charge is not None else {"ocs": {"data": {}}}
        self.text = ""

    def json(self):
        return self._charge

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception("HTTP %s" % self.status_code)


class _FauxNextcloud:
    """Nextcloud (et Shlink) simulés : chaque POST de salle rend un jeton neuf.

    Les participants ajoutés et promus sont retenus par salle, comme dans
    `test_talk_moderation.py` : c'est ce qui dit QUI modère une salle.
    """

    _compte = 0

    def __init__(self, statut_renommage=200, renommage_leve=False,
                 statut_repointage=200):
        # Jetons uniques sur toute la classe : deux réservations d'un même
        # essai ne doivent jamais recevoir la même salle simulée.
        _FauxNextcloud._compte += 1
        self.prefixe = "n%d" % _FauxNextcloud._compte
        self.salles = []
        self.renommages = []
        self.statut_renommage = statut_renommage
        self.renommage_leve = renommage_leve
        self.creation_echoue = False
        self.participants = {}
        self.moderateurs = []
        self.repointages = []
        self.statut_repointage = statut_repointage

    @staticmethod
    def _jeton(url):
        return url.split("/room/", 1)[1].split("/", 1)[0]

    def post(self, url, **kw):
        if url.endswith("/api/v4/room"):
            if self.creation_echoue:
                return _Reponse(503)
            jeton = "%ssalle%d" % (self.prefixe, len(self.salles) + 1)
            self.salles.append((jeton, (kw.get("data") or {}).get("roomName")))
            return _Reponse(200, {"ocs": {"data": {"token": jeton}}})
        if url.endswith("/participants"):
            self.participants.setdefault(self._jeton(url), []).append(
                kw["data"]["newParticipant"])
            return _Reponse(200)
        if url.endswith("/moderators"):
            jeton = self._jeton(url)
            self.moderateurs.append(
                (jeton, self.participants[jeton][kw["data"]["attendeeId"] - 100]))
            return _Reponse(200)
        return _Reponse(200)

    def get(self, url, **kw):
        if url.endswith("/participants"):
            return _Reponse(200, {"ocs": {"data": [
                {"actorType": "users", "actorId": login, "attendeeId": 100 + i}
                for i, login in enumerate(self.participants.get(self._jeton(url), []))
            ]}})
        return _Reponse(404)

    def put(self, url, **kw):
        if self.renommage_leve:
            raise ConnectionError("Nextcloud injoignable")
        self.renommages.append((url.rsplit("/", 1)[-1], (kw.get("data") or {}).get("roomName")))
        return _Reponse(self.statut_renommage)

    def patch(self, url, **kw):
        self.repointages.append((url.rsplit("/", 1)[-1], (kw.get("json") or {}).get("longUrl")))
        return _Reponse(self.statut_repointage)

    def __enter__(self):
        self._patches = [
            patch("requests.%s" % verbe, side_effect=getattr(self, verbe))
            for verbe in ("post", "get", "put", "patch")
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


def _monter_type(env, nom):
    attendances = [
        Command.create({
            "name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
            "hour_to": 24.0, "day_period": "morning",
        })
        for d in range(7)
    ]
    calendrier = env["resource.calendar"].create({
        "name": "24/7 %s" % nom, "attendance_ids": attendances, "tz": "UTC"})
    ressource = env["resource.resource"].create({
        "name": "Ressource %s" % nom, "calendar_id": calendrier.id,
        "resource_type": "material", "tz": "UTC"})
    combinaison = env["resource.booking.combination"].create({
        "resource_ids": [Command.set([ressource.id])]})
    ICP = env["ir.config_parameter"].sudo()
    ICP.set_param("bf_appointment.nc_talk_base_url", BASE)
    ICP.set_param("bf_appointment.nc_talk_user", "fox-bot")
    ICP.set_param("bf_appointment.nc_talk_password_encrypted", "chiffre")
    ICP.set_param("bf_appointment.nc_talk_moderators", "")
    ICP.set_param("bf_appointment.nc_talk_generic_room_url", "")
    ICP.set_param("bf_appointment.shlink_base_url", "")
    return env["resource.booking.type"].create({
        "name": "Type %s" % nom, "slug": "type-%s" % nom,
        "duration": 1.0, "slot_duration": 1.0,
        "modifications_deadline": 0.0, "combination_assignment": "sorted",
        "resource_calendar_id": calendrier.id,
        "video_provider": "nextcloud_talk",
        "requires_recording_consent": False,
        "combination_rel_ids": [
            Command.create({"sequence": 0, "combination_id": combinaison.id})],
    })


@tagged("bf_appointment", "bf_appointment_video", "post_install", "-at_install")
class TestSalleGardeeAuDeplacement(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        cls.type_rdv = _monter_type(cls.env, "salle-27106")
        cls.partner = cls.env["res.partner"].create({
            "name": "Client 27106", "email": "client27106@test.invalid"})

    def setUp(self):
        super().setUp()
        Modele = type(self.env["resource.booking"])
        dechiffre = patch.object(
            Modele, "_decrypt_nc_talk_password", lambda self, enc: "secret")
        dechiffre.start()
        self.addCleanup(dechiffre.stop)

    def _creneaux(self, n=6):
        now = fields.Datetime.context_timestamp(
            self.env["resource.booking"], fields.Datetime.now())
        creneaux = self.type_rdv._bf_candidate_slots(
            now + timedelta(hours=1), now + timedelta(days=7), limit=n)
        self.assertGreaterEqual(len(creneaux), n)
        return [c.astimezone(pytz.utc).replace(tzinfo=None) for c in creneaux]

    def _confirmee(self, nc):
        debut = self._creneaux()[0]
        resa = self.type_rdv._bf_create_booking(
            debut, partners=self.partner, confirm=False)
        resa.action_confirm()
        self.assertEqual(len(nc.salles), 1, "la première confirmation crée la salle")
        self.assertEqual(resa.videocall_location, "%s/index.php/call/%ssalle1" % (BASE, nc.prefixe))
        return resa

    def _deplacer(self, resa):
        resa.start = self._creneaux()[4]
        resa.action_confirm()

    def test_premiere_confirmation_cree_la_salle_sans_renommer(self):
        with _FauxNextcloud() as nc:
            self._confirmee(nc)
        self.assertEqual(nc.renommages, [], "rien à renommer à la création")

    def test_deplacement_garde_la_salle_et_la_renomme(self):
        """Le cas d'origine : le lien reçu reste le bon."""
        with _FauxNextcloud() as nc:
            resa = self._confirmee(nc)
            lien = resa.videocall_location
            self._deplacer(resa)
        self.assertEqual(len(nc.salles), 1, "aucune salle de plus au déplacement")
        self.assertEqual(resa.videocall_location, lien)
        self.assertEqual(resa.meeting_id.videocall_location, lien)
        self.assertEqual(len(nc.renommages), 1)
        jeton, nom = nc.renommages[0]
        self.assertEqual(jeton, nc.prefixe + "salle1")
        self.assertEqual(nom, resa._nc_talk_room_name())
        self.assertIn(resa.start.strftime("%Y-%m-%d"), nom,
                      "la salle porte la nouvelle date")

    def test_salle_disparue_en_cree_une_neuve(self):
        with _FauxNextcloud(statut_renommage=404) as nc:
            resa = self._confirmee(nc)
            self._deplacer(resa)
        self.assertEqual(len(nc.salles), 2)
        self.assertEqual(resa.videocall_location, "%s/index.php/call/%ssalle2" % (BASE, nc.prefixe))

    def test_renommage_refuse_garde_le_lien(self):
        for statut in (403, 500):
            with self.subTest(statut=statut), _FauxNextcloud(statut_renommage=statut) as nc:
                resa = self._confirmee(nc)
                lien = resa.videocall_location
                self._deplacer(resa)
                self.assertEqual(len(nc.salles), 1)
                self.assertEqual(resa.videocall_location, lien)

    def test_nextcloud_injoignable_garde_le_lien(self):
        with _FauxNextcloud(renommage_leve=True) as nc:
            resa = self._confirmee(nc)
            lien = resa.videocall_location
            self._deplacer(resa)
        self.assertEqual(len(nc.salles), 1)
        self.assertEqual(resa.videocall_location, lien)

    def test_salle_de_repli_retente_une_salle_dediee(self):
        with _FauxNextcloud() as nc:
            resa = self._confirmee(nc)
            resa.write({
                "videocall_location": "https://l.test.invalid/rdv-x",
                "video_fallback_active": True,
            })
            self._deplacer(resa)
        self.assertEqual(nc.renommages, [])
        self.assertEqual(len(nc.salles), 2)
        self.assertFalse(resa.video_fallback_active)

    def test_lien_fixe_du_type_n_est_jamais_renomme(self):
        """La salle du type est PARTAGÉE : la renommer l'afficherait à tous."""
        commune = "%s/index.php/call/commune" % BASE
        self.type_rdv.videocall_location = commune
        with _FauxNextcloud() as nc:
            resa = self._confirmee(nc)
            resa.videocall_location = commune
            self._deplacer(resa)
        self.assertEqual(nc.renommages, [])
        self.assertEqual(len(nc.salles), 2)

    def test_salle_d_une_autre_reservation_n_est_jamais_reprise(self):
        """Copie au back-office : le lien suit la copie, la salle reste à l'original."""
        with _FauxNextcloud() as nc:
            resa = self._confirmee(nc)
            copie = resa.copy({"start": self._creneaux()[2]})
            copie.videocall_location = resa.videocall_location
            copie.action_confirm()
        self.assertEqual(nc.renommages, [], "la salle de l'original n'est pas renommée")
        self.assertEqual(len(nc.salles), 2)
        self.assertNotEqual(copie.videocall_location, resa.videocall_location)

    def test_salle_generique_n_est_jamais_reprise(self):
        generique = "%s/index.php/call/generique" % BASE
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_generic_room_url", generique)
        with _FauxNextcloud() as nc:
            resa = self._confirmee(nc)
            resa.videocall_location = generique
            self._deplacer(resa)
        self.assertEqual(nc.renommages, [])
        self.assertEqual(len(nc.salles), 2)

    def test_lien_hors_nextcloud_n_est_pas_renomme(self):
        with _FauxNextcloud() as nc:
            resa = self._confirmee(nc)
            resa.videocall_location = "https://autre.test.invalid/index.php/call/abc"
            self._deplacer(resa)
        self.assertEqual(nc.renommages, [])
        self.assertEqual(len(nc.salles), 2)

    # -- Cas limites --------------------------------------------------------

    def test_premiere_confirmation_ne_reprend_pas_une_salle_collee(self):
        """Sans salle générée AVANT, rien n'est repris.

        Une salle du compte de service collée à la main sur une réservation en attente
        n'est pas la sienne. Organisateur vide : l'hôte de la salle (vide)
        et celui de la réservation (vide) coïncident, seule la garde du jeton
        préalable retient la reprise.
        """
        collee = "%s/index.php/call/collee" % BASE
        with _FauxNextcloud() as nc:
            resa = self.type_rdv._bf_create_booking(
                self._creneaux()[0], partners=self.partner, confirm=False)
            resa.write({"user_id": False, "videocall_location": collee})
            self.assertFalse(resa.video_room_token)
            self.assertFalse(resa.user_id)
            resa.action_confirm()
        self.assertEqual(nc.renommages, [], "la salle collée n'est pas renommée")
        self.assertEqual(len(nc.salles), 1)
        self.assertEqual(resa.videocall_location, "%s/index.php/call/%ssalle1" % (BASE, nc.prefixe))

    def test_lien_fixe_d_un_autre_type_n_est_jamais_renomme(self):
        """Le lien fixe de N'IMPORTE QUEL type est partagé."""
        autre_salle = "%s/index.php/call/autretype" % BASE
        self.env["resource.booking.type"].create({
            "name": "Autre type 27106", "slug": "autre-type-27106",
            "videocall_location": autre_salle + "/",
        })
        with _FauxNextcloud() as nc:
            resa = self._confirmee(nc)
            resa.videocall_location = autre_salle
            self._deplacer(resa)
        self.assertEqual(nc.renommages, [])
        self.assertEqual(len(nc.salles), 2)

    def test_salle_d_une_autre_reservation_reconnue_par_son_jeton(self):
        """La même salle écrite autrement reste la même salle."""
        with _FauxNextcloud() as nc:
            resa = self._confirmee(nc)
            copie = resa.copy({"start": self._creneaux()[2]})
            copie.videocall_location = resa.videocall_location.replace(
                "/index.php/call/", "/call/") + "/"
            self._deplacer(resa)
        self.assertEqual(nc.renommages, [], "la salle que la copie porte n'est pas renommée")
        self.assertEqual(len(nc.salles), 2)

    def test_salle_d_avant_la_version_garde_son_hote(self):
        """Sans le rattrapage de la migration, toute salle
        existante passerait pour celle d'un autre hôte."""
        with _FauxNextcloud() as nc:
            resa = self._confirmee(nc)
            lien = resa.videocall_location
            self.env.cr.execute(
                "UPDATE resource_booking SET video_room_host_id = NULL WHERE id = %s",
                [resa.id])
            resa.invalidate_recordset(["video_room_host_id"])
            self.assertGreaterEqual(resa._bf_backfill_video_room_host(), 1)
            self.assertEqual(resa.video_room_host_id, resa.user_id)
            self._deplacer(resa)
        self.assertEqual(len(nc.salles), 1)
        self.assertEqual(resa.videocall_location, lien)

    # -- Lien court Shlink ---------------------------------------------------

    def _lien_court(self, resa, actif):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_appointment.shlink_base_url", "https://l.test.invalid")
        ICP.set_param("bf_appointment.shlink_api_key_encrypted", "cle")
        court = "https://l.test.invalid/%s" % resa._booking_shlink_slug()
        resa.write({"videocall_location": court, "video_fallback_active": actif})
        return court

    def test_lien_court_garde_et_repointe_au_deplacement(self):
        for actif in (True, False):
            with self.subTest(repli_actif=actif), _FauxNextcloud() as nc:
                resa = self._confirmee(nc)
                court = self._lien_court(resa, actif)
                self._deplacer(resa)
                self.assertEqual(resa.videocall_location, court, "le lien reçu reste le bon")
                self.assertEqual(len(nc.salles), 2)
                self.assertEqual(nc.repointages, [(
                    resa._booking_shlink_slug(),
                    "%s/index.php/call/%ssalle2" % (BASE, nc.prefixe),
                )])
                self.assertFalse(resa.video_fallback_active)
                self.assertEqual(nc.renommages, [])

    def test_lien_court_non_repointe_donne_la_salle_directe(self):
        with _FauxNextcloud(statut_repointage=500) as nc:
            resa = self._confirmee(nc)
            self._lien_court(resa, True)
            self._deplacer(resa)
        self.assertEqual(resa.videocall_location, "%s/index.php/call/%ssalle2" % (BASE, nc.prefixe))
        self.assertFalse(resa.video_fallback_active)

    def test_lien_court_nextcloud_injoignable_garde_sa_cible(self):
        # La salle générique posée : c'est vers elle que l'ancien chemin
        # (`_fallback_booking_shlink`) ramenait le lien court.
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_generic_room_url", "%s/index.php/call/generique" % BASE)
        with _FauxNextcloud() as nc:
            resa = self._confirmee(nc)
            court = self._lien_court(resa, False)
            nc.creation_echoue = True
            self._deplacer(resa)
        self.assertEqual(resa.videocall_location, court)
        self.assertEqual(nc.repointages, [], "la cible n'est pas ramenée sur la salle générique")
        self.assertTrue(resa.video_fallback_active, "la réparation de la nuit s'en chargera")


@tagged("bf_appointment", "bf_appointment_video", "post_install", "-at_install")
class TestSalleNeuveQuandL_HoteChange(TransactionCase):
    """Un déplacement
    qui change d'hôte crée une salle neuve, modérée par le nouvel hôte.

    Type à deux hôtes : ressources `user`, deux combinaisons, l'hôte A libre
    de 0 h à 12 h UTC, l'hôte B de 12 h à 24 h. L'attribution automatique met
    un créneau du matin sur A, un de l'après-midi sur B.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        groupe = cls.env.ref("base.group_user")
        cls.type_rdv = _monter_type(cls.env, "hotes-27106")
        cls.type_rdv.combination_rel_ids.unlink()

        def calendrier(nom, debut, fin):
            return cls.env["resource.calendar"].create({
                "name": nom, "tz": "UTC",
                "attendance_ids": [Command.create({
                    "name": "j%s" % d, "dayofweek": str(d), "hour_from": debut,
                    "hour_to": fin, "day_period": "morning" if debut < 12 else "afternoon",
                }) for d in range(7)],
            })

        cls.hotes = []
        for sequence, (lettre, debut, fin) in enumerate((("a", 0.0, 12.0), ("b", 12.0, 24.0))):
            hote = cls.env["res.users"].create({
                "name": "Hôte %s 27106" % lettre.upper(),
                "login": "hote-%s-27106@test.invalid" % lettre,
                "tz": "UTC",
                "groups_id": [Command.set([groupe.id])],
            })
            ressource = cls.env["resource.resource"].create({
                "name": "Hôte %s 27106" % lettre.upper(), "resource_type": "user",
                "user_id": hote.id, "tz": "UTC",
                "calendar_id": calendrier("Hôte %s" % lettre, debut, fin).id,
            })
            combinaison = cls.env["resource.booking.combination"].create({
                "resource_ids": [Command.set([ressource.id])]})
            cls.type_rdv.combination_rel_ids = [Command.create({
                "sequence": sequence, "combination_id": combinaison.id})]
            cls.hotes.append(hote)
        cls.hote_a, cls.hote_b = cls.hotes
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.nc_talk_moderators",
            "hote-a-27106@test.invalid=ncA,hote-b-27106@test.invalid=ncB")
        cls.partner = cls.env["res.partner"].create({
            "name": "Client hôtes 27106", "email": "hotes27106@test.invalid"})

    def setUp(self):
        super().setUp()
        Modele = type(self.env["resource.booking"])
        dechiffre = patch.object(
            Modele, "_decrypt_nc_talk_password", lambda self, enc: "secret")
        dechiffre.start()
        self.addCleanup(dechiffre.stop)
        now = fields.Datetime.context_timestamp(
            self.env["resource.booking"], fields.Datetime.now())
        creneaux = [
            c.astimezone(pytz.utc).replace(tzinfo=None)
            for c in self.type_rdv._bf_candidate_slots(
                now + timedelta(hours=1), now + timedelta(days=7), limit=80)
        ]
        self.matins = [c for c in creneaux if c.hour < 11]
        self.apres_midis = [c for c in creneaux if 12 <= c.hour <= 22]
        self.assertGreaterEqual(len(self.matins), 2)
        self.assertTrue(self.apres_midis)

    def _confirmee_chez_a(self, nc):
        resa = self.type_rdv._bf_create_booking(
            self.matins[0], partners=self.partner, confirm=False)
        resa.action_confirm()
        self.assertEqual(resa.user_id, self.hote_a, "un matin va chez A")
        self.assertEqual(resa.video_room_host_id, self.hote_a)
        self.assertEqual(nc.moderateurs, [(nc.prefixe + "salle1", "ncA")])
        return resa

    def test_changement_d_hote_cree_une_salle_neuve(self):
        with _FauxNextcloud() as nc:
            resa = self._confirmee_chez_a(nc)
            resa.start = self.apres_midis[0]
            resa.action_confirm()
        self.assertEqual(resa.user_id, self.hote_b, "un après-midi va chez B")
        self.assertEqual(nc.renommages, [], "la salle de A n'est ni reprise ni renommée")
        self.assertEqual(len(nc.salles), 2)
        self.assertEqual(resa.videocall_location, "%s/index.php/call/%ssalle2" % (BASE, nc.prefixe))
        self.assertEqual(resa.meeting_id.videocall_location, resa.videocall_location)
        self.assertIn((nc.prefixe + "salle2", "ncB"), nc.moderateurs,
                      "le nouvel hôte modère la salle neuve")
        self.assertEqual(resa.video_room_host_id, self.hote_b)

    def test_meme_hote_garde_la_salle(self):
        """Le témoin : sur ce même type, un déplacement chez le même hôte garde la salle."""
        with _FauxNextcloud() as nc:
            resa = self._confirmee_chez_a(nc)
            lien = resa.videocall_location
            resa.start = self.matins[-1]
            resa.action_confirm()
        self.assertEqual(resa.user_id, self.hote_a)
        self.assertEqual(len(nc.salles), 1)
        self.assertEqual(resa.videocall_location, lien)
        self.assertEqual([j for j, _n in nc.renommages], [nc.prefixe + "salle1"])


@tagged("bf_appointment", "bf_appointment_video", "post_install", "-at_install")
class TestCreneauRenvoyeParHttp(HttpCase):
    """Le vrai chemin du client : des POST sur `/confirm`."""

    def setUp(self):
        super().setUp()
        if not self.env["ir.module.module"].sudo().search_count(
                [("name", "=", "website"), ("state", "=", "installed")]):
            self.skipTest("module `website` absent : routes non servies")
        self.type_rdv = _monter_type(self.env, "http-27106")
        self.partner = self.env["res.partner"].create({
            "name": "Client HTTP 27106", "email": "http27106@test.invalid"})
        Modele = type(self.env["resource.booking"])
        dechiffre = patch.object(
            Modele, "_decrypt_nc_talk_password", lambda self, enc: "secret")
        dechiffre.start()
        self.addCleanup(dechiffre.stop)
        self.envois = []

        def _envoi(rec, template, attach_ics=True, recipient=None):
            self.envois.append((rec.id, template.xml_id if hasattr(template, "xml_id") else template.id, recipient))

        envoi = patch.object(Modele, "_send_appointment_email", _envoi)
        envoi.start()
        self.addCleanup(envoi.stop)
        now = fields.Datetime.now()
        creneaux = self.type_rdv._bf_candidate_slots(
            fields.Datetime.context_timestamp(self.env["resource.booking"], now) + timedelta(hours=1),
            fields.Datetime.context_timestamp(self.env["resource.booking"], now) + timedelta(days=7),
            limit=6)
        self.creneaux = [c.astimezone(pytz.utc) for c in creneaux]
        self.resa = self.type_rdv._bf_create_booking(
            self.creneaux[0].replace(tzinfo=None), partners=self.partner, confirm=False)
        self.resa._portal_ensure_token()
        self.env.cr.flush()

    def _poster(self, quand):
        base = "/appointment/b/%d/%s" % (self.resa.id, self.resa.access_token)
        page = self.url_open(base + "/schedule")
        self.assertEqual(page.status_code, 200)
        jeton = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
        self.assertTrue(jeton, "la page de créneaux porte son jeton CSRF")
        reponse = self.url_open(base + "/confirm", data={
            "csrf_token": jeton.group(1),
            "when": quand.isoformat(),
        }, allow_redirects=False)
        self.assertIn(reponse.status_code, (302, 303))
        self.resa.invalidate_recordset()
        return reponse

    def test_meme_creneau_renvoye_ne_fait_rien(self):
        with _FauxNextcloud() as nc:
            self._poster(self.creneaux[0])
            self.assertEqual(self.resa.state, "confirmed")
            salles, envois, lien = len(nc.salles), len(self.envois), self.resa.videocall_location
            self.assertEqual(salles, 1)
            self.assertTrue(envois, "la première confirmation écrit")
            self._poster(self.creneaux[0])
        self.assertEqual(len(nc.salles), salles, "aucune salle de plus")
        self.assertEqual(len(self.envois), envois, "aucun courriel de plus")
        self.assertEqual(nc.renommages, [])
        self.assertEqual(self.resa.videocall_location, lien)

    def test_meme_creneau_confirme_a_la_main_confirme_pour_de_vrai(self):
        """`confirmed` posé hors d'`action_confirm` (le demandeur accepté à la
        main dans l'agenda) : le même créneau renvoyé fait la salle et la
        confirmation, au lieu d'être pris pour un double clic."""
        evenement = self.resa.meeting_id
        self.assertTrue(evenement, "la réservation a son événement")
        invites = evenement.attendee_ids.filtered(
            lambda a: a.partner_id in self.resa.partner_ids)
        self.assertTrue(invites)
        invites.write({"state": "accepted"})
        self.resa.invalidate_recordset()
        self.assertEqual(self.resa.state, "confirmed")
        self.assertFalse(self.resa.video_room_token)
        self.env.cr.flush()
        with _FauxNextcloud() as nc:
            self._poster(self.creneaux[0])
        self.assertEqual(len(nc.salles), 1, "la salle part")
        self.assertTrue(self.resa.video_room_token)
        self.assertIn("/call/", self.resa.videocall_location or "")
        self.assertIn(self.resa.id, [rid for rid, _t, qui in self.envois if qui == "booker"],
                      "la confirmation part au demandeur")

    def test_deplacement_par_le_client_garde_la_salle(self):
        with _FauxNextcloud() as nc:
            self._poster(self.creneaux[0])
            lien = self.resa.videocall_location
            self._poster(self.creneaux[4])
        self.assertEqual(len(nc.salles), 1)
        self.assertEqual(self.resa.videocall_location, lien)
        self.assertEqual(self.resa.start, self.creneaux[4].replace(tzinfo=None))
        self.assertEqual([j for j, _n in nc.renommages], [nc.prefixe + "salle1"])
