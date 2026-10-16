"""Le dépôt du portail Progression par le stockage S3 du socle.

Ce qui compte : le jeton du brouillon de transfert ne sort jamais ; un petit
fichier finit en pièce jointe, son objet S3 est effacé et son brouillon reste au
ménage horaire du socle ; les brouillons du pont sont cachés à tout usager ; un
gros fichier reste
dans le seau, hors du ménage du socle, jusqu'à son échéance ; chaque route
repasse par la garde du portail et par celle de l'envoi.

Toute la passerelle S3 est remplacée sur `bf_securetransfer.models.s3` : pas de
réseau, et les routes HTTP voient les mêmes remplacements (même processus).
"""
import contextlib
import re
import json

import psycopg2
from datetime import timedelta
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.bf_stepbystep_clients.tests.test_portail import _Mandat
from odoo.addons.bf_stepbystep_securetransfer.controllers.portal import ProgressionPortailS3

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
MO = 1024 * 1024
PETIT = b"%PDF-1.4 petit fichier"


class _Corps:
    def __init__(self, donnees):
        self.donnees = donnees

    def read(self):
        return self.donnees


@tagged("post_install", "-at_install")
class TestDepotS3(HttpCase, _Mandat):

    def setUp(self):
        super().setUp()
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        self.env.company.email = "cabinet@example.com"
        self.projet, self.colonnes = self._mandat(self.env)
        self.projet.partner_id.email = "client@example.com"
        Document = self.env["bf.progression.document"]
        self.certificat = Document.create({"name": "Certificat", "stage_id": self.colonnes[1].id})
        self.futur = Document.create({"name": "Procuration", "stage_id": self.colonnes[3].id})
        self.projet.action_progression_portal_publish()
        self.jeton = self.projet.progression_portal_token
        self.base = "/progression/%s/%s" % (self.projet.id, self.jeton)
        self.objets = {}
        self.supprimes = []

    # ------------------------------------------------------------------ faux S3
    @contextlib.contextmanager
    def _s3(self, purge_en_echec=False, purge_incomplete=False):
        objets, supprimes = self.objets, self.supprimes

        def supprimer(env, keys):
            if purge_en_echec:
                raise ConnectionError("point d'accès S3 injoignable")
            if purge_incomplete:
                return list(keys)
            for cle in keys:
                objets.pop(cle, None)
            return supprimes.extend(keys) or []
        client = MagicMock()
        client.get_object.side_effect = lambda Bucket, Key: {"Body": _Corps(objets[Key])}
        with contextlib.ExitStack() as pile:
            pile.enter_context(patch(S3_MOD + ".params", return_value={
                "bucket": "seau-essai", "endpoint_url": "https://s3.example", "region": "ca-east-1",
                "path_style": True, "access_key": "a", "secret_key": "b",
                "put_ttl": 900, "part_ttl": 3600, "get_ttl": 300}))
            pile.enter_context(patch(S3_MOD + ".client", return_value=client))
            pile.enter_context(patch(S3_MOD + ".presign_put", side_effect=lambda env, key, n, ttl=None: {
                "url": "https://s3.example/" + key, "headers": {"Content-Length": str(n)}}))
            pile.enter_context(patch(S3_MOD + ".presign_get", return_value="https://s3.example/get"))
            pile.enter_context(patch(S3_MOD + ".head_object", side_effect=lambda env, key: (
                {"size": len(objets[key]), "etag": '"e"'} if key in objets else None)))
            # Le vrai rend la liste des clés EN ÉCHEC : vide quand tout est parti.
            pile.enter_context(patch(S3_MOD + ".delete_keys", side_effect=supprimer))
            pile.enter_context(patch(S3_MOD + ".mpu_initiate", return_value="mpu-1"))
            pile.enter_context(patch(S3_MOD + ".mpu_sign_parts", side_effect=lambda env, key, uid, nums: {
                n: "https://s3.example/part/%s" % n for n in nums}))
            pile.enter_context(patch(S3_MOD + ".mpu_list_parts", side_effect=lambda env, key, uid: [
                {"n": 1, "etag": '"p"', "size": len(objets.get(key, b""))}]))
            pile.enter_context(patch(S3_MOD + ".mpu_complete", return_value=True))
            pile.enter_context(patch(S3_MOD + ".mpu_abort", return_value=True))
            # Le balayage du seau par le ménage horaire du socle : rien à balayer.
            pile.enter_context(patch(S3_MOD + ".list_stale_mpus", return_value=[]))
            pile.enter_context(patch(S3_MOD + ".list_objects", return_value=[]))
            yield

    def _json(self, url, **params):
        r = self.url_open(url, data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": params}),
                          headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 200)
        # La route a écrit dans le même curseur : on relit, le cache de l'essai est périmé.
        self.env.invalidate_all()
        return r.json()["result"], r.text

    def _envoi(self, reponse):
        return self.env["bf.progression.upload"].sudo().browse(reponse["upload_id"])

    # ------------------------------------------------------------------ essais
    def test_le_mode_suit_la_configuration_du_socle(self):
        page = self.url_open(self.base).text
        self.assertIn('enctype="multipart/form-data"', page)
        self.assertNotIn("o_progression_s3_form", page)
        with self._s3():
            page = self.url_open(self.base).text
        self.assertIn("o_progression_s3_form", page)
        self.assertNotIn('enctype="multipart/form-data"', page)
        # Le bouton naît désactivé : seul le script, une fois branché, l'active.
        formulaire = re.search(r'<form[^>]*o_progression_s3_form.*?</form>', page, re.S).group(0)
        self.assertRegex(formulaire, r'<button type="submit"[^>]*disabled')

    def test_un_petit_fichier_devient_une_piece_jointe(self):
        with self._s3():
            depart, brut = self._json(self.base + "/s3/start", document_id=self.certificat.id,
                                      filename="certificat.pdf", size=len(PETIT))
            envoi = self._envoi(depart)
            # 🔴 Le jeton du brouillon de transfert ne sort jamais.
            self.assertNotIn(envoi.transfer_id.upload_token, brut)
            self.assertEqual(depart["mode"], "simple")
            self.objets[envoi.file_id.s3_key] = PETIT
            fin, _brut = self._json(self.base + "/s3/%s/complete" % envoi.id,
                                    upload_token=depart["upload_token"])
        self.assertEqual(fin, {"ok": True})
        depot = self.projet.progression_deposit_ids
        self.assertEqual(depot.storage, "odoo")
        self.assertEqual(depot.attachment_id.raw, PETIT)
        self.assertEqual(depot.attachment_id.res_model, "project.task")
        self.assertTrue(depot.checksum)
        self.assertIn(envoi.file_id.s3_key, self.supprimes, "l'objet S3 est effacé après versement")
        # Le brouillon reste au ménage horaire du socle (voir l'essai du second PUT).
        self.assertEqual(envoi.transfer_id.state, "draft")
        self.assertEqual(envoi.state, "done")

    def test_un_second_put_apres_versement_ne_laisse_pas_d_orphelin(self):
        """L'URL PUT présignée vaut encore après `complete` : un second PUT
        recrée l'objet à la même clé. Le brouillon resté ouvert est récolté par
        le ménage horaire du socle, qui efface la clé ; le dépôt reste intact."""
        with self._s3():
            envoi, fin = self._demarrer_et_deposer()
            self.assertEqual(fin, {"ok": True})
            cle = envoi.file_id.s3_key
            self.assertNotIn(cle, self.objets)
            self.objets[cle] = PETIT  # second PUT, même URL, encore valide
            self.supprimes.clear()
            # Le délai des brouillons (24 h) est passé.
            self.env.flush_all()
            self.env.cr.execute(
                "UPDATE secure_transfer SET create_date = %s WHERE id = %s",
                (fields.Datetime.now() - timedelta(hours=25), envoi.transfer_id.id))
            self.env.invalidate_all()
            self.env["secure.transfer"]._cron_gc_drafts()
        self.assertIn(cle, self.supprimes)
        self.assertNotIn(cle, self.objets, "la clé recréée est effacée")
        self.assertEqual(envoi.transfer_id.state, "cancelled")
        depot = self.projet.progression_deposit_ids
        self.assertEqual(depot.storage, "odoo")
        self.assertEqual(depot.attachment_id.raw, PETIT, "le dépôt reste intact")

    def test_les_brouillons_du_portail_sont_caches_au_groupe_du_socle(self):
        """Le groupe Utilisateur du socle lit tous les transferts : sans règle,
        il obtenait par RPC le lien de téléchargement d'un fichier d'un projet
        qu'il ne lit pas, ou faisait signer des parties d'un envoi en cours."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.multipart_threshold_mb", "1")
        usager = new_test_user(
            self.env, "progression_socle_seul",
            groups="base.group_user,bf_securetransfer.group_securetransfer_user")
        self.assertFalse(usager.has_group("project.group_project_user"))
        Envoi = self.env["bf.progression.upload"].sudo()
        with self._s3():
            envoi, _plan = Envoi._demarrer(self.projet, self.certificat, "plan.pdf", 2 * MO,
                                           ip="198.51.100.7")
            self.assertEqual(envoi.file_id.upload_mode, "multipart")
            transfert, fichier = envoi.transfer_id, envoi.file_id
            # Témoin : un transfert ordinaire du socle, lui, reste lisible.
            temoin = self.env["secure.transfer"].sudo().create({
                "brand_id": transfert.brand_id.id, "sender_name": "Témoin",
                "sender_email": "temoin@example.com", "recipient_emails": "dest@example.com",
                "ip_created": "198.51.100.8", "state": "draft"})
            self.env.flush_all()
            T = self.env["secure.transfer"].with_user(usager)
            F = self.env["secure.transfer.file"].with_user(usager)
            Journal = self.env["secure.transfer.access.log"].with_user(usager)
            self.assertEqual(T.search([("id", "in", (transfert | temoin).ids)]), temoin.with_user(usager))
            self.assertFalse(F.search([("id", "=", fichier.id)]))
            self.assertFalse(Journal.search([("transfer_id", "=", transfert.id)]))
            with self.assertRaises(AccessError):
                F.browse(fichier.id).presign_download()
            with self.assertRaises(AccessError):
                F.browse(fichier.id).mpu_sign([1])
            with self.assertRaises(AccessError):
                T.browse(transfert.id).read(["subject"])
            # Le portail, lui, lit toujours son brouillon (en sudo, derrière ses gardes).
            self.assertTrue(fichier.sudo().mpu_sign([1]))
            self.assertTrue(transfert.bf_progression_upload)

    def test_une_erreur_de_concurrence_remonte_pour_etre_rejouee(self):
        """Avalée, une `SerializationFailure` rendait un refus au client au lieu
        de laisser Odoo relancer la requête sur un instantané neuf."""
        def conflit():
            raise psycopg2.errors.SerializationFailure("could not serialize access")
        module = "odoo.addons.bf_stepbystep_securetransfer.controllers.portal.request"
        with patch(module, MagicMock(env=self.env)):
            with self.assertRaises(psycopg2.errors.SerializationFailure):
                ProgressionPortailS3._progression_appel(conflit)
            # Le reste garde son code, sans trace.
            with mute_logger("odoo.addons.bf_stepbystep_securetransfer.controllers.portal"):
                self.assertEqual(ProgressionPortailS3._progression_appel(lambda: 1 / 0)["error"], "server")

    def test_un_gros_fichier_reste_dans_le_seau_jusqu_a_son_echeance(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_stepbystep_securetransfer.ingest_max_mb", "1")
        icp.set_param("bf_securetransfer.multipart_threshold_mb", "1")
        gros = b"x" * (2 * MO)
        with self._s3():
            depart, _b = self._json(self.base + "/s3/start", document_id=self.certificat.id,
                                    filename="plan.pdf", size=len(gros))
            self.assertEqual(depart["mode"], "multipart")
            envoi = self._envoi(depart)
            signe, _b = self._json(self.base + "/s3/%s/sign" % envoi.id,
                                   upload_token=depart["upload_token"], parts=[1])
            self.assertTrue(signe["urls"])
            self.objets[envoi.file_id.s3_key] = gros
            self._json(self.base + "/s3/%s/complete" % envoi.id, upload_token=depart["upload_token"])
            depot = self.projet.progression_deposit_ids
            self.assertEqual(depot.storage, "s3")
            self.assertFalse(depot.attachment_id)
            self.assertEqual(depot.transfer_file_id, envoi.file_id)
            self.assertTrue(depot.s3_expiry)
            self.assertFalse(self.supprimes)
            # Le ménage horaire du socle ne le récolte pas.
            cutoff = fields.Datetime.now() + timedelta(days=1)
            domaine = self.env["secure.transfer"]._gc_drafts_domain(cutoff)
            self.assertNotIn(envoi.transfer_id, self.env["secure.transfer"].search(domaine))
            # L'équipe le télécharge ; le client non (route d'action interne).
            chef = new_test_user(self.env, "progression_chef_s3", groups="project.group_project_user")
            action = depot.with_user(chef).action_download_stored_file()
            self.assertEqual(action["url"], "https://s3.example/get")
            # À l'échéance, notre cron le purge.
            depot.s3_expiry = fields.Datetime.now() - timedelta(minutes=1)
        # Une purge incomplète (une clé restée au seau) ne marque rien « purgé » :
        # le dépôt reste « s3 », et le prochain passage réessaie.
        with self._s3(purge_incomplete=True):
            self.env["bf.progression.upload"]._cron_menage()
        self.assertEqual(depot.storage, "s3")
        with self._s3(purge_en_echec=True):
            self.env["bf.progression.upload"]._cron_menage()
        self.assertEqual(depot.storage, "s3")
        with self._s3():
            self.env["bf.progression.upload"]._cron_menage()
            self.assertEqual(depot.storage, "purged")
            self.assertIn(envoi.file_id.s3_key, self.supprimes)

    def test_les_gardes_des_routes(self):
        with self._s3():
            depart, _b = self._json(self.base + "/s3/start", document_id=self.certificat.id,
                                    filename="c.pdf", size=len(PETIT))
            envoi = self._envoi(depart)
            for nom, url, params in [
                ("mauvais jeton d'envoi", self.base + "/s3/%s/complete" % envoi.id, {"upload_token": "x" * 43}),
                ("mauvais jeton de portail", "/progression/%s/%s/s3/%s/complete" % (self.projet.id, "y" * 43, envoi.id),
                 {"upload_token": depart["upload_token"]}),
                ("pièce d'une étape à venir", self.base + "/s3/start",
                 {"document_id": self.futur.id, "filename": "p.pdf", "size": 10}),
            ]:
                with self.subTest(nom):
                    reponse, _b = self._json(url, **params)
                    self.assertEqual(reponse.get("error"), "not_found")
            # Le jeton de portail VALIDE d'un autre projet n'ouvre pas cet envoi.
            autre, _c = self._mandat(self.env)
            autre.action_progression_portal_publish()
            reponse, _b = self._json("/progression/%s/%s/s3/%s/complete" % (
                autre.id, autre.progression_portal_token, envoi.id),
                upload_token=depart["upload_token"])
            self.assertEqual(reponse.get("error"), "not_found")
            reponse, _b = self._json(self.base + "/s3/start", document_id=self.certificat.id,
                                     filename="virus.exe", size=10)
            self.assertEqual(reponse["error"], "type")
            # Un envoi terminé ne se termine pas deux fois.
            self.objets[envoi.file_id.s3_key] = PETIT
            self._json(self.base + "/s3/%s/complete" % envoi.id, upload_token=depart["upload_token"])
            reponse, _b = self._json(self.base + "/s3/%s/complete" % envoi.id,
                                     upload_token=depart["upload_token"])
            self.assertEqual(reponse.get("error"), "not_found")
            self.assertEqual(len(self.projet.progression_deposit_ids), 1)

    def test_un_fichier_incomplet_n_est_pas_verse(self):
        with self._s3():
            depart, _b = self._json(self.base + "/s3/start", document_id=self.certificat.id,
                                    filename="c.pdf", size=len(PETIT))
            envoi = self._envoi(depart)
            self.objets[envoi.file_id.s3_key] = PETIT[:5]
            reponse, _b = self._json(self.base + "/s3/%s/complete" % envoi.id,
                                     upload_token=depart["upload_token"])
        self.assertEqual(reponse["error"], "storage")
        self.assertIn("did not arrive in full", reponse["message"])
        self.assertFalse(self.projet.progression_deposit_ids)
        self.assertEqual(envoi.state, "failed")

    def test_la_cadence_des_envois_demarres_est_bornee(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_stepbystep_clients.portal_deposits_per_hour", 1)
        Envoi = self.env["bf.progression.upload"].sudo()
        with self._s3():
            for _i in range(2):
                Envoi._demarrer(self.projet, self.certificat, "c.pdf", 10, ip="198.51.100.7")
            with self.assertRaises(UserError):
                Envoi._demarrer(self.projet, self.certificat, "c.pdf", 10, ip="198.51.100.7")

    def _json_brut(self, url, **params):
        r = self.url_open(url, data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": params}),
                          headers={"Content-Type": "application/json"})
        self.env.invalidate_all()
        return r

    def _demarrer_et_deposer(self):
        depart, _b = self._json(self.base + "/s3/start", document_id=self.certificat.id,
                                filename="certificat.pdf", size=len(PETIT))
        envoi = self._envoi(depart)
        self.objets[envoi.file_id.s3_key] = PETIT
        fin, _b = self._json(self.base + "/s3/%s/complete" % envoi.id,
                             upload_token=depart["upload_token"])
        return envoi, fin

    def test_un_jeton_non_ascii_est_refuse_sans_trace(self):
        """consteq lève TypeError sur du non ASCII : la route JSON rendait la trace."""
        with self._s3():
            r = self._json_brut("/progression/%s/%%C3%%A9/s3/start" % self.projet.id,
                                document_id=self.certificat.id, filename="c.pdf", size=10)
            self.assertNotIn("Traceback", r.text)
            self.assertEqual(r.json().get("result", {}).get("error"), "not_found")
            depart, _b = self._json(self.base + "/s3/start", document_id=self.certificat.id,
                                    filename="c.pdf", size=len(PETIT))
            envoi = self._envoi(depart)
            for jeton in ("é", 12345, ["x"], "x" * 4000):
                with self.subTest(repr(jeton)[:20]):
                    self.assertFalse(envoi._verifier_jeton(jeton))
                    r = self._json_brut(self.base + "/s3/%s/complete" % envoi.id, upload_token=jeton)
                    self.assertNotIn("Traceback", r.text)
                    self.assertEqual(r.json().get("result", {}).get("error"), "not_found")
            self.assertTrue(envoi._verifier_jeton(depart["upload_token"]))

    def test_le_client_n_est_pas_abonne_et_la_note_parle_sa_langue(self):
        """Le versement S3 passe par la même note que le dépôt direct : consignée,
        sans abonner le client, dans sa langue."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_stepbystep_clients"], ["fr_CA"])
        client = self.projet.partner_id
        client.lang = "fr_CA"
        with self._s3():
            _envoi, fin = self._demarrer_et_deposer()
        self.assertEqual(fin, {"ok": True})
        tache = self.projet.progression_deposit_ids.task_id
        self.assertNotIn(client, tache.message_partner_ids)
        note = tache.message_ids.filtered(lambda m: "Certificat" in (m.body or ""))
        self.assertIn("Pièce déposée par le client au portail", note.body)
        message = tache.message_post(
            body="Bien reçu.", message_type="comment", subtype_xmlid="mail.mt_comment")
        self.assertFalse(self.env["mail.notification"].search([
            ("mail_message_id", "=", message.id), ("res_partner_id", "=", client.id)]))

    def test_une_purge_en_echec_ne_defait_pas_le_versement(self):
        """L'envoi est clos avant la purge, et la purge ne lève jamais."""
        with self._s3(purge_en_echec=True):
            envoi, fin = self._demarrer_et_deposer()
        self.assertEqual(fin, {"ok": True})
        self.assertEqual(envoi.state, "done")
        self.assertEqual(len(self.projet.progression_deposit_ids), 1)
        self.assertEqual(self.projet.progression_deposit_ids.attachment_id.raw, PETIT)

    def test_une_erreur_du_stockage_ne_laisse_rien_et_ne_dit_rien_de_la_configuration(self):
        """Point de sauvegarde : le brouillon créé avant l'erreur disparaît. Le
        visiteur ne lit pas le texte du socle (point d'accès, seau)."""
        Transfert = self.env["secure.transfer"].sudo()
        avant = Transfert.search_count([])
        socle = "Endpoint https://s3.example unreachable for bucket seau-essai"
        with self._s3(), patch.object(type(Transfert), "_register_file",
                                      side_effect=UserError(socle)):
            r = self._json_brut(self.base + "/s3/start", document_id=self.certificat.id,
                                filename="c.pdf", size=len(PETIT))
        resultat = r.json()["result"]
        self.assertEqual(resultat["error"], "storage")
        self.assertEqual(resultat["message"], "The file could not be stored. Try again later.")
        self.assertNotIn("seau-essai", r.text)
        self.assertEqual(Transfert.search_count([]), avant, "aucun brouillon à moitié écrit")
        self.assertFalse(self.env["bf.progression.upload"].sudo().search(
            [("project_id", "=", self.projet.id)]))

    def test_le_seuil_de_versement_est_plafonne(self):
        icp = self.env["ir.config_parameter"].sudo()
        Envoi = self.env["bf.progression.upload"]
        icp.set_param("bf_stepbystep_securetransfer.ingest_max_mb", "5000")
        self.assertEqual(Envoi._seuil_versement(), 100 * MO)
        icp.set_param("bf_stepbystep_securetransfer.ingest_max_mb", "10")
        self.assertEqual(Envoi._seuil_versement(), 10 * MO)

    def test_une_piece_archivee_n_accepte_plus_d_envoi(self):
        self.certificat.active = False
        with self._s3():
            reponse, _b = self._json(self.base + "/s3/start", document_id=self.certificat.id,
                                     filename="c.pdf", size=len(PETIT))
        self.assertEqual(reponse.get("error"), "not_found")

    def test_le_lien_de_telechargement_exige_de_lire_le_projet(self):
        """Un usager de Projet d'une autre société n'obtient pas de lien présigné
        vers le dépôt d'un projet privé, même si la règle des dépôts manquait."""
        autre_societe = self.env["res.company"].create({"name": "Autre cabinet"})
        etranger = new_test_user(
            self.env, "progression_etranger_s3", groups="project.group_project_user",
            company_id=autre_societe.id, company_ids=[(6, 0, [autre_societe.id])])
        self.projet.write({"privacy_visibility": "followers", "company_id": self.env.company.id})
        depot = self.env["bf.progression.deposit"].sudo()._creer_depot(
            self.projet, self.certificat, "plan.pdf", 10, valeurs={"storage": "s3"})
        with self.assertRaises(AccessError):
            depot.with_user(etranger).action_download_stored_file()
        # Défense en profondeur : sans les règles des dépôts, la garde du projet tient.
        self.env["ir.rule"].search([("model_id.model", "=", "bf.progression.deposit")]).active = False
        with self.assertRaises(AccessError):
            depot.with_user(etranger).action_download_stored_file()
