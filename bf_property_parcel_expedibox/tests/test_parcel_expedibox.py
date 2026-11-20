"""Essais de l'avis de colis : la route, le journal, et ce qui est consigné.

Rien ne sort du banc. ``requests.Session.request`` est remplacé pour TOUS les
essais de ce fichier — créer un point d'accès suffit à faire pousser le socle
vers Expedibox —, et un appel qu'aucun essai n'a prévu fait ÉCHOUER l'essai au
lieu de partir pour de vrai (leçon d'un lot précédent : le harnais visait
``requests.request`` quand le module est passé à ``Session.request``, et
22 essais sont tombés après que les appels soient partis).

Le harnais est écrit ici, pas importé du module voisin : ce qui mesure ne doit
rien devoir à ce qui est mesuré.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from odoo.addons.bf_property_access.tests.common import AccessCase

# 🔴 Le client d'API parle par une SESSION (`trust_env = False`) : c'est
# `Session.request` qu'il faut remplacer.
REQUEST = "requests.Session.request"
ROUTE = "/symbifox/expedibox/avis/%s"
ORG = "N5vM3LDX"
LOCKER = "qovN9wxk"


class FakeResponse:
    def __init__(self, status, payload=None, raw=None):
        self.status_code = status
        self.content = raw if raw is not None else (
            b"" if payload is None else json.dumps(payload).encode())
        self.text = self.content.decode(errors="replace")
        self.headers = {"content-type": "application/json"}

    def json(self):
        return json.loads(self.content.decode())


class FakeHttp:
    """Répond selon (méthode, chemin). Un appel non prévu lève ET se consigne."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.unexpected = []

    def __call__(self, method, url, headers=None, json=None, params=None,
                 timeout=None, allow_redirects=None):
        path = url.replace("https://api.expedibox.com", "")
        self.calls.append({"method": method, "path": path, "params": params or {},
                           "headers": headers or {}, "payload": json})
        for (route_method, route_path), answer in self.routes.items():
            if route_method == method and (
                route_path == path
                or (route_path.endswith("*") and path.startswith(route_path[:-1]))
            ):
                return answer(self) if callable(answer) else answer
        self.unexpected.append("%s %s" % (method, path))
        raise AssertionError("appel non prévu par l'essai : %s %s" % (method, path))


