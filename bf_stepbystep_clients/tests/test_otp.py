"""La double authentification de la page de progression.

Ce qui compte : la page fermée ne dit RIEN du mandat, le code part à la partie
que la page NOMME et jamais au payeur à sa place, il prouve l'accès à la boîte,
l'appareil est retenu une fois, les essais et les envois sont bornés, le dépôt
de pièces est fermé lui aussi, et le témoin d'un mandat n'ouvre pas celui d'un
autre.
"""
import re
from unittest.mock import patch

from psycopg2.errors import SerializationFailure

from odoo import fields
from odoo.tests import HttpCase, tagged

from .test_portail import NOM_INTERNE, _Mandat

JETON_CSRF = re.compile(r'name="csrf_token"\s+value="([^"]+)"')
JETON_CSRF_SCRIPT = re.compile(r'csrf_token:\s*"([^"]+)"')
TITRE_FERME = "Confirm that it is you"
CODE = "123456"
FAUX = "000000"


class CourseSimulee(SerializationFailure):
    """La course perdue, telle que PostgreSQL la lève.

    ⚠️ Le `pgcode` n'est pas décoratif : `retrying()` d'Odoo le passe à
    `errorcodes.lookup()` avant de rejouer, et une erreur construite à la main
    sans code faisait lever KeyError là.
    """
    pgcode = "40001"  # serialization_failure


