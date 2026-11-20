"""Essais du suivi chez le transporteur.

Rien ne sort : ``requests.Session.request`` est remplacé pour TOUS les essais,
et un appel qu'aucun essai n'a prévu fait échouer l'essai. La garde laisse
passer le serveur des essais (``url_open`` s'en sert aussi) et refuse tout le
reste.

Ce que ces essais mesurent, c'est ce qui PART (adresse, en-têtes, paramètres)
et ce qui est CONSIGNÉ, jamais ce que le module croit avoir fait.
"""
import json
import re
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from odoo.addons.bf_property_portal.models.bf_property_delivery import PARCEL_STATES

REQUEST = "requests.Session.request"
UPS_TEST = "https://wwwcie.ups.com"
UPS_PROD = "https://onlinetools.ups.com"
DHL = "https://api-eu.dhl.com/track/shipments"


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
    """Répond selon (méthode, adresse). Un appel non prévu lève."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.unexpected = []

    def __call__(self, method, url, headers=None, params=None, data=None,
                 auth=None, json=None, timeout=None, allow_redirects=None):
        self.calls.append({"method": method, "url": url, "params": params or {},
                           "headers": headers or {}, "data": data, "auth": auth})
        for (route_method, route_url), answer in self.routes.items():
            if route_method == method and (
                route_url == url
                or (route_url.endswith("*") and url.startswith(route_url[:-1]))
            ):
                return answer(self) if callable(answer) else answer
        self.unexpected.append("%s %s" % (method, url))
        raise AssertionError("appel non prévu par l'essai : %s %s" % (method, url))

    def urls(self, method=None):
        return [c["url"] for c in self.calls if method in (None, c["method"])]


class TrackingCase(HttpCase):
    """Un syndicat, une résidente, un comptoir, et des comptes de transporteur."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, tz="America/Toronto"))
        cls.Parcel = cls.env["bf.property.parcel"]
        cls.Account = cls.env["bf.property.carrier.account"]
        cls.manager = cls.env["res.users"].create({
            "name": "Gestionnaire (banc)", "login": "suivi_gestion",
            "password": "suivi_gestion_pwd", "tz": "America/Toronto",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_property_core.group_bf_property_manager").id,
            ])],
        })
        cls.concierge = cls.env["res.users"].create({
            "name": "Concierge (banc)", "login": "suivi_concierge",
            "password": "suivi_concierge_pwd", "tz": "America/Toronto",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_property_core.group_bf_property_user").id,
            ])],
        })
        cls.org = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat du suivi (banc)", "kind": "syndicat"})
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble du suivi", "organisation_id": cls.org.id})
        cls.unit = cls.env["bf.property.unit"].create(
            {"name": "201", "building_id": cls.building.id})
        cls.resident = cls.env["res.partner"].create(
            {"name": "Rosalie Exemple", "email": "rosalie@exemple.test"})
        cls.env["bf.property.ownership"].create({
            "partner_id": cls.resident.id, "unit_id": cls.unit.id,
            "date_start": fields.Date.context_today(cls.env.user)
            - timedelta(days=400),
        })
        cls.ups = cls.Account.create({
            "carrier": "ups", "environment": "test",
            "ups_client_id": "client-du-banc",
            "ups_client_secret": "secret-du-banc",
            "company_id": cls.env.company.id,
        })

    def setUp(self):
        super().setUp()
        import requests
        real = requests.Session.request

        def guard(session, method, url, *args, **kwargs):
            if str(url).startswith(("http://127.0.0.1", "http://localhost")):
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

    def parcel(self, carrier="ups", number="1Z999AA10123456784", state="expected",
               **kw):
        values = {
            "organisation_id": self.org.id,
            "building_id": self.building.id,
            "unit_id": self.unit.id,
            "partner_id": self.resident.id,
            "state": state,
            "tracking_carrier": carrier,
            "tracking_number": number,
        }
        values.update(kw)
        return self.Parcel.create(values)

    def token_route(self, host=UPS_TEST, lifetime="14399"):
        return {("POST", host + "/security/v1/oauth/token"):
                FakeResponse(200, {"access_token": "jeton-ups",
                                   "expires_in": lifetime,
                                   "token_type": "Bearer"})}

    def ups_answer(self, package, host=UPS_TEST):
        routes = self.token_route(host)
        routes[("GET", host + "/api/track/v1/details/*")] = FakeResponse(
            200, {"trackResponse": {"shipment": [{"package": [package]}]}})
        return routes

    def run_cron(self, routes):
        http = FakeHttp(routes)
        with patch(REQUEST, side_effect=http):
            checked = self.Parcel._cron_track()
        self.assertEqual(http.unexpected, [])
        return checked, http

    def delivered_package(self, date="20261119", end="181500"):
        return {
            "trackingNumber": "1Z999AA10123456784",
            "currentStatus": {"type": "D", "statusCode": "011",
                              "description": "Livré",
                              "simplifiedTextDescription": "Delivered"},
            "deliveryDate": [{"type": "DEL", "date": date}],
            "deliveryTime": {"type": "DEL", "endTime": end},
        }


