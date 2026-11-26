"""Le courriel qu'on ne reçoit plus, rangé quand même (11.67.0).

Qui lit ses avis dans Odoo ne reçoit plus de courriel pour eux : c'est voulu, et
le composeur de ce module l'applique déjà. Mais ce qui n'arrive plus par
courriel n'est plus cherchable dans son logiciel de courrier. Le dépôt range une
copie **directement dans un dossier IMAP**, par `APPEND` : aucun SMTP, aucun
saut externe, aucun rebond possible.

⚠️ Les essais ne touchent évidemment aucun serveur : la connexion est remplacée
par un faux qui note les `APPEND`. Ce qu'on mesure, c'est **qui** entre en file,
**ce** qui est déposé, et surtout ce qui ne l'est pas.
"""

from unittest.mock import patch

from odoo import Command
from odoo.tests import TransactionCase, tagged

CHEMIN = "odoo.addons.bf_email_management.models.bf_email_depot"


class _FauxImap:
    """Un serveur qui accepte tout et se souvient de ce qu'on y range."""

    def __init__(self):
        self.appends = []
        self.dossiers = []

    def append(self, dossier, flags, date, corps):
        self.appends.append((dossier, flags, corps))
        return ("OK", [b"APPEND completed"])

    def logout(self):
        return ("BYE", [b""])


