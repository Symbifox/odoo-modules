"""Une invitation qui ne part pas doit le dire.

Sur cette lignée, le rendu du gabarit est en sudo depuis la
18.0.2.3.2 : le cours non publié ne fait plus tomber le rendu. Ce qui restait,
et que ces essais tiennent :

- ``reminder_count`` et ``last_reminder_date`` s'écrivaient AVANT la garde
  ``if gabarit and rec.partner_id.email`` : le compteur montait et la date
  mentait même quand rien ne partait ;
- l'``except Exception`` ne faisait qu'un ``_logger.warning`` : le fil de
  l'assignation restait vide, et personne n'apprenait que l'invitation était
  restée en chemin ;
- la relance se planifiait quand même, avec le résumé « relancer la personne
  au sujet de la formation » alors qu'elle n'avait rien reçu ;
- et pour un cours **non publié**, le courriel qui partait désormais menait à un
  mur : la règle du cœur de l'eLearning
  (``website_slides.rule_slide_channel_visibility_signed_in_user``) exige
  ``website_published`` pour TOUT compte portail, membre inscrit ou pas.

Trois cours, parce que trois états différents comptent :

===================  ==============  ================  ========================
cours                publié          visibilité        ce qui doit arriver
===================  ==============  ================  ========================
``prepa``            non             connected         le clic est REFUSÉ
``membres``          oui             members           l'invitation PART, et
                                                       l'Opérateur ne lit pas
                                                       le cours (le rendu en
                                                       sudo gagne sa place)
``ouvert``           oui             connected         chemin heureux
===================  ==============  ================  ========================

⚠️ ``assertRaises`` d'Odoo défait ce que le bloc a écrit : les essais d'état et
les essais de levée sont séparés ici, jamais mélangés.
"""
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.mail.models.mail_template import MailTemplate