class NoticeCase(AccessCase):
    """Un syndicat, un casier relié, une résidente inscrite, une adresse d'écoute."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Event = cls.env["bf.property.parcel.expedibox.event"]
        cls.Parcel = cls.env["bf.property.parcel"]
        cls.connector = cls.env["bf.property.access.connector"].create({
            "name": "Casier du hall (banc)",
            "kind": "expedibox",
            "expedibox_app_token": "jeton-du-banc",
            "expedibox_organization_id": ORG,
            "expedibox_notice_user_id": cls.manager.id,
            "expedibox_notice_token": "jeton-d-ecoute-du-banc-0123456789",
        })
        cls.alice.write({"email": "alice@exemple.test"})

    def setUp(self):
        super().setUp()
        # Porte fermée par défaut : créer un point suffit à faire pousser le
        # socle vers Expedibox.
        #
        # 🔴 Mais pas fermée pour tout le monde : `url_open` des essais de
        # route passe par la MÊME `requests.Session.request`. Une garde qui
        # lève sans regarder l'adresse ne bloque pas un appel sortant, elle
        # bâillonne le client des essais — et les onze essais de la route
        # échouaient sur ma propre garde (2026-11-19). La garde laisse donc
        # passer le serveur des essais, et refuse tout le reste.
        import requests
        real = requests.Session.request

        def guard(session, method, url, *args, **kwargs):
            if str(url).startswith(("http://127.0.0.1", "http://localhost",
                                    "https://127.0.0.1", "https://localhost")):
                return real(session, method, url, *args, **kwargs)
            raise AssertionError(
                "appel sortant hors d'un patch d'essai : %s %s" % (method, url))

        self.door = patch.object(requests.Session, "request", guard)
        self.door.start()
        self.addCleanup(self.open_door)

    def open_door(self):
        try:
            self.door.stop()
        except RuntimeError:
            pass

    # ── Outils ───────────────────────────────────────────────────────────

    def locker_point(self, **kw):
        vals = {"name": "Boîte à colis du hall", "point_type": "parcel",
                "connector_id": self.connector.id, "expedibox_locker_id": LOCKER}
        vals.update(kw)
        return self.make_point(**vals)

    def enrol(self, partner=None, unit=None, recipient="rec-7"):
        """Fait arriver une résidente, casier qui répond bien. Rend son droit."""
        partner = partner or self.alice
        unit = unit or self.unit1
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, {
                "id": recipient, "locker_id": LOCKER}),
        })
        with patch(REQUEST, side_effect=http):
            self.own(partner, unit)
            self.sync()
        self.assertEqual(http.unexpected, [])
        grant = self.env["bf.property.access.grant"].search([
            ("partner_id", "=", partner.id),
            ("point_id.expedibox_locker_id", "=", LOCKER),
        ], limit=1)
        self.assertTrue(grant, "la résidente doit avoir un droit au casier")
        return grant

    def identifier(self, grant):
        return grant._expedibox_identifier()

    def reservation(self, grant=None, locker=LOCKER, **kw):
        """Ce qu'Expedibox rend pour une réservation (champs utiles seulement).

        ⚠️ `locker` est un paramètre NOMMÉ : passé dans `kw`, il ajoutait une
        clé `locker` que personne ne lit et la réservation désignait encore le
        bon casier — deux essais mesuraient alors le contraire de leur nom
        (2026-11-19).
        """
        data = {
            "id": "res-1",
            "locker_id": locker,
            "recipient_id": "rec-7",
            "recipient_custom_identifier": self.identifier(grant) if grant else None,
            "deposit_at": "2026-11-19T18:05:00Z",
            "pickup_at": None,
            "deposit_expired": False,
            "notify_receiver": True,
            "carrier": "Purolator",
            "tracking_number": "329812345678",
            "unit": "101",
            # Des valeurs qui ne se retrouvent dans AUCUN autre champ : le
            # premier essai de vie privée cherchait « 1234 », qui est dans le
            # numéro de suivi « 329812345678 ». Il criait sur une valeur
            # légitime.
            "door": {"id": "d1", "position": "A3", "unlock_code": 765432},
            "pickup_code": 987654,
            "deposit_code": 876543,
            "pickup_qr_code_src": "data:image/png;base64,AAAA",
        }
        data.update(kw)
        return data

    def notice(self, event="deposit", reference="res-1", occurred="2026-11-19T18:05:02Z",
               locker=LOCKER):
        """Le corps que la route reçoit (minimal : elle n'en lit que trois champs)."""
        return {"event": event, "timestamp": occurred,
                "reservation": {"id": reference, "locker_id": locker,
                                "first_name": "Alice", "last_name": "Exemple",
                                "email": "alice@exemple.test",
                                "pickup_code": 4321}}

    def receive(self, event="deposit", reference="res-1",
               occurred="2026-11-19T18:05:02Z", locker=LOCKER):
        """Écrit l'avis comme la route le ferait, sans passer par HTTP."""
        from odoo.addons.bf_property_parcel_expedibox.models \
            .bf_property_parcel_expedibox_event import parse_instant
        avis, _neuf = self.Event._bf_receive(
            self.connector, event, parse_instant(occurred), reference, locker)
        return avis

    def process(self, routes):
        http = FakeHttp(routes)
        with patch(REQUEST, side_effect=http):
            done = self.Event._cron_notices()
        self.assertEqual(http.unexpected, [])
        return done, http

    def read_routes(self, reservation=None, status=200):
        return {("GET", "/reservations/res-1/"): FakeResponse(status, reservation)}


@tagged("post_install", "-at_install")
class TestNoticeConnector(NoticeCase):
    """Le connecteur : ce qu'il faut pour écouter, et ce qu'on voit."""

    def test_une_adresse_d_ecoute_exige_l_identifiant_d_organisation(self):
        connector = self.env["bf.property.access.connector"].create(
            {"name": "Deuxième casier", "kind": "expedibox",
             "expedibox_notice_user_id": self.manager.id})
        with self.assertRaises(ValidationError):
            connector.expedibox_notice_token = "jeton-sans-organisation-012345"

    def test_une_adresse_d_ecoute_exige_un_responsable(self):
        connector = self.env["bf.property.access.connector"].create(
            {"name": "Troisième casier", "kind": "expedibox",
             "expedibox_organization_id": "AUTRE123"})
        connector.expedibox_notice_user_id = False
        with self.assertRaises(ValidationError):
            connector.expedibox_notice_token = "jeton-sans-responsable-012345"

    def test_un_connecteur_manuel_n_ecoute_pas(self):
        connector = self.env["bf.property.access.connector"].create(
            {"name": "Connecteur manuel", "kind": "manual"})
        with self.assertRaises(ValidationError):
            connector.expedibox_notice_token = "jeton-sur-connecteur-manuel-01"

    def test_l_adresse_montree_est_celle_de_la_base(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://exemple.test/")
        self.connector.invalidate_recordset(["expedibox_notice_url"])
        self.assertEqual(
            self.connector.expedibox_notice_url,
            "https://exemple.test/symbifox/expedibox/avis/"
            "jeton-d-ecoute-du-banc-0123456789")

    def test_un_jeton_neuf_remplace_l_ancien_et_previent(self):
        before = self.connector.expedibox_notice_token
        action = self.connector.with_user(self.manager).action_expedibox_new_notice_token()
        self.assertNotEqual(self.connector.expedibox_notice_token, before)
        self.assertTrue(len(self.connector.expedibox_notice_token) > 30)
        self.assertEqual(action["tag"], "display_notification")
        self.assertIn("Reservation Webhook URL", action["params"]["message"])

    def test_le_concierge_ne_change_pas_le_jeton(self):
        with self.assertRaises(AccessError):
            self.connector.with_user(self.concierge).action_expedibox_new_notice_token()

    def test_le_concierge_ne_lit_pas_le_jeton(self):
        connector = self.connector.with_user(self.concierge)
        # Un champ réservé au groupe Gestionnaire n'est pas lisible par la
        # Consultation : le concierge voit le casier, pas le secret.
        with self.assertRaises(AccessError):
            connector.read(["expedibox_notice_token"])

    def test_l_identite_de_l_organisation_se_compare_en_entier(self):
        self.assertTrue(self.connector._expedibox_notice_identity(ORG))
        self.assertFalse(self.connector._expedibox_notice_identity(ORG.lower()))
        self.assertFalse(self.connector._expedibox_notice_identity(ORG[:-1]))
        self.assertFalse(self.connector._expedibox_notice_identity(""))
        self.assertFalse(self.connector._expedibox_notice_identity(None))


@tagged("post_install", "-at_install")
class TestNoticeProcessing(NoticeCase):
    """Le traitement : relire, rapprocher, consigner."""

    def test_un_depot_consigne_le_colis_a_l_heure_du_depot(self):
        point = self.locker_point()
        grant = self.enrol()
        event = self.receive()
        self.assertEqual(event.state, "received")
        done, http = self.process(self.read_routes(self.reservation(grant)))
        self.assertEqual(done, 1)
        parcel = event.parcel_id
        self.assertEqual(event.state, "done")
        self.assertTrue(parcel)
        self.assertEqual(parcel.partner_id, self.alice)
        self.assertEqual(parcel.organisation_id, self.org)
        self.assertEqual(parcel.unit_id, self.unit1)
        self.assertEqual(parcel.state, "held")
        self.assertEqual(parcel.carrier, "Purolator")
        self.assertEqual(parcel.reference, "329812345678")
        self.assertEqual(parcel.expedibox_reservation_ref, "res-1")
        self.assertEqual(parcel.date_received,
                         datetime(2026, 11, 19, 18, 5, 0))
        self.assertIn("A3", parcel.storage_location)
        self.assertIn(LOCKER, parcel.storage_location)
        # La relecture a bien demandé à ne PAS recevoir les images QR.
        self.assertEqual(http.calls[0]["params"], {"include_qr_code_src": "false"})
        self.assertEqual(event.point_id, point)
        self.assertEqual(event.partner_id, self.alice)

    def test_aucun_code_ni_image_n_entre_dans_la_base(self):
        self.locker_point()
        grant = self.enrol()
        event = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        parcel = event.parcel_id
        trail = " ".join(
            (parcel.read()[0].get(f) or "") if isinstance(parcel.read()[0].get(f), str)
            else "" for f in ("carrier", "reference", "storage_location", "note")
        ) + " ".join(parcel.message_ids.mapped("body"))
        for secret in ("987654", "876543", "765432", "base64"):
            self.assertNotIn(secret, trail)
        for field, value in event.read()[0].items():
            if isinstance(value, str):
                self.assertNotIn("base64", value)
                self.assertNotIn("alice@exemple.test", value)

    def test_le_casier_avisant_lui_meme_coupe_le_texto(self):
        self.locker_point()
        grant = self.enrol()
        event = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        trail = " ".join(event.parcel_id.message_ids.mapped("body"))
        self.assertIn("avise lui-même", trail)
        self.assertNotIn("Avis par texto envoyé", trail)

    def test_un_casier_muet_laisse_partir_le_texto(self):
        self.connector.expedibox_locker_notifies = False
        self.locker_point()
        grant = self.enrol()
        event = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        trail = " ".join(event.parcel_id.message_ids.mapped("body"))
        # Sans consentement ni réglage du syndicat, le texto ne part pas — mais
        # la raison consignée est celle du canal, pas la nôtre.
        self.assertNotIn("avise lui-même", trail)
        self.assertIn("Aucun avis par texto", trail)

    def test_un_retrait_est_date_de_l_heure_du_retrait(self):
        self.locker_point()
        grant = self.enrol()
        deposit = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        parcel = deposit.parcel_id
        pickup = self.receive(event="pickup", occurred="2026-11-19T21:30:05Z")
        self.process(self.read_routes(self.reservation(
            grant, pickup_at="2026-11-19T21:30:00Z")))
        self.assertEqual(pickup.state, "done")
        self.assertEqual(parcel.state, "collected")
        self.assertEqual(parcel.date_closed, datetime(2026, 11, 19, 21, 30, 0))

    def test_un_retrait_sur_un_colis_deja_remis_ne_casse_rien(self):
        self.locker_point()
        grant = self.enrol()
        deposit = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        parcel = deposit.parcel_id
        parcel.with_user(self.manager).action_collected()
        pickup = self.receive(event="pickup", occurred="2026-11-19T21:30:05Z")
        self.process(self.read_routes(self.reservation(
            grant, pickup_at="2026-11-19T21:30:00Z")))
        self.assertEqual(pickup.state, "done")
        self.assertIn("n'était plus en dépôt", pickup.message)
        self.assertEqual(parcel.state, "collected")

    def test_un_depot_rejoue_ne_consigne_pas_deux_colis(self):
        self.locker_point()
        grant = self.enrol()
        first = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        # Même réservation, même événement, heure différente : Expedibox a
        # renvoyé l'avis (notre 200 s'est perdu).
        again = self.receive(occurred="2026-11-19T18:06:00Z")
        self.assertNotEqual(first, again)
        self.process(self.read_routes(self.reservation(grant)))
        self.assertEqual(again.state, "ignored")
        self.assertIn("déjà consigné", again.message)
        self.assertEqual(self.Parcel.search_count(
            [("expedibox_reservation_ref", "=", "res-1")]), 1)

    def test_le_meme_avis_exactement_n_est_ecrit_qu_une_fois(self):
        self.locker_point()
        self.enrol()
        first = self.receive()
        second = self.receive()
        self.assertEqual(first, second)
        self.assertEqual(self.Event.search_count(
            [("reservation_ref", "=", "res-1")]), 1)

    def test_la_garde_d_installation_juge_la_version_du_voisin(self):
        """🔴 « Un seul avis » repose sur une clé de contexte qu'un
        `bf_property_sms` antérieur ignore : le casier avise ET le texto part."""
        from odoo.addons.bf_property_parcel_expedibox.hooks import (
            EXIGE, version_suffisante)
        self.assertTrue(version_suffisante(EXIGE))
        self.assertFalse(version_suffisante("18.0.2.2.0"))
        self.assertFalse(version_suffisante(None))

    def test_un_colis_annonce_et_depose_au_casier_ne_fait_qu_une_fiche(self):
        """🔴 La résidente annonce son colis, le livreur le dépose au casier :
        deux fiches, un texto de trop à la livraison, et l'annonce restait « en
        dépôt » pour toujours (aucune purge ne prend une fiche ouverte)."""
        Parcel = self.env["bf.property.parcel"]
        if "tracking_number" not in Parcel._fields:
            self.skipTest("bf_property_parcel_tracking n'est pas installé")
        self.locker_point()
        grant = self.enrol()
        annonce = Parcel.sudo().create({
            "organisation_id": self.org.id,
            "building_id": self.building.id,
            "unit_id": self.unit1.id,
            "partner_id": self.alice.id,
            "state": "expected",
            "tracking_carrier": "ups",
            "tracking_number": "329812345678",
        })
        event = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        self.assertEqual(event.state, "done")
        self.assertEqual(event.parcel_id, annonce)
        self.assertEqual(annonce.state, "held")
        self.assertEqual(annonce.expedibox_reservation_ref, "res-1")
        self.assertEqual(annonce.date_received, datetime(2026, 11, 19, 18, 5, 0))
        self.assertIn(LOCKER, annonce.storage_location)
        self.assertTrue(annonce.tracking_notified)
        self.assertFalse(annonce.tracking_next_check)
        self.assertEqual(Parcel.search_count(
            [("partner_id", "=", self.alice.id)]), 1)

    def test_un_faux_retrait_ne_remet_aucun_colis(self):
        """🔴 Le cas que la relecture adverse a trouvé.

        Avec l'adresse d'écoute (vue au portail développeur, dans un billet de
        soutien, dans un journal de proxy), on postait « pickup » sur un colis
        en dépôt : il passait « remis », la résidente lisait « remis » au
        portail, et le colis était toujours dans le casier. Le mot reçu ne
        décide plus : la réservation n'a pas d'heure de retrait, donc il n'y a
        pas eu de retrait.
        """
        self.locker_point()
        grant = self.enrol()
        depot = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        parcel = depot.parcel_id
        self.assertEqual(parcel.state, "held")
        faux = self.receive(event="pickup", occurred="2026-11-19T19:00:00Z")
        self.process(self.read_routes(self.reservation(grant)))
        self.assertEqual(parcel.state, "held")
        self.assertEqual(faux.state, "ignored")
        self.assertIn("déjà consigné", faux.message)

    def test_un_faux_depot_ne_consigne_aucun_colis(self):
        """Même porte, même réponse : sans heure de dépôt chez Expedibox,
        aucun colis n'entre au journal et aucun avis ne part."""
        self.locker_point()
        grant = self.enrol()
        faux = self.receive()
        self.process(self.read_routes(self.reservation(grant, deposit_at=None)))
        self.assertEqual(faux.state, "ignored")
        self.assertFalse(faux.parcel_id)
        self.assertEqual(self.Parcel.search_count([]), 0)

    def test_un_retrait_confirme_l_emporte_sur_le_depot(self):
        """Une réservation retirée porte les DEUX dates : c'est le retrait qui
        fait foi, sinon un avis en retard rouvrirait un colis déjà remis."""
        self.locker_point()
        grant = self.enrol()
        depot = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        parcel = depot.parcel_id
        retrait = self.receive(event="pickup", occurred="2026-11-19T20:00:00Z")
        self.process(self.read_routes(self.reservation(
            grant, pickup_at="2026-11-19T19:58:00Z")))
        self.assertEqual(retrait.state, "done")
        self.assertEqual(parcel.state, "collected")
        self.assertEqual(parcel.date_closed, datetime(2026, 11, 19, 19, 58, 0))

    def test_un_droit_retire_ne_rouvre_aucun_journal_et_ne_nomme_personne(self):
        """🔴 La personne est partie, son inscription traîne encore au casier.

        Sans filtre sur l'état du droit, elle était reconnue, la création du
        colis levait « n'a aucune fraction dans ce syndicat », et son NOM
        finissait dans le message de l'avis — lu par la Consultation — et au
        journal du serveur.
        """
        self.locker_point()
        grant = self.enrol()
        identifiant = self.identifier(grant)
        grant.sudo().write({"state": "removed"})
        event = self.receive()
        self.process(self.read_routes(self.reservation(
            None, recipient_custom_identifier=identifiant)))
        self.assertEqual(event.state, "handed")
        self.assertFalse(event.partner_id)
        self.assertFalse(event.parcel_id)
        self.assertNotIn(self.alice.name, event.message or "")
        activity = self.env["mail.activity"].search([
            ("res_model", "=", "bf.property.parcel.expedibox.event"),
            ("res_id", "=", event.id)])
        self.assertNotIn(self.alice.name, activity.note or "")

    def test_un_evenement_sans_effet_ne_remet_rien_a_personne(self):
        """🔴 Vingt résidents inscrits à la main au casier produisaient cinq
        activités par colis, dont une pour un RETRAIT, rédigée « un dépôt… ne
        correspond à aucun résident »."""
        self.locker_point()
        self.enrol()
        event = self.receive(event="pickup", occurred="2026-11-19T19:30:00Z")
        self.process(self.read_routes(self.reservation(
            None, recipient_custom_identifier=None, recipient_id=None,
            pickup_at="2026-11-19T19:29:00Z")))
        self.assertEqual(event.state, "ignored")
        self.assertFalse(self.env["mail.activity"].search_count([
            ("res_model", "=", "bf.property.parcel.expedibox.event"),
            ("res_id", "=", event.id)]))

    def test_le_casier_rangé_vient_de_la_relecture_pas_du_corps(self):
        """Le corps annonce un casier, la relecture en dit un autre : c'est la
        relecture qui est recopiée dans « où il est rangé », que la résidente
        lit au portail."""
        point = self.locker_point()
        grant = self.enrol()
        event = self.receive(locker="CASIER-ANNONCÉ-PAR-LE-CORPS")
        self.process(self.read_routes(self.reservation(grant)))
        self.assertEqual(event.locker_ref, LOCKER)
        self.assertEqual(event.point_id, point)
        self.assertIn(LOCKER, event.parcel_id.storage_location)
        self.assertNotIn("ANNONCÉ", event.parcel_id.storage_location)

    def test_la_reservation_qui_n_avise_pas_fait_partir_le_texto(self):
        """Le réglage dit ce que le casier fait d'habitude ; la réservation dit
        ce qu'il a fait pour CE colis. Sans la seconde, personne n'était avisé."""
        self.locker_point()
        grant = self.enrol()
        event = self.receive()
        self.process(self.read_routes(self.reservation(
            grant, notify_receiver=False)))
        trail = " ".join(event.parcel_id.message_ids.mapped("body"))
        self.assertNotIn("avise lui-même", trail)
        self.assertIn("Aucun avis par texto", trail)

    def test_la_purge_garde_une_remise_que_personne_n_a_traitee(self):
        """🔴 Effacer l'avis emportait l'activité avec lui : la tâche
        disparaissait de la liste du responsable, sans trace."""
        self.locker_point()
        self.enrol()
        event = self.receive()
        self.process(self.read_routes({"detail": "bad token"}, status=401))
        self.assertEqual(event.state, "handed")
        event.write({"received_at": fields.Datetime.now() - timedelta(days=200)})
        self.assertEqual(self.Event._cron_purge(), 0)
        self.assertTrue(event.exists())
        # L'activité fermée, l'avis part comme les autres.
        event.activity_ids.action_done()
        self.assertEqual(self.Event._cron_purge(), 1)
        self.assertFalse(event.exists())

    def test_une_reservation_qu_expedibox_ne_connait_plus_ne_consigne_rien(self):
        self.locker_point()
        self.enrol()
        event = self.receive()
        self.process(self.read_routes(None, status=404))
        self.assertEqual(event.state, "ignored")
        self.assertFalse(event.parcel_id)
        self.assertIn("ne connaît plus", event.message)

    def test_un_refus_d_expedibox_revient_au_responsable_des_avis(self):
        """Un 401 arrive AVANT qu'on sache de quel casier il s'agit.

        Donc l'avis va au responsable des avis du connecteur, pas au
        responsable d'un point qu'on n'a pas encore identifié. (Mon premier
        essai attendait le concierge : il attendait une information que le
        module ne peut pas avoir à ce moment-là.)
        """
        self.locker_point()
        self.enrol()
        event = self.receive()
        self.process(self.read_routes({"detail": "bad token"}, status=401))
        self.assertEqual(event.state, "handed")
        self.assertIn("refusé la relecture", event.message)
        self.assertFalse(event.point_id)
        activity = self.env["mail.activity"].search([
            ("res_model", "=", "bf.property.parcel.expedibox.event"),
            ("res_id", "=", event.id)])
        self.assertEqual(activity.user_id, self.manager)

    def test_un_casier_sans_point_revient_au_responsable_des_avis(self):
        self.locker_point()
        self.enrol()
        event = self.receive(locker="AUTRE-CASIER")
        self.process(self.read_routes(self.reservation(locker="AUTRE-CASIER")))
        self.assertEqual(event.state, "handed")
        self.assertIn("qu'aucun point d'accès ne réclame", event.message)
        activity = self.env["mail.activity"].search([
            ("res_model", "=", "bf.property.parcel.expedibox.event"),
            ("res_id", "=", event.id)])
        self.assertEqual(activity.user_id, self.manager)

    def test_un_depot_sans_identifiant_ne_nomme_personne(self):
        point = self.locker_point()
        self.enrol()
        event = self.receive()
        self.process(self.read_routes(self.reservation(
            None, recipient_custom_identifier=None, recipient_id=None)))
        self.assertEqual(event.state, "handed")
        self.assertFalse(event.partner_id)
        self.assertFalse(event.parcel_id)
        activity = self.env["mail.activity"].search([
            ("res_model", "=", "bf.property.parcel.expedibox.event"),
            ("res_id", "=", event.id)])
        self.assertEqual(activity.user_id, point.user_id)
        # ⛔ Ni le nom, ni le courriel de la personne du casier.
        self.assertNotIn("Alice", activity.note)
        self.assertNotIn("alice@exemple.test", activity.note)
        self.assertIn("A3", activity.note)

    def test_l_identifiant_d_une_autre_base_n_est_pas_le_notre(self):
        """🔴 L'identifiant désigne la BONNE personne et le BON point : seul le
        préfixe de base diffère. Un essai qui nomme « p1-pt1 » passerait même
        sans la garde, parce que ces identifiants ne désignent personne."""
        point = self.locker_point()
        grant = self.enrol()
        etranger = "sfx-deadbeef-p%s-pt%s" % (self.alice.id, point.id)
        self.assertNotEqual(etranger, self.identifier(grant))
        event = self.receive()
        self.process(self.read_routes(self.reservation(
            None, recipient_custom_identifier=etranger, recipient_id=None)))
        self.assertEqual(event.state, "handed")
        self.assertFalse(event.partner_id)
        self.assertFalse(event.parcel_id)

    def test_un_identifiant_qui_designe_un_autre_point_est_refuse(self):
        self.locker_point()
        grant = self.enrol()
        other = self.locker_point(name="Casier du sous-sol",
                                  expedibox_locker_id="AUTRE")
        event = self.receive(locker="AUTRE")
        self.process(self.read_routes(self.reservation(grant, locker="AUTRE")))
        self.assertEqual(event.state, "handed")
        self.assertEqual(event.point_id, other)
        self.assertFalse(event.parcel_id)

    def test_l_identifiant_se_lit_au_destinataire_quand_la_reservation_se_taît(self):
        self.locker_point()
        grant = self.enrol()
        event = self.receive()
        routes = self.read_routes(self.reservation(
            None, recipient_custom_identifier=None, recipient_id="rec-7"))
        routes[("GET", "/recipients/rec-7/")] = FakeResponse(200, {
            "id": "rec-7", "custom_identifier": self.identifier(grant),
            "email": "alice@exemple.test", "mobile_number": "+15555550123"})
        done, http = self.process(routes)
        self.assertEqual(event.state, "done")
        self.assertTrue(event.parcel_id)
        # Le courriel et le mobile du destinataire sont lus, jamais gardés.
        for field, value in event.read()[0].items():
            if isinstance(value, str):
                self.assertNotIn("5555550123", value)

    def test_une_panne_se_rejoue_puis_revient_au_responsable(self):
        self.locker_point()
        self.enrol()
        event = self.receive()
        import requests
        panne = {("GET", "/reservations/res-1/"):
                 lambda http: (_ for _ in ()).throw(requests.ConnectionError("coupé"))}
        for attempt in range(1, 5):
            with mute_logger("odoo.addons.bf_property_parcel_expedibox.models"
                             ".bf_property_parcel_expedibox_event"):
                self.process(panne)
            self.assertEqual(event.state, "received")
            self.assertEqual(event.attempts, attempt)
        with mute_logger("odoo.addons.bf_property_parcel_expedibox.models"
                         ".bf_property_parcel_expedibox_event"):
            self.process(panne)
        self.assertEqual(event.attempts, 5)
        self.assertEqual(event.state, "handed")
        self.assertIn("après 5 essais", event.message)
        self.assertFalse(event.parcel_id)

    def test_un_avis_en_echec_n_emporte_pas_les_autres(self):
        self.locker_point()
        grant = self.enrol()
        bad = self.receive(reference="res-fantome")
        good = self.receive(reference="res-1")
        routes = {
            ("GET", "/reservations/res-fantome/"): FakeResponse(500, {"e": 1}),
            ("GET", "/reservations/res-1/"): FakeResponse(200, self.reservation(grant)),
        }
        with mute_logger("odoo.addons.bf_property_parcel_expedibox.models"
                         ".bf_property_parcel_expedibox_event"):
            done, http = self.process(routes)
        self.assertEqual(good.state, "done")
        self.assertTrue(good.parcel_id)
        self.assertEqual(bad.state, "received")
        self.assertEqual(bad.attempts, 1)

    def test_un_depot_echu_demande_sans_conclure(self):
        point = self.locker_point()
        grant = self.enrol()
        deposit = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        parcel = deposit.parcel_id
        # ⚠️ Pas deux jours plus tard : le module refuse désormais un
        # horodatage à plus de deux jours dans l'avenir (un instant forgé
        # épinglait un colis en tête du journal).
        expired = self.receive(event="deposit_expired",
                               occurred="2026-11-19T22:05:00Z")
        self.process(self.read_routes(self.reservation(grant, deposit_expired=True)))
        self.assertEqual(expired.state, "handed")
        # 🔴 Le colis n'est PAS déclaré retourné : personne ne l'a constaté.
        self.assertEqual(parcel.state, "held")
        activity = self.env["mail.activity"].search([
            ("res_model", "=", "bf.property.parcel.expedibox.event"),
            ("res_id", "=", expired.id)])
        self.assertEqual(activity.user_id, point.user_id)
        self.assertIn(parcel.name, activity.note)

    def test_une_reservation_creee_ne_consigne_aucun_colis(self):
        """Une porte réservée n'est pas un colis déposé : la réservation le dit
        elle-même, elle n'a pas encore d'heure de dépôt."""
        self.locker_point()
        grant = self.enrol()
        event = self.receive(event="new_reservation")
        self.process(self.read_routes(self.reservation(grant, deposit_at=None)))
        self.assertEqual(event.state, "ignored")
        self.assertIn("ne confirme ni dépôt", event.message)
        self.assertFalse(event.parcel_id)
        self.assertEqual(self.Parcel.search_count([]), 0)

    def test_un_evenement_inconnu_est_garde_sans_effet(self):
        self.locker_point()
        grant = self.enrol()
        event = self.receive(event="locker_exploded")
        self.assertEqual(event.event, "other")
        self.assertEqual(event.event_label, "locker_exploded")
        self.process(self.read_routes(self.reservation(grant, deposit_at=None)))
        self.assertEqual(event.state, "ignored")

    def test_un_evenement_inconnu_sur_un_depot_reel_consigne_quand_meme(self):
        """🔴 C'est la réservation qui fait foi, pas le mot reçu.

        Si Expedibox nomme un jour son événement autrement, ou si l'avis de
        dépôt se perd et qu'un rappel arrive à sa place, le colis se consigne
        quand même : sa réservation porte une heure de dépôt.
        """
        self.locker_point()
        grant = self.enrol()
        event = self.receive(event="reminder")
        self.process(self.read_routes(self.reservation(grant)))
        self.assertEqual(event.state, "done")
        self.assertTrue(event.parcel_id)
        self.assertEqual(event.parcel_id.state, "held")

    def test_un_identifiant_sans_droit_ne_rouvre_aucun_journal(self):
        """Un identifiant resté au casier après un départ ne rouvre rien.

        🔴 C'est le cas réel du départ : le destinataire reste au casier (le
        module ne supprime jamais d'office, il signale), et son identifiant
        continuerait d'arriver dans les avis. Sans droit, pas de journal.
        """
        point = self.locker_point()
        grant = self.enrol()
        prefix = self.env["bf.property.access.grant"]._expedibox_prefix()
        parti = "%sp%s-pt%s" % (prefix, self.bruno.id, point.id)
        self.assertNotEqual(parti, self.identifier(grant))
        event = self.receive()
        self.process(self.read_routes(self.reservation(
            None, recipient_custom_identifier=parti)))
        self.assertEqual(event.state, "handed")
        self.assertFalse(event.partner_id)
        self.assertFalse(event.parcel_id)

    def test_le_texte_libre_du_livreur_est_borne_et_nettoye(self):
        self.locker_point()
        grant = self.enrol()
        event = self.receive()
        self.process(self.read_routes(self.reservation(
            grant, carrier="Pur\x00olator\n\nExpress  " + "x" * 400,
            tracking_number="  329\t812  ")))
        parcel = event.parcel_id
        self.assertEqual(parcel.reference, "329 812")
        self.assertNotIn("\x00", parcel.carrier)
        self.assertTrue(len(parcel.carrier) <= 120)

    def test_la_purge_garde_ce_qui_n_est_pas_traite(self):
        self.locker_point()
        grant = self.enrol()
        done_event = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        pending = self.receive(reference="res-2", occurred="2026-11-19T18:07:00Z")
        old = fields.Datetime.now() - timedelta(days=120)
        (done_event | pending).write({"received_at": old})
        purged = self.Event._cron_purge()
        self.assertEqual(purged, 1)
        self.assertFalse(done_event.exists())
        self.assertTrue(pending.exists())

    def test_la_purge_suit_une_conservation_plus_courte(self):
        self.locker_point()
        grant = self.enrol()
        event = self.receive()
        self.process(self.read_routes(self.reservation(grant)))
        self.org.log_retention_days = 10
        event.write({"received_at": fields.Datetime.now() - timedelta(days=20)})
        self.assertEqual(self.Event._cron_purge(), 1)
        self.assertFalse(event.exists())

    def test_deux_colis_ne_partagent_pas_une_reservation(self):
        self.locker_point()
        self.enrol()
        parcel = self.Parcel.sudo().create({
            "organisation_id": self.org.id, "partner_id": self.alice.id,
            "expedibox_reservation_ref": "res-9",
        })
        self.assertTrue(parcel)
        with self.assertRaises(Exception):
            with mute_logger("odoo.sql_db"):
                self.Parcel.sudo().create({
                    "organisation_id": self.org.id, "partner_id": self.alice.id,
                    "expedibox_reservation_ref": "res-9",
                })
                self.env.flush_all()


@tagged("post_install", "-at_install")
class TestNoticeRoute(NoticeCase, HttpCase):
    """La route : ce qu'elle accepte, ce qu'elle refuse, ce qu'elle n'apprend pas."""

    def post(self, body, token=None, organization=ORG, raw=None):
        url = ROUTE % (token or self.connector.expedibox_notice_token)
        data = raw if raw is not None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if organization is not None:
            headers["X-Expedibox-Organization-Id"] = organization
        return self.url_open(url, data=data, headers=headers, timeout=30)

    def test_la_route_ecrit_l_avis_et_repond_200(self):
        self.locker_point()
        self.enrol()
        answer = self.post(self.notice())
        self.assertEqual(answer.status_code, 200)
        event = self.Event.search([("reservation_ref", "=", "res-1")])
        self.assertEqual(len(event), 1)
        self.assertEqual(event.event, "deposit")
        self.assertEqual(event.state, "received")
        self.assertEqual(event.occurred_at, datetime(2026, 11, 19, 18, 5, 2))
        self.assertEqual(event.locker_ref, LOCKER)
        self.assertEqual(event.connector_id, self.connector)

    def test_la_route_ne_garde_rien_du_corps(self):
        self.locker_point()
        self.enrol()
        self.post(self.notice())
        event = self.Event.search([("reservation_ref", "=", "res-1")])
        for field, value in event.read()[0].items():
            if isinstance(value, str):
                self.assertNotIn("alice@exemple.test", value)
                self.assertNotIn("4321", value)
                self.assertNotIn("Alice", value)

    def test_un_jeton_inconnu_rend_404_et_n_apprend_rien(self):
        answer = self.post(self.notice(), token="jeton-qui-n-existe-pas-012345")
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(self.Event.search_count([]), 0)

    def test_un_connecteur_archive_n_ecoute_plus(self):
        """Archiver le connecteur coupe son adresse : c'est le geste par lequel
        une organisation débranche son casier."""
        self.locker_point()
        self.enrol()
        self.connector.active = False
        answer = self.post(self.notice())
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(self.Event.search_count([]), 0)

    def test_un_jeton_accentue_rend_404_et_pas_500(self):
        """🔴 `compare_digest` LÈVE sur deux chaînes non ASCII : la route
        rendait 500 avec une trace, et ce 500 — obtenu seulement là où un
        connecteur écoute — disait ce que le 404 refuse de dire."""
        self.locker_point()
        self.enrol()
        answer = self.post(self.notice(), token="é" * 30)
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(self.Event.search_count([]), 0)

    def test_un_en_tete_d_organisation_accentue_rend_403_et_pas_500(self):
        with mute_logger("odoo.addons.bf_property_parcel_expedibox.controllers"
                         ".expedibox_notice"):
            answer = self.post(self.notice(), organization="BELVÉDÈRE")
        self.assertEqual(answer.status_code, 403)

    def test_un_jeton_trop_court_rend_404(self):
        answer = self.post(self.notice(), token="court")
        self.assertEqual(answer.status_code, 404)

    def test_un_en_tete_d_organisation_qui_ne_correspond_pas_rend_403(self):
        with mute_logger("odoo.addons.bf_property_parcel_expedibox.controllers"
                         ".expedibox_notice"):
            answer = self.post(self.notice(), organization="AUTRE")
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(self.Event.search_count([]), 0)

    def test_un_en_tete_d_organisation_absent_rend_403(self):
        with mute_logger("odoo.addons.bf_property_parcel_expedibox.controllers"
                         ".expedibox_notice"):
            answer = self.post(self.notice(), organization=None)
        self.assertEqual(answer.status_code, 403)

    def test_un_corps_qui_n_est_pas_du_json_rend_400(self):
        answer = self.post(None, raw=b"ceci n'est pas du json")
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(self.Event.search_count([]), 0)

    def test_un_avis_sans_horodatage_rend_400(self):
        body = self.notice()
        body.pop("timestamp")
        self.assertEqual(self.post(body).status_code, 400)

    def test_un_avis_sans_reservation_rend_400(self):
        body = self.notice()
        body["reservation"] = {"locker_id": LOCKER}
        self.assertEqual(self.post(body).status_code, 400)

    def test_un_avis_sans_evenement_rend_400(self):
        body = self.notice()
        body["event"] = "   "
        self.assertEqual(self.post(body).status_code, 400)

    def test_un_corps_sans_taille_declaree_rend_411(self):
        """🔴 Un corps « chunked » n'annonce aucune taille : le plafond ne
        tirerait pas, et le corps entrerait en mémoire avant d'être mesuré.
        `requests` envoie en chunked dès que le corps est un générateur."""
        def flot():
            yield json.dumps(self.notice()).encode()

        answer = self.opener.post(
            self.base_url() + ROUTE % self.connector.expedibox_notice_token,
            data=flot(), timeout=30,
            headers={"Content-Type": "application/json",
                     "X-Expedibox-Organization-Id": ORG})
        self.assertEqual(answer.status_code, 411)
        self.assertEqual(self.Event.search_count([]), 0)

    def test_un_corps_demesure_rend_413(self):
        body = self.notice()
        body["reservation"]["pickup_qr_code_src"] = "A" * (600 * 1024)
        answer = self.post(body)
        self.assertEqual(answer.status_code, 413)
        self.assertEqual(self.Event.search_count([]), 0)

    def test_un_flot_est_plafonne(self):
        base = datetime(2026, 11, 19, 18, 0, 0)
        self.Event.sudo().create([{
            "connector_id": self.connector.id,
            "reservation_ref": "res-flot-%s" % i,
            "event": "deposit",
            "occurred_at": base + timedelta(seconds=i),
            "received_at": fields.Datetime.now(),
        } for i in range(120)])
        with mute_logger("odoo.addons.bf_property_parcel_expedibox.controllers"
                         ".expedibox_notice"):
            answer = self.post(self.notice())
        self.assertEqual(answer.status_code, 429)
        self.assertFalse(self.Event.search([("reservation_ref", "=", "res-1")]))

    def test_un_avis_n_ouvre_pas_de_session(self):
        self.locker_point()
        self.enrol()
        answer = self.post(self.notice())
        self.assertEqual(answer.status_code, 200)
        self.assertFalse(answer.cookies.get("session_id"))