@tagged("post_install", "-at_install")
class TestCodeDuPortail(HttpCase, _Mandat):

    def setUp(self):
        super().setUp()
        self.projet, self.colonnes = self._mandat(self.env)
        self.projet.partner_id.email = "client@essai.invalid"
        self.projet.action_progression_portal_publish()
        self.jeton = self.projet.progression_portal_token
        # Le jeton CSRF se prend sur la page OUVERTE, une fois : le reprendre
        # plus tard sur la page fermée enverrait un code neuf, et les essais qui
        # mesurent l'épuisement ou la péremption mesureraient le code neuf.
        self.csrf = self._csrf(self._ouvrir().text)
        self.projet.progression_otp_required = True
        self.Otp = self.env["bf.progression.otp"]
        self.Appareils = self.env["bf.progression.device"]
        # ⚠️ L'environnement d'essai ÉTEINT tous les crons après chaque geste, et la fonction
        # refuse de créer un code quand la file de courriel ne tourne pas. La
        # prémisse de production, c'est une file active : on la rallume ici, et
        # un essai la rééteint exprès pour mesurer le refus.
        self.cron_file = self.env.ref("mail.ir_cron_mail_scheduler_action")
        self.cron_file.sudo().active = True

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------

    def _avec_code_fixe(self):
        """Fixe le tirage du code, sans toucher à la façon dont il est gardé."""
        return patch(
            "odoo.addons.bf_stepbystep_clients.models.progression_otp"
            ".secrets.randbelow", return_value=int(CODE))

    def _ouvrir(self, jeton=None, projet=None):
        projet = projet or self.projet
        return self.url_open(
            "/progression/%s/%s" % (projet.id, jeton or projet.progression_portal_token),
            allow_redirects=False)

    def _demander(self, projet=None, jeton=None):
        """Charge la page fermée : un code part, et c'est CODE."""
        with self._avec_code_fixe():
            return self._ouvrir(projet=projet, jeton=jeton)

    def _csrf(self, page):
        trouve = JETON_CSRF.search(page) or JETON_CSRF_SCRIPT.search(page)
        self.assertTrue(trouve, "aucun jeton CSRF sur la page")
        return trouve.group(1)

    def _poster_le_code(self, code, projet=None, jeton=None, csrf=None):
        """Poste le code SANS recharger la page : recharger en enverrait un neuf."""
        projet = projet or self.projet
        return self.url_open(
            "/progression/%s/%s/code" % (projet.id, jeton or projet.progression_portal_token),
            data={"csrf_token": csrf or self.csrf, "code": code},
            allow_redirects=False)

    def _codes(self):
        return self.Otp.sudo().search([("project_id", "=", self.projet.id)])

    def _courriels(self):
        """Les messages des codes de ce mandat, par leur LIEN, pas par leur
        sujet : le gabarit rend le sujet dans la langue du client."""
        return self._codes().sudo().mapped("mail_id")

    def _perimer_les_codes(self):
        """Comme le temps qui passe : les codes sortent de leur fenêtre."""
        self._codes().sudo().write({
            "expires_at": fields.Datetime.subtract(fields.Datetime.now(), minutes=1)})

    # ------------------------------------------------------------------
    # La page fermée
    # ------------------------------------------------------------------

    def test_sans_la_coche_la_page_s_ouvre_comme_avant(self):
        self.projet.progression_otp_required = False
        page = self._ouvrir().text
        self.assertIn("Pièces du client", page)
        self.assertNotIn(TITRE_FERME, page)
        self.assertFalse(self._codes())

    def test_la_page_fermee_ne_dit_rien_du_mandat(self):
        reponse = self._demander()
        page = reponse.text
        self.assertEqual(reponse.status_code, 200)
        self.assertIn(TITRE_FERME, page)
        # Rien du dossier : ni client, ni étape, ni consigne, ni pièce.
        self.assertNotIn("Famille Essai", page)
        self.assertNotIn("Pièces du client", page)
        self.assertNotIn("Déposez votre certificat", page)
        self.assertNotIn(NOM_INTERNE, page)
        # L'adresse est masquée : assez pour reconnaître sa boîte, pas pour
        # apprendre l'adresse à qui a trouvé le lien.
        self.assertIn("c***@essai.invalid", page)
        self.assertNotIn("client@essai.invalid", page)
        self.assertEqual(reponse.headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(reponse.headers.get("X-Frame-Options"), "DENY")

    def test_le_code_part_une_fois_et_se_redit_au_rechargement(self):
        """Un détonateur de liens charge la page avant le client : le code et son
        horloge partent alors sans lui. Le rechargement ne consomme pas d'envoi,
        et dit quand même où le code est allé."""
        self._demander()
        self.assertEqual(len(self._codes()), 1)
        page = self._demander().text
        self.assertEqual(len(self._codes()), 1)
        self.assertIn("c***@essai.invalid", page)

    def test_le_code_n_est_en_clair_nulle_part_de_durable(self):
        """🔴 L'empreinte du modèle ne vaut que si le code ne traîne pas
        ailleurs : le message qui le porte est `auto_delete`, et il ne pose
        aucun message au chatter du projet."""
        self._demander()
        otp = self._codes()
        self.assertNotIn(CODE, otp.code_hash)
        self.assertEqual(len(otp.code_hash), 64)
        courriel = self._courriels()
        self.assertEqual(len(courriel), 1)
        self.assertTrue(courriel.auto_delete, "le message garderait le code en clair")
        # Détaché du dossier : le gabarit pose `model`/`res_id`, et le chatter
        # du projet aurait porté le code en clair jusqu'à l'envoi.
        self.assertFalse(courriel.mail_message_id.model)
        self.assertFalse(courriel.mail_message_id.res_id)
        self.assertFalse(self.env["mail.message"].sudo().search_count([
            ("model", "=", "project.project"), ("res_id", "=", self.projet.id),
            ("body", "ilike", CODE)]))
        # Le corps porte bien le code, et il est passé par le gabarit.
        self.assertIn(CODE, courriel.body_html)

    def test_sans_adresse_la_page_le_dit_au_lieu_d_attendre(self):
        self.projet.partner_id.email = False
        page = self._demander().text
        self.assertIn("no email address on file", page)
        self.assertFalse(self._codes())

    def test_le_code_part_a_la_partie_que_la_page_nomme(self):
        """Le code suit `portal_client`, comme le nom affiché : la société mère
        quand le mandat porte un contact."""
        societe = self.env["res.partner"].create({
            "name": "Groupe du mandat", "email": "groupe@essai.invalid"})
        contact = self.env["res.partner"].create({
            "name": "Contact du dossier", "email": "contact@essai.invalid",
            "parent_id": societe.id})
        self.projet.partner_id = contact
        page = self._demander().text
        self.assertIn("g***@essai.invalid", page)
        self.assertEqual(self._codes().email, "groupe@essai.invalid")

    def test_le_code_ne_part_jamais_au_payeur_a_la_place_du_lecteur(self):
        """🔴 Le cas d'un donneur d'ordre : la page nomme le bénéficiaire, qui
        n'a pas d'adresse propre. Le code ne doit PAS retomber sur le payeur —
        il ouvrirait la page d'un dossier qui n'est pas le sien, et le lecteur
        resterait dehors. Mesuré en remplaçant `portal_client`, comme le fait le
        module pont des bénéficiaires."""
        payeur = self.env["res.partner"].create({
            "name": "Donneur d'ordre", "email": "payeur@essai.invalid"})
        beneficiaire = self.env["res.partner"].create({"name": "Bénéficiaire sans courriel"})
        self.projet.partner_id = payeur
        Portail = self._classe_du_portail()
        vrai = Portail._progression_valeurs.__func__  # celui-là EST un classmethod

        def avec_beneficiaire(cls, projet):
            valeurs = vrai(cls, projet)
            valeurs["portal_client"] = beneficiaire
            return valeurs

        with patch.object(Portail, "_progression_valeurs",
                          classmethod(avec_beneficiaire)):
            page = self._demander().text
        self.assertIn("no email address on file", page)
        self.assertFalse(self._codes())
        self.assertFalse(self._courriels())

    def _classe_du_portail(self):
        from odoo.addons.bf_stepbystep_clients.controllers.portal import ProgressionPortail
        return ProgressionPortail

    def test_le_code_part_bien_a_la_partie_nommee_quand_elle_a_son_adresse(self):
        """La branche POSITIVE du correctif : la partie nommée a sa propre
        adresse, différente du payeur, et c'est là que le code va. Sans cet
        essai, retirer la branche laissait tout vert."""
        payeur = self.env["res.partner"].create({
            "name": "Donneur d'ordre", "email": "payeur@essai.invalid"})
        beneficiaire = self.env["res.partner"].create({
            "name": "Bénéficiaire", "email": "lecteur@essai.invalid"})
        self.projet.partner_id = payeur
        Portail = self._classe_du_portail()
        vrai = Portail._progression_valeurs.__func__

        def avec_beneficiaire(cls, projet):
            valeurs = vrai(cls, projet)
            valeurs["portal_client"] = beneficiaire
            return valeurs

        with patch.object(Portail, "_progression_valeurs",
                          classmethod(avec_beneficiaire)):
            page = self._demander().text
        self.assertIn("l***@essai.invalid", page)
        self.assertEqual(self._codes().email, "lecteur@essai.invalid")
        self.assertNotIn("payeur@essai.invalid", self._courriels().email_to or "")

    def test_une_adresse_mal_formee_ne_fait_pas_naitre_de_code(self):
        """🔴 Une adresse sale est pire qu'une adresse absente : le code naissait,
        la page annonçait un envoi « à rien » (le masque ne trouve pas de @), et
        le cœur d'Odoo EFFAÇAIT le message pour adresse invalide — donc son
        absence se relisait ensuite comme un succès."""
        # ⚠️ « jean dupont@essai.invalid » n'est PAS dans la liste : Odoo le
        # normalise en `dupont@essai.invalid`, une adresse livrable. L'essai
        # mesure ce que le normalisateur refuse vraiment, pas ce que je
        # supposais qu'il refuse.
        for sale in ("n/a", "jean@", "aucune", "@essai.invalid", "jean@@x.ca"):
            with self.subTest(sale):
                self._codes().sudo().unlink()
                self.projet.partner_id.email = sale
                page = self._demander().text
                self.assertIn("no email address on file", page)
                self.assertFalse(self._codes())

    def test_une_adresse_formatee_est_acceptee_et_masquee(self):
        """« Jean <jean@x.ca> » est une adresse valable, et le masque ne doit
        pas rendre `J***@x.ca>`."""
        self.projet.partner_id.email = "Jean Exemple <jean@essai.invalid>"
        page = self._demander().text
        self.assertIn("j***@essai.invalid", page)
        self.assertNotIn("&gt;", page.split("alert alert-info")[1][:200])
        self.assertEqual(self._codes().email, "jean@essai.invalid")

    # ------------------------------------------------------------------
    # L'envoi
    # ------------------------------------------------------------------

    def test_un_envoi_reste_en_echec_et_la_page_le_dit_au_chargement_suivant(self):
        """La file rend la main avant de savoir : l'échec se constate au
        chargement SUIVANT, sur l'état du message."""
        self._demander()
        self._courriels().sudo().write({"state": "exception"})
        page = self._demander().text
        self.assertIn("could not be sent", page)
        # Le code mort ne reste pas « en vol », et sa ligne reste au plafond.
        self.assertEqual(len(self._codes()), 1)
        self.assertFalse(self.Otp._code_en_vol(self.projet))

    def test_un_echec_de_mise_en_file_ne_laisse_pas_de_code_vivant(self):
        """Si même la mise en file échoue, le code est mort tout de suite."""
        Courriel = type(self.env["mail.mail"])

        def refuser(self, *a, **k):
            raise ValueError("file refusée")

        with patch.object(Courriel, "create", refuser), self._avec_code_fixe():
            page = self._ouvrir().text
        self.assertIn("could not be sent", page)
        self.assertFalse(self.Otp._code_en_vol(self.projet))

    def test_un_message_qui_dort_dans_la_file_est_un_echec(self):
        """🔴 `_trigger()` sur un cron ÉTEINT est un non-geste, et Odoo éteint
        lui-même un cron qui a planté. Sans grâce sur « outgoing », la page
        annonçait « envoyé » pour toujours, sur tous les mandats à la fois."""
        self._demander()
        self.assertEqual(self._courriels().state, "outgoing")
        # Le temps passe : le message dort encore.
        self._codes().sudo().write({
            "sent_at": fields.Datetime.subtract(fields.Datetime.now(), minutes=10)})
        page = self._demander().text
        self.assertIn("could not be sent", page)

    def test_la_file_eteinte_ne_fait_pas_naitre_de_code(self):
        """Rien ne partira : le dire tout de suite plutôt que de créer un code
        que personne ne recevra."""
        self.cron_file.sudo().active = False
        try:
            page = self._demander().text
        finally:
            self.cron_file.sudo().active = True
        self.assertIn("could not be sent", page)
        self.assertFalse(self._codes())

    def test_le_plafond_horaire_borne_les_envois(self):
        """Cinq codes par heure, échecs compris : sinon chaque panne d'envoi
        rouvrait la rafale de courriels vers le client."""
        for _i in range(5):
            self._demander()
            self._perimer_les_codes()
        self.assertEqual(len(self._codes()), 5)
        page = self._demander().text
        self.assertIn("Too many codes", page)
        self.assertEqual(len(self._codes()), 5)

    def test_un_geste_concurrent_est_rejoue(self):
        """Le plafond horaire tient au rejeu d'Odoo, donc à la REMONTÉE de
        l'erreur de sérialisation : avalée par le filet de la route, elle
        rendrait 404 au client et le plafond ne compterait plus rien."""
        Otp = type(self.env["bf.progression.otp"])
        # ⚠️ `@api.model` ne fait pas un `classmethod` : la méthode reste une
        # fonction ordinaire appelée sur un recordset, donc pas de `__func__` et
        # pas de `classmethod()` au remplacement.
        vrai = Otp._envoyer
        appels = []

        def faux(modele, projet, destinataire=None, ip=None):
            appels.append(1)
            if len(appels) == 1:
                raise CourseSimulee("course simulée")
            return vrai(modele, projet, destinataire=destinataire, ip=ip)

        with patch.object(Otp, "_envoyer", faux), self._avec_code_fixe():
            reponse = self._ouvrir()
        self.assertEqual(len(appels), 2, "la requête n'a pas été rejouée")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn(TITRE_FERME, reponse.text)

    # ------------------------------------------------------------------
    # Le code
    # ------------------------------------------------------------------

    def test_le_bon_code_ouvre_la_page_et_retient_l_appareil(self):
        # ⚠️ Le jeton CSRF vient du formulaire DU CODE, pas de la page ouverte :
        # un `csrf_token` absent du gabarit neuf rendait la fonction
        # inutilisable en production avec tous les essais verts.
        page = self._demander().text
        self.assertIn('name="csrf_token"', page)
        reponse = self._poster_le_code(CODE, csrf=self._csrf(page))
        self.assertEqual(reponse.status_code, 303)
        temoin = "bf_progression_device_%s" % self.projet.id
        self.assertIn(temoin, reponse.cookies)
        appareil = self.Appareils.sudo().search([("project_id", "=", self.projet.id)])
        self.assertEqual(len(appareil), 1)
        # Le jeton de l'appareil n'est pas en base, seule son empreinte.
        self.assertNotEqual(appareil.token_hash, reponse.cookies[temoin])
        # Et la page s'ouvre maintenant pour de vrai.
        page = self._ouvrir().text
        self.assertIn("Pièces du client", page)
        self.assertNotIn(TITRE_FERME, page)

    def test_un_mauvais_code_ne_passe_pas_et_compte_l_essai(self):
        self._demander()
        reponse = self._poster_le_code(FAUX)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("does not match", reponse.text)
        self.assertFalse(self.Appareils.sudo().search_count([]))
        self.assertEqual(self._codes().attempts, 1)

    def test_les_essais_epuises_ne_font_pas_naitre_un_code_neuf(self):
        """🔴 Le cœur du plafond : qui tient le lien brûlait cinq essais,
        rechargeait, et repartait avec cinq essais neufs. Et le message lui
        disait de recharger."""
        self._demander()
        for _i in range(5):
            self._poster_le_code(FAUX)
        page = self._demander().text
        self.assertEqual(len(self._codes()), 1, "un code neuf est né")
        self.assertIn("Too many codes", page)
        self.assertNotIn("Reload this page", page)
        # Et le bon code ne vaut plus rien.
        self.assertEqual(self._poster_le_code(CODE).status_code, 200)
        self.assertFalse(self.Appareils.sudo().search_count([]))

    def test_les_essais_du_jour_sont_bornes(self):
        """Vingt essais par mandat et par jour, par-dessus les cinq par code :
        sans ça, 90 jours de lien donnaient des milliers d'essais."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_stepbystep_clients.portal_otp_attempts_per_day", "3")
        self._demander()
        for _i in range(3):
            self._poster_le_code(FAUX)
        # ⚠️ Au plafond du jour, la page disait au client qui tient le BON code
        # que son code ne correspond pas.
        self.assertEqual(self.Otp._verifier(self.projet, CODE), "too_many_today")
        reponse = self._poster_le_code(CODE)
        self.assertIn("Too many attempts today", reponse.text)
        self.assertNotIn("does not match", reponse.text)
        self._perimer_les_codes()
        self.assertIn("Too many attempts today", self._demander().text)

    def test_un_code_perime_ne_passe_pas(self):
        self._demander()
        self._perimer_les_codes()
        self.assertEqual(self.Otp._verifier(self.projet, CODE), "expired")
        reponse = self._poster_le_code(CODE)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("has expired", reponse.text)
        self.assertFalse(self.Appareils.sudo().search_count([]))

    def test_le_code_ne_sert_pas_deux_fois(self):
        self._demander()
        self.assertEqual(self.Otp._verifier(self.projet, CODE), "ok")
        # Consommé : le même code ne vaut plus rien, et la raison le dit.
        self.assertEqual(self.Otp._verifier(self.projet, CODE), "expired")

    # ------------------------------------------------------------------
    # L'appareil
    # ------------------------------------------------------------------

    def test_un_appareil_perime_redemande_un_code(self):
        self._demander()
        self._poster_le_code(CODE)
        self.assertIn("Pièces du client", self._ouvrir().text)
        self.Appareils.sudo().search([]).expires_at = fields.Datetime.subtract(
            fields.Datetime.now(), days=1)
        page = self._demander().text
        self.assertIn(TITRE_FERME, page)
        self.assertNotIn("Pièces du client", page)

    def test_le_jeton_d_un_mandat_presente_a_un_autre_ne_vaut_rien(self):
        """Explicitement : le témoin du mandat A posé sous le nom de témoin du
        mandat B. Le sel et le domaine de recherche portent l'identifiant du
        mandat, donc l'empreinte ne correspond à rien."""
        autre, _colonnes = self._mandat(self.env)
        autre.partner_id.email = "autre@essai.invalid"
        autre.action_progression_portal_publish()
        autre.progression_otp_required = True
        self._demander()
        reponse = self._poster_le_code(CODE)
        jeton_appareil = reponse.cookies["bf_progression_device_%s" % self.projet.id]

        adresse = "/progression/%s/%s" % (autre.id, autre.progression_portal_token)
        with self._avec_code_fixe():
            page = self.url_open(
                adresse, allow_redirects=False,
                headers={"Cookie": "bf_progression_device_%s=%s" % (autre.id, jeton_appareil)},
            ).text
        self.assertIn(TITRE_FERME, page)
        self.assertNotIn("Pièces du client", page)

    def test_un_temoin_forge_ne_vaut_rien(self):
        self._demander()
        with self._avec_code_fixe():
            page = self.url_open(
                "/progression/%s/%s" % (self.projet.id, self.jeton),
                allow_redirects=False,
                headers={"Cookie": "bf_progression_device_%s=%s" % (
                    self.projet.id, "x" * 43)},
            ).text
        self.assertIn(TITRE_FERME, page)

    def test_le_rearmement_oublie_appareils_et_codes(self):
        self._demander()
        self._poster_le_code(CODE)
        self.assertIn("Pièces du client", self._ouvrir().text)
        self.projet.action_progression_otp_reset()
        self.assertFalse(self.Appareils.sudo().search_count([]))
        self.assertFalse(self._codes())
        self.assertIn(TITRE_FERME, self._demander().text)

    # ------------------------------------------------------------------
    # Les autres chemins
    # ------------------------------------------------------------------

    def test_le_depot_de_pieces_est_ferme_sans_le_code(self):
        """🔴 La garde vit dans `_progression_projet`, donc le dépôt la subit
        aussi : sinon la protection de la page se contournerait par la route du
        dépôt, qui écrit."""
        document = self.env["bf.progression.document"].create({
            "name": "Certificat", "stage_id": self.colonnes[1].id})
        reponse = self.url_open(
            "/progression/%s/%s/deposit/%s" % (self.projet.id, self.jeton, document.id),
            data={"csrf_token": self.csrf}, allow_redirects=False)
        self.assertEqual(reponse.status_code, 404)
        self.assertFalse(self.env["bf.progression.deposit"].sudo().search_count([]))

    def test_la_page_du_code_parle_la_langue_du_client(self):
        """Le catalogue est SERVI, pas seulement écrit : un msgid absent du .pot
        est jeté en silence, et le client lirait la page du code en anglais."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        self.env["ir.module.module"]._load_module_terms(["bf_stepbystep_clients"], ["fr_CA"])
        self.projet.partner_id.lang = "fr_CA"
        page = self._demander().text
        self.assertIn("Confirmez que c'est bien vous", page)
        self.assertIn("Un code à six chiffres a été envoyé", page)
        self.assertIn("Code d'accès", page)