@tagged("post_install", "-at_install")
class TestInvitationMuette(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.write({"email": "securite.essai@exemple.test"})
        Cours = cls.env["slide.channel"]
        commun = {"channel_type": "training", "enroll": "invite"}
        # Non publié : l'état courant pendant la préparation d'une campagne.
        cls.prepa = Cours.create(dict(
            commun, name="Hameçonnage (en préparation)", visibility="connected"))
        cls.prepa.is_published = False
        # Publié mais réservé aux inscrits : l'Opérateur ne le lit pas non plus,
        # et là l'invitation DOIT partir.
        cls.membres = Cours.create(dict(
            commun, name="Hameçonnage (inscrits)", visibility="members"))
        cls.membres.is_published = True
        cls.ouvert = Cours.create(dict(
            commun, name="Hameçonnage (ouvert)", visibility="connected"))
        cls.ouvert.is_published = True
        cls.operateur = new_test_user(
            cls.env, login="operateur_t27371", name="Opératrice Sécurité",
            email="operatrice.essai@exemple.test", notification_type="email",
            groups="base.group_user,bf_security_awareness.group_bf_secaware_user")
        cls.avec = cls.env["res.partner"].create({
            "name": "Alice Avec", "email": "alice.avec.essai@exemple.test"})
        cls.sans = cls.env["res.partner"].create({"name": "Bruno Sans"})

    # -- outils ---------------------------------------------------------------
    def _assignation(self, partenaire, cours=None):
        return self.env["bf.training.assignment"].create({
            "partner_id": partenaire.id,
            "channel_id": (cours or self.ouvert).id,
            "assigned_by_id": self.operateur.id,
        })

    def _inviter(self, assignation):
        """Le clic de l'opérateur. Rend les courriels nés de CE clic.

        🔴 `invalidate_all()` avant le clic, et ce n'est pas du zèle : `create`
        et le `search` d'un essai lisent le cours en sudo, et une valeur déjà au
        cache est servie **sans aucun contrôle de règle d'enregistrement** (le
        cache est indexé par enregistrement, champ et langue — jamais par
        l'usager). Sans ce vidage, un essai mesure son propre cache et non un
        droit : le mutant qui retire le `sudo()` du rendu SURVIT (mesuré sur
        l'autre lignée du module, mutant 8).
        """
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        self.env.invalidate_all()
        assignation.with_user(self.operateur).action_send_invite()
        return self.env["mail.mail"].search([
            ("id", ">", dernier),
            ("model", "=", "bf.training.assignment"),
            ("res_id", "=", assignation.id),
        ])

    def _refus_de(self, assignation, gabarit):
        """La phrase du refus, lue par la méthode elle-même."""
        return assignation.with_user(self.operateur)._refus_invitation(gabarit)

    def _fil(self, assignation):
        return "\n".join(assignation.message_ids.mapped(lambda m: m.body or ""))

    def _relances(self, assignation):
        return assignation.activity_ids.filtered(
            lambda a: a.activity_type_id == self.env.ref(
                "mail.mail_activity_data_todo"))

    def _refus(self, assignations):
        """Joue le clic et rend la levée, SANS point de sauvegarde.

        ⚠️ Pas d'`assertRaises` : il enveloppe le bloc d'un point de sauvegarde
        et le DÉFAIT quand l'exception tombe. Tout ce que le code aurait écrit
        avant de lever serait annulé, l'assertion lirait l'état d'AVANT le bloc,
        et l'essai passerait sans rien prouver. Un `try/except` nu garde ce que
        le code a écrit : c'est la seule façon de mesurer « rien n'a été fait ».
        """
        leve = None
        self.env.invalidate_all()
        try:
            assignations.with_user(self.operateur).action_send_invite()
        except UserError as exc:
            leve = exc
        self.env.invalidate_all()
        self.assertIsNotNone(leve, "le clic devait être refusé")
        return leve

    # -- 1. le mécanisme, pour que personne ne retire le sudo par distraction --
    def test_l_operateur_ne_lit_ni_le_cours_en_preparation_ni_celui_des_inscrits(self):
        """La règle du cœur de l'eLearning cache les deux à l'Opérateur."""
        for cours in (self.prepa, self.membres):
            self.env.invalidate_all()
            with self.subTest(cours=cours.name), self.assertRaises(AccessError):
                cours.with_user(self.operateur).read(["name"])

    # -- 2. le refus ----------------------------------------------------------
    def test_un_cours_non_publie_refuse_le_clic(self):
        """⚠️ Rien sur le LIBELLÉ ici : il suit la langue de qui clique, et une
        assertion sur un mot anglais est tombée dès que le catalogue français
        est arrivé. La langue a son propre essai, plus bas."""
        assignation = self._assignation(self.avec, self.prepa)
        with self.assertRaises(UserError):
            self._inviter(assignation)

    def test_le_refus_ne_nomme_pas_le_cours_que_l_operateur_ne_lit_pas(self):
        """Un refus n'est pas un canal de lecture : le nom du cours n'y est pas."""
        assignation = self._assignation(self.avec, self.prepa)
        with self.assertRaises(UserError) as leve:
            self._inviter(assignation)
        self.assertNotIn("préparation", str(leve.exception))

    def test_un_cours_non_publie_ne_compte_aucune_relance(self):
        assignation = self._assignation(self.avec, self.prepa)
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        self._refus(assignation)
        self.assertFalse(self.env["mail.mail"].search([
            ("id", ">", dernier), ("model", "=", "bf.training.assignment"),
            ("res_id", "=", assignation.id)]), "rien ne doit être parti")
        self.assertEqual(assignation.reminder_count, 0)
        self.assertFalse(assignation.last_reminder_date)
        self.assertFalse(self._relances(assignation))

    def test_sans_adresse_le_clic_refuse_et_nomme_la_personne(self):
        assignation = self._assignation(self.sans)
        with self.assertRaises(UserError) as leve:
            self._inviter(assignation)
        self.assertIn("Bruno Sans", str(leve.exception))

    def test_sans_adresse_rien_ne_part_et_rien_ne_se_compte(self):
        assignation = self._assignation(self.sans)
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        self._refus(assignation)
        self.assertFalse(self.env["mail.mail"].search([
            ("id", ">", dernier), ("model", "=", "bf.training.assignment"),
            ("res_id", "=", assignation.id)]), "rien ne doit être parti")
        self.assertEqual(assignation.reminder_count, 0)
        self.assertFalse(assignation.last_reminder_date)
        self.assertFalse(self._relances(assignation))

    def test_gabarit_absent_refuse_le_clic(self):
        """Le gabarit supprimé : `env.ref` rend vide, et rien ne partait."""
        self.env.ref("bf_security_awareness.mail_template_training_invite").unlink()
        assignation = self._assignation(self.avec)
        self._refus(assignation)
        self.assertEqual(assignation.reminder_count, 0)

    def test_un_refus_n_emporte_pas_l_invitation_du_meme_lot(self):
        """🔴 Le refus se lève AVANT le premier envoi, jamais en chemin."""
        bonne = self._assignation(self.avec)
        mauvaise = self._assignation(self.sans)
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        self._refus(bonne | mauvaise)
        self.assertFalse(self.env["mail.mail"].search([
            ("id", ">", dernier), ("model", "=", "bf.training.assignment"),
            ("res_id", "in", (bonne | mauvaise).ids)]),
            "aucune invitation du lot ne doit être partie avant le refus")
        self.assertEqual(
            bonne.reminder_count, 0,
            "rien ne doit avoir été compté avant le refus")

    # -- 3. l'échec imprévu s'écrit au fil ------------------------------------
    def test_un_echec_de_rendu_s_ecrit_au_fil_et_ne_compte_pas(self):
        """🔴 L'`except Exception` avalait tout dans un `_logger.warning`."""
        assignation = self._assignation(self.avec)
        avant = len(assignation.message_ids)

        def casse(*args, **kwargs):
            raise ValueError("rendu cassé pour la mesure")

        with patch.object(MailTemplate, "send_mail", casse):
            self._inviter(assignation)
        self.assertGreater(
            len(assignation.message_ids), avant,
            "un échec d'envoi doit s'écrire au fil de l'assignation")
        self.assertIn("cassé", self._fil(assignation))
        self.assertEqual(
            assignation.reminder_count, 0, "une relance en échec ne se compte pas")
        self.assertFalse(assignation.last_reminder_date)
        self.assertFalse(
            self._relances(assignation),
            "pas de relance à planifier quand l'invitation n'est pas partie")

    def test_un_echec_n_emporte_pas_l_invitation_suivante_du_lot(self):
        casse_ci = self._assignation(self.avec)
        suivante = self._assignation(self.avec, self.membres)
        vrai = MailTemplate.send_mail

        def send_mail(gabarit, res_id, **kw):
            if res_id == casse_ci.id:
                raise ValueError("envoi cassé pour la mesure")
            return vrai(gabarit, res_id, **kw)

        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        with patch.object(MailTemplate, "send_mail", send_mail):
            (casse_ci | suivante).with_user(self.operateur).action_send_invite()
        partis = self.env["mail.mail"].search([
            ("id", ">", dernier), ("model", "=", "bf.training.assignment"),
            ("res_id", "=", suivante.id)])
        self.assertEqual(len(partis), 1, "la suivante du lot part quand même")
        self.assertEqual(suivante.reminder_count, 1)
        self.assertEqual(casse_ci.reminder_count, 0)

    # -- 4. ce que le rendu en sudo gagne, et le chemin heureux ---------------
    def test_un_cours_publie_reserve_aux_inscrits_laisse_partir_l_invitation(self):
        """Le rendu en sudo : l'Opérateur ne lit pas ce cours, l'invitation part."""
        assignation = self._assignation(self.avec, self.membres)
        courriels = self._inviter(assignation)
        self.assertEqual(len(courriels), 1, "l'invitation doit partir")
        self.assertIn(self.avec, courriels.recipient_ids)
        self.assertEqual(assignation.reminder_count, 1)

    def test_le_chemin_heureux_compte_toujours_sa_relance(self):
        assignation = self._assignation(self.avec)
        courriels = self._inviter(assignation)
        self.assertEqual(len(courriels), 1)
        self.assertEqual(assignation.reminder_count, 1)
        self.assertTrue(assignation.last_reminder_date)
        self.assertEqual(len(self._relances(assignation)), 1)

    # -- 5. le bouton voisin -------------------------------------------------
    def test_la_note_d_inscription_nomme_le_cours(self):
        """Ce que la note d'inscription doit porter, et rien de plus.

        ⚠️ Cet essai ne mesure PAS le `sudo()` de cette lecture : mesuré
        dans un processus neuf, cache vidé juste avant le clic, elle
        ne lève rien avec ou sans sudo, parce que `_action_add_members` tourne
        en sudo juste avant et remplit le cache. Ce qui se mesure ici, c'est que
        la note **nomme** à l'opérateur le cours qu'il vient de poser — et
        qu'aucune trace ne remonte à l'écran.
        """
        assignation = self._assignation(self.avec, self.prepa)
        self.env.invalidate_all()
        try:
            assignation.with_user(self.operateur).action_enroll()
        except AccessError as exc:
            self.fail(
                "le bouton « Inscrire » rend une AccessError nue sur un cours "
                f"non publié : {exc}")
        self.assertIn(
            "préparation", self._fil(assignation),
            "la note d'inscription nomme à l'opérateur le cours qu'il vient de poser")

    # -- 5bis. les deux cours peuvent diverger ------------------------------
    def test_un_cours_de_lien_non_publie_refuse_aussi(self):
        """🔴 `channel_id` est le cours que le CORPS du
        courriel nomme ; `slide_channel_id` (= `activity_id.slide_channel_id`)
        est celui que le BOUTON emploie. Ils ne sont synchronisés qu'au
        `create` : repointer le cours de l'activité vers une version non
        publiée laisse `channel_id` en arrière, et une garde qui ne lirait que
        `channel_id` laisserait partir une invitation vers un mur.
        """
        assignation = self._assignation(self.avec, self.ouvert)
        self.assertTrue(assignation.activity_id, "le registre adosse l'activité")
        # Le cours de l'activité passe à une version non publiée ; `channel_id`,
        # lui, reste sur le cours publié.
        assignation.activity_id.sudo().slide_channel_id = self.prepa
        assignation.invalidate_recordset()
        self.assertEqual(assignation.channel_id, self.ouvert)
        self.assertEqual(assignation.slide_channel_id, self.prepa)
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        self._refus(assignation)
        self.assertFalse(self.env["mail.mail"].search([
            ("id", ">", dernier), ("model", "=", "bf.training.assignment"),
            ("res_id", "=", assignation.id)]), "rien ne doit être parti")
        self.assertEqual(assignation.reminder_count, 0)

    # -- 6. ce que le refus dit, et dans quelle langue -----------------------
    def test_les_trois_refus_disent_trois_choses_differentes(self):
        """Trois causes, trois phrases : un message interverti se verrait ici."""
        gabarit = self.env.ref("bf_security_awareness.mail_template_training_invite")
        cours = self._refus_de(self._assignation(self.avec, self.prepa), gabarit)
        adresse = self._refus_de(self._assignation(self.sans), gabarit)
        gabarit.unlink()
        absent = self._refus_de(
            self._assignation(self.avec, self.membres),
            self.env.ref("bf_security_awareness.mail_template_training_invite",
                         raise_if_not_found=False))
        phrases = [cours, adresse, absent]
        self.assertTrue(all(phrases), phrases)
        self.assertEqual(len(set(phrases)), 3, phrases)

    def test_le_refus_suit_la_langue_de_qui_clique(self):
        """🔴 Trouvé au parcours au navigateur : le refus sortait en anglais
        devant un opérateur en fr_CA, parce que les chaînes neuves n'étaient pas
        au catalogue. Un essai de modèle ne l'avait pas vu."""
        # ⚠️ Les DEUX langues sont activées : `reader_lang` ne retient qu'une
        # langue INSTALLÉE, et sur une base réelle `en_US` ne l'est souvent
        # pas — le repli tombait alors sur la société, en français, et l'essai
        # lisait du français là où il attendait la source.
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        gabarit = self.env.ref("bf_security_awareness.mail_template_training_invite")
        assignation = self._assignation(self.avec, self.prepa)
        self.operateur.lang = "fr_CA"
        self.assertIn("publié", self._refus_de(assignation, gabarit))
        self.operateur.lang = "en_US"
        self.assertIn("published", self._refus_de(assignation, gabarit).lower())

    # -- 7. ce que le sudo du rendu protège, mesuré directement -------------
    def test_le_rendu_du_gabarit_exige_le_sudo(self):
        """Le gabarit lit le cours : sous l'Opérateur il tombe, en sudo il passe.

        C'est la décision que `action_send_invite` applique en rendant le
        gabarit `.sudo()`. Mesurée ici sur le rendu lui-même, parce que par le
        bouton elle n'est pas observable : la garde de publication lit le cours
        en sudo juste avant, et même bornée elle laisse `website_published` au
        cache.
        """
        assignation = self._assignation(self.avec, self.membres)
        gabarit = self.env.ref("bf_security_awareness.mail_template_training_invite")
        self.env.invalidate_all()
        # ⚠️ Deux couches d'enveloppe, mesurées : QWeb enveloppe l'`AccessError`
        # dans une `QWebException`, et `mail_render_mixin._render_template_qweb`
        # réenveloppe celle-là dans un `UserError`. C'est donc un `UserError`
        # qui sort — et c'est aussi pourquoi un échec de rendu arrive dans
        # l'`except` d'`action_send_invite` comme une erreur d'usager.
        with self.assertRaises(UserError) as leve:
            gabarit.with_user(self.operateur)._render_field(
                "body_html", assignation.ids)
        self.assertIn("slide.channel", str(leve.exception))
        self.env.invalidate_all()
        rendu = gabarit.sudo()._render_field("body_html", assignation.ids)
        self.assertTrue(rendu.get(assignation.id))

    # -- 8. une erreur de BASE n'emporte plus le lot ------------------------
    def test_une_erreur_de_base_laisse_le_fil_et_le_lot_intacts(self):
        """🔴 Une erreur venue de la BASE abandonne la
        transaction, et tout ordre suivant échoue en « current transaction is
        aborted ». Sans point de sauvegarde, le `message_post` du gestionnaire
        lèverait depuis l'intérieur du `except` : trace à l'écran, lot emporté.
        """
        casse_ci = self._assignation(self.avec)
        suivante = self._assignation(self.avec, self.membres)
        vrai = MailTemplate.send_mail

        def send_mail(gabarit, res_id, **kw):
            if res_id == casse_ci.id:
                gabarit.env.cr.execute("SELECT 1/0")
            return vrai(gabarit, res_id, **kw)

        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        with patch.object(MailTemplate, "send_mail", send_mail):
            (casse_ci | suivante).with_user(self.operateur).action_send_invite()
        # La note porte le message de la base elle-même : c'est la preuve que
        # le point de sauvegarde a rendu le curseur utilisable AVANT de l'écrire.
        self.assertIn("division by zero", self._fil(casse_ci).lower())
        self.assertEqual(casse_ci.reminder_count, 0)
        partis = self.env["mail.mail"].search([
            ("id", ">", dernier), ("model", "=", "bf.training.assignment"),
            ("res_id", "=", suivante.id)])
        self.assertEqual(len(partis), 1, "la suivante du lot part quand même")
        self.assertEqual(suivante.reminder_count, 1)