@tagged("bf_email", "post_install", "-at_install")
class TestDepotAvisImap(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.collegue = Users.create({
            "name": "Collègue dépôt",
            "login": "essai.depot.collegue@test.invalid",
            "email": "essai.depot.collegue@test.invalid",
            "notification_type": "inbox",
        })
        cls.compte = cls.env["bf.email.account"].create({
            "name": "Boîte du collègue",
            "user_id": cls.collegue.id,
            "own_inbox": True,
            "host": "imap.test.invalid",
            "port": 993,
            "login": "essai.depot.collegue@test.invalid",
            "password": "secret",
        })
        cls.fiche = cls.env["res.partner"].create({"name": "Fiche dépôt"})

    def _message(self, corps="<p>Un avis.</p>", sujet="Un avis"):
        return self.env["mail.message"].create({
            "model": "res.partner",
            "res_id": self.fiche.id,
            "body": corps,
            "subject": sujet,
            "message_type": "notification",
            "author_id": self.env.user.partner_id.id,
        })

    def _file(self, usager=None):
        return self.env["bf.email.depot"].sudo().search(
            [("user_id", "=", (usager or self.collegue).id)])

    # -- qui entre en file -------------------------------------------------

    def test_sans_la_preference_rien_n_entre_en_file(self):
        """L'état par défaut : on ne range dans la boîte de personne sans le dire."""
        self.collegue.bf_depot_imap_avis = False
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        self.assertFalse(self._file())

    def test_avec_la_preference_le_message_entre_en_file(self):
        self.collegue.sudo().bf_depot_imap_avis = True
        message = self._message()
        self.env["bf.email.depot"]._bf_mettre_en_file(
            message, self.collegue.partner_id)
        file = self._file()
        self.assertEqual(len(file), 1)
        self.assertEqual(file.message_id, message)
        self.assertEqual(file.state, "pending")

    def test_jamais_deux_fois_le_meme_message(self):
        """Le noyau peut rappeler l'accroche : la boîte ne prend pas de doublon."""
        self.collegue.sudo().bf_depot_imap_avis = True
        message = self._message()
        Depot = self.env["bf.email.depot"]
        Depot._bf_mettre_en_file(message, self.collegue.partner_id)
        Depot._bf_mettre_en_file(message, self.collegue.partner_id)
        self.assertEqual(len(self._file()), 1)

    def test_un_externe_n_entre_jamais_en_file(self):
        externe = self.env["res.partner"].create({
            "name": "Client dépôt",
            "email": "client.depot@ailleurs.invalid"})
        self.env["bf.email.depot"]._bf_mettre_en_file(self._message(), externe)
        self.assertFalse(self.env["bf.email.depot"].sudo().search(
            [("user_id.login", "=", "client.depot@ailleurs.invalid")]))

    def test_la_mise_en_file_ne_leve_jamais(self):
        """⚠️ Une file qui refuse ne doit pas faire échouer la publication."""
        self.collegue.sudo().bf_depot_imap_avis = True
        Depot = self.env["bf.email.depot"]
        with patch.object(type(Depot), "create",
                          side_effect=ValueError("file cassée")):
            self.assertFalse(Depot._bf_mettre_en_file(
                self._message(), self.collegue.partner_id))

    # -- l'accroche du noyau ----------------------------------------------

    def _poster(self, corps, differe=False):
        """Publie un commentaire au chatter de la fiche, pour le collègue.

        ⚠️ `mail_defer_seconds=0` par défaut, et c'est le sujet de l'essai
        suivant : `mail_post_defer` (installé ici) pose une date d'envoi, et le
        cœur qui voit une date **crée une programmation et SORT** sans appeler
        `_notify_thread_by_inbox`. Un essai qui publie normalement et regarde
        tout de suite ne mesure donc rien du tout.
        """
        fiche = self.fiche
        if not differe:
            fiche = fiche.with_context(mail_defer_seconds=0)
        return fiche.message_post(
            body=corps, subtype_xmlid="mail.mt_comment",
            partner_ids=self.collegue.partner_id.ids)

    def test_un_avis_lu_dans_odoo_entre_en_file_tout_seul(self):
        """🔴 Le point d'accroche EST la définition du lot.

        `_notify_thread_by_inbox` reçoit exactement les gens qui auraient eu un
        courriel si leur préférence était « par courriel ». On range donc ce
        qui ne part plus, et rien d'autre.
        """
        self.collegue.sudo().bf_depot_imap_avis = True
        self.fiche.message_subscribe(partner_ids=self.collegue.partner_id.ids)
        self._poster("<p>Un commentaire pour le collègue.</p>")
        self.assertTrue(self._file(), "le message devait entrer en file")

    def test_le_chemin_REPORTE_depose_aussi(self):
        """🔴 C'est le chemin courant, pas l'exception.

        `mail_post_defer` reporte toute notification de 30 secondes par défaut.
        Le cœur crée alors une `mail.message.schedule` et n'appelle PAS
        l'accroche; c'est le cron qui rejoue `_notify_thread` plus tard. Sans
        cet essai, le lot serait éprouvé seulement sur un chemin que personne
        n'emprunte.
        """
        self.collegue.sudo().bf_depot_imap_avis = True
        self.fiche.message_subscribe(partner_ids=self.collegue.partner_id.ids)
        self._poster("<p>Un commentaire reporté.</p>", differe=True)
        programmees = self.env["mail.message.schedule"].sudo().search([])
        if not programmees:
            self.skipTest("ce locataire ne reporte pas ses notifications")
        self.assertFalse(self._file(), "rien avant que le cron passe")
        programmees._send_notifications()
        self.assertTrue(self._file(), "le cron devait déposer en file")

    def test_sans_la_preference_l_accroche_ne_met_rien_en_file(self):
        self.collegue.sudo().bf_depot_imap_avis = False
        self.fiche.message_subscribe(partner_ids=self.collegue.partner_id.ids)
        self._poster("<p>Un autre commentaire.</p>")
        self.assertFalse(self._file())

    # -- le dépôt lui-même -------------------------------------------------

    def _deposer(self):
        faux = _FauxImap()
        with patch.object(type(self.env["bf.email.account"]),
                          "_ouvrir_imap", return_value=faux), \
             patch("%s.bf_email_imap.ensure_folder" % CHEMIN) as assure:
            deposes = self.env["bf.email.depot"].sudo()._cron_deposer()
        return faux, assure, deposes

    def test_le_cron_depose_et_marque_la_ligne(self):
        self.collegue.sudo().bf_depot_imap_avis = True
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        faux, assure, deposes = self._deposer()
        self.assertEqual(deposes, 1)
        self.assertEqual(len(faux.appends), 1)
        self.assertEqual(self._file().state, "done")
        self.assertTrue(self._file().deposited_on)
        assure.assert_called_once()

    def test_la_copie_est_deposee_DEJA_LUE(self):
        """La personne lit l'avis dans Odoo : la copie archive, elle ne sonne pas."""
        self.collegue.sudo().bf_depot_imap_avis = True
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        faux, _a, _d = self._deposer()
        (_dossier, flags, _corps) = faux.appends[0]
        self.assertIn("Seen", flags)

    def test_la_copie_porte_le_sujet_le_corps_et_son_entete(self):
        self.collegue.sudo().bf_depot_imap_avis = True
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(corps="<p>Relire le devis v3.</p>",
                          sujet="Avis au collègue"),
            self.collegue.partner_id)
        faux, _a, _d = self._deposer()
        corps = faux.appends[0][2].decode("utf-8", "replace")
        self.assertIn("Avis au", corps)
        self.assertIn("Relire le devis v3.", corps)
        self.assertIn("X-BF-Depot-Odoo", corps)

    def test_le_dossier_vise_est_celui_du_compte(self):
        self.collegue.sudo().bf_depot_imap_avis = True
        self.compte.bf_depot_imap_folder = "Avis Odoo"
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        faux, _a, _d = self._deposer()
        self.assertIn("Avis Odoo", faux.appends[0][0])

    def test_sans_dossier_le_depot_vise_Odoo(self):
        self.collegue.sudo().bf_depot_imap_avis = True
        self.compte.bf_depot_imap_folder = False
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        faux, _a, _d = self._deposer()
        self.assertIn("Odoo", faux.appends[0][0])

    def test_sans_boite_propre_la_ligne_est_sans_objet(self):
        """Pas d'échec à répétition pour quelqu'un qui n'a pas de boîte."""
        self.collegue.sudo().bf_depot_imap_avis = True
        self.compte.own_inbox = False
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        self.env["bf.email.depot"].sudo()._cron_deposer()
        self.assertEqual(self._file().state, "skipped")

    def test_une_boite_injoignable_laisse_la_ligne_a_deposer(self):
        """⚠️ Pas « en échec » : un serveur muet se retente, il ne se jette pas."""
        self.collegue.sudo().bf_depot_imap_avis = True
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        with patch.object(type(self.env["bf.email.account"]), "_ouvrir_imap",
                          side_effect=OSError("serveur muet")):
            self.env["bf.email.depot"].sudo()._cron_deposer()
        ligne = self._file()
        self.assertEqual(ligne.state, "pending")
        self.assertIn("serveur muet", ligne.error)
        self.assertEqual(ligne.attempts, 1)

    def test_un_append_refuse_marque_la_ligne_en_echec(self):
        self.collegue.sudo().bf_depot_imap_avis = True
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        faux = _FauxImap()
        with patch.object(type(self.env["bf.email.account"]),
                          "_ouvrir_imap", return_value=faux), \
             patch("%s.bf_email_imap.ensure_folder" % CHEMIN), \
             patch.object(_FauxImap, "append",
                          side_effect=ValueError("dossier refusé")):
            self.env["bf.email.depot"].sudo()._cron_deposer()
        self.assertEqual(self._file().state, "error")
        self.assertIn("dossier refusé", self._file().error)

    def test_une_ligne_deja_deposee_ne_repart_pas(self):
        self.collegue.sudo().bf_depot_imap_avis = True
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        self._deposer()
        faux, _a, deposes = self._deposer()
        self.assertEqual(deposes, 0)
        self.assertFalse(faux.appends)

    # -- qui voit la file --------------------------------------------------

    def test_un_collegue_ne_voit_PAS_la_file_d_un_autre(self):
        """🔴 Une ligne dit QUI reçoit QUEL avis.

        Sans règle d'enregistrement, l'ACL seule laissait tout usager interne
        lire la file entière. Ce n'est pas le contenu du message qui fuit (il a
        ses propres droits), c'est la métadonnée : qui est notifié de quoi.
        """
        self.collegue.sudo().bf_depot_imap_avis = True
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        autre = self.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Curieux",
                "login": "essai.curieux@test.invalid",
                "groups_id": [Command.set(self.env.ref("base.group_user").ids)]})
        self.env.invalidate_all()
        vu = self.env["bf.email.depot"].with_user(autre).search([])
        self.assertFalse(vu, "la file d'un autre doit être invisible")

    def test_chacun_voit_la_sienne(self):
        self.collegue.sudo().bf_depot_imap_avis = True
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        self.env.invalidate_all()
        vu = self.env["bf.email.depot"].with_user(self.collegue).search([])
        self.assertEqual(len(vu), 1)
        self.assertEqual(vu.user_id, self.collegue)

    def test_l_admin_courriel_voit_tout_en_lecture_seule(self):
        """Les règles de groupes différents se combinent en OU."""
        groupe = self.env.ref("bf_email_management.group_email_admin",
                              raise_if_not_found=False)
        if not groupe:
            self.skipTest("zone admin absente de cette version")
        self.collegue.sudo().bf_depot_imap_avis = True
        self.env["bf.email.depot"]._bf_mettre_en_file(
            self._message(), self.collegue.partner_id)
        admin = self.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Admin courriel",
                "login": "essai.admincourriel@test.invalid",
                "groups_id": [Command.set(
                    (self.env.ref("base.group_user") + groupe).ids)]})
        self.env.invalidate_all()
        vu = self.env["bf.email.depot"].with_user(admin).search([])
        self.assertTrue(vu, "l'admin courriel doit voir la file")
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            vu.with_user(admin).write({"state": "skipped"})

    # -- l'interrupteur ----------------------------------------------------

    def test_l_etat_se_cache_sans_boite_propre(self):
        self.compte.own_inbox = False
        etat = self.env["res.users"].with_user(self.collegue).bf_depot_avis_state()
        self.assertFalse(etat["enabled"])

    def test_l_etat_s_affiche_avec_une_boite(self):
        etat = self.env["res.users"].with_user(self.collegue).bf_depot_avis_state()
        self.assertTrue(etat["enabled"])
        self.assertTrue(etat["inbox"], "ce collègue lit ses avis dans Odoo")

    def test_la_bascule_change_sa_propre_preference(self):
        avant = self.collegue.bf_depot_imap_avis
        etat = self.collegue.with_user(self.collegue).action_bf_depot_avis_toggle()
        self.assertNotEqual(self.collegue.bf_depot_imap_avis, avant)
        self.assertEqual(etat["active"], self.collegue.bf_depot_imap_avis)

    def test_on_ne_bascule_pas_la_preference_d_un_autre(self):
        """🔴 Un champ qu'on écrit en sudo doit refuser une cible qui n'est pas soi."""
        from odoo.exceptions import AccessError
        autre = self.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Autre dépôt",
                "login": "essai.depot.autre@test.invalid"})
        with self.assertRaises(AccessError):
            autre.with_user(self.collegue).action_bf_depot_avis_toggle()


