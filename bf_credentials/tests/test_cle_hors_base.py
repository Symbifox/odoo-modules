"""La clé de chiffrement ne vit plus dans la base, et le clair ne s'écrit plus.

Deux défauts se couvraient l'un l'autre. La clé Fernet dormait
dans ``ir.config_parameter``, donc dans le même ``pg_dump`` que les secrets
qu'elle protège. Et quand le chiffrement échouait, l'ancien code rangeait la
valeur NUE avec un simple avertissement au journal : une base pouvait se
remplir de clair sans une seule erreur, et rien après coup ne distinguait une
valeur chiffrée d'une valeur qui ne l'avait jamais été.

Ces essais tiennent les deux bouts : où la clé a le droit d'être, et ce qui
arrive quand elle manque. Aucun n'écrit ni ne journalise un secret.
"""

import os
from contextlib import contextmanager
from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user
from odoo.tools import config


class TestCleHorsBase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Credential = cls.env['project.credential']
        cls.projet = cls.env['project.project'].create({'name': 'Projet clé hors base'})
        cls.type_identifiant = cls.env['project.credential.type'].create({
            'name': "Type d'essai clé hors base", 'code': 'TCLEHB',
        })
        cls.cle_a = Fernet.generate_key().decode()
        cls.cle_b = Fernet.generate_key().decode()

    # ------------------------------------------------------------------
    # Outils : poser les clés là où le code les cherche, et nulle part ailleurs
    # ------------------------------------------------------------------

    @contextmanager
    def _cles(self, env=None, conf=None, param=None):
        """Pose exactement les clés demandées, et RETIRE les autres.

        Sans le retrait, un essai passerait au vert grâce à une clé laissée par
        l'environnement réel du banc, et ne prouverait rien.
        """
        environnement = dict(os.environ)
        environnement.pop(self.Credential._CLE_ENV, None)
        if env:
            environnement[self.Credential._CLE_ENV] = env

        options = dict(config.options)
        options.pop(self.Credential._CLE_CONF, None)
        if conf:
            options[self.Credential._CLE_CONF] = conf

        ICP = self.env['ir.config_parameter'].sudo()
        nom_param = self.Credential._CLE_PARAM_HERITE
        ancien = ICP.get_param(nom_param)
        if param:
            ICP.set_param(nom_param, param)
        else:
            ICP.search([('key', '=', nom_param)]).unlink()

        try:
            with patch.dict(os.environ, environnement, clear=True), \
                 patch.dict(config.options, options, clear=True):
                yield
        finally:
            ICP.search([('key', '=', nom_param)]).unlink()
            if ancien:
                ICP.set_param(nom_param, ancien)

    def _creer(self, **kwargs):
        valeurs = {
            'name': 'Identifiant clé hors base',
            'project_id': self.projet.id,
            'type_id': self.type_identifiant.id,
        }
        valeurs.update(kwargs)
        return self.Credential.create(valeurs)

    # ------------------------------------------------------------------
    # Où la clé a le droit d'être
    # ------------------------------------------------------------------

    def test_la_cle_ne_se_genere_plus_toute_seule(self):
        """Sans clé nulle part, le module lève. Il n'en invente pas une.

        L'ancien code en fabriquait une et la rangeait dans la base au premier
        appel. Une clé qui apparaît toute seule est une clé que personne n'a
        mise à l'abri : le jour d'un sinistre, elle n'existe que dans ce que le
        sinistre a emporté.
        """
        with self._cles():
            self.assertIsNone(self.Credential._get_encryption_key())
            with self.assertRaises(UserError):
                self.Credential._exige_une_cle()
            self.assertFalse(
                self.env['ir.config_parameter'].sudo().get_param(
                    self.Credential._CLE_PARAM_HERITE),
                "Le module a écrit une clé dans la base malgré tout.",
            )

    def test_la_conf_fournit_la_cle(self):
        with self._cles(conf=self.cle_a):
            self.assertEqual(
                self.Credential._get_encryption_key(), self.cle_a.encode())
            chiffre = self.Credential._encrypt_value('secret-conf')
            self.assertEqual(
                self.Credential._decrypt_value(chiffre), 'secret-conf')

    def test_l_environnement_a_priorite_sur_la_conf(self):
        """Une clé passée à l'exécution l'emporte sur le fichier.

        C'est ce qui permet à un banc de tourner avec SA clé sans toucher au
        odoo.conf monté en lecture seule.
        """
        with self._cles(env=self.cle_a, conf=self.cle_b):
            self.assertEqual(
                self.Credential._get_encryption_key(), self.cle_a.encode())

    def test_la_cle_de_la_base_lit_mais_n_ecrit_plus(self):
        """Le paramètre système hérité reste une porte de LECTURE.

        Il faut qu'un dump d'avant la bascule reste lisible. Mais chiffrer du
        neuf avec la clé qui dort dans la base perpétuerait exactement le
        défaut que la bascule ferme, alors l'écriture, elle, est refusée.
        """
        with self._cles(param=self.cle_a):
            ancien = Fernet(self.cle_a.encode()).encrypt(b'secret-herite').decode()
            self.assertEqual(
                self.Credential._decrypt_value(ancien), 'secret-herite')
            with self.assertRaises(UserError):
                self.Credential._encrypt_value('un-secret-neuf')

    def test_la_cle_hors_base_l_emporte_sur_celle_de_la_base(self):
        with self._cles(conf=self.cle_b, param=self.cle_a):
            self.assertEqual(
                self.Credential._get_encryption_key(), self.cle_b.encode())

    # ------------------------------------------------------------------
    # Ce qui arrive quand ça rate
    # ------------------------------------------------------------------

    def test_rien_ne_s_ecrit_en_clair_quand_la_cle_manque(self):
        """Créer un identifiant sans clé lève, et ne range RIEN.

        L'ancien comportement : un avertissement au journal, et le mot de passe
        nu dans la colonne « chiffrée ».
        """
        with self._cles():
            with self.assertRaises(UserError):
                self._creer(password='mot-de-passe-en-clair')
        reste = self.Credential.search([('project_id', '=', self.projet.id)])
        self.assertFalse(
            reste, "Un identifiant a été créé malgré l'absence de clé.")

    def test_une_valeur_illisible_leve_au_lieu_de_se_rendre_telle_quelle(self):
        """La mauvaise clé lève. Avant, elle rendait le jeton chiffré.

        Le piège : à l'écran, « gAAAAA… » s'affichait à la place du mot de
        passe comme si c'était lui, et réenregistrer la fiche chiffrait le
        chiffré une deuxième fois, cette fois pour de bon.
        """
        chiffre_avec_a = Fernet(self.cle_a.encode()).encrypt(b'secret').decode()
        with self._cles(conf=self.cle_b):
            with self.assertRaises(UserError):
                self.Credential._decrypt_value(chiffre_avec_a)

    def test_le_calcul_affiche_une_marque_et_l_inverse_la_refuse(self):
        """Une fiche illisible se voit, sans rendre la liste inouvrable.

        Un calcul qui lève ferme la liste entière, fiches saines comprises. La
        fiche fautive porte donc une marque voyante, et l'enregistrement du
        formulaire ne doit surtout pas réécrire cette marque par-dessus le
        secret.
        """
        with self._cles(conf=self.cle_a):
            cred = self._creer(password='secret-a')
            chiffre_origine = cred.password_encrypted

        with self._cles(conf=self.cle_b):
            cred.invalidate_recordset()
            self.assertEqual(cred.password, cred.MARQUE_ILLISIBLE)
            # Ce que fait l'enregistrement d'un formulaire : réécrire les champs
            # tels qu'ils sont affichés.
            cred.write({'password': cred.MARQUE_ILLISIBLE})
            self.assertEqual(
                cred.password_encrypted, chiffre_origine,
                "La marque d'illisibilité a été chiffrée par-dessus le secret.",
            )

    # ------------------------------------------------------------------
    # Le contrôle qui distingue le chiffré du clair
    # ------------------------------------------------------------------

    def test_la_forme_d_un_jeton_fernet_se_reconnait_sans_cle(self):
        jeton = Fernet(self.cle_a.encode()).encrypt(b'peu importe').decode()
        self.assertTrue(self.Credential.est_un_jeton_fernet(jeton))
        self.assertFalse(self.Credential.est_un_jeton_fernet('motdepasse123'))
        self.assertFalse(self.Credential.est_un_jeton_fernet(''))
        # Du base64 valide qui n'est pas un jeton Fernet : la version 0x80
        # manque. Sans ce contrôle, n'importe quelle chaîne base64 passerait
        # pour du chiffré.
        self.assertFalse(self.Credential.est_un_jeton_fernet('YWJjZGVm'))

    def test_verifier_chiffrement_repere_le_clair_laisse_en_base(self):
        """Le contrôle que la bascule réclamait : y a-t-il déjà du clair ?

        Le clair est posé en SQL, comme l'ancien repli le faisait, sans passer
        par le chiffrement.
        """
        with self._cles(conf=self.cle_a):
            sain = self._creer(password='secret-sain')
            malade = self._creer(name='Identifiant en clair')
            self.env.cr.execute(
                "UPDATE project_credential SET password_encrypted = %s "
                "WHERE id = %s", ('mot-de-passe-nu', malade.id))
            malade.invalidate_recordset()

            # Borné à ce projet : la base du banc porte d'autres fiches,
            # écrites avec une AUTRE clé, que cet essai déclarerait illisibles
            # pour une raison qui ne le regarde pas.
            bilan = self.Credential.verifier_chiffrement(
                [('project_id', '=', self.projet.id)])
            self.assertEqual(bilan['total'], 2)
            self.assertEqual(bilan['chiffres'], 1)
            self.assertEqual(bilan['en_clair'],
                             ['%s.password_encrypted' % malade.id])
            self.assertFalse(bilan['illisibles'])
            self.assertNotIn('%s.password_encrypted' % sain.id, bilan['en_clair'])

    def test_verifier_chiffrement_repere_l_illisible(self):
        """Une fiche écrite avec une autre clé est comptée comme illisible.

        C'est le cas qui distingue ce contrôle d'un simple coup d'œil à la
        forme : le jeton est bien un jeton Fernet, il ne s'ouvre simplement
        pas avec la clé d'ici.
        """
        with self._cles(conf=self.cle_a):
            cred = self._creer(password='secret-a')
        with self._cles(conf=self.cle_b):
            bilan = self.Credential.verifier_chiffrement(
                [('project_id', '=', self.projet.id)])
            self.assertEqual(bilan['illisibles'],
                             ['%s.password_encrypted' % cred.id])
            self.assertFalse(bilan['en_clair'])
            self.assertEqual(bilan['chiffres'], 0)

    def test_verifier_chiffrement_est_reserve_aux_gestionnaires(self):
        """Publique pour le déploiement, donc verrouillée pour le reste.

        Elle ouvre chaque secret pour savoir s'il s'ouvre : c'est un geste de
        gestionnaire, et elle est appelable par RPC.
        """
        simple = new_test_user(
            self.env, login='cred.simple.clehb',
            groups='base.group_user,bf_credentials.group_credential_user',
        )
        with self._cles(conf=self.cle_a):
            with self.assertRaises(AccessError):
                self.Credential.with_user(simple).verifier_chiffrement()
