"""Compteurs de distribution.

Les cinq compteurs sont agrégés en une requête. Trois choses peuvent casser
sans que rien ne se voie à l'écran : une valeur fausse, un compteur qui se
remplit bien pour un enregistrement seul mais pas pour un lot, et un retour
silencieux au ``search()`` par enregistrement.
"""

from unittest.mock import patch

from .common import KnowledgeCase


class TestDistributionCounts(KnowledgeCase):

    # ------------------------------------------------------------------
    # Valeurs — comptées à la main dans common.KnowledgeCase
    # ------------------------------------------------------------------

    def test_version_counts(self):
        """Les compteurs par version, lus un par un."""
        self.assertEqual(self.ver_a1.distribution_count, 3)
        self.assertEqual(self.ver_a1.pending_acknowledgment_count, 1)

        self.assertEqual(self.ver_a2.distribution_count, 1)
        self.assertEqual(self.ver_a2.pending_acknowledgment_count, 1)

        self.assertEqual(self.ver_b1.distribution_count, 2)
        self.assertEqual(self.ver_b1.pending_acknowledgment_count, 0)

    def test_document_counts(self):
        """Les quatre compteurs par document, lus un par un."""
        self.assertEqual(self.doc_a.distribution_count, 4)
        self.assertEqual(self.doc_a.acknowledgment_count, 1)
        self.assertEqual(self.doc_a.pending_acknowledgment_count, 2)
        self.assertEqual(self.doc_a.outdated_distribution_count, 2)

        self.assertEqual(self.doc_b.distribution_count, 2)
        self.assertEqual(self.doc_b.acknowledgment_count, 1)
        self.assertEqual(self.doc_b.pending_acknowledgment_count, 0)
        self.assertEqual(self.doc_b.outdated_distribution_count, 0)

    def test_recalled_is_not_outdated_material(self):
        """Une distribution rappelée est périmée sans entrer dans le compteur.

        ``dist_3`` porte ``is_outdated`` à vrai — sa version n'est plus la
        dernière — mais son état est ``recalled``. Le compteur ne retient que
        ``pending`` et ``acknowledged`` : sans cette distinction, A afficherait
        3 obsolètes au lieu de 2.
        """
        self.assertTrue(self.dist_3.is_outdated)
        self.assertEqual(self.doc_a.outdated_distribution_count, 2)

    def test_document_without_distribution_reads_zero(self):
        """Un document sans version ni distribution rend zéro, pas faux.

        Les quatre champs sont initialisés avant l'agrégation ; sans ce prélude
        l'ORM lèverait une erreur de champ non assigné.
        """
        self.assertEqual(self.doc_c.distribution_count, 0)
        self.assertEqual(self.doc_c.acknowledgment_count, 0)
        self.assertEqual(self.doc_c.pending_acknowledgment_count, 0)
        self.assertEqual(self.doc_c.outdated_distribution_count, 0)

    # ------------------------------------------------------------------
    # Lot — le vrai risque de la réécriture
    # ------------------------------------------------------------------

    def test_counts_hold_when_read_as_a_batch(self):
        """Lire les trois documents d'un coup donne les mêmes valeurs.

        C'est le mode d'emploi réel : la liste et le kanban lisent le lot
        entier. Une agrégation qui se trompe de clé rend des compteurs
        plausibles mais attribués au mauvais document — invisible tant qu'on
        lit un enregistrement à la fois.
        """
        docs = self.doc_a | self.doc_b | self.doc_c
        docs.invalidate_recordset()
        attendu = {
            self.doc_a.id: (4, 1, 2, 2),
            self.doc_b.id: (2, 1, 0, 0),
            self.doc_c.id: (0, 0, 0, 0),
        }
        obtenu = {
            doc.id: (
                doc.distribution_count,
                doc.acknowledgment_count,
                doc.pending_acknowledgment_count,
                doc.outdated_distribution_count,
            )
            for doc in docs
        }
        self.assertEqual(obtenu, attendu)

    def test_version_counts_hold_when_read_as_a_batch(self):
        versions = self.ver_a1 | self.ver_a2 | self.ver_b1
        versions.invalidate_recordset()
        obtenu = {
            v.id: (v.distribution_count, v.pending_acknowledgment_count)
            for v in versions
        }
        self.assertEqual(obtenu, {
            self.ver_a1.id: (3, 1),
            self.ver_a2.id: (1, 1),
            self.ver_b1.id: (2, 0),
        })

    def test_new_record_does_not_crash_the_counters(self):
        """Un enregistrement virtuel (onchange) n'a pas d'identifiant en base.

        Les deux calculs filtrent les ``NewId`` avant d'interroger la base. Sans
        ce filtre, le ``_read_group`` reçoit un identifiant virtuel et lève.
        """
        doc_neuf = self.env['project.document'].new({
            'name': 'Document virtuel', 'type_id': self.type_externe.id,
        })
        self.assertEqual(doc_neuf.distribution_count, 0)

        version_neuve = self.env['project.document.version'].new({
            'document_id': self.doc_a.id, 'version_number': '9.9',
        })
        self.assertEqual(version_neuve.distribution_count, 0)

    # ------------------------------------------------------------------
    # Le N+1 lui-même
    # ------------------------------------------------------------------

    def _compter_les_appels(self, records, champs):
        """Rejoue la lecture de ``champs`` en comptant search et _read_group."""
        Distribution = self.env['project.document.distribution']
        classe = type(Distribution)
        appels = {'search': 0, '_read_group': 0}
        origine_search = classe.search
        origine_read_group = classe._read_group

        def search_compte(self, *args, **kwargs):
            appels['search'] += 1
            return origine_search(self, *args, **kwargs)

        def read_group_compte(self, *args, **kwargs):
            appels['_read_group'] += 1
            return origine_read_group(self, *args, **kwargs)

        records.invalidate_recordset()
        with patch.object(classe, 'search', search_compte), \
                patch.object(classe, '_read_group', read_group_compte):
            for champ in champs:
                records.mapped(champ)
        return appels

    def test_document_counters_cost_one_query_whatever_the_volume(self):
        """Le coût de l'agrégation ne suit pas le nombre de documents.

        C'est l'invariant que l'agrégation a acheté : sur un parc de deux cents
        documents, la liste passe de plus de 400 ms à une centaine. Un retour au
        ``search()`` par enregistrement rendrait les mêmes valeurs et
        repasserait ce test au rouge.
        """
        Document = self.env['project.document']
        supplementaires = Document.create([
            {
                'name': f'Document en série {i}',
                'code': f'TEST-SERIE-{i}',
                'type_id': self.type_externe.id,
            }
            for i in range(12)
        ])
        lot = self.doc_a | self.doc_b | self.doc_c | supplementaires
        self.assertEqual(len(lot), 15)

        appels = self._compter_les_appels(lot, [
            'distribution_count', 'acknowledgment_count',
            'pending_acknowledgment_count', 'outdated_distribution_count',
        ])
        self.assertEqual(appels['_read_group'], 1, appels)
        self.assertEqual(appels['search'], 0, appels)

    def test_version_counters_cost_one_query_whatever_the_volume(self):
        Version = self.env['project.document.version']
        supplementaires = Version.create([
            {'document_id': self.doc_c.id, 'version_number': f'0.{i}'}
            for i in range(12)
        ])
        lot = self.ver_a1 | self.ver_a2 | self.ver_b1 | supplementaires
        self.assertEqual(len(lot), 15)

        appels = self._compter_les_appels(
            lot, ['distribution_count', 'pending_acknowledgment_count'])
        self.assertEqual(appels['_read_group'], 1, appels)
        self.assertEqual(appels['search'], 0, appels)

    # ------------------------------------------------------------------
    # Recalcul — les @api.depends des compteurs
    # ------------------------------------------------------------------

    def test_counters_follow_a_state_change(self):
        """Accuser réception déplace le compteur sans invalidation manuelle."""
        self.assertEqual(self.doc_a.pending_acknowledgment_count, 2)
        self.assertEqual(self.doc_a.acknowledgment_count, 1)

        self.dist_1.state = 'acknowledged'

        self.assertEqual(self.doc_a.pending_acknowledgment_count, 1)
        self.assertEqual(self.doc_a.acknowledgment_count, 2)
        self.assertEqual(self.ver_a1.pending_acknowledgment_count, 0)

    def test_counters_follow_a_new_distribution(self):
        self.env['project.document.distribution'].create({
            'version_id': self.ver_b1.id, 'recipient_type': 'partner',
            'partner_id': self.partner_3.id, 'state': 'pending',
        })
        self.assertEqual(self.doc_b.distribution_count, 3)
        self.assertEqual(self.doc_b.pending_acknowledgment_count, 1)
        self.assertEqual(self.ver_b1.distribution_count, 3)