@tagged("bf_email", "post_install", "-at_install")
class TestCronDepotSousSonUsager(TransactionCase):
    """🔴 Le cron se lance sous SON usager, pas en sudo (18.0.11.67.2).

    Les essais du dépôt appellent `_cron_deposer()` en sudo : ils n'ont jamais
    vu que `ir.actions.server.run()` vérifie d'abord le droit d'écriture de
    l'usager du cron sur le modèle. Sous l'admin sans le groupe admin courriel,
    le cron levait à chaque passe. Ici on passe par le même chemin que le
    planificateur : l'action serveur, sous `cron.user_id`.
    """

    def setUp(self):
        super().setUp()
        self.cron = self.env.ref("bf_email_management.ir_cron_bf_email_depot")
        self.action = self.cron.ir_actions_server_id

    def test_le_cron_tourne_sous_le_compte_systeme(self):
        self.assertEqual(self.cron.user_id, self.env.ref("base.user_root"))

    def test_l_action_du_cron_passe_sous_son_usager(self):
        # Ne lève pas : file vide ou non, le contrôle d'Odoo est passé.
        self.action.with_user(self.cron.user_id).run()

    def test_le_controle_discrimine_un_usager_sans_le_groupe(self):
        """L'ancienne configuration : un interne sans l'admin courriel lève."""
        from odoo.exceptions import AccessError
        interne = self.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Interne cron",
                "login": "interne_cron_essai",
                "groups_id": [Command.set([self.env.ref("base.group_user").id])],
            })
        with self.assertRaises(AccessError):
            self.action.with_user(interne).run()


