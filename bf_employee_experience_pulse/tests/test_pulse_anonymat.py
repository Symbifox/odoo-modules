"""Les essais qui prouvent la promesse. Si l'un d'eux tombe, le module ment."""

from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from .common import PulseCase

VERS_UNE_PERSONNE = ("res.users", "res.partner", "hr.employee")


@tagged("post_install", "-at_install")
class TestPulseAnonymat(PulseCase):

    def test_la_reponse_ne_porte_aucun_chemin_vers_une_personne(self):
        """Parcourt les champs DÉCLARÉS, pas le fichier source.

        Un champ ajouté plus tard par un pont ou par une surcharge tombe dans
        ce filet, alors qu'une relecture du fichier ne l'aurait pas vu.
        """
        modele = self.env["bf.ex.pulse.answer"]
        fautifs = [
            nom for nom, champ in modele._fields.items()
            if champ.relational and champ.comodel_name in VERS_UNE_PERSONNE
        ]
        self.assertFalse(
            fautifs,
            "bf.ex.pulse.answer porte un chemin vers une personne : %s"
            % fautifs,
        )

    def test_la_reponse_ne_porte_pas_les_colonnes_automatiques(self):
        """`_log_access = False` : sans ça, l'heure trahit le répondant."""
        for nom_modele in ("bf.ex.pulse.answer", "bf.ex.pulse.invitation",
                           "bf.ex.pulse.staging"):
            modele = self.env[nom_modele]
            self.assertFalse(
                modele._log_access,
                "%s garde les colonnes automatiques d'Odoo" % nom_modele,
            )
            for colonne in ("create_date", "create_uid", "write_date",
                            "write_uid"):
                self.assertNotIn(
                    colonne, modele._fields,
                    "%s porte %s" % (nom_modele, colonne),
                )

    def test_la_colonne_absente_l_est_aussi_en_base(self):
        """Un champ retiré du Python peut survivre en colonne SQL."""
        self.env.cr.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'bf_ex_pulse_answer'
        """)
        colonnes = {row[0] for row in self.env.cr.fetchall()}
        for colonne in ("create_date", "create_uid", "write_date",
                        "write_uid", "employee_id", "partner_id", "user_id"):
            self.assertNotIn(
                colonne, colonnes,
                "la table des réponses porte la colonne %s" % colonne,
            )

    def test_personne_ne_lit_le_registre_des_reponses(self):
        """La seule porte est l'agrégat, qui applique les seuils."""
        administration = self.env["res.users"].create({
            "name": "Administration RH", "login": "rh-pulse-acl",
            "company_ids": [(6, 0, [self.company.id])],
            "company_id": self.company.id,
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("hr.group_hr_manager").id,
            ])],
        })
        Answer = self.env["bf.ex.pulse.answer"].with_user(administration)
        with self.assertRaises(AccessError):
            Answer.search([])
        Staging = self.env["bf.ex.pulse.staging"].with_user(administration)
        with self.assertRaises(AccessError):
            Staging.search([])

    def test_le_versement_casse_l_ordre_d_arrivee(self):
        """Les identifiants ne doivent pas raconter qui a répondu en premier.

        On fait répondre huit personnes dans un ordre connu, puis on vérifie
        que l'ordre des réponses versées n'est pas celui-là. L'essai tolère la
        coïncidence : sur huit lots, l'ordre exact a une chance sur 40 320.
        """
        campaign = self._campaign()
        campaign.action_open()
        invitations = campaign.invitation_ids
        for rang, invitation in enumerate(invitations):
            self._repondre(invitation, note=rang)
        campaign._flush_staged()
        notes = self.env["bf.ex.pulse.answer"].sudo().search([
            ("campaign_id", "=", campaign.id),
            ("question_id", "=", self.question_scale.id),
        ], order="id").mapped("value_scale")
        self.assertEqual(sorted(notes), list(range(len(invitations))))
        self.assertNotEqual(
            notes, list(range(len(invitations))),
            "les réponses ont été versées dans l'ordre d'arrivée",
        )

    def test_rien_ne_se_verse_sous_le_seuil(self):
        """Verser une réponse seule la daterait par son rang."""
        campaign = self._campaign()
        campaign.action_open()
        self._repondre(campaign.invitation_ids[0])
        self._repondre(campaign.invitation_ids[1])
        versees = campaign._flush_staged()
        self.assertEqual(versees, 0)
        self.assertEqual(
            self.env["bf.ex.pulse.answer"].sudo().search_count(
                [("campaign_id", "=", campaign.id)]), 0,
        )
        self.assertEqual(campaign.pending_flush_count, 2)

    def test_le_jeton_disparait_au_versement(self):
        campaign = self._campaign()
        campaign.action_open()
        for invitation in campaign.invitation_ids[:5]:
            self._repondre(invitation)
        campaign._flush_staged()
        self.assertEqual(
            self.env["bf.ex.pulse.staging"].sudo().search_count(
                [("campaign_id", "=", campaign.id)]), 0,
            "le sas garde des lignes après le versement",
        )
        self.assertNotIn(
            "token", self.env["bf.ex.pulse.answer"]._fields,
            "la réponse versée garde son jeton",
        )

    def test_le_garde_des_colonnes_de_journal_passe_sur_une_table_propre(self):
        """Le garde tourne à chaque mise à jour : il doit être inoffensif.

        ⚠️ Cet essai NE pose PAS de colonne pour voir le garde la retirer.
        Mesuré à l'essai : un `ALTER TABLE` posé dans un essai survit au
        démontage, et ni un `ROLLBACK TO SAVEPOINT` ni un `DROP` en nettoyage
        ne le reprennent. La base d'essai gardait la colonne, le garde la
        retirait à la passe SUIVANTE, et cet avertissement ressemblait à un
        défaut du module sans en être un. Un essai qui salit le schéma fait
        mentir la passe d'après.

        La preuve que le garde travaille vraiment a été faite autrement, et en
        vrai : la passe de mutation a posé `_log_access = True`, Odoo a créé
        les quatre colonnes, et la remise en état les a laissées derrière
        (Odoo ne fait jamais tomber une colonne). Le garde en a retiré huit,
        sur deux tables, au `-u` suivant.
        """
        for nom in ("bf.ex.pulse.answer", "bf.ex.pulse.invitation",
                    "bf.ex.pulse.staging"):
            modele = self.env[nom]
            modele._bf_pulse_drop_log_columns()
            self.env.cr.execute("""
                SELECT column_name FROM information_schema.columns
                 WHERE table_name = %s
                   AND column_name IN ('create_uid', 'create_date',
                                       'write_uid', 'write_date')
            """, (modele._table,))
            self.assertFalse(self.env.cr.fetchall(), nom)

    def test_le_garde_retire_vraiment_une_colonne(self):
        """Le ménage éprouvé sur une table à nous, pas sur celle du module.

        🔴 Deux trous de mutation ont mené ici. Une version antérieure de cet
        essai lisait le SOURCE de `init()` pour y chercher le nom de la
        méthode : `if False: self._bf_pulse_drop_log_columns()` passait le
        contrôle sans rien faire. Une autre ne vérifiait jamais qu'une colonne
        tombe vraiment : remplacer le `ALTER TABLE` par un `SELECT 1` restait
        vert.

        ⚠️ La table est à nous parce qu'un `ALTER TABLE` posé sur la vraie
        survit au démontage de l'essai, et la passe suivante se met à signaler
        un résidu qui ressemble à un défaut du module.
        """
        table = "bf_ex_pulse_table_d_essai"
        cr = self.env.cr
        cr.execute('DROP TABLE IF EXISTS "%s"' % table)
        self.addCleanup(cr.execute, 'DROP TABLE IF EXISTS "%s"' % table)
        cr.execute(
            'CREATE TABLE "%s" (id SERIAL PRIMARY KEY, create_uid INTEGER, '
            'create_date TIMESTAMP, garde_moi INTEGER)' % table)

        retirees = self.env["bf.ex.pulse.answer"]._bf_pulse_drop_log_columns(
            table=table)
        self.assertEqual(sorted(retirees), ["create_date", "create_uid"])

        cr.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name = %s ORDER BY column_name
        """, (table,))
        restantes = [row[0] for row in cr.fetchall()]
        self.assertEqual(
            restantes, ["garde_moi", "id"],
            "le ménage n'a pas fait tomber les colonnes de journal",
        )

    def test_le_garde_est_branche_sur_init(self):
        """🔴 Trou trouvé par mutation : un garde débranché passe inaperçu.

        Aucun essai ne vérifiait que `init()` appelle le ménage. En remplaçant
        cet appel par `pass`, les 45 essais restaient verts et le garde ne
        tournait plus jamais.

        Le contrôle est fait par OBSERVATION et non en lisant du texte : on
        pose une colonne sur une table à nous, on appelle `init()` en lui
        faisant regarder cette table, et on vérifie qu'elle est tombée. Un
        `if False:` autour de l'appel ne trompe pas ce contrôle-là.
        """
        table = "bf_ex_pulse_table_init"
        cr = self.env.cr
        cr.execute('DROP TABLE IF EXISTS "%s"' % table)
        self.addCleanup(cr.execute, 'DROP TABLE IF EXISTS "%s"' % table)
        cr.execute(
            'CREATE TABLE "%s" (id SERIAL PRIMARY KEY, write_uid INTEGER)'
            % table)

        for nom in ("bf.ex.pulse.answer", "bf.ex.pulse.invitation",
                    "bf.ex.pulse.staging"):
            modele = self.env[nom].with_context(bf_pulse_table_d_essai=table)
            modele.init()

        cr.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name = %s AND column_name = 'write_uid'
        """, (table,))
        self.assertFalse(
            cr.fetchall(),
            "init() n'a pas fait tomber la colonne de journal : le garde est "
            "débranché",
        )

    def test_le_garde_refuse_un_modele_qui_garde_son_journal(self):
        """Mêler le garde sans retirer le drapeau est une contradiction.

        La méthode est appelée sur un modèle qui a légitimement gardé son
        journal, plutôt qu'en retournant le drapeau d'un des nôtres : on ne
        touche pas à la classe de registre.
        """
        from odoo.addons.bf_employee_experience_pulse.models import (
            pulse_sans_journal,
        )
        avec_journal = self.env["bf.ex.pulse.campaign"]
        self.assertTrue(avec_journal._log_access)
        with self.assertRaises(ValueError):
            pulse_sans_journal.SansJournal._bf_pulse_drop_log_columns(
                avec_journal)

    def test_les_trois_modeles_de_collecte_portent_le_garde(self):
        """Un quatrième modèle sans journal doit hériter du même ménage."""
        for nom in ("bf.ex.pulse.answer", "bf.ex.pulse.invitation",
                    "bf.ex.pulse.staging"):
            self.assertIn(
                "bf.ex.pulse.sans.journal", self.env[nom]._inherit,
                "%s ne porte pas le garde des colonnes de journal" % nom,
            )
