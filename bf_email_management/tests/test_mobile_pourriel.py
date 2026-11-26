"""Signaler un pourriel depuis Symbifox Mobile.

Le geste lui-même est éprouvé par `test_pourriel_et_garde` : ranger dans
Indésirables, la règle, la plainte, l'annulation. Ici, on éprouve la PORTE du
téléphone — ce qu'elle annonce, ce qu'elle refuse, et qu'elle passe bien par la
fenêtre du bureau au lieu d'en rejouer les gestes.
"""
import json
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged

from odoo.addons.bf_email_management.models.bf_email_mobile import MAX_BULK_IDS

from .common import MobileApiCase
from .test_imap_folders_and_uid_guard import FakeImap

IMAP = "odoo.addons.bf_email_management.models.bf_email_imap"


@tagged("post_install", "-at_install")
class TestPourrielAuTelephone(MobileApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account.folder_cache = json.dumps([
            {"name": "INBOX", "special": []},
            {"name": "Junk", "special": ["\\Junk"]},
        ])
        cls.spam = cls.env["bf.email"].with_user(cls.owner).create(cls._vals(
            subject="Buy cheap domains now", sender="Vendeur <promo@spam.test>",
            direction="in", status="new", root=False,
            body="Achetez maintenant.", uid="211",
        ))
        cls.crtc = cls.env.ref("bf_email_management.report_authority_crtc")

    def _desactiver_plaintes(self):
        """🔴 Une base d'essai peut être le clone d'une base réelle, où la
        société a les plaintes activées et un projet choisi. Un essai qui dit « la société ne les a pas activées »
        doit les désactiver, pas l'espérer de la base : sinon il mesure la
        configuration du client, pas le code — et il rougit (ou verdit) selon
        la base sur laquelle il tombe.
        """
        self.owner.company_id.write({
            "bf_email_complaints_enabled": False,
            "bf_email_complaint_project_id": False,
        })

    def _activer_plaintes(self):
        projet = self.env["project.project"].create({"name": "Plaintes (essai)"})
        self.owner.company_id.write({
            "bf_email_complaints_enabled": True,
            "bf_email_complaint_project_id": projet.id,
        })
        self.owner.groups_id = [(4, self.env.ref("project.group_project_user").id)]
        return projet

    # -- ce que la feuille sait avant de s'ouvrir -------------------------
    def test_options_annoncent_le_dossier_et_les_offres(self):
        self._desactiver_plaintes()
        options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertTrue(options["ok"])
        self.assertEqual(options["count"], 1)
        self.assertEqual(options["senders"], ["promo@spam.test"])
        self.assertEqual(options["junk_folder"], "Junk")
        self.assertTrue(options["block_offered"])
        self.assertFalse(options["complaints_enabled"],
                         "la société ne les a pas activées")
        self.assertEqual(options["authorities"], {"spam": [], "phish": []})

    def test_options_sans_dossier_indesirables_ne_promettent_pas_de_regle(self):
        self.account.folder_cache = json.dumps([{"name": "INBOX", "special": []}])
        options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertEqual(options["junk_folder"], "",
                         "l'appli doit pouvoir dire que rien ne bougera au serveur")
        self.assertFalse(options["block_offered"],
                         "une règle sans destination archiverait au lieu de ranger")

    def test_options_portent_les_deux_natures_d_un_coup(self):
        """🔴 L'égalité d'ensembles d'origine rejouait le MÊME
        domaine que le code mesuré — un domaine faux (les deux natures mêlées,
        la société ignorée) y passait sans rougir : un
        attendu recalculé comme le code ne prouve rien. L'attendu
        est maintenant ancré sur des autorités NOMMÉES, et leur nature est
        affirmée avant d'être mesurée.
        """
        self._activer_plaintes()
        ftc = self.env.ref("bf_email_management.report_authority_ftc")
        apwg = self.env.ref("bf_email_management.report_authority_apwg")
        self.assertTrue(self.crtc.for_spam and not self.crtc.for_phish,
                        "prémisse : le CRTC ne prend que le pourriel")
        self.assertTrue(ftc.for_spam and ftc.channel == "form",
                        "prémisse : la FTC prend le pourriel, par formulaire")
        self.assertTrue(apwg.for_phish and not apwg.for_spam,
                        "prémisse : l'APWG ne prend que l'hameçonnage")
        options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertTrue(options["complaints_enabled"])
        spam = {a["name"]: a for a in options["authorities"]["spam"]}
        phish = {a["name"]: a for a in options["authorities"]["phish"]}
        self.assertIn(self.crtc.name, spam)
        self.assertIn(ftc.name, spam)
        self.assertNotIn(apwg.name, spam,
                         "une autorité d'hameçonnage n'est pas offerte pour un "
                         "pourriel")
        self.assertIn(apwg.name, phish,
                      "les deux listes descendent d'un coup : basculer "
                      "Pourriel/Hameçonnage ne doit pas coûter un aller-retour")
        self.assertNotIn(self.crtc.name, phish)
        # Le canal voyage avec l'autorité : la feuille doit savoir laquelle
        # rendra un texte à coller plutôt qu'une plainte en brouillon.
        self.assertEqual(spam[ftc.name]["channel"], "form")
        self.assertEqual(spam[self.crtc.name]["channel"], "email")

    def test_un_lot_n_offre_pas_de_plainte(self):
        self._activer_plaintes()
        options = self.as_owner().mobile_report_options(
            [self.spam.id, self.with_attachment.id])
        self.assertEqual(options["count"], 2)
        self.assertFalse(options["complaints_enabled"],
                         "une plainte vise UN courriel, pas quinze d'un geste")
        self.assertEqual(options["authorities"]["spam"], [])

    # -- les refus, les mêmes que ceux de la fenêtre ----------------------
    def test_un_envoi_est_refuse_avant_d_ouvrir_la_feuille(self):
        options = self.as_owner().mobile_report_options([self.outbound.id])
        self.assertFalse(options["ok"])
        self.assertIn("envois", options["blocked"])
        with self.assertRaises(UserError):
            self.as_owner().mobile_report([self.outbound.id])

    def test_un_courriel_classe_sur_une_fiche_est_refuse(self):
        tache = self.env["project.task"].create({"name": "Dossier"})
        self.spam.write({"res_model": "project.task", "res_id": tache.id})
        options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertFalse(options["ok"])
        with self.assertRaises(UserError):
            self.as_owner().mobile_report([self.spam.id])

    def test_le_courriel_d_un_autre_usager_est_introuvable(self):
        with self.assertRaises(UserError):
            self.as_owner().mobile_report_options([self.foreign.id])

    def test_une_nature_inventee_est_refusee(self):
        with self.assertRaises(UserError):
            self.as_owner().mobile_report([self.spam.id], kind="chaussette")

    # -- le geste --------------------------------------------------------
    def test_signaler_range_bloque_et_rend_le_compte_rendu(self):
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            retour = self.as_owner().mobile_report(
                [self.spam.id], kind="spam", block_sender=True)
        self.spam.invalidate_recordset()
        self.assertTrue(retour["ok"])
        self.assertEqual(retour["ids"], self.spam.ids)
        self.assertIn("Indésirables", retour["message"])
        self.assertIn("counts", retour, "l'appli pose ses pastilles sur la réponse")
        self.assertEqual(fake.copied, [("211", "Junk")])
        self.assertEqual(self.spam.report_kind, "spam")
        self.assertFalse(self.spam.active)
        self.assertTrue(self.env["bf.email.rule"].search([
            ("user_id", "=", self.owner.id),
            ("name", "ilike", "promo@spam.test"),
        ]))

    def test_annuler_passe_par_la_route_d_archivage(self):
        # L'appli n'a pas de route d'annulation : /handle handled=false est
        # déjà `action_unhandle`, qui défait le dossier ET la visibilité.
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.as_owner().mobile_report([self.spam.id])
            self.as_owner().mobile_set_handled([self.spam.id], handled=False)
        self.spam.invalidate_recordset()
        self.assertTrue(self.spam.active, "la ligne doit redevenir visible")
        self.assertFalse(self.spam.report_kind)
        self.assertEqual(self.spam.imap_folder, "INBOX")

    def test_une_autorite_poussee_sans_plaintes_activees_est_ignoree(self):
        """⚠️ Celui-ci mesure le chemin « plaintes DÉSACTIVÉES », protégé par
        deux gardes indépendantes (`complaints_enabled` de la fenêtre et
        l'intersection) : il reste vert si l'une disparaît. L'intersection seule
        est éprouvée par `test_une_autorite_de_l_autre_nature_est_ignoree`.
        """
        self._desactiver_plaintes()
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        avant = self.env["mail.scheduled.message"].search_count([])
        with patch(f"{IMAP}.open_connection", return_value=fake):
            retour = self.as_owner().mobile_report(
                [self.spam.id], authority_ids=[self.crtc.id])
        self.assertEqual(self.env["mail.scheduled.message"].search_count([]), avant)
        self.assertNotIn("plainte", retour["message"])

    def test_la_plainte_part_en_brouillon_sur_la_tache(self):
        projet = self._activer_plaintes()
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            retour = self.as_owner().mobile_report(
                [self.spam.id], authority_ids=[self.crtc.id])
        self.assertIn("plainte", retour["message"])
        brouillon = self.env["mail.scheduled.message"].search(
            [("model", "=", "project.task")], order="id desc", limit=1)
        self.assertTrue(brouillon.bf_is_draft,
                        "rien ne part à une autorité sans « Envoyer maintenant »")
        self.assertEqual(
            self.env["project.task"].browse(brouillon.res_id).project_id, projet)

    def test_un_hameconnage_ne_bloque_pas_l_expediteur(self):
        # La sécurité retire les copies ; bloquer enverrait les exercices
        # suivants dans Indésirables.
        #
        # 🔴 La prémisse est POSÉE, pas héritée de la base. Sur un
        # locataire sans `bf_security_awareness`, la garde neuve de
        # `mobile_report` lève et l'essai rougirait pour une raison étrangère à
        # ce qu'il annonce — comme `_desactiver_plaintes` pose la sienne.
        BfEmail = type(self.env["bf.email"])
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake), \
                patch.object(BfEmail, "_report_phish_available",
                             return_value=True):
            retour = self.as_owner().mobile_report(
                [self.spam.id], kind="phish", block_sender=True)
        self.spam.invalidate_recordset()
        self.assertEqual(self.spam.report_kind, "phish")
        # 🔴 Demandé et pas fait se DIT. Le compte rendu se taisait,
        # et l'appli l'affiche tel quel : la personne croyait l'expéditeur
        # bloqué.
        self.assertIn("Aucune règle de blocage", retour["message"])
        self.assertFalse(self.env["bf.email.rule"].search([
            ("user_id", "=", self.owner.id),
            ("name", "ilike", "promo@spam.test"),
        ]))

    def test_une_autorite_par_formulaire_rend_son_texte(self):
        self._activer_plaintes()
        ftc = self.env.ref("bf_email_management.report_authority_ftc")
        self.assertEqual(ftc.channel, "form")
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            retour = self.as_owner().mobile_report(
                [self.spam.id], authority_ids=[ftc.id])
        self.assertEqual(len(retour["forms"]), 1)
        formulaire = retour["forms"][0]
        self.assertEqual(formulaire["url"], ftc.url)
        self.assertIn("promo@spam.test", formulaire["text"],
                      "le texte se compose AVANT que le courriel soit rangé")
        # 🔴 Pas « aucun chevron » : un texte de plainte en clair porte
        # légitimement des adresses entre chevrons (`- From: Vendeur
        # <promo@spam.test>`), et l'assertion d'origine ne pouvait pas passer.
        # Ce qu'elle voulait dire : aucune BALISE, et aucune entité restée
        # échappée.
        self.assertIn("From: Vendeur <promo@spam.test>", formulaire["text"],
                      "les chevrons d'une adresse sont du texte, pas une balise")
        for balise in ("<p>", "<ul>", "<li>", "<br", "<pre", "</"):
            self.assertNotIn(balise, formulaire["text"],
                             "du texte brut, pas du HTML")
        self.assertNotIn("&lt;", formulaire["text"],
                         "les entités sont rendues, pas recopiées")

    def test_une_autorite_de_l_autre_nature_est_ignoree(self):
        """Plaintes ACTIVÉES et autorité de l'AUTRE nature — ici,
        seule l'intersection `authority_ids & available_authority_ids` protège.
        """
        self._activer_plaintes()
        apwg = self.env.ref("bf_email_management.report_authority_apwg")
        self.assertFalse(apwg.for_spam, "sinon l'essai ne mesure rien")
        options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertTrue(options["complaints_enabled"],
                        "l'autre garde doit être OUVERTE pour que celle-ci soit "
                        "la seule éprouvée")
        self.assertNotIn(apwg.id, [a["id"] for a in options["authorities"]["spam"]])
        avant = self.env["mail.scheduled.message"].search_count([])
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            retour = self.as_owner().mobile_report(
                [self.spam.id], kind="spam", authority_ids=[apwg.id])
        self.assertEqual(self.env["mail.scheduled.message"].search_count([]), avant,
                         "une autorité d'hameçonnage ne porte pas plainte pour "
                         "un pourriel")
        self.assertNotIn("plainte", retour["message"])

    def test_un_lot_avec_des_autorites_ne_porte_aucune_plainte(self):
        """La règle « une plainte vise UN courriel » n'était
        éprouvée qu'au niveau des options, jamais sur le geste."""
        self._activer_plaintes()
        lot = self.spam | self.with_attachment
        avant = self.env["mail.scheduled.message"].search_count([])
        fake = FakeImap(inbox={"211": self.spam.message_id_header,
                               "103": self.with_attachment.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            retour = self.as_owner().mobile_report(
                lot.ids, authority_ids=[self.crtc.id])
        self.assertEqual(self.env["mail.scheduled.message"].search_count([]), avant,
                         "une plainte vise UN courriel, même par le téléphone")
        self.assertNotIn("plainte", retour["message"])
        lot.invalidate_recordset()
        self.assertEqual(set(lot.mapped("report_kind")), {"spam"},
                         "le lot, lui, est bien rangé")

    # -- ce que la feuille annonce est ce qui sera posé -------
    def test_les_options_annoncent_l_adresse_qui_sera_bloquee(self):
        """🔴 `From: "service@paypal.com" <x@phish.ru>` : la feuille proposait
        « toujours ranger les courriels de service@paypal.com » et le serveur
        créait la règle sur x@phish.ru. C'est la seule chose que la personne
        voit avant de consentir."""
        self.spam.email_from = '"service@paypal.com" <x@phish.ru>'
        options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertEqual(options["senders"], ["x@phish.ru"],
                         "l'adresse annoncée est celle que la règle visera")
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.as_owner().mobile_report([self.spam.id], block_sender=True)
        regles = self.env["bf.email.rule"].search([
            ("user_id", "=", self.owner.id), ("recipe_key", "=", "bf_report_block"),
        ])
        self.assertEqual(len(regles), 1)
        self.assertIn("x@phish.ru", regles.name)
        self.assertNotIn("paypal", regles.name)
        self.assertEqual(
            sorted(regles.condition_ids.mapped("value")),
            ["<x@phish.ru>", "x@phish.ru"],
            "la règle vise l'adresse réelle, sous ses deux formes d'en-tête")

    def test_les_options_ne_tronquent_pas_les_expediteurs(self):
        """`adresses[:3]` tronquait sans le dire, alors que
        `_report_block_senders` crée une règle PAR adresse du lot."""
        quatrieme = self.env["bf.email"].with_user(self.owner).create(self._vals(
            subject="Encore une offre", sender="Autre <autre@spam.test>",
            direction="in", status="new", root=False, body="Bis.", uid="214"))
        lot = self.spam | self.inbound | self.with_attachment | quatrieme
        options = self.as_owner().mobile_report_options(lot.ids)
        self.assertEqual(options["count"], 4)
        self.assertEqual(len(options["senders"]), 4,
                         "annoncer trois adresses faisait consentir à moins que "
                         "ce qui serait posé")
        self.assertIn("autre@spam.test", options["senders"])

    # -- l'hameçonnage ---------------------------------------
    def test_un_hameconnage_est_refuse_sans_le_module(self):
        """Sans `bf_security_awareness`, `_report_to_awareness`
        rend 0 en silence — le courriel était rangé comme un pourriel et
        l'appli recevait `ok:true`."""
        BfEmail = type(self.env["bf.email"])
        with patch.object(BfEmail, "_report_phish_available", return_value=False):
            options = self.as_owner().mobile_report_options([self.spam.id])
            self.assertFalse(options["phish_available"])
            with self.assertRaises(UserError):
                self.as_owner().mobile_report([self.spam.id], kind="phish")
        self.spam.invalidate_recordset()
        self.assertFalse(self.spam.report_kind, "le refus n'a rien rangé")

    # -- rejouer le geste ------------------------------------
    def test_rejouer_le_signalement_est_refuse(self):
        """🔴 Une réponse perdue, ou un geste retouché sur une liste pas encore
        rafraîchie, faisait une DEUXIÈME plainte en brouillon sur la même
        tâche."""
        self._activer_plaintes()
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.as_owner().mobile_report(
                [self.spam.id], authority_ids=[self.crtc.id])
        brouillons = self.env["mail.scheduled.message"].search_count(
            [("model", "=", "project.task")])
        self.assertTrue(brouillons)
        options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertFalse(options["ok"])
        self.assertIn("déjà signalés", options["blocked"])
        with self.assertRaises(UserError):
            self.as_owner().mobile_report(
                [self.spam.id], authority_ids=[self.crtc.id])
        self.assertEqual(
            self.env["mail.scheduled.message"].search_count(
                [("model", "=", "project.task")]),
            brouillons, "aucune deuxième plainte sur la même tâche")

    def test_annuler_rouvre_le_geste(self):
        """L'autre moitié : la garde ne doit pas condamner la
        ligne. `action_unhandle` remet `report_kind` à vide."""
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.as_owner().mobile_report([self.spam.id])
            self.as_owner().mobile_set_handled([self.spam.id], handled=False)
            self.spam.invalidate_recordset()
            self.assertFalse(self.spam.report_kind)
            options = self.as_owner().mobile_report_options([self.spam.id])
            self.assertTrue(options["ok"], "« Annuler » rouvre le geste")
            self.as_owner().mobile_report([self.spam.id])
        self.spam.invalidate_recordset()
        self.assertEqual(self.spam.report_kind, "spam")

    # -- ce que la plainte demande vraiment -------------------
    def test_les_plaintes_exigent_un_projet(self):
        self._activer_plaintes()
        self.owner.company_id.bf_email_complaint_project_id = False
        options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertFalse(
            options["complaints_enabled"],
            "sans projet, `_report_stage_complaint` lève et le refus annule "
            "TOUTE la transaction : le rangement est perdu avec la plainte")
        self.assertEqual(options["authorities"], {"spam": [], "phish": []})

    def test_les_plaintes_exigent_le_droit_de_creer_une_tache(self):
        """⚠️ La garde ne lit que l'ACL de MODÈLE, et c'est tout ce que cet
        essai promet.

        `has_access("create")` sur un ensemble VIDE n'évalue que
        `ir.model.access` : aucune règle d'enregistrement (`ir.rule`) ne peut
        s'appliquer à un enregistrement qui n'existe pas encore. Sur une base
        d'essai clonée d'une base réelle, l'ACL de modèle peut laisser passer un
        simple interne, et c'est la règle d'enregistrement de `project.task`
        qui refuse la création (« Access Denied by record rules for operation:
        create »). Ce refus-là n'est pas pré-vérifiable : la
        règle ne se mesure qu'après l'INSERT. Donc on patche le droit, ce qui
        éprouve que la garde le LIT — pas qu'elle couvre tous les refus.
        """
        self._activer_plaintes()
        options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertTrue(options["complaints_enabled"],
                        "l'offre est ouverte quand tout est en place")
        with patch.object(type(self.env["project.task"]), "has_access",
                          return_value=False):
            options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertFalse(options["complaints_enabled"],
                         "sans le droit de créer la tâche, la plainte n'est pas "
                         "offerte")
        self.assertEqual(options["authorities"], {"spam": [], "phish": []})

    # -- une entrée malformée -------------------------------
    def test_une_autorite_inexistante_ne_fait_pas_tomber_le_geste(self):
        """🔴 `[(6, 0, [999999])]` levait une IntegrityError, rendue en 500 —
        et un 500 dit à l'appli de rejouer sans fin."""
        self._activer_plaintes()
        avant = self.env["mail.scheduled.message"].search_count([])
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            retour = self.as_owner().mobile_report(
                [self.spam.id], authority_ids=[999999])
        self.assertTrue(retour["ok"])
        self.assertEqual(self.env["mail.scheduled.message"].search_count([]), avant)
        self.spam.invalidate_recordset()
        self.assertEqual(self.spam.report_kind, "spam")

    def test_une_autorite_en_chaine_est_une_seule_autorite(self):
        """🔴 Form-encodé, `authority_ids` arrive en CHAÎNE : « 12 » s'itérait
        caractère par caractère et portait plainte aux autorités 1 ET 2, que
        personne n'avait cochées."""
        self._activer_plaintes()
        avant = self.env["mail.scheduled.message"].search_count([])
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            retour = self.as_owner().mobile_report(
                [self.spam.id], authority_ids=str(self.crtc.id))
        self.assertTrue(retour["ok"])
        self.assertEqual(
            self.env["mail.scheduled.message"].search_count([]), avant + 1,
            "une autorité demandée, une plainte — jamais une par chiffre")

    # -- plus dans la boîte ----------------------------------
    def test_une_ligne_sortie_de_la_boite_est_refusee(self):
        """Les options la comptaient, la fenêtre ne la voyait pas.

        Un courriel JETÉ (`action_trash` écrit `active=False`) rendait
        `complaints_enabled: true` et une liste d'autorités pour une plainte
        qui n'avait plus de courriel en boîte, et le geste demandait au serveur
        de déplacer depuis un dossier que le message avait déjà quitté. Les
        deux côtés sont maintenant d'accord, par une seule garde
        (`_report_refus`), et le refus DIT quoi faire.
        """
        self._activer_plaintes()
        self.spam.active = False
        options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertFalse(options["ok"])
        self.assertIn("ne sont plus dans la boîte", options["blocked"])
        self.assertNotIn("authorities", options,
                         "un refus n'annonce aucune offre")
        avant = self.env["mail.scheduled.message"].search_count([])
        with self.assertRaises(UserError) as capture:
            self.as_owner().mobile_report([self.spam.id],
                                          authority_ids=[self.crtc.id])
        self.assertIn("ne sont plus dans la boîte", str(capture.exception))
        self.assertEqual(self.env["mail.scheduled.message"].search_count([]),
                         avant, "aucune plainte sur un courriel hors de la boîte")

    def test_un_lot_mele_d_une_ligne_jetee_est_refuse(self):
        """L'autre moitié : un lot d'une ligne en boîte et d'une
        ligne jetée partait à moitié — la fenêtre et les options ne
        comptaient pas la même chose. Le lot entier se refuse, et il nomme le
        nombre de lignes fautives."""
        self.with_attachment.active = False
        lot = self.spam | self.with_attachment
        options = self.as_owner().mobile_report_options(lot.ids)
        self.assertFalse(options["ok"])
        self.assertIn("1 courriel(s)", options["blocked"])
        with self.assertRaises(UserError):
            self.as_owner().mobile_report(lot.ids)
        lot.invalidate_recordset()
        self.assertFalse(any(lot.mapped("report_kind")),
                         "le refus est entier : rien n'est signalé à moitié")

    # -- la garde au point d'action --------------------------
    def test_une_fenetre_restee_ouverte_ne_repose_pas_tout(self):
        """La garde « déjà signalé » vivait dans
        `action_report_spam` et dans `mobile_report`, jamais au point d'action.
        Elle y est maintenant — c'est le seul endroit que rien ne contourne.

        ⚠️ Mesuré sur une base d'essai, et noté ici parce que l'essai dirait
        faux autrement : le scénario redouté — le téléphone
        signale pendant que la fenêtre du bureau est ouverte — n'allait PAS
        jusqu'à reposer la plainte. Signaler désactive la ligne
        (`_report_mark` écrit `active=False`), la lecture du many2many
        `email_ids` ne la rend alors plus, et `action_confirm` refusait déjà
        par « Aucun courriel à signaler. ». Les DEUX moitiés sont donc
        éprouvées : d'abord le cas qui restait ouvert et que seule la garde
        refuse (la ligne classée sur une fiche entre l'ouverture et le clic,
        qui reste active et donc visible de la fenêtre), puis le cas décrit,
        où ce qui compte est que rien ne se repose.
        """
        self._activer_plaintes()
        fake = FakeImap(inbox={"211": self.spam.message_id_header,
                               "103": self.with_attachment.message_id_header})
        # -- la moitié que seule la garde neuve refuse ----------------------
        fenetre = self.env["bf.email.report.wizard"].with_user(self.owner).create({
            "email_ids": [(6, 0, self.spam.ids)],
            "kind": "spam",
            "authority_ids": [(6, 0, self.crtc.ids)],
        })
        tache = self.env["project.task"].create({"name": "Classé entre-temps"})
        self.spam.write({"res_model": "project.task", "res_id": tache.id})
        avant = self.env["mail.scheduled.message"].search_count([])
        with patch(f"{IMAP}.open_connection", return_value=fake):
            with self.assertRaises(UserError) as capture:
                fenetre.action_confirm()
        self.assertIn("classés sur une fiche", str(capture.exception),
                      "le refus est celui de la garde, au point d'action")
        self.spam.invalidate_recordset()
        self.assertFalse(self.spam.report_kind, "rien n'a été signalé")
        self.assertFalse(fake.copied, "et rien n'a bougé au serveur")
        self.assertEqual(self.env["mail.scheduled.message"].search_count([]),
                         avant, "aucune plainte préparée")
        # -- le cas décrit : le téléphone signale pendant que la fenêtre est
        #    ouverte sur le même courriel ------------------------------------
        seconde = self.env["bf.email.report.wizard"].with_user(self.owner).create({
            "email_ids": [(6, 0, self.with_attachment.ids)],
            "kind": "spam",
            "authority_ids": [(6, 0, self.crtc.ids)],
        })
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.as_owner().mobile_report([self.with_attachment.id],
                                          authority_ids=[self.crtc.id])
        brouillons = self.env["mail.scheduled.message"].search_count(
            [("model", "=", "project.task")])
        self.assertTrue(brouillons, "le téléphone a bien porté la plainte")
        self.assertTrue(seconde.exists(),
                        "la fenêtre est toujours là : l'aspiration des "
                        "transitoires est au cron, pas au `create`")
        self.assertFalse(seconde.email_ids,
                         "mais la ligne signalée est désactivée, et la lecture "
                         "du many2many ne la rend plus")
        with patch(f"{IMAP}.open_connection", return_value=fake):
            with self.assertRaises(UserError):
                seconde.action_confirm()
        self.assertEqual(
            self.env["mail.scheduled.message"].search_count(
                [("model", "=", "project.task")]),
            brouillons, "aucune deuxième plainte sur la même tâche")

    # -- la case masquée du bureau ---------------------------
    def test_basculer_sur_hameconnage_depique_le_blocage(self):
        """La vue MASQUE la case
        (`invisible="kind == 'phish'"`), elle ne la dépique pas — invisible ≠
        non posé. `_onchange_kind` ne remettait que `authority_ids`, et depuis
        la phrase neuve du lot le compte rendu annonçait « Aucune règle de
        blocage… » pour un blocage que la personne avait visuellement
        dé-demandé. L'appli, elle, oublie la case en basculant.

        Par la fenêtre réelle (`Form`), pas par un appel direct à l'onchange :
        ce qu'on veut mesurer, c'est ce que le bureau POSE.
        """
        BfEmail = type(self.env["bf.email"])
        with patch.object(BfEmail, "_report_phish_available", return_value=True):
            fenetre = Form(self.env["bf.email.report.wizard"].with_user(
                self.owner).with_context(
                    default_email_ids=[(6, 0, self.spam.ids)]))
            fenetre.block_sender = True
            fenetre.kind = "phish"
            wiz = fenetre.save()
            self.assertFalse(wiz.block_sender,
                             "la case masquée ne doit pas rester posée")
            fake = FakeImap(inbox={"211": self.spam.message_id_header})
            with patch(f"{IMAP}.open_connection", return_value=fake):
                retour = wiz.action_confirm()
        message = retour["infos"]["message"]
        self.assertNotIn("Aucune règle de blocage", message,
                         "la personne a dé-demandé le blocage : le compte rendu "
                         "n'a pas à parler d'un blocage refusé")
        self.assertFalse(self.env["bf.email.rule"].search([
            ("user_id", "=", self.owner.id),
            ("name", "ilike", "promo@spam.test"),
        ]), "et surtout : aucune règle")

    # -- rien à bloquer --------------------------------------
    def test_un_from_illisible_n_offre_pas_le_blocage_et_le_dit(self):
        """`block_offered` ne regardait que le dossier
        Indésirables. Un `From` vide ou que `parseaddr` ne lit pas rendait
        `senders: []` ET `block_offered: true` ; `POST /report
        {block_sender:true}` créait alors ZÉRO règle et le compte rendu ne
        disait RIEN — ce que le lot vient d'interdire pour le hameçonnage.
        """
        self.spam.email_from = "Vendeur sans adresse"
        options = self.as_owner().mobile_report_options([self.spam.id])
        self.assertEqual(options["senders"], [])
        self.assertEqual(options["junk_folder"], "Junk",
                         "le dossier, lui, existe : c'est bien l'adresse qui "
                         "manque")
        self.assertFalse(options["block_offered"],
                         "il n'y a aucune adresse à bloquer")
        fake = FakeImap(inbox={"211": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            retour = self.as_owner().mobile_report(
                [self.spam.id], block_sender=True)
        self.assertFalse(self.env["bf.email.rule"].search([
            ("user_id", "=", self.owner.id),
            ("recipe_key", "=", "bf_report_block"),
        ]), "aucune règle n'est possible sans adresse")
        self.assertIn("sans adresse d'expéditeur lisible", retour["message"],
                      "demandé et pas fait se DIT, comme pour le hameçonnage")

    # -- un drapeau n'est pas une liste ----------------------
    def test_un_booleen_n_est_pas_une_liste_d_autorites(self):
        """🔴 `isinstance(True, int)` vaut VRAI en Python, donc
        `authority_ids: true` passait la garde du scalaire — `[True]` →
        `int(True)` → 1 → un brouillon de plainte à l'autorité n° 1, que
        personne n'avait cochée. Même trou que la chaîne « 12 », un autre
        type."""
        self._activer_plaintes()
        avant = self.env["mail.scheduled.message"].search_count([])
        for drapeau in (True, False, [True]):
            with self.subTest(drapeau=drapeau):
                with self.assertRaises(UserError):
                    self.as_owner().mobile_report([self.spam.id],
                                                  authority_ids=drapeau)
        self.assertEqual(
            self.env["mail.scheduled.message"].search_count([]), avant,
            "aucune plainte préparée à une autorité que personne n'a cochée")
        self.spam.invalidate_recordset()
        self.assertFalse(self.spam.report_kind, "et rien n'a été rangé")

    def test_un_booleen_n_est_pas_une_liste_de_courriels(self):
        """🔴 L'autre moitié : `email_ids: true` devenait `[1]` — le
        courriel n° 1 de la base, avec l'écriture IMAP, la règle de boîte et la
        plainte de ce lot derrière la coercition."""
        for drapeau in (True, False, [True]):
            with self.subTest(drapeau=drapeau):
                with self.assertRaises(UserError) as capture:
                    self.as_owner().mobile_report_options(drapeau)
                self.assertNotIsInstance(
                    capture.exception, AccessError,
                    "refusé parce qu'un booléen n'est pas une liste "
                    "d'identifiants — pas parce que le courriel n° 1 serait "
                    "celui d'un autre usager")
                with self.assertRaises(UserError):
                    self.as_owner().mobile_report(drapeau)

    # -- la borne de la liste d'autorités --------------------
    def test_la_liste_d_autorites_est_bornee(self):
        """`email_ids` était plafonné (`MAX_BULK_IDS`),
        `authority_ids` ne l'était pas — et chaque autorité retenue prépare un
        brouillon de plainte sur une tâche."""
        self._activer_plaintes()
        avant = self.env["mail.scheduled.message"].search_count([])
        with self.assertRaises(UserError) as capture:
            self.as_owner().mobile_report(
                [self.spam.id],
                authority_ids=list(range(1, MAX_BULK_IDS + 2)))
        self.assertIn("Trop d'autorités", str(capture.exception))
        self.assertEqual(self.env["mail.scheduled.message"].search_count([]),
                         avant)
        self.spam.invalidate_recordset()
        self.assertFalse(self.spam.report_kind, "le refus n'a rien rangé")

    def test_la_config_annonce_la_fonction(self):
        config = self.as_owner().get_mobile_config()
        self.assertEqual(config["report_api"], 1)