@tagged("bf_email", "post_install", "-at_install")
class TestFileDesDepotsNeSeBouchePas(TransactionCase):
    """Une boîte injoignable ne bouche pas la file des autres.

    Une boîte dont le mot de passe a changé, ou dont le jeton a expiré, garde
    ses lignes « À déposer ». Trois choses ferment la file à ce cas : les
    lignes jamais tentées passent d'abord, chaque ligne compte ses propres
    tentatives et sort de la file au plafond, et le ménage jette les lignes
    terminées depuis longtemps, par lots bornés.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.panne = Users.create({
            "name": "Boîte en panne",
            "login": "depot.file.panne@test.invalid",
            "email": "depot.file.panne@test.invalid",
            "notification_type": "inbox",
            "bf_depot_imap_avis": True,
        })
        cls.saine = Users.create({
            "name": "Boîte saine",
            "login": "depot.file.saine@test.invalid",
            "email": "depot.file.saine@test.invalid",
            "notification_type": "inbox",
            "bf_depot_imap_avis": True,
        })
        Compte = cls.env["bf.email.account"]
        cls.compte_panne = Compte.create({
            "name": "Boîte en panne",
            "user_id": cls.panne.id,
            "own_inbox": True,
            "host": "imap.panne.invalid",
            "port": 993,
            "login": "depot.file.panne@test.invalid",
            "password": "secret",
        })
        cls.compte_saine = Compte.create({
            "name": "Boîte saine",
            "user_id": cls.saine.id,
            "own_inbox": True,
            "host": "imap.saine.invalid",
            "port": 993,
            "login": "depot.file.saine@test.invalid",
            "password": "secret",
        })
        cls.fiche = cls.env["res.partner"].create({"name": "Fiche de la file"})
        cls.Depot = cls.env["bf.email.depot"].sudo()
        from odoo.addons.bf_email_management.models import bf_email_depot
        cls.module = bf_email_depot

    def _message(self):
        return self.env["mail.message"].create({
            "model": "res.partner",
            "res_id": self.fiche.id,
            "body": "<p>Un avis.</p>",
            "subject": "Un avis",
            "message_type": "notification",
            "author_id": self.env.user.partner_id.id,
        })

    def _en_file(self, usager, nombre=1):
        lignes = self.Depot.browse()
        for _i in range(nombre):
            lignes |= self.Depot._bf_mettre_en_file(
                self._message(), usager.partner_id)
        return lignes

    def _passe(self, limite=None):
        """Une passe du cron : la boîte en panne refuse, la saine accepte."""
        faux = _FauxImap()
        ouvertures = []

        def ouvrir(compte):
            ouvertures.append(compte.id)
            if compte == self.compte_panne:
                raise OSError("authentification refusée")
            return faux

        kwargs = {} if limite is None else {"limite": limite}
        with patch.object(type(self.env["bf.email.account"]), "_ouvrir_imap",
                          autospec=True, side_effect=ouvrir), \
             patch("%s.bf_email_imap.ensure_folder" % CHEMIN):
            self.Depot._cron_deposer(**kwargs)
        return faux, ouvertures

    # -- l'ordre de la file ------------------------------------------------

    def test_une_boite_en_panne_n_affame_pas_les_autres(self):
        """La boîte en panne remplit la passe ; la saine passe à la suivante."""
        en_panne = self._en_file(self.panne, 3)
        saine = self._en_file(self.saine, 1)
        self._passe(limite=3)
        self.assertEqual(saine.state, "pending",
                         "la passe ne voyait que les trois premières lignes")
        self.assertEqual(set(en_panne.mapped("attempts")), {1})
        faux, _o = self._passe(limite=3)
        self.assertEqual(saine.state, "done",
                         "une ligne jamais tentée passe avant les retentatives")
        self.assertEqual(len(faux.appends), 1)
        self.assertEqual(set(en_panne.mapped("state")), {"pending"})

    def test_chaque_ligne_compte_ses_propres_tentatives(self):
        vieille, neuve = self._en_file(self.panne, 2)
        vieille.attempts = 5
        self._passe()
        self.assertEqual(vieille.attempts, 6)
        self.assertEqual(neuve.attempts, 1)

    # -- le plafond --------------------------------------------------------

    def test_sous_le_plafond_la_ligne_reste_en_file(self):
        ligne = self._en_file(self.panne)
        ligne.attempts = self.module.PLAFOND_TENTATIVES - 2
        self._passe()
        self.assertEqual(ligne.state, "pending")
        self.assertIn("authentification refusée", ligne.error)

    def test_au_plafond_la_ligne_sort_de_la_file_avec_sa_raison(self):
        ligne = self._en_file(self.panne)
        ligne.attempts = self.module.PLAFOND_TENTATIVES - 1
        self._passe()
        self.assertEqual(ligne.state, "error")
        self.assertEqual(ligne.attempts, self.module.PLAFOND_TENTATIVES)
        self.assertIn("authentification refusée", ligne.error)
        # Sortie de la file : la passe suivante n'ouvre plus cette boîte.
        _faux, ouvertures = self._passe()
        self.assertNotIn(self.compte_panne.id, ouvertures)
        self.assertEqual(ligne.attempts, self.module.PLAFOND_TENTATIVES)

    def test_une_ligne_qui_echoue_n_fois_sort_de_la_file(self):
        """Le chemin complet, passe après passe, sans raccourci."""
        ligne = self._en_file(self.panne)
        for _i in range(self.module.PLAFOND_TENTATIVES):
            self.assertEqual(ligne.state, "pending")
            self._passe()
        self.assertEqual(ligne.state, "error")
        self.assertIn("authentification refusée", ligne.error)

    # -- le ménage ---------------------------------------------------------

    def _vieillir(self, lignes, jours):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_email_depot SET write_date = now() at time zone 'UTC'"
            " - make_interval(days => %s) WHERE id IN %s",
            (jours, tuple(lignes.ids)))
        self.env.invalidate_all()

    def test_le_menage_jette_les_lignes_terminees_anciennes(self):
        lignes = self._en_file(self.saine, 3)
        lignes[0].state = "done"
        lignes[1].state = "skipped"
        lignes[2].state = "error"
        self._vieillir(lignes, self.module.GARDE_JOURS + 1)
        self._passe()
        self.assertFalse(lignes.exists())

    def test_le_menage_garde_les_lignes_recentes(self):
        ligne = self._en_file(self.saine)
        ligne.state = "done"
        self._vieillir(ligne, self.module.GARDE_JOURS - 1)
        self._passe()
        self.assertTrue(ligne.exists())

    def test_le_menage_ne_jette_jamais_une_ligne_a_deposer(self):
        ligne = self._en_file(self.panne)
        self._vieillir(ligne, self.module.GARDE_JOURS + 10)
        self._passe()
        self.assertTrue(ligne.exists())
        self.assertEqual(ligne.state, "pending")

    def test_le_menage_est_borne_par_passe(self):
        lignes = self._en_file(self.saine, 3)
        lignes.write({"state": "done"})
        self._vieillir(lignes, self.module.GARDE_JOURS + 1)
        with patch("%s.PURGE_PAR_PASSE" % CHEMIN, 2):
            self._passe()
        self.assertEqual(len(lignes.exists()), 1)
        with patch("%s.PURGE_PAR_PASSE" % CHEMIN, 2):
            self._passe()
        self.assertFalse(lignes.exists())
