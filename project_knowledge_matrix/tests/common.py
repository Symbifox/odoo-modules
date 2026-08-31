"""Jeu d'essai partagé — celui du 19 août, figé.

Deux documents, trois versions, six distributions. Les valeurs attendues sont
calculées à la main dans la docstring de ``KnowledgeCase`` et reprises telles
quelles dans les assertions : si un compteur change, c'est le code qui a bougé,
pas le jeu d'essai.
"""

from odoo.tests import TransactionCase


class KnowledgeCase(TransactionCase):
    """Socle du filet de tests du module.

    Arbre monté par ``setUpClass`` :

    ::

        Document A (code TEST-A, type externe)
          ├─ v1.0  publiée le 2026-01-01   ← périmée (v2.0 est la dernière)
          │    ├─ dist 1  en attente   → obsolète
          │    ├─ dist 2  accusée      → obsolète
          │    └─ dist 3  rappelée     → obsolète, mais hors des compteurs
          └─ v2.0  publiée le 2026-02-01   ← version courante
               └─ dist 4  en attente

        Document B (code TEST-B, type interne)
          └─ v1.0  publiée le 2026-01-15   ← version courante
               ├─ dist 5  accusée
               └─ dist 6  remplacée

        Document C (code TEST-C) — aucune version, aucune distribution

    Compteurs attendus, comptés à la main sur ce schéma :

    Par version (``_compute_distribution_stats``)
      * A v1.0 : 3 distributions, 1 en attente
      * A v2.0 : 1 distribution,  1 en attente
      * B v1.0 : 2 distributions, 0 en attente

    Par document (``_compute_distribution_count``)
      * A : 4 au total, 1 accusée, 2 en attente, 2 obsolètes
      * B : 2 au total, 1 accusée, 0 en attente, 0 obsolète
      * C : 0 partout

    Les deux « obsolètes » de A sont les distributions 1 et 2. La 3 est rappelée :
    le compteur ne retient que les états ``pending`` et ``acknowledged``.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.type_externe = cls.env['project.document.type'].create({
            'name': 'Type d\'essai externe',
            'code': 'TEST-EXT',
            'is_internal': False,
        })
        cls.type_interne = cls.env['project.document.type'].create({
            'name': 'Type d\'essai interne',
            'code': 'TEST-INT',
            'is_internal': True,
        })

        cls.partner_1 = cls.env['res.partner'].create({
            'name': 'Destinataire Un', 'is_company': True,
        })
        cls.partner_2 = cls.env['res.partner'].create({
            'name': 'Destinataire Deux', 'is_company': True,
        })
        cls.partner_3 = cls.env['res.partner'].create({
            'name': 'Destinataire Trois', 'is_company': True,
        })

        Document = cls.env['project.document']
        cls.doc_a = Document.create({
            'name': 'Document A', 'code': 'TEST-A', 'type_id': cls.type_externe.id,
        })
        cls.doc_b = Document.create({
            'name': 'Document B', 'code': 'TEST-B', 'type_id': cls.type_interne.id,
        })
        cls.doc_c = Document.create({
            'name': 'Document C', 'code': 'TEST-C', 'type_id': cls.type_externe.id,
        })

        Version = cls.env['project.document.version']
        cls.ver_a1 = Version.create({
            'document_id': cls.doc_a.id, 'version_number': '1.0',
            'state': 'released', 'release_date': '2026-01-01 12:00:00',
        })
        cls.ver_a2 = Version.create({
            'document_id': cls.doc_a.id, 'version_number': '2.0',
            'state': 'released', 'release_date': '2026-02-01 12:00:00',
        })
        cls.ver_b1 = Version.create({
            'document_id': cls.doc_b.id, 'version_number': '1.0',
            'state': 'released', 'release_date': '2026-01-15 12:00:00',
        })

        Distribution = cls.env['project.document.distribution']
        cls.dist_1 = Distribution.create({
            'version_id': cls.ver_a1.id, 'recipient_type': 'partner',
            'partner_id': cls.partner_1.id, 'state': 'pending',
        })
        cls.dist_2 = Distribution.create({
            'version_id': cls.ver_a1.id, 'recipient_type': 'partner',
            'partner_id': cls.partner_2.id, 'state': 'acknowledged',
        })
        cls.dist_3 = Distribution.create({
            'version_id': cls.ver_a1.id, 'recipient_type': 'partner',
            'partner_id': cls.partner_3.id, 'state': 'recalled',
        })
        cls.dist_4 = Distribution.create({
            'version_id': cls.ver_a2.id, 'recipient_type': 'partner',
            'partner_id': cls.partner_1.id, 'state': 'pending',
        })
        cls.dist_5 = Distribution.create({
            'version_id': cls.ver_b1.id, 'recipient_type': 'partner',
            'partner_id': cls.partner_1.id, 'state': 'acknowledged',
        })
        cls.dist_6 = Distribution.create({
            'version_id': cls.ver_b1.id, 'recipient_type': 'partner',
            'partner_id': cls.partner_2.id, 'state': 'superseded',
        })