@tagged("post_install", "-at_install")
class TestCarrierAccount(TrackingCase):

    def test_ce_qui_manque_se_dit_en_clair(self):
        nu = self.Account.create({"carrier": "ups", "environment": "production",
                                  "company_id": self.env.company.id})
        self.assertIn("identifiant client", nu._blocking_reason())
        dhl = self.Account.create({"carrier": "dhl",
                                   "company_id": self.env.company.id})
        self.assertIn("DHL-API-Key", dhl._blocking_reason())
        dhl.dhl_api_key = "cle-du-banc"
        self.assertFalse(dhl._blocking_reason())
        self.assertFalse(self.ups._blocking_reason())

    def test_purolator_refuse_et_dit_pourquoi(self):
        puro = self.Account.create({
            "carrier": "purolator", "company_id": self.env.company.id,
            "purolator_key": "cle", "purolator_password": "mot",
            "purolator_account_number": "1234567"})
        reason = puro._blocking_reason()
        self.assertIn("n'est pas branché", reason)
        self.assertIn("ship.purolator.com/developer", reason)
        self.assertIn("trois contacts", reason)

    def test_un_seul_compte_par_transporteur_et_environnement(self):
        with self.assertRaises(Exception):
            with mute_logger("odoo.sql_db"):
                self.Account.create({
                    "carrier": "ups", "environment": "test",
                    "company_id": self.env.company.id})
                self.env.flush_all()

    def test_la_production_passe_avant_l_essai(self):
        prod = self.Account.create({
            "carrier": "ups", "environment": "production",
            "ups_client_id": "prod", "ups_client_secret": "prod",
            "company_id": self.env.company.id})
        found = self.Account._for("ups", self.env.company)
        self.assertEqual(found, prod)

    def test_le_jeton_ups_est_garde_puis_renouvele(self):
        http = FakeHttp(self.token_route())
        with patch(REQUEST, side_effect=http):
            first = self.ups._ups_token()
            second = self.ups._ups_token()
        self.assertEqual(first, "jeton-ups")
        self.assertEqual(second, "jeton-ups")
        # Un seul appel pour deux demandes : le jeton est gardé.
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(http.calls[0]["auth"],
                         ("client-du-banc", "secret-du-banc"))
        self.assertEqual(http.calls[0]["data"], {"grant_type": "client_credentials"})
        # Périmé : on en redemande un.
        self.ups.sudo().ups_token_expires_at = fields.Datetime.now() - timedelta(minutes=1)
        with patch(REQUEST, side_effect=FakeHttp(self.token_route())) as _p:
            self.assertEqual(self.ups._ups_token(), "jeton-ups")

    def test_une_duree_de_vie_illisible_ne_fait_pas_tomber_l_appel(self):
        """⚠️ `expires_in` est une CHAÎNE dans leur spécification."""
        http = FakeHttp(self.token_route(lifetime=""))
        with patch(REQUEST, side_effect=http):
            self.assertEqual(self.ups._ups_token(), "jeton-ups")
        self.assertTrue(self.ups.sudo().ups_token_expires_at
                        > fields.Datetime.now())

    def test_un_numero_de_compte_voyage_dans_l_en_tete(self):
        self.ups.sudo().ups_account_number = "123456"
        http = FakeHttp(self.token_route())
        with patch(REQUEST, side_effect=http):
            self.ups._ups_token()
        self.assertEqual(http.calls[0]["headers"].get("x-merchant-id"), "123456")

    def test_l_essai_des_cles_demande_un_jeton_et_rien_d_autre(self):
        http = FakeHttp(self.token_route())
        with patch(REQUEST, side_effect=http):
            action = self.ups.with_user(self.manager).action_test_credentials()
        self.assertEqual(action["params"]["type"], "success")
        self.assertEqual(http.urls("GET"), [])

    def test_la_garde_d_installation_juge_la_version_du_voisin(self):
        """🔴 Elle lit la version du DISQUE : les deux modules montent dans la
        même fenêtre, et celui-ci se charge AVANT le voisin."""
        from odoo.addons.bf_property_parcel_tracking.hooks import (
            EXIGE, version_suffisante)
        self.assertTrue(version_suffisante(EXIGE))
        self.assertTrue(version_suffisante("18.0.2.4.0"))
        self.assertFalse(version_suffisante("18.0.2.2.0"))
        self.assertFalse(version_suffisante(""))
        self.assertFalse(version_suffisante(None))

    def test_l_interrogation_n_est_pas_appelable_par_rpc(self):
        """🔴 Une méthode sans tiret bas est appelable par call_kw : publique,
        elle offrait les clés de la société à qui voulait suivre un numéro."""
        self.assertFalse(hasattr(self.Account, "track"))
        self.assertTrue(hasattr(self.Account, "_bf_track"))

    def test_le_concierge_n_essaie_pas_les_cles(self):
        with self.assertRaises(AccessError):
            self.ups.with_user(self.concierge).action_test_credentials()

    def test_dhl_n_a_pas_d_essai_sans_colis_et_le_dit(self):
        dhl = self.Account.create({"carrier": "dhl", "dhl_api_key": "cle",
                                   "company_id": self.env.company.id})
        with self.assertRaises(UserError):
            dhl.with_user(self.manager).action_test_credentials()

    def test_une_langue_qui_n_est_pas_une_langue_est_refusee(self):
        with self.assertRaises(ValidationError):
            self.ups.locale = "fr-CA; drop"

    def test_le_plafond_horaire_se_compte_sur_les_colis(self):
        self.ups.hourly_limit = 2
        self.assertTrue(self.ups._tracking_allowance())
        for index in range(2):
            self.parcel(number="1Z999AA1012345678%s" % index).sudo().write(
                {"tracking_checked_at": fields.Datetime.now()})
        self.assertFalse(self.ups._tracking_allowance())
        self.ups.hourly_limit = 0
        self.assertTrue(self.ups._tracking_allowance())


