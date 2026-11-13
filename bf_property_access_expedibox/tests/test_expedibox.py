"""Essais du connecteur Expedibox.

Rien ne sort : `requests.request` est remplacé pour TOUS les essais de ce
fichier (le gabarit du socle crée des droits, donc pousse, dès qu'un point
naît), et chaque essai dit ensuite ce qu'Expedibox répond. Le harnais mesure ce
qui PART (méthode, adresse, en-têtes, corps), pas ce que le module croit avoir
envoyé, et il refuse tout appel qu'un essai n'a pas prévu.
"""
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.bf_property_access.tests.common import AccessCase

# 🔴 Le module parle par une SESSION (pour refuser l'environnement) : c'est
# donc `Session.request` qu'il faut remplacer. Avec `requests.request`, le
# harnais n'interceptait plus rien et les essais partaient pour de vrai.
REQUEST = "requests.Session.request"


class FakeResponse:
    def __init__(self, status, payload=None, raw=None, headers=None):
        self.status_code = status
        if raw is not None:
            self.content = raw
        else:
            self.content = b"" if payload is None else json.dumps(payload).encode()
        self.text = self.content.decode(errors="replace")
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        return json.loads(self.content.decode())


class FakeHttp:
    """Répond selon (méthode, chemin) et garde la trace de tous les appels.

    Un appel non prévu est gardé dans `inattendus` ET levé : le socle avale les
    exceptions du connecteur, donc l'essai doit pouvoir le constater après coup.
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.inattendus = []

    def __call__(self, method, url, headers=None, json=None, params=None,
                 timeout=None, allow_redirects=None):
        path = url.replace("https://api.expedibox.com", "")
        self.calls.append({"method": method, "path": path, "headers": headers or {},
                           "payload": json, "params": params or {},
                           "timeout": timeout, "allow_redirects": allow_redirects})
        for (route_method, route_path), answer in self.routes.items():
            if route_method == method and (
                route_path == path or (route_path.endswith("*")
                                       and path.startswith(route_path[:-1]))
            ):
                return answer(self) if callable(answer) else answer
        self.inattendus.append("%s %s" % (method, path))
        raise AssertionError("appel non prévu par l'essai : %s %s" % (method, path))

    def paths(self, method=None):
        return [c["path"] for c in self.calls if method in (None, c["method"])]

    def payloads(self, method):
        return [c["payload"] for c in self.calls if c["method"] == method]


@tagged("post_install", "-at_install")
class TestExpedibox(AccessCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connector = cls.env["bf.property.access.connector"].create({
            "name": "Casier Expedibox (banc)",
            "kind": "expedibox",
            "expedibox_app_token": "jeton-du-banc",
            "expedibox_organization_id": "N5vM3LDX",
        })
        cls.alice.write({"email": "alice@exemple.test"})

    def setUp(self):
        super().setUp()
        # 🔴 Porte fermée par défaut : créer un point suffit à faire pousser le
        # socle. Sans ce garde-fou, un essai futur enverrait un vrai appel à
        # api.expedibox.com depuis le banc.
        self.porte = patch(REQUEST, side_effect=AssertionError(
            "aucun appel sortant hors d'un patch d'essai"))
        self.porte.start()
        self.addCleanup(self.rouvrir_la_porte)

    def rouvrir_la_porte(self):
        """`autospec` refuse de se poser sur un objet déjà simulé : un essai qui
        a besoin de la vraie signature arrête d'abord la garde."""
        try:
            self.porte.stop()
        except RuntimeError:
            pass

    # ── Outils ───────────────────────────────────────────────────────────

    def locker_point(self, **kw):
        vals = {"name": "Boîte à colis", "point_type": "parcel",
                "connector_id": self.connector.id,
                "expedibox_locker_id": "qovN9wxk"}
        vals.update(kw)
        return self.make_point(**vals)

    def created(self, recipient_id="rec-1", identifier=None, locker="qovN9wxk",
                **kw):
        valeurs = {"id": recipient_id, "custom_identifier": identifier,
                   "locker_id": locker}
        valeurs.update(kw)
        return valeurs

    def arrive(self, partner=None, unit=None):
        partner = partner or self.alice
        unit = unit or self.unit1
        self.own(partner, unit)
        self.sync()
        resident = self.resident(partner, unit)
        self.assertEqual(resident.state, "active")
        return resident, resident.grant_ids.order_ids

    def inscrire(self, partner=None, unit=None, ref="rec-7"):
        """Fait arriver un résident avec un casier qui répond bien."""
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, self.created(ref)),
        })
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive(partner, unit)
        self.assertEqual(http.inattendus, [])
        self.assertEqual(order.state, "done")
        return resident, http

    def remove_order(self, resident):
        wizard = self.env["bf.property.access.departure"].with_user(self.manager).create(
            {"resident_ids": [(6, 0, resident.ids)], "date_keys": self.today})
        wizard.action_confirm()
        return resident.grant_ids.order_ids.filtered(lambda o: o.action == "remove")

    # ── Arrivée ──────────────────────────────────────────────────────────

    def test_le_resident_qui_arrive_est_inscrit_au_casier(self):
        point = self.locker_point()
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-7")),
        })
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
        grant = resident.grant_ids
        self.assertEqual(grant.point_id, point)
        self.assertEqual((order.action, order.state), ("add", "done"))
        self.assertEqual(grant.state, "issued")
        self.assertEqual(grant.external_ref, "rec-7")
        self.assertFalse(order.activity_ids, "aucune activité : le casier a répondu")
        sent = http.payloads("POST")[0]
        self.assertEqual(sent, {
            "first_name": "Alice", "last_name": "Exemple",
            "unit": "101", "locker_id": "qovN9wxk",
            "custom_identifier": grant._expedibox_identifier(),
            "email": "alice@exemple.test",
        })
        appel = [c for c in http.calls if c["method"] == "POST"][0]
        self.assertEqual(appel["headers"]["X-App-Token"], "jeton-du-banc")
        self.assertIn("Symbifox", appel["headers"]["User-Agent"])
        self.assertTrue(appel["timeout"], "un appel sans délai d'attente pend")
        self.assertFalse(appel["allow_redirects"])

    def test_le_mobile_part_quand_il_n_y_a_pas_de_courriel(self):
        self.locker_point()
        self.bruno.write({"mobile": "514 555-0142 poste 2"})
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-8")),
        })
        with patch(REQUEST, side_effect=http):
            self.arrive(self.bruno, self.unit2)
        payload = http.payloads("POST")[0]
        self.assertEqual(payload["mobile_number"], "5145550142")
        self.assertNotIn("email", payload)

    def test_un_numero_fixe_ne_part_jamais_au_casier(self):
        """Le casier avise par message texte : un fixe n'avise personne, et
        l'avis remis au résident ne nomme que le courriel et le mobile."""
        self.locker_point()
        self.bruno.write({"phone": "450 555-0177"})
        resident, order = self.arrive(self.bruno, self.unit2)
        self.assertEqual(order.state, "waiting")
        self.assertEqual(order.activity_ids.user_id, self.concierge)
        self.assertIn("mobile", order.last_message)
        self.assertEqual(resident.grant_ids.state, "to_issue")

    def test_le_courriel_seul_part_par_defaut_les_deux_sur_demande(self):
        self.alice.write({"mobile": "514 555-0142"})
        self.locker_point()
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-9")),
        })
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
        self.assertNotIn("mobile_number", http.payloads("POST")[0],
                         "minimisation : un seul moyen de joindre la personne")
        # Le même ordre rejoué, connecteur réglé sur « les deux » : mesuré sur
        # le fil, pas sur ce que rend la méthode.
        self.connector.expedibox_contact = "both"
        order.sudo()._bf_set({"state": "pending", "attempts": 0})
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-9b")),
        })
        with patch(REQUEST, side_effect=http):
            order.sudo()._bf_send()
        payload = http.payloads("POST")[0]
        self.assertEqual(payload["email"], "alice@exemple.test")
        self.assertEqual(payload["mobile_number"], "5145550142")

    def test_un_destinataire_deja_inscrit_n_est_pas_cree_deux_fois(self):
        self.locker_point()
        resident, _http = self.inscrire(ref="rec-3")
        grant = resident.grant_ids
        order = grant.order_ids
        order.sudo()._bf_set({"state": "pending", "attempts": 0})
        already = self.created("rec-3", grant._expedibox_identifier(),
                              first_name="Alice", last_name="Exemple", unit="101")
        http = FakeHttp({("GET", "/recipients/"): FakeResponse(200, [already])})
        with patch(REQUEST, side_effect=http):
            order.sudo()._bf_send()
        self.assertEqual(order.state, "done")
        self.assertEqual(grant.external_ref, "rec-3")
        self.assertEqual(http.paths("POST"), [], "aucune création en double")

    def test_un_destinataire_d_un_autre_logiciel_ne_compte_pas(self):
        """Même casier, identifiant d'un autre système : il faut inscrire la
        personne, pas s'approprier le destinataire du voisin."""
        self.locker_point()
        etranger = self.created("rec-bl", "buildinglink-42",
                                first_name="Autre", last_name="Personne")
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, [etranger]),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-ici")),
        })
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
        self.assertEqual(order.state, "done")
        self.assertEqual(resident.grant_ids.external_ref, "rec-ici")
        self.assertEqual(len(http.paths("POST")), 1)

    def test_un_destinataire_d_un_autre_casier_ne_compte_pas(self):
        """Un même jeton voit plusieurs casiers : la liste n'est pas filtrée par l'API."""
        self.locker_point()
        ailleurs = self.created("rec-ailleurs", locker="AUTRE")
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-ici")),
        })
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
        grant = resident.grant_ids
        ailleurs["custom_identifier"] = grant._expedibox_identifier()
        order.sudo()._bf_set({"state": "pending", "attempts": 0})
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, [ailleurs]),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-ici2")),
        })
        with patch(REQUEST, side_effect=http):
            order.sudo()._bf_send()
        self.assertEqual(order.state, "done")
        self.assertEqual(order.external_ref, "rec-ici2",
                         "le destinataire d'un autre casier ne vaut pas inscription ici")
        self.assertEqual(http.payloads("POST")[0]["locker_id"], "qovN9wxk")

    def test_un_nom_change_est_corrige_a_l_inscription_retrouvee(self):
        self.locker_point()
        resident, _http = self.inscrire()
        grant = resident.grant_ids
        order = grant.order_ids
        order.sudo()._bf_set({"state": "pending", "attempts": 0})
        vieux = self.created("rec-7", grant._expedibox_identifier(),
                             first_name="Alicia", last_name="Exemple", unit="101")
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, [vieux]),
            ("PATCH", "/recipients/rec-7/"): FakeResponse(200, self.created("rec-7")),
        })
        with patch(REQUEST, side_effect=http):
            order.sudo()._bf_send()
        self.assertEqual(order.state, "done")
        self.assertEqual(http.payloads("PATCH"), [{"first_name": "Alice"}])

    # ── Ce qui revient au responsable ────────────────────────────────────

    def test_sans_courriel_ni_mobile_l_ordre_revient_au_responsable(self):
        self.locker_point()
        resident, order = self.arrive(self.chloe, self.unit2)
        self.assertEqual(order.state, "waiting")
        self.assertEqual(order.activity_ids.user_id, self.concierge)
        self.assertIn("courriel", order.last_message)
        # Le bouton de renvoi est réservé aux gestionnaires : le message ne
        # doit pas envoyer le concierge sur un bouton qui lui est interdit.
        self.assertIn("gestionnaire", order.last_message)
        self.assertEqual(resident.grant_ids.state, "to_issue")

    def test_sans_jeton_l_ordre_revient_au_responsable(self):
        self.connector.expedibox_app_token = False
        self.locker_point()
        resident, order = self.arrive()
        self.assertEqual(order.state, "waiting")
        self.assertEqual(order.activity_ids.user_id, self.concierge)
        self.assertIn("jeton", order.last_message)

    def test_un_casier_efface_sous_la_contrainte_ne_part_pas_sans_casier(self):
        """Dernier rempart : si le champ est vidé hors de l'ORM (SQL, module tiers),
        l'ordre revient au responsable au lieu d'appeler Expedibox sans casier."""
        point = self.locker_point()
        self.env.cr.execute(
            "update bf_property_access_point set expedibox_locker_id = null where id = %s",
            (point.id,))
        point.invalidate_recordset(["expedibox_locker_id"])
        resident, order = self.arrive()
        self.assertEqual(order.state, "waiting")
        self.assertEqual(order.activity_ids.user_id, self.concierge)
        self.assertIn("casier", order.last_message)

    def test_un_acces_temporaire_ne_s_inscrit_pas_au_casier(self):
        """Le casier n'a pas de plage de validité, et l'avis ne couvre que les
        résidents : un visiteur se fait à la main, pas en silence."""
        point = self.locker_point()
        now = fields.Datetime.now()
        temp = self.env["bf.property.access.pass"].with_user(self.manager).create({
            "kind": "contractor", "holder_name": "Plomberie Exemple",
            "building_id": self.building.id, "point_ids": [(6, 0, [point.id])],
            "date_from": now, "date_to": now + timedelta(hours=4),
        })
        temp.action_confirm()
        order = temp.grant_ids.order_ids
        self.assertEqual(order.state, "waiting")
        self.assertEqual(order.activity_ids.user_id, self.concierge)
        self.assertIn("accès temporaire", order.last_message)

    def test_un_refus_d_expedibox_revient_au_responsable_sans_rejeu(self):
        self.locker_point()
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(
                400, {"unit": ["Ce champ est obligatoire."]}),
        })
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
        self.assertEqual(order.state, "waiting")
        self.assertEqual(order.activity_ids.user_id, self.concierge)
        self.assertIn("400", order.last_message)
        self.assertEqual(len(http.paths("POST")), 1, "un refus ne se rejoue pas")

    def test_une_redirection_revient_au_responsable(self):
        """Une redirection veut dire qu'on ne parle pas à l'API (pare-feu, portail)."""
        self.locker_point()
        http = FakeHttp({("GET", "/recipients/"): FakeResponse(
            302, raw=b"", headers={"location": "https://ailleurs.test/"})})
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
        self.assertEqual(order.state, "waiting")
        self.assertIn("redirection", order.last_message)

    def test_une_reponse_enorme_revient_au_responsable(self):
        self.locker_point()
        http = FakeHttp({("GET", "/recipients/"): FakeResponse(
            200, raw=b"x" * (9 * 1024 * 1024))})
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
        self.assertEqual(order.state, "waiting")
        self.assertIn("octets", order.last_message)

    def test_une_panne_laisse_l_ordre_a_renvoyer_puis_le_remet(self):
        self.locker_point()
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(503, raw=b"Service Unavailable"),
        })
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
            self.assertEqual((order.state, order.attempts), ("pending", 1))
            self.assertFalse(order.activity_ids, "une panne n'est pas un ordre à la main")
            order.sudo()._bf_send()
            self.assertEqual((order.state, order.attempts), ("pending", 2))
            order.sudo()._bf_send()
        # Au troisième essai, le socle passe la main : l'ordre finit « à faire »
        # (l'état « échec » ne dure que le temps de la remise), avec la raison.
        self.assertEqual((order.state, order.attempts), ("waiting", 3))
        self.assertEqual(order.activity_ids.user_id, self.concierge)
        self.assertIn("3 fois", order.last_message)
        self.assertIn("503", order.last_message)

    def test_un_refus_ne_laisse_qu_une_activite_meme_a_un_seul_essai(self):
        """Avec max_attempts = 1, le socle passe la main lui aussi : une seule activité."""
        self.connector.max_attempts = 1
        self.locker_point()
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(400, {"unit": ["obligatoire"]}),
        })
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
        self.assertEqual(order.state, "waiting")
        self.assertEqual(len(order.activity_ids), 1,
                         "deux activités pour un ordre : l'une resterait orpheline")
        self.assertEqual(order.hand_over_activity_id, order.activity_ids)

    # ── Départ ───────────────────────────────────────────────────────────

    def test_le_depart_confirme_retire_le_destinataire_du_casier(self):
        self.locker_point()
        resident, _http = self.inscrire()
        grant = resident.grant_ids
        mine = self.created("rec-7", grant._expedibox_identifier())
        http = FakeHttp({
            ("GET", "/recipients/rec-7/"): FakeResponse(200, mine),
            ("DELETE", "/recipients/rec-7/"): FakeResponse(204),
        })
        with patch(REQUEST, side_effect=http):
            order = self.remove_order(resident)
        self.assertEqual(order.state, "done")
        self.assertEqual(grant.state, "removed")
        self.assertEqual(http.paths("DELETE"), ["/recipients/rec-7/"])

    def test_une_reference_etrangere_n_est_jamais_supprimee(self):
        self.locker_point()
        resident, _http = self.inscrire()
        # Quelqu'un a retouché la référence du droit à la main : elle désigne
        # le destinataire d'un autre logiciel.
        resident.grant_ids.sudo().write({"external_ref": "rec-99"})
        autre = self.created("rec-99", "buildinglink-42")
        http = FakeHttp({("GET", "/recipients/rec-99/"): FakeResponse(200, autre)})
        with patch(REQUEST, side_effect=http):
            order = self.remove_order(resident)
        self.assertEqual(http.paths("DELETE"), [], "aucune suppression")
        self.assertEqual(order.state, "waiting")
        self.assertEqual(order.activity_ids.user_id, self.concierge)
        self.assertIn("rec-99", order.last_message)

    def test_un_destinataire_d_un_autre_casier_n_est_jamais_supprime(self):
        self.locker_point()
        resident, _http = self.inscrire()
        grant = resident.grant_ids
        # Bon identifiant, mais le destinataire est inscrit sur un autre casier.
        ailleurs = self.created("rec-7", grant._expedibox_identifier(), locker="AUTRE")
        http = FakeHttp({("GET", "/recipients/rec-7/"): FakeResponse(200, ailleurs)})
        with patch(REQUEST, side_effect=http):
            order = self.remove_order(resident)
        self.assertEqual(http.paths("DELETE"), [])
        self.assertEqual(order.state, "waiting")
        self.assertIn("AUTRE", order.last_message)

    def test_un_destinataire_deja_absent_clot_le_retrait(self):
        self.locker_point()
        resident, _http = self.inscrire()
        http = FakeHttp({("GET", "/recipients/rec-7/"): FakeResponse(404, {"detail": "x"})})
        with patch(REQUEST, side_effect=http):
            order = self.remove_order(resident)
        self.assertEqual(order.state, "done")
        self.assertEqual(resident.grant_ids.state, "removed")
        self.assertEqual(http.paths("DELETE"), [])

    def test_un_destinataire_efface_entre_temps_clot_le_retrait(self):
        """Quelqu'un l'a retiré au casier entre la relecture et la suppression."""
        self.locker_point()
        resident, _http = self.inscrire()
        mine = self.created("rec-7", resident.grant_ids._expedibox_identifier())
        http = FakeHttp({
            ("GET", "/recipients/rec-7/"): FakeResponse(200, mine),
            ("DELETE", "/recipients/rec-7/"): FakeResponse(404, {"detail": "introuvable"}),
        })
        with patch(REQUEST, side_effect=http):
            order = self.remove_order(resident)
        self.assertEqual(order.state, "done")
        self.assertEqual(resident.grant_ids.state, "removed")

    def test_un_retrait_sans_reference_retrouve_le_destinataire(self):
        self.locker_point()
        resident, _http = self.inscrire()
        grant = resident.grant_ids
        grant.sudo().write({"external_ref": False})
        mine = self.created("rec-7", grant._expedibox_identifier())
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, [mine]),
            ("GET", "/recipients/rec-7/"): FakeResponse(200, mine),
            ("DELETE", "/recipients/rec-7/"): FakeResponse(204),
        })
        with patch(REQUEST, side_effect=http):
            order = self.remove_order(resident)
        self.assertEqual(order.state, "done")
        self.assertEqual(http.paths("DELETE"), ["/recipients/rec-7/"])

    def test_un_retrait_sans_inscription_reperable_revient_au_responsable(self):
        """🔴 Le chemin NORMAL quand le concierge a inscrit la personne à la main :
        son destinataire ne porte pas notre identifiant. Conclure « personne à
        retirer » laisserait la personne au casier avec un journal qui dit le
        contraire."""
        self.locker_point()
        resident, order = self.arrive(self.chloe, self.unit2)
        # Sans courriel ni mobile : l'ordre est revenu au concierge, qui a
        # inscrit Chloé au casier à la main et coché l'ordre.
        self.assertEqual(order.state, "waiting")
        order.with_user(self.concierge).action_done()
        self.assertEqual(resident.grant_ids.state, "issued")
        self.assertFalse(resident.grant_ids.external_ref)
        http = FakeHttp({("GET", "/recipients/"): FakeResponse(200, [])})
        with patch(REQUEST, side_effect=http):
            retrait = self.remove_order(resident)
        self.assertEqual(retrait.state, "waiting",
                         "un retrait qui ne trouve rien n'est pas un retrait fait")
        self.assertEqual(resident.grant_ids.state, "to_remove")
        self.assertEqual(retrait.activity_ids.user_id, self.concierge)
        self.assertIn("à la main", retrait.last_message)
        self.assertEqual(http.paths("DELETE"), [])

    # ── Configuration ────────────────────────────────────────────────────

    def test_un_connecteur_de_casier_exige_un_point_de_type_colis(self):
        with self.assertRaises(ValidationError):
            self.make_point(name="Portier branché au casier", point_type="entrance",
                            connector_id=self.connector.id,
                            expedibox_locker_id="qovN9wxk")

    def test_un_point_relie_a_expedibox_exige_l_identifiant_du_casier(self):
        with self.assertRaises(ValidationError):
            self.make_point(name="Colis sans casier", point_type="parcel",
                            connector_id=self.connector.id)

    def test_basculer_un_connecteur_manuel_vers_expedibox_est_refuse(self):
        """La contrainte du point ne se rejoue pas quand c'est le connecteur qui change."""
        manuel = self.env["bf.property.access.connector"].create(
            {"name": "À la main (banc)", "kind": "manual"})
        self.make_point(name="Portier sud", connector_id=manuel.id)
        with self.assertRaises(ValidationError):
            manuel.kind = "expedibox"

    def test_l_identifiant_de_casier_se_nettoie(self):
        """Recopié depuis « qovN9wxk — Casier A » : une espace au bout et plus
        rien ne se reconnaît au casier."""
        point = self.locker_point(expedibox_locker_id="  qovN9wxk ")
        self.assertEqual(point.expedibox_locker_id, "qovN9wxk")
        point.write({"expedibox_locker_id": "qovN9wxk "})
        self.assertEqual(point.expedibox_locker_id, "qovN9wxk")

    def test_changer_le_casier_retire_a_l_ancien_et_reinscrit_au_nouveau(self):
        point = self.locker_point()
        resident, _http = self.inscrire()
        grant = resident.grant_ids
        mine = self.created("rec-7", grant._expedibox_identifier())
        http = FakeHttp({
            ("GET", "/recipients/rec-7/"): FakeResponse(200, mine),
            ("DELETE", "/recipients/rec-7/"): FakeResponse(204),
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-neuf",
                                                                    locker="7pLm2aQr")),
        })
        with patch(REQUEST, side_effect=http):
            point.write({"expedibox_locker_id": "7pLm2aQr"})
        self.assertEqual(http.inattendus, [])
        self.assertEqual(http.paths("DELETE"), ["/recipients/rec-7/"],
                         "le retrait part à l'ANCIEN casier")
        self.assertEqual(grant.state, "removed")
        neuf = resident.grant_ids.filtered(lambda g: g.state == "issued")
        self.assertEqual(len(neuf), 1, "un droit neuf pour le nouveau casier")
        self.assertEqual(neuf.external_ref, "rec-neuf")
        self.assertEqual(http.payloads("POST")[0]["locker_id"], "7pLm2aQr")

    # ── Identifiant de rapprochement ─────────────────────────────────────

    def test_l_identifiant_nomme_la_base_la_personne_et_le_point(self):
        point = self.locker_point()
        resident, _http = self.inscrire()
        grant = resident.grant_ids
        identifier = grant._expedibox_identifier()
        uuid = self.env["ir.config_parameter"].sudo().get_param("database.uuid")
        self.assertTrue(uuid, "le banc doit avoir un database.uuid")
        self.assertTrue(identifier.startswith("sfx-%s-" % uuid.replace("-", "")[:8]))
        self.assertEqual(identifier, "sfx-%s-p%s-pt%s" % (
            uuid.replace("-", "")[:8], self.alice.id, point.id))
        self.assertLessEqual(len(identifier), 50)

    def test_l_identifiant_survit_a_la_perte_du_droit(self):
        """🔴 Le socle pousse dans la transaction qui crée le droit : si elle est
        rejetée après l'appel, le droit et son id disparaissent. L'identifiant
        ne doit donc pas dépendre de l'id du droit, sinon le destinataire resté
        au casier devient intouchable."""
        self.locker_point()
        resident, _http = self.inscrire()
        grant = resident.grant_ids
        avant = grant._expedibox_identifier()
        grant.sudo().unlink()
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-7")),
        })
        with patch(REQUEST, side_effect=http):
            resident.sudo()._bf_ensure_grants()
        apres = resident.grant_ids._expedibox_identifier()
        self.assertNotEqual(resident.grant_ids.id, grant.id)
        self.assertEqual(avant, apres,
                         "le droit recréé doit retrouver son destinataire au casier")

    def test_sans_identifiant_de_base_rien_ne_part(self):
        """Échouer fermé : deux bases sans database.uuid se reconnaîtraient
        l'une l'autre au casier et s'effaceraient mutuellement."""
        self.locker_point()
        # Odoo refuse de supprimer ce paramètre : on le vide, ce qui est le cas
        # réel (une base neutralisée dont le paramètre a sauté).
        self.env["ir.config_parameter"].sudo().search(
            [("key", "=", "database.uuid")]).write({"value": ""})
        with self.assertRaises(UserError):
            self.env["bf.property.access.grant"]._expedibox_prefix()

    # ── Mise en forme ────────────────────────────────────────────────────

    def test_le_numero_se_normalise_ou_se_tait(self):
        normalise = self.connector._expedibox_number
        self.assertEqual(normalise("514 555-0142"), "5145550142")
        self.assertEqual(normalise("+1 (514) 555-0142"), "+15145550142")
        self.assertEqual(normalise("514 555-0142 poste 2"), "5145550142")
        self.assertEqual(normalise("514 555-0142, 2"), "5145550142")
        self.assertEqual(normalise("1-877-555-0188 ext 3"), "18775550188")
        self.assertEqual(normalise("123"), "", "trop court pour être un numéro")
        self.assertEqual(normalise("+1 514 513 2535 0000 0000"), "",
                         "plus de 15 caractères : Expedibox le refuserait")
        self.assertEqual(normalise(False), "")

    def test_le_nom_d_un_seul_mot_et_la_personne_morale_remplissent_les_deux_champs(self):
        self.locker_point()
        seul = self.env["res.partner"].create(
            {"name": "Madonna", "email": "madonna@exemple.test"})
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-m")),
        })
        with patch(REQUEST, side_effect=http):
            self.arrive(seul, self.unit2)
        payload = http.payloads("POST")[0]
        self.assertEqual((payload["first_name"], payload["last_name"]),
                         ("Madonna", "Madonna"))
        morale = self.env["res.partner"].create(
            {"name": "9123-4567 Québec inc.", "is_company": True,
             "email": "compta@exemple.test"})
        grant = self.env["bf.property.access.grant"].new(
            {"partner_id": morale.id})
        self.assertEqual(self.connector._expedibox_names(grant),
                         ("9123-4567 Québec inc.", "9123-4567 Québec inc."))

    def test_une_chambre_sans_fraction_part_avec_un_logement_non_vide(self):
        """Expedibox exige un logement : une maison de chambres n'en a pas."""
        self.locker_point()
        self.bruno.write({"email": "bruno@exemple.test"})
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-ch")),
        })
        with patch(REQUEST, side_effect=http):
            # Un occupant déclaré sur l'immeuble, sans fraction (chambre).
            chambre = self.Resident.with_user(self.manager).create({
                "partner_id": self.bruno.id, "building_id": self.building.id,
            })
        self.assertEqual(chambre.state, "active")
        self.assertFalse(chambre.unit_id)
        self.assertEqual(http.inattendus, [])
        payload = http.payloads("POST")[0]
        self.assertEqual((payload["first_name"], payload["unit"]), ("Bruno", "-"))

    # ── L'appel sortant ──────────────────────────────────────────────────

    def test_la_session_refuse_l_environnement(self):
        """Ni mandataire, ni ~/.netrc, ni paquet d'autorités pris dans les
        variables du conteneur ne doivent pouvoir dérouter l'appel."""
        self.locker_point()
        self.porte.stop()
        vus = []

        def capture(session, method, url, **kw):
            vus.append(session.trust_env)
            if method == "POST":
                return FakeResponse(201, self.created("rec-env"))
            return FakeResponse(200, [])

        with patch(REQUEST, autospec=True, side_effect=capture):
            resident, order = self.arrive()
        self.assertEqual(order.state, "done")
        self.assertTrue(vus, "aucun appel mesuré")
        self.assertEqual(set(vus), {False})

    def test_une_reference_tordue_ne_change_pas_l_adresse(self):
        """La référence d'un droit s'écrit à la main : « ../lockers » ne doit
        pas pouvoir viser autre chose que /recipients/."""
        self.locker_point()
        resident, _http = self.inscrire()
        resident.grant_ids.sudo().write({"external_ref": "../lockers"})
        http = FakeHttp({("GET", "/recipients/*"): FakeResponse(404, {"detail": "x"})})
        with patch(REQUEST, side_effect=http):
            order = self.remove_order(resident)
        self.assertEqual(http.paths("GET"), ["/recipients/..%2Flockers/"])
        self.assertEqual(order.state, "done", "404 : le destinataire n'y est plus")

    def test_un_retrait_seulement_accepte_ne_passe_pas_pour_fait(self):
        """202 veut dire « accepté », pas « retiré » : l'ordre se rejoue."""
        self.locker_point()
        resident, _http = self.inscrire()
        mine = self.created("rec-7", resident.grant_ids._expedibox_identifier())
        http = FakeHttp({
            ("GET", "/recipients/rec-7/"): FakeResponse(200, mine),
            ("DELETE", "/recipients/rec-7/"): FakeResponse(202, {"detail": "accepté"}),
        })
        with patch(REQUEST, side_effect=http):
            order = self.remove_order(resident)
        self.assertEqual(order.state, "pending")
        self.assertEqual(resident.grant_ids.state, "to_remove")

    def test_un_destinataire_cree_sur_un_autre_casier_est_refuse(self):
        """Sinon on ne le saurait qu'au départ, quand la garde refuserait."""
        self.locker_point()
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-x",
                                                                    locker="AUTRE")),
        })
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
        self.assertEqual(order.state, "waiting")
        self.assertEqual(resident.grant_ids.state, "to_issue")
        self.assertIn("AUTRE", order.last_message)

    def test_un_destinataire_sans_identifiant_de_casier_ne_compte_pas(self):
        """Une réponse sans `id` ne vaut pas inscription : sans ça, le droit
        passait « émis » sans référence, et le départ se perdait."""
        point = self.locker_point()
        # L'identifiant ne dépend que de la personne et du point : il se calcule
        # AVANT que le droit existe, donc l'essai peut poser le cas dangereux,
        # une réponse qui porte NOTRE identifiant sans `id`.
        Grant = self.env["bf.property.access.grant"]
        identifier = "%sp%s-pt%s" % (Grant._expedibox_prefix(), self.alice.id, point.id)
        sans_id = {"id": "", "custom_identifier": identifier, "locker_id": "qovN9wxk"}
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, [sans_id]),
            ("POST", "/recipients/"): FakeResponse(201, self.created("rec-ok")),
        })
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
        self.assertEqual(identifier, resident.grant_ids._expedibox_identifier())
        self.assertEqual(order.state, "done")
        self.assertEqual(len(http.paths("POST")), 1,
                         "une réponse sans `id` ne vaut pas inscription")
        self.assertEqual(resident.grant_ids.external_ref, "rec-ok")

    # ── Le jeton, les vues ───────────────────────────────────────────────

    def test_les_formulaires_montrent_le_casier_mais_cachent_le_jeton(self):
        """Le concierge (Consultation) lit les connecteurs : il n'a rien à faire du jeton."""
        self.locker_point()
        Point = self.env["bf.property.access.point"]
        arch = Point.with_user(self.manager).get_view(
            self.env.ref("bf_property_access.view_access_point_form").id, "form")["arch"]
        self.assertIn("expedibox_locker_id", arch)
        Connector = self.env["bf.property.access.connector"]
        vue = self.env.ref("bf_property_access.view_access_connector_form").id
        self.assertIn("expedibox_app_token",
                      Connector.with_user(self.manager).get_view(vue, "form")["arch"])
        self.assertNotIn("expedibox_app_token",
                         Connector.with_user(self.concierge).get_view(vue, "form")["arch"])

    def test_le_concierge_ne_lit_pas_le_jeton(self):
        with self.assertRaises(AccessError):
            self.connector.with_user(self.concierge).read(["expedibox_app_token"])
        self.assertTrue(
            self.connector.with_user(self.concierge).read(["name", "kind"]),
            "le reste du connecteur reste lisible")

    def test_le_jeton_ne_paraît_dans_aucun_message(self):
        self.locker_point()
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, []),
            ("POST", "/recipients/"): FakeResponse(400, {"unit": ["obligatoire"]}),
        })
        with patch(REQUEST, side_effect=http):
            resident, order = self.arrive()
        traces = [order.last_message or ""] + [
            a.note or "" for a in order.activity_ids] + [
            m.body or "" for m in order.message_ids]
        for trace in traces:
            self.assertNotIn("jeton-du-banc", trace)

    # ── Le bouton des casiers ────────────────────────────────────────────

    def test_le_bouton_liste_tous_les_casiers_page_apres_page(self):
        def page(http):
            offset = http.calls[-1]["params"].get("offset")
            if offset:
                return FakeResponse(200, {"count": 3, "next": None, "results": [
                    {"id": "7pLm2aQr", "name": "Casier B", "test": False}]})
            return FakeResponse(200, {"count": 3, "next": "https://api.expedibox.com/lockers/?offset=2",
                                      "results": [
                                          {"id": "qovN9wxk", "name": "Casier A", "test": True},
                                          {"id": "zz", "name": "Casier Z", "test": False}]})
        http = FakeHttp({("GET", "/lockers/"): page})
        with patch(REQUEST, side_effect=http):
            action = self.connector.with_user(self.manager).action_expedibox_lockers()
        self.assertEqual(len(http.paths("GET")), 2, "la deuxième page est lue")
        message = action["params"]["message"]
        for attendu in ("qovN9wxk", "7pLm2aQr", "Casier B"):
            self.assertIn(attendu, message)
        self.assertIn("essai", message, "le casier de test est signalé")

    def test_le_bouton_des_casiers_est_refuse_au_concierge(self):
        with self.assertRaises(AccessError):
            self.connector.with_user(self.concierge).action_expedibox_lockers()

    # ── Passage de nuit ──────────────────────────────────────────────────

    def test_le_passage_de_nuit_corrige_un_nom_au_casier(self):
        self.locker_point()
        resident, _http = self.inscrire()
        grant = resident.grant_ids
        self.alice.write({"name": "Alice Exemple-Tremblay"})
        vieux = self.created("rec-7", grant._expedibox_identifier(),
                             first_name="Alice", last_name="Exemple", unit="101")
        http = FakeHttp({
            ("GET", "/recipients/"): FakeResponse(200, [vieux]),
            ("PATCH", "/recipients/rec-7/"): FakeResponse(200, self.created("rec-7")),
        })
        with patch(REQUEST, side_effect=http):
            self.env["bf.property.access.connector"].sudo()._expedibox_reconcile()
        self.assertEqual(http.payloads("PATCH"), [{"last_name": "Exemple-Tremblay"}])
        self.assertTrue(any("corrigée" in (m.body or "")
                            for m in grant.point_id.message_ids))

    def test_le_passage_de_nuit_signale_un_destinataire_sans_droit_sans_le_supprimer(self):
        point = self.locker_point()
        resident, _http = self.inscrire()
        grant = resident.grant_ids
        orphelin = self.created("rec-orphelin",
                                grant._expedibox_prefix() + "p999-pt999")
        mien = self.created("rec-7", grant._expedibox_identifier(),
                            first_name="Alice", last_name="Exemple", unit="101")
        http = FakeHttp({("GET", "/recipients/"): FakeResponse(200, [mien, orphelin])})
        with patch(REQUEST, side_effect=http):
            self.env["bf.property.access.connector"].sudo()._expedibox_reconcile()
        self.assertEqual(http.paths("DELETE"), [],
                         "le logiciel ne retire personne d'un casier de lui-même")
        self.assertTrue(any("rec-orphelin" in (m.body or "")
                            for m in point.message_ids))
        activite = self.env["mail.activity"].search([
            ("res_model", "=", "bf.property.access.point"), ("res_id", "=", point.id)])
        self.assertTrue(activite, "personne n'a été averti")
        self.assertEqual(activite.user_id, self.concierge)

    def test_le_passage_de_nuit_ignore_le_destinataire_d_un_autre_logiciel(self):
        point = self.locker_point()
        resident, _http = self.inscrire()
        etranger = self.created("rec-bl", "buildinglink-42")
        mien = self.created("rec-7", resident.grant_ids._expedibox_identifier(),
                            first_name="Alice", last_name="Exemple", unit="101")
        http = FakeHttp({("GET", "/recipients/"): FakeResponse(200, [mien, etranger])})
        with patch(REQUEST, side_effect=http):
            self.env["bf.property.access.connector"].sudo()._expedibox_reconcile()
        self.assertEqual(http.paths("DELETE"), [])
        self.assertEqual(http.paths("PATCH"), [])
        self.assertFalse(self.env["mail.activity"].search([
            ("res_model", "=", "bf.property.access.point"), ("res_id", "=", point.id)]))

    # ── Vie privée ───────────────────────────────────────────────────────

    def test_l_efvp_declare_ce_que_le_casier_recoit(self):
        self.locker_point()
        extras = self.org._bf_access_efvp_extras()
        section = [e for e in extras if "Expedibox" in e["title"]]
        self.assertTrue(section, "l'EFVP ne nomme pas le casier connecté")
        texte = " ".join(section[0]["items"])
        for attendu in ("courriel", "18.3", "identifiant", "écran", "art. 28",
                        "accès temporaire"):
            self.assertIn(attendu, texte)

    def test_sans_casier_connecte_l_efvp_ne_dit_rien(self):
        self.make_point()
        self.assertEqual(
            [e for e in self.org._bf_access_efvp_extras() if "Expedibox" in e["title"]],
            [])