class TestRecipientName(KnowledgeCase):
    """Le nom du destinataire ne doit pas figer un nom périmé.

    ``recipient_name`` est un calcul stocké. Sans ``partner_id.name`` dans ses
    dépendances, un changement de nom ne le rejoue jamais : la distribution
    garde le nom du jour de l'envoi, à l'écran comme dans les rapports et les
    rappels d'accusé.
    """

    def test_partner_rename_refreshes_the_stored_name(self):
        self.assertEqual(self.dist_1.recipient_name, 'Destinataire Un')
        self.partner_1.name = 'Nom Corrigé'
        self.assertEqual(self.dist_1.recipient_name, 'Nom Corrigé')

    def test_user_rename_refreshes_the_stored_name(self):
        utilisateur = self.env['res.users'].create({
            'name': 'Employé Un', 'login': 'employe.essai.pkm',
        })
        distribution = self.env['project.document.distribution'].create({
            'version_id': self.ver_b1.id, 'recipient_type': 'employee',
            'user_id': utilisateur.id,
        })
        self.assertEqual(distribution.recipient_name, 'Employé Un')
        utilisateur.name = 'Employé Corrigé'
        self.assertEqual(distribution.recipient_name, 'Employé Corrigé')

    def test_switching_recipient_type_refreshes_the_stored_name(self):
        """Passer de client à employé ne laisse pas l'ancien nom en place."""
        utilisateur = self.env['res.users'].create({
            'name': 'Employé Deux', 'login': 'employe.essai.pkm.2',
        })
        self.dist_1.write({
            'recipient_type': 'employee', 'user_id': utilisateur.id,
        })
        self.assertEqual(self.dist_1.recipient_name, 'Employé Deux')
