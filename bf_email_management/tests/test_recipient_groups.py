"""Groupes de destinataires : bornes, dépliage, portée.

L'ordre des classes suit le risque, pas la fonctionnalité. Ce qui peut faire
partir un courriel à des gens qui ne devaient pas le recevoir passe en premier :
une étiquette de contacts issue d'un import peut porter des dizaines de milliers
de fiches, et le composeur envoie UN courriel où chaque destinataire voit
l'adresse de tous les autres. Un groupe dynamique sans plafond serait un
incident, pas une commodité.
"""

from odoo import fields
from odoo.addons.mail.tests.common import MailCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, tagged


class RecipientGroupCase(MailCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # La fonction est livrée DORMANTE (voir TestGroupeInterrupteur) : les
        # essais qui suivent décrivent son comportement en service.
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.recipient_group_enabled", "1")
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        groupe_interne = cls.env.ref("base.group_user")

        cls.alice = Users.create({
            "name": "Alice Interne",
            "login": "alice.groupes@test.invalid",
            "email": "alice@test.invalid",
            "groups_id": [(6, 0, [groupe_interne.id])],
        })
        cls.bob = Users.create({
            "name": "Bob Interne",
            "login": "bob.groupes@test.invalid",
            "email": "bob@test.invalid",
            "groups_id": [(6, 0, [groupe_interne.id])],
        })

        Partner = cls.env["res.partner"]
        cls.societe = Partner.create({"name": "Société Test", "is_company": True})
        cls.membres = Partner.create([{
            "name": "Membre %d" % i,
            "email": "membre%d@test.invalid" % i,
            "parent_id": cls.societe.id,
        } for i in range(3)])
        cls.sans_adresse = Partner.create({"name": "Sans Adresse"})

        cls.groupe = cls.env["bf.recipient.group"].with_user(cls.alice).create({
            "name": "Équipe de test",
            "partner_ids": [(6, 0, cls.membres.ids)],
        })

    def _coquille(self, user):
        """La ligne `bf.email` que le bouton « Composer » crée sous le composeur.

        C'est la cible réelle d'un courriel composé depuis la boîte, et la
        seule sur laquelle un usager interne ordinaire peut poster sans droit
        particulier : la règle de `bf.email` est une règle de propriétaire.
        """
        return self.env["bf.email"].with_user(user).create({
            "subject": "", "direction": "out", "source": "chatter",
            "status": "read", "is_handled": True,
            "user_id": user.id, "date": fields.Datetime.now(),
        })

    def _composeur(self, user, cible=None, **valeurs):
        """Un composeur en mode commentaire, posé sur une fiche réelle."""
        cible = cible if cible is not None else self.societe
        vals = {
            "model": cible._name,
            "res_ids": [cible.id],
            "composition_mode": "comment",
            "subject": "Essai",
            "body": "<p>Bonjour.</p>",
        }
        vals.update(valeurs)
        return self.env["mail.compose.message"].with_user(user).create(vals)


@tagged("post_install", "-at_install")
class TestGroupeBornes(RecipientGroupCase):
    """Le rayon de dégât. Tout ce qui suit doit REFUSER, pas tronquer."""

    def test_a_resolution_de_base(self):
        self.assertEqual(
            self.groupe.with_user(self.alice)._resolve_partners(), self.membres)

    def test_a_membre_sans_adresse_est_ecarte(self):
        """Offrir un nom auquel on ne peut pas écrire vaut moins que rien."""
        self.groupe.partner_ids = [(4, self.sans_adresse.id)]
        resolus = self.groupe.with_user(self.alice)._resolve_partners()
        self.assertNotIn(self.sans_adresse, resolus)
        self.assertEqual(resolus, self.membres)

    def test_a_membre_archive_est_ecarte(self):
        self.membres[0].active = False
        self.assertEqual(
            self.groupe.with_user(self.alice)._resolve_partners(),
            self.membres[1:])

    def test_a_filtre_dynamique_sajoute_aux_membres(self):
        autre = self.env["res.partner"].create({
            "name": "Trouvé par filtre", "email": "filtre@test.invalid",
            "comment": "MARQUEUR25278"})
        self.groupe.filter_domain = "[('comment', 'ilike', 'MARQUEUR25278')]"
        resolus = self.groupe.with_user(self.alice)._resolve_partners()
        self.assertEqual(resolus, self.membres | autre)

    # ------------------------------------------------------------- plafond
    def _fabriquer_foule(self, combien):
        return self.env["res.partner"].create([{
            "name": "Foule %d" % i,
            "email": "foule%d@test.invalid" % i,
            "comment": "FOULE25278",
        } for i in range(combien)])

    def test_b_groupe_au_dela_du_plafond_est_refuse(self):
        """Le cas de la grande base, en miniature : un filtre qui ratisse large."""
        self._fabriquer_foule(60)
        self.groupe.filter_domain = "[('comment', 'ilike', 'FOULE25278')]"
        with self.assertRaises(UserError):
            self.groupe.with_user(self.alice)._resolve_partners()

    def test_b_plafond_absent_vaut_le_defaut_et_non_un(self):
        """🔴 La régression qui aurait tué la fonction au premier déploiement.

        `get_param` rend False quand la clé n'existe pas, et `int(False)` vaut
        0 : un `max(1, int(raw))` seul ramenait le plafond à UN, donc refusait
        tout groupe de deux personnes sur une base neuve, en accusant l'usager
        d'en avoir mis trop.
        """
        self.env["ir.config_parameter"].sudo().search(
            [("key", "=", "bf_email_management.recipient_group_max")]).unlink()
        self.assertEqual(
            self.env["bf.recipient.group"]._max_recipients(), 50)
        self.assertEqual(
            len(self.groupe.with_user(self.alice)._resolve_partners()), 3)

    def test_b_le_plafond_est_reglable(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.recipient_group_max", "2")
        with self.assertRaises(UserError):
            self.groupe.with_user(self.alice)._resolve_partners()
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.recipient_group_max", "50")
        self.assertEqual(
            len(self.groupe.with_user(self.alice)._resolve_partners()), 3)

    def test_b_union_de_groupes_au_dela_du_plafond_est_refusee(self):
        """Trois groupes sous la limite en font un envoi au-dessus.

        Le contrôle par groupe ne suffit pas : c'est l'union qui atterrit
        dans le champ « À ».
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.recipient_group_max", "4")
        Groupe = self.env["bf.recipient.group"].with_user(self.alice)
        foule = self._fabriquer_foule(9)
        groupes = Groupe.browse()
        for i in range(3):
            groupes |= Groupe.create({
                "name": "Tiers %d" % i,
                "partner_ids": [(6, 0, foule[i * 3:(i + 1) * 3].ids)],
            })
        for groupe in groupes:
            self.assertEqual(len(groupe._resolve_partners()), 3)
        composeur = self._composeur(self.alice)
        composeur.bf_recipient_group_ids = [(6, 0, groupes.ids)]
        with self.assertRaises(UserError):
            composeur._bf_expand_recipient_groups()

    def test_b_un_envoi_refuse_ne_laisse_aucun_courriel(self):
        """Le refus arrive AVANT que le `mail.mail` existe.

        Un rollback ne rappelle pas un courriel : si la borne se contentait de
        lever après l'envoi, elle ne servirait à rien.
        """
        self._fabriquer_foule(60)
        self.groupe.filter_domain = "[('comment', 'ilike', 'FOULE25278')]"
        composeur = self._composeur(self.alice, cible=self._coquille(self.alice))
        composeur.bf_recipient_group_ids = [(6, 0, self.groupe.ids)]
        avant = self.env["mail.mail"].search_count([])
        with self.mock_mail_gateway():
            with self.assertRaises(UserError):
                composeur._action_send_mail()
            self.assertFalse(self._mails, "un courriel est parti malgré le refus")
        self.assertEqual(self.env["mail.mail"].search_count([]), avant)

    def test_b_envoi_de_masse_refuse_les_groupes(self):
        """L'envoi de masse écrit à chacun séparément : il n'a rien à faire ici.

        Et surtout, `partner_ids` n'y a pas le même sens : y verser un groupe
        multiplierait les destinataires par le nombre de fiches visées.
        """
        composeur = self._composeur(
            self.alice, composition_mode="mass_mail",
            res_domain="[('id', '=', %d)]" % self.societe.id, res_ids=False)
        composeur.bf_recipient_group_ids = [(6, 0, self.groupe.ids)]
        with self.assertRaises(UserError):
            composeur._bf_expand_recipient_groups()


@tagged("post_install", "-at_install")
class TestGroupeComposeur(RecipientGroupCase):
    """Le dépliage : il doit se voir à l'écran, et survivre à tout."""

    def test_c_fiche_groupe_dans_to_est_remplacee_par_les_membres(self):
        """Le geste Outlook : on tape le nom du groupe dans « À »."""
        composeur = self._composeur(self.alice)
        composeur.partner_ids = [(6, 0, self.groupe.proxy_ids.ids)]
        composeur._onchange_bf_recipient_groups()
        self.assertEqual(composeur.partner_ids, self.membres)
        self.assertEqual(composeur.bf_recipient_group_ids, self.groupe)

    def test_c_le_groupe_va_dans_le_champ_quil_declare(self):
        self.groupe.recipient_field = "bcc"
        composeur = self._composeur(self.alice)
        composeur.bf_recipient_group_ids = [(6, 0, self.groupe.ids)]
        composeur._onchange_bf_recipient_groups()
        self.assertEqual(composeur.partner_bcc_ids, self.membres)
        self.assertFalse(composeur.partner_ids)

    def test_c_retirer_un_gabarit_ne_vide_pas_les_destinataires(self):
        """`partner_ids` est un calcul stocké qui dépend de `template_id`.

        ⚠️ Le geste qui efface n'est pas de CHOISIR un gabarit, c'est de le
        RETIRER : la branche `elif not composer.template_id` du noyau remet
        `partner_ids` à False. Le premier jet de ce test posait un gabarit et
        passait même en débranchant le raccord, donc il ne contrôlait rien.
        Relevé par une passe de mutation le 2026-09-03.
        """
        gabarit = self.env["mail.template"].create({
            "name": "Gabarit d'essai",
            "model_id": self.env["ir.model"]._get("bf.email").id,
            "subject": "Objet gabarit"})
        composeur = self._composeur(
            self.alice, cible=self._coquille(self.alice), template_id=gabarit.id)
        composeur.bf_recipient_group_ids = [(6, 0, self.groupe.ids)]
        composeur._onchange_bf_recipient_groups()
        self.assertEqual(composeur.partner_ids, self.membres)

        composeur.template_id = False
        self.assertEqual(composeur.partner_ids, self.membres,
                         "retirer le gabarit a effacé les membres du groupe")

    def test_c_aucune_fiche_groupe_ne_survit_a_lenvoi(self):
        """Le filet contre un appel RPC, qui ne passe par aucun onchange.

        La fiche du groupe ne porte pas d'adresse : partie telle quelle, le
        courriel n'aurait atteint personne, sans erreur ni trace.
        """
        coquille = self._coquille(self.alice)
        composeur = self._composeur(self.alice, cible=coquille)
        composeur.partner_ids = [(6, 0, self.groupe.proxy_ids.ids)]
        with self.mock_mail_gateway():
            composeur._action_send_mail()
        self.assertNotIn(self.groupe.proxy_ids, composeur.partner_ids)

        message = coquille.message_ids.sorted("id")[-1]
        self.assertNotIn(self.groupe.proxy_ids, message.partner_ids,
                         "la fiche du groupe figure parmi les destinataires")
        for membre in self.membres:
            self.assertIn(membre, message.partner_ids,
                          "%s n'est pas destinataire" % membre.email)
        courriels = self.env["mail.mail"].search(
            [("mail_message_id", "=", message.id)])
        self.assertTrue(courriels, "aucun courriel n'a été préparé")
        vises = courriels.mapped("recipient_ids")
        self.assertNotIn(self.groupe.proxy_ids, vises)
        for membre in self.membres:
            self.assertIn(membre, vises)

    def test_c_le_client_web_deplie_sans_javascript(self):
        """Le chemin réel du navigateur, rejoué par `Form`.

        `Form` applique les onchanges exactement comme le client web. Si le
        dépliage passe ici, il passe à l'écran : c'est ce qui a permis de ne
        PAS écrire de widget OWL. Le contrôle `checkEmails` de
        `many2many_tags_email`, qui éjecte toute fiche sans adresse, ne tourne
        qu'après le retour de l'onchange, donc il ne voit jamais la
        fiche-groupe.
        """
        coquille = self._coquille(self.alice)
        formulaire = Form(
            self.env["mail.compose.message"].with_user(self.alice).with_context(
                default_model="bf.email",
                default_res_ids=[coquille.id],
                default_composition_mode="comment"))
        formulaire.subject = "Essai par formulaire"
        formulaire.partner_ids.add(self.groupe.proxy_ids)
        composeur = formulaire.save()
        self.assertEqual(composeur.partner_ids, self.membres)
        self.assertEqual(composeur.bf_recipient_group_ids, self.groupe)

    def test_c_au_dela_du_seuil_le_composeur_avertit(self):
        """Un avertissement, pas un refus : la liste vient d'apparaître."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.recipient_group_confirm_above", "2")
        composeur = self._composeur(self.alice)
        composeur.bf_recipient_group_ids = [(6, 0, self.groupe.ids)]
        resultat = composeur._onchange_bf_recipient_groups()
        self.assertIn("warning", resultat or {})

    def test_c_sous_le_seuil_le_composeur_se_tait(self):
        composeur = self._composeur(self.alice)
        composeur.bf_recipient_group_ids = [(6, 0, self.groupe.ids)]
        self.assertFalse(composeur._onchange_bf_recipient_groups())

    def test_c_lavertissement_ne_depend_pas_du_moment_du_depliage(self):
        """⚠️ Piège mesuré : compter les AJOUTS rendait toujours zéro.

        Lire `partner_ids` déclenche son calcul, donc `_bf_restore_groups`,
        donc le dépliage a déjà eu lieu au moment de mesurer. L'avertissement
        se règle sur le TOTAL, pas sur la différence.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.recipient_group_confirm_above", "2")
        composeur = self._composeur(self.alice)
        composeur.bf_recipient_group_ids = [(6, 0, self.groupe.ids)]
        composeur._bf_expand_recipient_groups()   # dépliage déjà fait
        self.assertIn("warning", composeur._onchange_bf_recipient_groups() or {},
                      "l'avertissement disparaît quand le dépliage a précédé")

    def test_c_sans_groupe_aucun_avertissement_meme_a_dix_destinataires(self):
        """Le seuil ne parle que des groupes : dix adresses tapées à la main
        sont un choix explicite, pas une surprise."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.recipient_group_confirm_above", "1")
        composeur = self._composeur(self.alice)
        composeur.partner_ids = [(6, 0, self.membres.ids)]
        self.assertFalse(composeur._onchange_bf_recipient_groups())

    def test_c_le_depliage_ne_touche_pas_un_composeur_sans_groupe(self):
        composeur = self._composeur(self.alice)
        composeur.partner_ids = [(6, 0, self.membres.ids)]
        composeur._bf_expand_recipient_groups()
        self.assertEqual(composeur.partner_ids, self.membres)
        self.assertFalse(composeur.bf_recipient_group_ids)


@tagged("post_install", "-at_install")
class TestGroupePortee(RecipientGroupCase):
    """Personnels, partageables. Deux règles, et surtout pas `[(1,'=',1)]`."""

    def test_d_groupe_personnel_invisible_des_autres(self):
        vu = self.env["bf.recipient.group"].with_user(self.bob).search(
            [("id", "=", self.groupe.id)])
        self.assertFalse(vu, "Bob voit un groupe personnel qui n'est pas le sien")

    def test_d_groupe_partage_est_visible(self):
        self.groupe.is_shared = True
        vu = self.env["bf.recipient.group"].with_user(self.bob).search(
            [("id", "=", self.groupe.id)])
        self.assertEqual(vu, self.groupe)

    def test_d_groupe_partage_reste_non_modifiable(self):
        self.groupe.is_shared = True
        with self.assertRaises(AccessError):
            self.groupe.with_user(self.bob).write({"name": "Détourné"})

    def test_d_le_filtre_sevalue_avec_les_droits_du_lecteur(self):
        """Un groupe partagé n'est pas un passe-droit sur le carnet.

        La résolution passe par l'ORM avec les droits de l'usager courant, et
        non en `sudo` : ce qu'on ne voit pas dans Contacts, on ne peut pas lui
        écrire par la bande.
        """
        cache = self.env["res.partner"].create({
            "name": "Contact caché", "email": "cache@test.invalid",
            "comment": "MARQUEUR25278"})
        self.env["ir.rule"].create({
            "name": "Essai 25278 : cacher un contact",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain_force": "[('id', '!=', %d)]" % cache.id,
            "groups": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self.groupe.write({
            "is_shared": True,
            "partner_ids": [(6, 0, [])],
            "filter_domain": "[('comment', 'ilike', 'MARQUEUR25278')]",
        })
        self.assertNotIn(
            cache, self.groupe.with_user(self.bob)._resolve_partners())


@tagged("post_install", "-at_install")
class TestGroupeFiche(RecipientGroupCase):
    """La fiche contact du groupe : utile dans « À », invisible ailleurs."""

    def test_e_la_fiche_existe_et_na_jamais_dadresse(self):
        fiche = self.groupe.proxy_ids
        self.assertEqual(len(fiche), 1)
        self.assertEqual(fiche.name, "Équipe de test")
        self.assertFalse(fiche.email,
                         "la fiche du groupe porte une adresse : un envoi non "
                         "déplié partirait vers elle")

    def test_e_la_fiche_est_masquee_de_la_recherche_par_nom(self):
        Partner = self.env["res.partner"].with_user(self.alice)
        self.assertFalse(Partner.name_search("Équipe de test"))
        avec_temoin = Partner.with_context(
            bf_show_recipient_groups=True).name_search("Équipe de test")
        self.assertEqual([ligne[0] for ligne in avec_temoin],
                         self.groupe.proxy_ids.ids)

    def test_e_la_fiche_ne_polluerait_pas_un_autre_selecteur(self):
        """Le vrai coût qu'on évite : un carnet de dizaines de milliers de fiches."""
        trouve = self.env["res.partner"].with_user(self.alice).name_search(
            "Équipe")
        self.assertNotIn(self.groupe.proxy_ids.id,
                         [ligne[0] for ligne in trouve])

    def test_e_renommer_le_groupe_renomme_la_fiche(self):
        self.groupe.name = "Équipe renommée"
        self.assertEqual(self.groupe.proxy_ids.name, "Équipe renommée")

    def test_e_archiver_puis_reactiver_ne_cree_pas_une_seconde_fiche(self):
        """⚠️ `proxy_ids` ne voit pas les fiches archivées.

        Sans `active_test=False` dans la synchronisation, la réactivation
        créait une DEUXIÈME fiche du même nom dans le carnet.
        """
        self.groupe.active = False
        self.groupe.active = True
        fiches = self.env["res.partner"].with_context(
            active_test=False).search(
                [("bf_recipient_group_id", "=", self.groupe.id)])
        self.assertEqual(len(fiches), 1)
        self.assertTrue(fiches.active)

    def test_e_supprimer_le_groupe_supprime_la_fiche(self):
        fiche = self.groupe.proxy_ids
        self.groupe.unlink()
        self.assertFalse(fiche.exists())

    def test_e_un_groupe_ne_peut_pas_contenir_un_groupe(self):
        autre = self.env["bf.recipient.group"].with_user(self.alice).create(
            {"name": "Groupe hôte"})
        with self.assertRaises(ValidationError):
            autre.partner_ids = [(6, 0, self.groupe.proxy_ids.ids)]

    def test_e_un_filtre_illisible_est_refuse_a_la_saisie(self):
        with self.assertRaises(ValidationError):
            self.groupe.filter_domain = "ceci n'est pas un domaine"


@tagged("post_install", "-at_install")
class TestGroupeMobile(RecipientGroupCase):
    """L'API mobile : rien ne change pour le client 2.37."""

    def test_f_les_groupes_sont_absents_par_defaut(self):
        rendu = self.env["bf.email"].with_user(self.alice).mobile_search_contacts(
            "Équipe de test")
        self.assertFalse(rendu["contacts"],
                         "le client 2.37 recevrait une entrée sans adresse")

    def test_f_include_groups_rend_le_groupe_et_ses_membres(self):
        rendu = self.env["bf.email"].with_user(self.alice).mobile_search_contacts(
            "Équipe de test", include_groups=True)
        self.assertEqual(len(rendu["contacts"]), 1)
        groupe = rendu["contacts"][0]
        self.assertTrue(groupe["is_group"])
        self.assertEqual(groupe["field"], "to")
        self.assertEqual(
            sorted(membre["email"] for membre in groupe["members"]),
            sorted(self.membres.mapped("email")))

    def test_f_un_groupe_trop_gros_nest_pas_rendu(self):
        """Amputée en silence, une liste est pire qu'absente."""
        self.env["res.partner"].create([{
            "name": "Foule %d" % i, "email": "foulem%d@test.invalid" % i,
            "comment": "FOULEMOB",
        } for i in range(60)])
        self.groupe.filter_domain = "[('comment', 'ilike', 'FOULEMOB')]"
        rendu = self.env["bf.email"].with_user(self.alice).mobile_search_contacts(
            "Équipe de test", include_groups=True)
        self.assertFalse(rendu["contacts"])


@tagged("post_install", "-at_install")
class TestGroupeInterrupteur(RecipientGroupCase):
    """La fonction arrive dormante, et dormante veut dire inerte partout.

    Elle est déployée en production avant d'avoir été vue à l'écran : entre le
    déploiement et le feu vert, aucun chemin ne doit pouvoir écrire à un
    groupe. Ce n'est pas de la prudence de principe, c'est la condition qui a
    permis de déployer tout de suite.
    """

    def _eteindre(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.recipient_group_enabled", "0")

    def test_g_eteint_le_composeur_refuse_le_groupe(self):
        self._eteindre()
        composeur = self._composeur(self.alice)
        composeur.bf_recipient_group_ids = [(6, 0, self.groupe.ids)]
        with self.assertRaises(UserError):
            composeur._bf_expand_recipient_groups()

    def test_g_eteint_une_fiche_groupe_dans_to_est_refusee(self):
        """Le chemin RPC, celui qui ne passe par aucun écran."""
        self._eteindre()
        composeur = self._composeur(
            self.alice, cible=self._coquille(self.alice))
        composeur.partner_ids = [(6, 0, self.groupe.proxy_ids.ids)]
        with self.assertRaises(UserError):
            composeur._action_send_mail()

    def test_g_eteint_la_fiche_reste_introuvable(self):
        """Même avec le témoin du composeur : on ne peut pas la taper."""
        self._eteindre()
        trouve = self.env["res.partner"].with_user(self.alice).with_context(
            bf_show_recipient_groups=True).name_search("Équipe de test")
        self.assertFalse(trouve)

    def test_g_eteint_le_mobile_ne_rend_rien(self):
        self._eteindre()
        rendu = self.env["bf.email"].with_user(self.alice).mobile_search_contacts(
            "Équipe de test", include_groups=True)
        self.assertFalse(rendu["contacts"])

    def test_g_eteint_on_peut_quand_meme_preparer_ses_groupes(self):
        """Éteint n'est pas absent : on prépare, on regarde, puis on allume."""
        self._eteindre()
        groupe = self.env["bf.recipient.group"].with_user(self.alice).create(
            {"name": "Préparé avant le feu vert",
             "partner_ids": [(6, 0, self.membres.ids)]})
        self.assertEqual(groupe.member_count, 3)

    def test_g_le_parametre_absent_vaut_EN_SERVICE(self):
        """⚠️ C'est le fichier de données qui éteint, pas le code.

        Un paramètre effacé à la main ne doit pas éteindre une fonction déjà
        en service sur un locataire.
        """
        self.env["ir.config_parameter"].sudo().search(
            [("key", "=", "bf_email_management.recipient_group_enabled")]).unlink()
        self.assertTrue(self.env["bf.recipient.group"]._groups_enabled())

    def test_g_rallume_tout_refonctionne(self):
        self._eteindre()
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.recipient_group_enabled", "1")
        composeur = self._composeur(self.alice)
        composeur.bf_recipient_group_ids = [(6, 0, self.groupe.ids)]
        composeur._bf_expand_recipient_groups()
        self.assertEqual(composeur.partner_ids, self.membres)