@tagged("post_install", "-at_install")
class TestUpsBackend(TrackingCase):

    def test_un_colis_livre_passe_en_depot_a_l_heure_dite(self):
        parcel = self.parcel()
        self.assertEqual(parcel.tracking_state, "pending")
        checked, http = self.run_cron(self.ups_answer(self.delivered_package()))
        self.assertEqual(checked, 1)
        self.assertEqual(parcel.tracking_state, "delivered")
        self.assertEqual(parcel.state, "held")
        # 🔴 18 h 15 est l'heure LOCALE qu'UPS rend ; Odoo range de l'UTC, et
        # le dossier est au fuseau de Montréal : 23 h 15 UTC.
        self.assertEqual(parcel.date_received, datetime(2026, 11, 19, 23, 15, 0))
        self.assertEqual(parcel.tracking_label, "Livré")
        self.assertEqual(parcel.tracking_raw_code, "D/011")
        self.assertFalse(parcel.tracking_next_check)
        trail = " ".join(parcel.message_ids.mapped("body"))
        self.assertIn("déclare ce colis livré", trail)

    def test_ce_qui_part_chez_ups(self):
        self.parcel()
        _checked, http = self.run_cron(self.ups_answer(self.delivered_package()))
        track = [c for c in http.calls if c["method"] == "GET"][0]
        self.assertTrue(track["url"].startswith(
            UPS_TEST + "/api/track/v1/details/1Z999AA10123456784"))
        self.assertEqual(track["headers"]["Authorization"], "Bearer jeton-ups")
        self.assertEqual(track["headers"]["transactionSrc"], "Symbifox")
        self.assertTrue(track["headers"]["transId"])
        self.assertEqual(track["params"], {"locale": "fr_CA"})
        # ⛔ Ni signature, ni preuve de livraison : on ne les demande pas.
        self.assertNotIn("returnSignature", track["params"])
        self.assertNotIn("returnPOD", track["params"])

    def test_une_date_prevue_ne_conclut_pas_a_une_livraison(self):
        parcel = self.parcel()
        package = {
            "currentStatus": {"type": "I", "statusCode": "005",
                              "description": "En route"},
            "deliveryDate": [{"type": "SDD", "date": "20261121"}],
        }
        self.run_cron(self.ups_answer(package))
        self.assertEqual(parcel.tracking_state, "in_transit")
        self.assertEqual(parcel.state, "expected")
        self.assertTrue(parcel.tracking_next_check)

    def test_un_etat_qu_ups_ne_documente_pas_ne_conclut_rien(self):
        """🔴 Le vocabulaire de `type` vit dans une annexe illisible : un code
        inconnu laisse le colis en « on ne sait pas », jamais en livré."""
        parcel = self.parcel()
        package = {"currentStatus": {"type": "ZZ", "statusCode": "999",
                                     "description": "Quelque chose"}}
        self.run_cron(self.ups_answer(package))
        self.assertEqual(parcel.tracking_state, "unknown")
        self.assertEqual(parcel.state, "expected")
        self.assertEqual(parcel.tracking_label, "Quelque chose")

    def test_un_code_de_livraison_sans_date_ne_conclut_pas(self):
        """🔴 Le seul signal documenté est une date `DEL`, pas le code d'état.

        Conclure sur `currentStatus.type == "D"` reviendrait à décider sur une
        liste qu'UPS ne publie pas (son annexe refuse les clients non-navigateur) :
        un colis serait annoncé livré sur un code deviné.
        """
        parcel = self.parcel()
        package = {"currentStatus": {"type": "D", "statusCode": "011",
                                     "description": "Quelque chose de livré"}}
        self.run_cron(self.ups_answer(package))
        self.assertEqual(parcel.tracking_state, "unknown")
        self.assertEqual(parcel.state, "expected")

    def test_un_numero_inconnu_six_fois_s_arrete(self):
        parcel = self.parcel()
        routes = self.token_route()
        routes[("GET", UPS_TEST + "/api/track/v1/details/*")] = FakeResponse(404)
        for turn in range(1, 6):
            parcel.sudo().tracking_next_check = fields.Datetime.now()
            self.run_cron(routes)
            self.assertEqual(parcel.tracking_state, "not_found", turn)
        parcel.sudo().tracking_next_check = fields.Datetime.now()
        self.run_cron(routes)
        self.assertEqual(parcel.tracking_state, "blocked")
        self.assertFalse(parcel.tracking_next_check)
        self.assertIn("mal saisi", parcel.tracking_message)

    def test_un_jeton_refuse_est_oublie(self):
        parcel = self.parcel()
        routes = self.token_route()
        routes[("GET", UPS_TEST + "/api/track/v1/details/*")] = FakeResponse(401)
        with mute_logger("odoo.addons.bf_property_parcel_tracking.models"
                         ".bf_property_parcel"):
            self.run_cron(routes)
        self.assertFalse(self.ups.sudo().ups_token)
        # Transitoire : le colis reste à interroger, plus tard.
        self.assertEqual(parcel.tracking_state, "pending")
        self.assertTrue(parcel.tracking_next_check > fields.Datetime.now())

    def test_un_compte_bloque_arrete_le_suivi(self):
        parcel = self.parcel()
        routes = self.token_route()
        routes[("GET", UPS_TEST + "/api/track/v1/details/*")] = FakeResponse(403)
        self.run_cron(routes)
        self.assertEqual(parcel.tracking_state, "blocked")
        self.assertIn("Blocked Merchant", parcel.tracking_message)

    def test_un_numero_trop_court_ne_part_jamais(self):
        parcel = self.parcel(number="123")
        http = FakeHttp(self.token_route())
        with patch(REQUEST, side_effect=http):
            self.Parcel._cron_track()
        self.assertEqual(parcel.tracking_state, "blocked")
        self.assertIn("7 à 34", parcel.tracking_message)
        self.assertEqual(http.urls("GET"), [])


