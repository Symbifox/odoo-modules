"""Une version publiée sans date de publication ne casse plus la fiche.

Le calcul de la version courante triait les versions publiées sur
``release_date`` sans valeur de repli. Une version publiée sans date y faisait
comparer un booléen à une date, l'erreur tombait au vidage de la transaction,
l'écriture était refusée en entier et la fiche du document devenait illisible,
le champ se recalculant à chaque lecture.

Trois gestes à éprouver : le repli au tri, qui répare le passé ; la contrainte,
qui refuse l'avenir ; le rattrapage, qui date ce qui est déjà en base. Le jeu
d'essai sème le défaut par SQL, puisque la contrainte interdit désormais de le
semer par l'ORM, et c'est justement le cas que le repli doit tenir : une
version entrée par un chemin qui ne passe pas les contraintes Python.
"""

from datetime import datetime

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import Form, TransactionCase


class TestVersionSansDate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.type_doc = cls.env['project.document.type'].create({
            'name': "Type d'essai sans date", 'code': 'TSD', 'is_internal': True,
        })
        cls.doc = cls.env['project.document'].create({
            'name': 'Document sans date', 'code': 'TEST-SD',
            'type_id': cls.type_doc.id,
        })

    def _version(self, numero, **vals):
        """Une version en préparation, par l'ORM."""
        return self.env['project.document.version'].create(dict(
            {'document_id': self.doc.id, 'version_number': numero}, **vals))

    def _sql(self, requete, params):
        """Écrit par SQL, hors de l'ORM.

        Le vidage d'abord : une écriture ORM encore en attente serait posée
        APRÈS celle-ci, donc par-dessus (mesuré le 2026-11-08 : la date du
        bouton « Publier » écrasait l'UPDATE qui la remplaçait). Le cache
        ensuite, puisque rien n'a prévenu l'ORM.
        """
        self.env.flush_all()
        self.env.cr.execute(requete, params)
        self.env.invalidate_all()

    def _publier_sans_date(self, version):
        """Pose l'état publié par SQL : la contrainte Python ne le permet plus."""
        self._sql(
            "UPDATE project_document_version SET state = 'released', "
            "release_date = NULL WHERE id = %s", (version.id,))

    def _recalculer(self):
        """Rejoue le calcul qui cassait, sur un cache vide."""
        self.env.invalidate_all()
        self.doc._compute_current_version()
        return self.doc.latest_version_id

    # ------------------------------------------------------------------
    # 1. Le repli au tri
    # ------------------------------------------------------------------

    def test_une_version_datee_melee_a_une_version_sans_date(self):
        """Le cas qui cassait : la fiche se calcule, et la datée l'emporte.

        L'ordre des deux gestes porte tout l'essai. Publier la datée EN DERNIER
        passerait la version sans date à « remplacée » (c'est ce que fait
        ``action_release``), le tri n'aurait plus qu'un élément et ne
        comparerait rien : l'essai passerait avec l'ancien tri brut. La datée
        part donc par le bouton d'abord, et la version sans date est posée par
        SQL ensuite, pour que les deux soient publiées au moment du calcul.
        """
        datee = self._version('2.0', effective_date='2026-09-15')
        datee.action_release()
        sans_date = self._version('1.0', effective_date='2026-08-31')
        self._publier_sans_date(sans_date)
        self.assertEqual(
            len(self.doc.version_ids.filtered(lambda v: v.state == 'released')), 2,
            "les deux versions doivent être publiées, sinon le tri ne compare rien")

        self.assertEqual(self._recalculer(), datee)
        self.assertEqual(self.doc.current_version, '2.0')

    def test_la_version_sans_date_la_plus_recente_l_emporte(self):
        """Le repli descend sur la date d'entrée en vigueur, pas sur rien.

        Une version sans date de publication mais entrée en vigueur APRÈS la
        version datée reste la dernière : le repli ordonne, il ne relègue pas.
        """
        datee = self._version('1.0', effective_date='2026-01-10')
        datee.action_release()
        self._sql(
            "UPDATE project_document_version SET release_date = %s WHERE id = %s",
            ('2026-01-10 12:00:00', datee.id))
        sans_date = self._version('2.0', effective_date='2026-08-31')
        self._publier_sans_date(sans_date)

        self.assertEqual(self._recalculer(), sans_date)
        self.assertEqual(self.doc.current_version, '2.0')

    def test_aucune_version_n_a_de_date(self):
        """Le cas dormant d'un document dont aucune version n'est datée.

        Trois versions sans aucune date : le tri ne compare que des valeurs
        vides, ce qui passait déjà, et doit continuer de passer. À défaut de
        toute date, c'est l'identifiant qui tranche, arbitraire mais stable.
        Le rang de publication, lui, n'entre PAS dans la clé : il vaut 0 pour
        toute version qui n'est pas passée par le bouton, donc justement pour
        toute cette population, et un rang élevé posé sur la plus vieille ne
        doit pas la faire élire.
        """
        versions = [self._version(n) for n in ('1.0', '2.0', '2.1')]
        for version in versions:
            self._publier_sans_date(version)
        self._sql(
            "UPDATE project_document_version SET sequence_index = 7 WHERE id = %s",
            (versions[0].id,))

        self.assertEqual(self._recalculer(), versions[-1])

    def test_la_cle_de_publication_reste_comparable(self):
        """Toutes les clés se comparent entre elles, datées ou non."""
        datee = self._version('1.0', effective_date='2026-01-10')
        datee.action_release()
        vide = self._version('2.0')
        effet = self._version('3.0', effective_date='2026-08-31')

        cles = {v: v._cle_de_publication() for v in (datee, vide, effet)}
        self.assertEqual(
            sorted(cles, key=cles.get), [vide, effet, datee],
            "la vide d'abord, puis celle qui n'a qu'une date d'effet, puis la "
            "datée de ce jour")
        self.assertEqual(cles[vide][0], datetime.min)
        self.assertEqual(
            cles[effet][0], datetime(2026, 8, 31, 5, 0, 0),
            "une date d'effet vaut 05:00 UTC, soit le bon jour à Montréal")
        self.assertEqual(cles[datee][0], datee.release_date)

    # ------------------------------------------------------------------
    # 2. La contrainte
    # ------------------------------------------------------------------

    def test_la_contrainte_refuse_la_creation_publiee_sans_date(self):
        # Le vidage est explicite : une contrainte Python se dit là, pas au
        # `create` (c'est tout le défaut d'origine, qui tombait au vidage).
        with self.assertRaises(ValidationError):
            self._version('1.0', state='released')
            self.env.flush_all()

    def test_la_contrainte_refuse_le_passage_a_publie_sans_date(self):
        version = self._version('1.0')
        with self.assertRaises(ValidationError):
            version.state = 'released'
            self.env.flush_all()

    def test_la_contrainte_laisse_passer_le_bouton_publier(self):
        version = self._version('1.0')
        version.action_release()
        self.assertEqual(version.state, 'released')
        self.assertTrue(version.release_date)

    def test_la_contrainte_laisse_passer_une_creation_datee(self):
        version = self._version(
            '1.0', state='released', release_date='2026-09-15 12:00:00')
        self.assertEqual(version.state, 'released')

    def test_la_contrainte_laisse_remplacer_une_version_sans_date(self):
        """Publier la suivante remplace la précédente : l'état change, pas la date.

        C'est le geste qui réveillait le défaut en production. Il doit passer
        même quand la version remplacée n'a pas de date de publication.
        """
        ancienne = self._version('1.0', effective_date='2026-08-31')
        self._publier_sans_date(ancienne)
        nouvelle = self._version('2.0')
        nouvelle.action_release()

        self.assertEqual(ancienne.state, 'superseded')
        self.assertFalse(ancienne.release_date)
        self.assertEqual(self._recalculer(), nouvelle)

    # ------------------------------------------------------------------
    # 3. Le rattrapage
    # ------------------------------------------------------------------

    def _sans_date_du_document(self):
        return self.env['project.document.version'].search([
            ('document_id', '=', self.doc.id),
            ('state', 'in', ('released', 'superseded')),
            ('release_date', '=', False),
        ])

    def test_le_rattrapage_date_les_publiees_sans_date(self):
        publiee = self._version('2.1', effective_date='2026-08-31')
        self._publier_sans_date(publiee)
        remplacee = self._version('2.0', effective_date='2026-08-31')
        self._publier_sans_date(remplacee)
        self._sql(
            "UPDATE project_document_version SET state = 'superseded' WHERE id = %s",
            (remplacee.id,))
        self.assertEqual(len(self._sans_date_du_document()), 2)

        datees = self.env['project.document.version']._dater_les_publiees_sans_date()

        self.assertIn(publiee, datees)
        self.assertIn(remplacee, datees)
        # 05:00 UTC : la date d'effet au bon jour à Montréal comme à Auckland.
        self.assertEqual(publiee.release_date, datetime(2026, 8, 31, 5, 0, 0))
        self.assertEqual(remplacee.release_date, datetime(2026, 8, 31, 5, 0, 0))
        self.assertEqual(remplacee.state, 'superseded')
        self.assertFalse(self._sans_date_du_document())

    def test_le_rattrapage_rejoue_ne_trouve_plus_rien(self):
        publiee = self._version('2.1', effective_date='2026-08-31')
        self._publier_sans_date(publiee)
        Version = self.env['project.document.version']
        Version._dater_les_publiees_sans_date()

        self.assertNotIn(publiee, Version._dater_les_publiees_sans_date())

    def test_le_rattrapage_sans_date_d_effet_prend_la_creation(self):
        """Sans date d'effet ni approbation ni gel, la création fait foi."""
        publiee = self._version('1.0')
        self._publier_sans_date(publiee)
        creee_le = publiee.create_date

        self.env['project.document.version']._dater_les_publiees_sans_date()

        self.assertEqual(publiee.release_date, creee_le)

    # ------------------------------------------------------------------
    # 4. Les deux chemins trouvés par la relecture adverse
    # ------------------------------------------------------------------

    def _publier_sans_aucune_date(self, version, etat='released'):
        """Une version publiée dont AUCUNE date n'est posée, pas même la création."""
        self._sql(
            "UPDATE project_document_version SET state = %s, release_date = NULL, "
            "approval_date = NULL, frozen_date = NULL, create_date = NULL WHERE id = %s",
            (etat, version.id))

    def test_la_cle_se_compare_sur_des_lignes_encore_neuves(self):
        """Dans un onchange, l'identifiant est un `NewId`, qui ne se compare pas.

        `NewId` définit `__eq__` et `__hash__`, jamais `__lt__`. Une clé qui
        rendrait `self.id` lèverait donc un `TypeError` dès que deux versions
        publiées égalisent sur la date, un chemin que l'ancien tri brut ne
        touchait pas, puisqu'il ne comparait que des dates.
        """
        Version = self.env['project.document.version']
        une = Version.new({
            'document_id': self.doc.id, 'version_number': '1.0',
            'state': 'released', 'release_date': '2026-08-31 05:00:00'})
        deux = Version.new({
            'document_id': self.doc.id, 'version_number': '2.0',
            'state': 'released', 'release_date': '2026-08-31 05:00:00'})

        self.assertEqual(une._cle_de_publication()[1], 0)
        self.assertEqual(
            sorted([une._cle_de_publication(), deux._cle_de_publication()]),
            [une._cle_de_publication(), deux._cle_de_publication()],
            "deux lignes neuves doivent se comparer sans lever")

    def test_le_formulaire_tient_une_version_neuve_a_la_meme_date(self):
        """Le chemin de l'écran : une ligne neuve publiée à la date d'une autre.

        L'onglet « Versions » est éditable en ligne et le formulaire affiche la
        version courante : l'onchange recalcule donc le tri à chaque frappe,
        sur des lignes qui sont toutes des `NewId`.
        """
        datee = self._version('1.0')
        datee.action_release()

        with Form(self.doc) as formulaire:
            with formulaire.version_ids.new() as ligne:
                ligne.version_number = '2.0'
                ligne.state = 'released'
                ligne.release_date = datee.release_date

        self.assertEqual(self.doc.current_version, '2.0')

    def test_le_rattrapage_ne_peut_pas_se_faire_refuser(self):
        """Une version publiée sans AUCUNE date, même pas de création.

        `Field.__set__` écrit sans condition et `write` valide les contraintes
        Python : sans repli terminal, la passe écrirait `False` sur une version
        publiée, la contrainte que ce même correctif installe lèverait, et la
        migration avorterait en roulant tout le `-u` en arrière.
        """
        publiee = self._version('1.0')
        self._publier_sans_aucune_date(publiee)
        avant = fields.Datetime.now()

        datees = self.env['project.document.version']._dater_les_publiees_sans_date()

        self.assertIn(publiee, datees)
        self.assertTrue(
            publiee.release_date, "la passe doit poser une date, pas False")
        self.assertGreaterEqual(publiee.release_date, avant)
        self.assertFalse(self._sans_date_du_document())

    def test_le_rattrapage_d_une_ligne_sans_aucune_date_se_rejoue_a_vide(self):
        """Le rejeu ne doit plus rien trouver, y compris dans cette branche.

        Une version remplacée sans date ne tombe pas sous la contrainte, donc
        rien ne signalait qu'elle restait sans date : le journal annonçait
        « None » et la passe la retrouvait à chaque rejeu.
        """
        remplacee = self._version('1.0')
        self._publier_sans_aucune_date(remplacee, etat='superseded')
        Version = self.env['project.document.version']
        Version._dater_les_publiees_sans_date()

        self.assertTrue(remplacee.release_date)
        self.assertNotIn(remplacee, Version._dater_les_publiees_sans_date())

    # ------------------------------------------------------------------
    # 5. Le rang de publication n'est plus l'autorité de l'ordre
    # ------------------------------------------------------------------

    def _forme_inversee(self):
        """Trois versions dont le rang dit le contraire de leurs dates.

        C'est la forme du document 176 en production : la plus récente porte le
        rang 0, parce qu'elle n'est jamais passée par le bouton, et les deux
        autres portent 1 et 2 dans l'ordre de leur création, qui n'est pas
        celui de leurs numéros.
        """
        v1 = self._version('1.0', effective_date='2025-08-01')
        v2 = self._version('2.0', effective_date='2026-02-25')
        v3 = self._version('2.1', effective_date='2026-08-31')
        for version in (v1, v2, v3):
            self._publier_sans_date(version)
        self.env['project.document.version']._dater_les_publiees_sans_date()
        for version, rang in ((v1, 2), (v2, 1), (v3, 0)):
            self._sql(
                "UPDATE project_document_version SET sequence_index = %s WHERE id = %s",
                (rang, version.id))
        return v1, v2, v3

    def test_la_version_precedente_se_lit_par_la_cle_pas_par_le_rang(self):
        """`_check_body_before_release` et `_freeze_body` en dépendent.

        Le rang désignait la plus VIEILLE comme précédente : une publication
        légitime pouvait se faire refuser pour « corps identique à la version
        X », et les sections modifiées gravées dans la version figée étaient
        celles d'un mauvais prédécesseur.
        """
        v1, v2, v3 = self._forme_inversee()
        nouvelle = self._version('3.0')

        self.assertEqual(nouvelle._previous_published(self.doc), v3)

    def test_la_renumerotation_remet_le_rang_dans_l_ordre_des_dates(self):
        v1, v2, v3 = self._forme_inversee()

        bouges = self.env['project.document.version'] \
            ._renumeroter_les_rangs_de_publication()

        self.assertGreaterEqual(bouges, 3)
        self.assertEqual(
            [v1.sequence_index, v2.sequence_index, v3.sequence_index], [1, 2, 3])

    def test_la_renumerotation_rejouee_ne_bouge_plus_rien(self):
        v1, v2, v3 = self._forme_inversee()
        Version = self.env['project.document.version']
        Version._renumeroter_les_rangs_de_publication()
        rangs = [v1.sequence_index, v2.sequence_index, v3.sequence_index]

        Version._renumeroter_les_rangs_de_publication()

        self.assertEqual(
            [v1.sequence_index, v2.sequence_index, v3.sequence_index], rangs)

    def test_l_assistant_propose_la_suite_de_la_vraie_derniere(self):
        """Le rang faisait proposer la suite de la plus vieille version."""
        self._forme_inversee()

        vals = self.env['project.document.release.wizard'].with_context(
            default_document_id=self.doc.id).default_get(['version_number'])

        self.assertEqual(vals['version_number'], '2.2')