@tagged("post_install", "-at_install")
class TestDhlBackend(TrackingCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dhl = cls.Account.create({
            "carrier": "dhl", "dhl_api_key": "cle-dhl-du-banc",
            "company_id": cls.env.company.id, "locale": "fr_CA"})

    def dhl_answer(self, status_payload, code=200):
        return {("GET", DHL): FakeResponse(code, status_payload)}

    def test_livre_exactement_conclut(self):
        parcel = self.parcel(carrier="dhl", number="00340434161094042557")
        self.run_cron(self.dhl_answer({"shipments": [{"status": {
            "statusCode": "delivered", "description": "Livré",
            "timestamp": "2026-11-19T18:15:00Z"}}]}))
        self.assertEqual(parcel.tracking_state, "delivered")
        self.assertEqual(parcel.state, "held")
        self.assertEqual(parcel.date_received, datetime(2026, 11, 19, 18, 15, 0))

    def test_ce_qui_part_chez_dhl(self):
        self.parcel(carrier="dhl", number="00340434161094042557")
        _checked, http = self.run_cron(self.dhl_answer(
            {"shipments": [{"status": {"statusCode": "transit"}}]}))
        call = http.calls[0]
        self.assertEqual(call["headers"]["DHL-API-Key"], "cle-dhl-du-banc")
        self.assertEqual(call["params"],
                         {"trackingNumber": "00340434161094042557",
                          "language": "fr"})

    def test_un_code_inconnu_ne_conclut_jamais_a_une_livraison(self):
        parcel = self.parcel(carrier="dhl", number="00340434161094042557")
        self.run_cron(self.dhl_answer({"shipments": [{"status": {
            "statusCode": "DELIVERED_TO_NEIGHBOUR", "description": "Chez le voisin"}}]}))
        self.assertEqual(parcel.tracking_state, "unknown")
        self.assertEqual(parcel.state, "expected")
        self.assertEqual(parcel.tracking_label, "Chez le voisin")
        self.assertEqual(parcel.tracking_raw_code, "DELIVERED_TO_NEIGHBOUR")

    def test_une_cle_refusee_arrete_le_suivi(self):
        parcel = self.parcel(carrier="dhl", number="00340434161094042557")
        self.run_cron(self.dhl_answer({"status": 401, "title": "Unauthorized",
                                       "detail": "Access to the resource is not "
                                                 "allowed."}, code=401))
        self.assertEqual(parcel.tracking_state, "blocked")
        self.assertIn("refusé la clé", parcel.tracking_message)

    def test_un_quota_atteint_se_rejoue(self):
        parcel = self.parcel(carrier="dhl", number="00340434161094042557")
        with mute_logger("odoo.addons.bf_property_parcel_tracking.models"
                         ".bf_property_parcel"):
            self.run_cron(self.dhl_answer({"detail": "quota"}, code=429))
        self.assertEqual(parcel.tracking_state, "pending")
        self.assertTrue(parcel.tracking_next_check > fields.Datetime.now())
        self.assertIn("quota", parcel.tracking_message)


@tagged("post_install", "-at_install")
class TestParcelTracking(TrackingCase):

    def test_la_liste_des_etats_contient_tout_le_journal(self):
        """🔴 La liste est construite depuis celle du journal : un état ajouté
        là-bas doit apparaître ici, sinon il disparaîtrait des écrans."""
        states = dict(self.Parcel._fields["state"]._description_selection(self.env))
        for code, _label in PARCEL_STATES:
            self.assertIn(code, states)
        self.assertIn("expected", states)
        self.assertEqual(list(states)[0], "expected")

    def test_un_transporteur_qu_on_n_interroge_pas_n_entre_pas_en_file(self):
        parcel = self.parcel(carrier="other", number="XYZ-123")
        self.assertFalse(parcel.tracking_state)
        self.assertFalse(parcel.tracking_next_check)

    def test_changer_le_numero_remet_en_file_et_oublie_le_passe(self):
        parcel = self.parcel()
        self.run_cron(self.ups_answer({
            "currentStatus": {"type": "I", "description": "En route"},
            "deliveryDate": [{"type": "SDD", "date": "20261121"}]}))
        self.assertEqual(parcel.tracking_state, "in_transit")
        parcel.tracking_number = "1Z999AA10123456785"
        self.assertEqual(parcel.tracking_state, "pending")
        self.assertFalse(parcel.tracking_label)
        self.assertEqual(parcel.tracking_attempts, 0)

    def test_retirer_le_numero_retire_le_suivi(self):
        parcel = self.parcel()
        parcel.tracking_number = False
        self.assertFalse(parcel.tracking_state)
        self.assertFalse(parcel.tracking_next_check)

    def test_sans_compte_le_suivi_s_arrete_en_le_disant(self):
        self.ups.unlink()
        parcel = self.parcel()
        http = FakeHttp({})
        with patch(REQUEST, side_effect=http):
            self.Parcel._cron_track()
        self.assertEqual(parcel.tracking_state, "blocked")
        self.assertIn("Aucun compte UPS", parcel.tracking_message)
        self.assertEqual(http.calls, [])

    def test_le_plafond_repousse_sans_rien_perdre(self):
        self.ups.hourly_limit = 1
        other = self.parcel(number="1Z999AA10123456781")
        other.sudo().write({"tracking_checked_at": fields.Datetime.now()})
        parcel = self.parcel(number="1Z999AA10123456782")
        http = FakeHttp({})
        with patch(REQUEST, side_effect=http):
            self.Parcel._cron_track()
        self.assertEqual(http.calls, [])
        self.assertEqual(parcel.tracking_state, "pending")
        self.assertTrue(parcel.tracking_next_check > fields.Datetime.now())

    def test_un_colis_annonce_il_y_a_un_mois_cesse_d_etre_interroge(self):
        parcel = self.parcel()
        # ⚠️ `create_date` est un champ magique : écrit par l'ORM, il peut être
        # remis par le même flush. On le pose en SQL, comme un vieux colis.
        self.env.cr.execute(
            "update bf_property_parcel set create_date = %s, tracking_since = %s "
            "where id = %s",
            (fields.Datetime.now() - timedelta(days=31),
             fields.Datetime.now() - timedelta(days=31), parcel.id))
        parcel.invalidate_recordset(["create_date", "tracking_since"])
        http = FakeHttp({})
        with patch(REQUEST, side_effect=http):
            self.Parcel._cron_track()
        self.assertEqual(parcel.tracking_state, "blocked")
        self.assertIn("plus de 30 jours", parcel.tracking_message)
        self.assertEqual(http.calls, [])

    def test_un_echec_n_emporte_pas_les_autres(self):
        good = self.parcel(number="1Z999AA10123456784")
        bad = self.parcel(number="1Z999AA10123456785")
        routes = self.token_route()

        def answer(http):
            url = http.calls[-1]["url"]
            if url.endswith("1Z999AA10123456785"):
                return FakeResponse(500, {"e": 1})
            return FakeResponse(200, {"trackResponse": {"shipment": [
                {"package": [self.delivered_package()]}]}})

        routes[("GET", UPS_TEST + "/api/track/v1/details/*")] = answer
        with mute_logger("odoo.addons.bf_property_parcel_tracking.models"
                         ".bf_property_parcel"):
            self.run_cron(routes)
        self.assertEqual(good.tracking_state, "delivered")
        self.assertEqual(good.state, "held")
        self.assertEqual(bad.tracking_state, "pending")
        self.assertEqual(bad.tracking_attempts, 1)

    def test_un_colis_deja_en_depot_n_est_pas_touche(self):
        parcel = self.parcel(state="held")
        received = parcel.date_received
        self.run_cron(self.ups_answer(self.delivered_package()))
        self.assertEqual(parcel.state, "held")
        self.assertEqual(parcel.date_received, received)
        self.assertIn("déclare ce colis livré",
                      " ".join(parcel.message_ids.mapped("body")))

    def test_l_avis_ne_part_pas_a_l_annonce_mais_a_la_livraison(self):
        """🔴 Le texto dit « un colis est arrivé » : à l'annonce, c'est faux."""
        parcel = self.parcel()
        trail = " ".join(parcel.message_ids.mapped("body"))
        # Le fil dit pourquoi, et il dit vrai : rien n'est retenu, l'avis n'est
        # pas encore dû.
        self.assertIn("l'avis partira à la livraison", trail)
        self.assertNotIn("n'est pas en dépôt", trail)
        self.run_cron(self.ups_answer(self.delivered_package()))
        after = " ".join(parcel.message_ids.mapped("body"))
        # L'avis a été tenté à la livraison : sa raison de non-envoi a changé.
        self.assertIn("canal texto du syndicat est fermé", after)

    def test_un_colis_annonce_n_avise_personne_meme_sans_contexte(self):
        """🔴 La garde de `bf_property_sms` tient sur l'ÉTAT, pas seulement sur
        la clé de contexte : un colis créé « annoncé » par un autre chemin (un
        import, un script) ne doit pas faire partir « votre colis est arrivé »."""
        parcel = self.parcel()
        avant = len(parcel.message_ids)
        # ⚠️ Une fiche relue à neuf : la clé de contexte posée à la création
        # voyage avec le jeu d'enregistrements, et masquerait la garde d'état.
        propre = self.env["bf.property.parcel"].browse(parcel.id)
        self.assertFalse(propre._notify_arrival())
        trail = " ".join(parcel.message_ids.mapped("body"))
        self.assertIn("n'est pas en dépôt", trail)
        self.assertGreater(len(parcel.message_ids), avant)

    def test_le_concierge_constate_une_arrivee_avant_le_transporteur(self):
        parcel = self.parcel()
        parcel.with_user(self.manager).action_arrived()
        self.assertEqual(parcel.state, "held")
        self.assertTrue(parcel.date_received)
        self.assertIn("constaté au comptoir",
                      " ".join(parcel.message_ids.mapped("body")))

    def test_constater_l_arrivee_n_entraine_pas_un_second_texto(self):
        """🔴 Le concierge constate, UPS se réveille deux heures plus tard, et
        un SECOND avis partait — facturé, pour le même colis."""
        parcel = self.parcel()
        parcel.with_user(self.manager).action_arrived()
        self.assertEqual(parcel.state, "held")
        self.assertTrue(parcel.tracking_notified)
        self.assertFalse(parcel.tracking_next_check)
        avant = len(parcel.message_ids)
        # Le colis n'est même plus dans la file : le passage ne le voit pas.
        http = FakeHttp({})
        with patch(REQUEST, side_effect=http):
            self.Parcel._cron_track()
        self.assertEqual(http.calls, [])
        self.assertEqual(len(parcel.message_ids), avant)

    def test_interroger_a_la_main_avise_le_destinataire(self):
        """🔴 Le bouton consignait le colis, écrivait « le destinataire est
        avisé », retirait la fiche de la file — et personne n'était avisé.
        C'est aussi le seul chemin de reprise d'un suivi arrêté."""
        parcel = self.parcel()
        http = FakeHttp(self.ups_answer(self.delivered_package()))
        with patch(REQUEST, side_effect=http):
            parcel.with_user(self.manager).action_track_now()
        self.assertEqual(http.unexpected, [])
        self.assertEqual(parcel.state, "held")
        self.assertEqual(parcel.tracking_state, "delivered")
        self.assertTrue(parcel.tracking_notified)
        trail = " ".join(parcel.message_ids.mapped("body"))
        self.assertIn("Aucun avis par texto", trail)

    def test_annoncer_deux_fois_le_meme_numero_ne_casse_pas_la_page(self):
        """🔴 La contrainte d'unicité levait une IntegrityError hors du
        `except` : page d'erreur au résident, portant le texte de la contrainte
        — qui lui apprend qu'un voisin a annoncé ce numéro."""
        portal = self.env["res.users"].create({
            "name": "Rosalie Exemple", "login": "suivi_double",
            "password": "suivi_double_pwd", "partner_id": self.resident.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        self.assertTrue(portal)
        self.authenticate("suivi_double", "suivi_double_pwd")
        page = self.url_open("/my/property/deliveries")
        import re as _re
        jeton = _re.search(r'csrf_token: "([^"]+)"', page.text).group(1)
        valeurs = {"tracking_carrier": "ups",
                   "tracking_number": "1Z999AA10123456796",
                   "unit_id": str(self.unit.id), "csrf_token": jeton}
        premier = self.url_open("/my/property/parcels/announce", data=valeurs, timeout=30)
        self.assertEqual(premier.status_code, 200)
        second = self.url_open("/my/property/parcels/announce", data=valeurs, timeout=30)
        self.assertEqual(second.status_code, 200)
        self.assertIn("error=double", second.url)
        self.assertEqual(self.Parcel.search_count(
            [("tracking_number", "=", "1Z999AA10123456796")]), 1)

    def test_constater_l_arrivee_d_un_colis_deja_la_est_refuse(self):
        parcel = self.parcel(state="held")
        with self.assertRaises(UserError):
            parcel.with_user(self.manager).action_arrived()

    def test_l_avis_de_livraison_ne_part_qu_une_fois(self):
        """🔴 Le texto sortait DANS le point de sauvegarde : un retour en
        arrière après l'envoi effaçait l'état, et le passage suivant renvoyait
        le même message. La marque est posée avant l'envoi et survit."""
        parcel = self.parcel()
        self.run_cron(self.ups_answer(self.delivered_package()))
        self.assertEqual(parcel.state, "held")
        self.assertTrue(parcel.tracking_notified)
        avant = len(parcel.message_ids)
        # Un second passage ne doit rien renvoyer.
        parcel.sudo().write({"tracking_next_check": fields.Datetime.now()})
        self.run_cron(self.ups_answer(self.delivered_package()))
        self.assertEqual(len(parcel.message_ids), avant)

    def test_la_marque_empeche_un_second_avis_meme_rappelee(self):
        """🔴 La marque, pas la file : `_tracking_notify_arrival` appelée deux
        fois ne doit aviser qu'une fois. Sans elle, le moindre rejeu d'un
        passage renvoyait le texto."""
        parcel = self.parcel()
        self.run_cron(self.ups_answer(self.delivered_package()))
        self.assertTrue(parcel.tracking_notified)
        avant = len(parcel.message_ids)
        parcel._tracking_notify_arrival()
        parcel._tracking_notify_arrival()
        self.assertEqual(len(parcel.message_ids), avant)

    def test_un_jeton_refuse_est_oublie_apres_le_retour_en_arriere(self):
        """🔴 L'oubli du jeton vivait dans le point de sauvegarde que
        l'exception annulait : les colis suivants repartaient tous avec le
        jeton qu'UPS venait de refuser."""
        self.parcel(number="1Z999AA10123456781")
        routes = self.token_route()
        routes[("GET", UPS_TEST + "/api/track/v1/details/*")] = FakeResponse(401)
        with patch(REQUEST, side_effect=FakeHttp(self.token_route())):
            self.ups._ups_token()
        self.assertTrue(self.ups.sudo().ups_token)
        with mute_logger("odoo.addons.bf_property_parcel_tracking.models"
                         ".bf_property_parcel"):
            self.run_cron(routes)
        self.assertFalse(self.ups.sudo().ups_token)

    def test_une_annonce_ne_vise_pas_l_immeuble_d_un_autre_syndicat(self):
        """🔴 La garde du journal ne vérifie que le SYNDICAT, et aucune règle
        ne regarde la fraction : un occupant pouvait faire paraître son nom et
        son numéro de suivi au registre d'un autre syndicat de la même société,
        dans une liste groupée par immeuble."""
        voisin_org = self.env["bf.property.organisation"].create(
            {"name": "Syndicat voisin (banc)", "kind": "syndicat"})
        voisin_immeuble = self.env["bf.property.building"].create(
            {"name": "Immeuble voisin", "organisation_id": voisin_org.id})
        voisine_unit = self.env["bf.property.unit"].create(
            {"name": "909", "building_id": voisin_immeuble.id})
        portal = self.env["res.users"].create({
            "name": "Rosalie Exemple", "login": "suivi_rpc",
            "partner_id": self.resident.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        with self.assertRaises(AccessError):
            self.Parcel.with_user(portal).create({
                "organisation_id": voisin_org.id,
                "building_id": voisin_immeuble.id,
                "unit_id": voisine_unit.id,
                "partner_id": self.resident.id,
                "tracking_carrier": "ups",
                "tracking_number": "1Z999AA10123456791",
            })
        self.assertEqual(self.Parcel.search_count(
            [("organisation_id", "=", voisin_org.id)]), 0)

    def test_une_annonce_sans_fraction_prend_la_sienne(self):
        """Le cas courant : une seule fraction, aucun sélecteur au formulaire.
        Le serveur la connaît, et la fiche ne reste pas sans porte."""
        portal = self.env["res.users"].create({
            "name": "Rosalie Exemple", "login": "suivi_rpc2",
            "partner_id": self.resident.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        parcel = self.Parcel.with_user(portal).create({
            "organisation_id": self.org.id,
            "partner_id": self.resident.id,
            "tracking_carrier": "ups",
            "tracking_number": "1Z999AA10123456792",
        })
        self.assertEqual(parcel.sudo().unit_id, self.unit)
        self.assertEqual(parcel.sudo().building_id, self.building)

    def test_un_occupant_ne_nomme_pas_un_autre_destinataire(self):
        """La liste blanche dit quels champs, pas avec quoi : le serveur impose
        le destinataire même sur un appel direct."""
        voisin = self.env["res.partner"].create({"name": "Voisin Exemple"})
        portal = self.env["res.users"].create({
            "name": "Rosalie Exemple", "login": "suivi_rpc3",
            "partner_id": self.resident.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        parcel = self.Parcel.with_user(portal).create({
            "organisation_id": self.org.id,
            "partner_id": voisin.id,
            "tracking_carrier": "ups",
            "tracking_number": "1Z999AA10123456793",
        })
        self.assertEqual(parcel.sudo().partner_id, self.resident)

    def test_deux_fiches_ne_suivent_pas_le_meme_numero(self):
        self.parcel(number="1Z999AA10123456794")
        with self.assertRaises(Exception):
            with mute_logger("odoo.sql_db"):
                self.parcel(number="1Z999AA10123456794")
                self.env.flush_all()

    def test_une_annonce_sans_suite_finit_par_se_purger(self):
        """🔴 La purge du journal ne juge que les fiches CLOSES : une annonce
        abandonnée n'avait pas de date de fermeture et restait pour toujours,
        alors que la page promet au résident qu'elle disparaît."""
        self.org.log_retention_days = 30
        parcel = self.parcel()
        parcel.sudo().write({"tracking_state": "blocked",
                             "tracking_since": fields.Datetime.now() - timedelta(days=40)})
        self.env.cr.execute(
            "update bf_property_parcel set create_date = %s where id = %s",
            (fields.Datetime.now() - timedelta(days=40), parcel.id))
        parcel.invalidate_recordset(["create_date"])
        self.assertEqual(self.Parcel._cron_purge_announcements(), 1)
        self.assertFalse(parcel.exists())

    def test_une_annonce_encore_suivie_n_est_pas_purgee(self):
        self.org.log_retention_days = 30
        parcel = self.parcel()
        parcel.sudo().write({"tracking_since": fields.Datetime.now() - timedelta(days=40)})
        self.assertEqual(self.Parcel._cron_purge_announcements(), 0)
        self.assertTrue(parcel.exists())

    def test_le_resident_retire_son_annonce_mais_pas_un_colis_recu(self):
        annonce = self.parcel()
        annonce.action_cancel_announcement()
        self.assertFalse(annonce.exists())
        recu = self.parcel(number="1Z999AA10123456795", state="held")
        with self.assertRaises(UserError):
            recu.action_cancel_announcement()

    def test_un_occupant_n_interroge_pas_un_transporteur(self):
        parcel = self.parcel()
        portal = self.env["res.users"].create({
            "name": "Occupante", "login": "suivi_occupante",
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
            "partner_id": self.resident.id,
        })
        with self.assertRaises(AccessError):
            parcel.with_user(portal).action_track_now()

    def test_interroger_un_transporteur_non_branche_est_refuse(self):
        parcel = self.parcel(carrier="other", number="XYZ")
        with self.assertRaises(UserError):
            parcel.with_user(self.manager).action_track_now()


@tagged("post_install", "-at_install")
class TestAnnouncePortal(TrackingCase):

    def setUp(self):
        super().setUp()
        self.portal_user = self.env["res.users"].create({
            "name": "Rosalie Exemple", "login": "suivi_portail",
            "password": "suivi_portail_pwd",
            "partner_id": self.resident.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
        })

    def csrf(self):
        """Le jeton de formulaire, lu dans la page comme un navigateur le fait."""
        page = self.url_open("/my/property/deliveries")
        self.assertEqual(page.status_code, 200)
        found = re.search(r'csrf_token: "([^"]+)"', page.text)
        self.assertTrue(found, "la page doit porter un jeton de formulaire")
        return found.group(1)

    def announce(self, **post):
        values = {"tracking_carrier": "ups",
                  "tracking_number": "1Z999AA10123456784",
                  "unit_id": str(self.unit.id),
                  "csrf_token": self.csrf()}
        values.update(post)
        return self.url_open("/my/property/parcels/announce", data=values,
                             timeout=30)

    def test_la_residente_annonce_son_colis(self):
        self.authenticate("suivi_portail", "suivi_portail_pwd")
        page = self.url_open("/my/property/deliveries")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Les colis que vous attendez", page.text)
        answer = self.announce()
        self.assertEqual(answer.status_code, 200)
        parcel = self.Parcel.search([("tracking_number", "=", "1Z999AA10123456784")])
        self.assertEqual(len(parcel), 1)
        self.assertEqual(parcel.state, "expected")
        self.assertEqual(parcel.partner_id, self.resident)
        self.assertEqual(parcel.unit_id, self.unit)
        self.assertEqual(parcel.tracking_state, "pending")

    def test_une_annonce_sans_numero_ne_cree_rien(self):
        self.authenticate("suivi_portail", "suivi_portail_pwd")
        answer = self.announce(tracking_number="   ")
        self.assertEqual(answer.status_code, 200)
        self.assertIn("error=parcel", answer.url)
        self.assertEqual(self.Parcel.search_count([]), 0)

    def test_une_annonce_est_toujours_au_nom_de_qui_l_annonce(self):
        """🔴 La liste blanche dit quels champs, pas avec quoi.

        Un appel direct qui nomme le voisin comme destinataire écrirait au
        journal de quelqu'un d'autre : la garde du journal ne vérifie que
        l'appartenance au syndicat.
        """
        voisin = self.env["res.partner"].create({"name": "Voisin Exemple"})
        # Le voisin habite une AUTRE fraction du même syndicat : la garde du
        # journal (appartenance au syndicat) le laisse donc passer, et c'est
        # exactement le cas dangereux.
        voisine_unit = self.env["bf.property.unit"].create(
            {"name": "203", "building_id": self.building.id})
        self.env["bf.property.ownership"].create({
            "partner_id": voisin.id, "unit_id": voisine_unit.id,
            "date_start": fields.Date.context_today(self.env.user)
            - timedelta(days=10)})
        self.authenticate("suivi_portail", "suivi_portail_pwd")
        answer = self.announce(partner_id=str(voisin.id))
        self.assertEqual(answer.status_code, 200)
        parcel = self.Parcel.search([("tracking_number", "=", "1Z999AA10123456784")])
        self.assertEqual(parcel.partner_id, self.resident)

    def test_un_etat_force_au_contexte_est_refuse(self):
        """🔴 `create` complète par `default_<champ>` pris au CONTEXTE, que
        l'appelant d'un RPC fournit : la garde du socle regarde les deux."""
        portal = self.env["res.users"].browse(self.portal_user.id)
        with self.assertRaises(AccessError):
            self.Parcel.with_user(portal).with_context(
                default_state="held", default_collected_by="moi").create({
                    "organisation_id": self.org.id,
                    "unit_id": self.unit.id,
                    "partner_id": self.resident.id,
                    "tracking_carrier": "ups",
                    "tracking_number": "1Z999AA10123456788",
                })

    def test_un_occupant_ne_consigne_pas_un_colis_recu(self):
        """Annoncer n'est pas écrire au registre : l'état est imposé."""
        portal = self.env["res.users"].browse(self.portal_user.id)
        parcel = self.Parcel.with_user(portal).create({
            "organisation_id": self.org.id,
            "unit_id": self.unit.id,
            "partner_id": self.resident.id,
            "state": "held",
            "tracking_carrier": "ups",
            "tracking_number": "1Z999AA10123456789",
        })
        self.assertEqual(parcel.sudo().state, "expected")

    def test_une_annonce_ne_vise_jamais_la_fraction_du_voisin(self):
        other = self.env["bf.property.unit"].create(
            {"name": "202", "building_id": self.building.id})
        self.authenticate("suivi_portail", "suivi_portail_pwd")
        answer = self.announce(unit_id=str(other.id))
        self.assertEqual(answer.status_code, 200)
        parcels = self.Parcel.search([])
        # La fraction du voisin n'est pas la sienne : le colis est annoncé sur
        # SA fraction (une seule organisation), jamais sur celle d'à côté.
        self.assertTrue(all(p.unit_id != other for p in parcels))
