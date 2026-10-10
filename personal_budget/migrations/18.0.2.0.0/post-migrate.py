"""18.0.2.0.0, après le chargement : ranger les données existantes dans des budgets.

Jusqu'en 18.0.1.x, chaque donnée appartenait à la personne qui l'avait créée
(règles sur `create_uid`). Désormais elle appartient à un budget. Pour que
personne ne voie ni plus ni moins qu'avant la montée :

* chaque personne qui a créé des données reçoit UN budget personnel, non
  partagé (« Mon budget », budget par défaut) ;
* chaque donnée qui porte son budget en propre (catégorie, contributeur, prêt,
  chèque, facture, registre de partage) va dans le budget de la personne qui
  l'a créée ;
* transactions, plans et lignes de prêt suivent leur parent (catégorie, prêt).

Les écarts qui changeraient la visibilité (une transaction créée par une autre
personne que sa catégorie) sont comptés et journalisés, jamais corrigés en
silence.

Enfin le verrou NOT NULL est posé sur `book_id` puis RELU dans
`information_schema` : Odoo n'a pas pu le poser au chargement, la colonne
était vide.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# Tables dont chaque ligne porte son budget en propre.
OWN_BOOK_TABLES = [
    'personal_budget_category',
    'personal_budget_contributor',
    'personal_budget_loan',
    'personal_budget_cheque',
    'personal_budget_invoice',
    'personal_budget_share_line',
]
# Tables dont le budget se déduit du parent : (table, colonne parent, table parent)
DERIVED_BOOK_TABLES = [
    ('personal_budget_transaction', 'category_id', 'personal_budget_category'),
    ('personal_budget_plan', 'category_id', 'personal_budget_category'),
    ('personal_budget_loan_line', 'loan_id', 'personal_budget_loan'),
]


def _owner_ids(cr):
    ids = set()
    for table in OWN_BOOK_TABLES:
        cr.execute('SELECT DISTINCT create_uid FROM "%s" WHERE book_id IS NULL' % table)
        ids.update(r[0] for r in cr.fetchall())
    return ids


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Book = env['personal.budget.book']

    owners = _owner_ids(cr)
    # Données sans auteur connu (compte supprimé) : dans le budget du compte système,
    # invisible de toutes et tous, comme elles l'étaient avant la montée. Jamais dans
    # celui de l'administrateur, qui les aurait vues apparaître.
    fallback = env['res.users'].browse(SUPERUSER_ID)
    book_by_uid = {}
    for uid in sorted(owners, key=lambda u: (u is None, u or 0)):
        user = env['res.users'].browse(uid).exists() if uid else env['res.users']
        if not user:
            _logger.warning(
                "personal_budget : données sans auteur connu (create_uid=%s), "
                "rangées dans le budget de %s", uid, fallback.login)
            user = fallback
        book = Book.with_context(active_test=False).search(
            [('user_id', '=', user.id), ('is_default', '=', True)], limit=1)
        if not book:
            # Le nom est une donnée : dans la langue de la personne (« Mon budget » en fr_CA).
            name = Book.with_context(lang=user.lang or 'en_US')._default_book_name()
            book = Book.create({'name': name, 'user_id': user.id, 'is_default': True})
        book_by_uid[uid] = book.id

    for table in OWN_BOOK_TABLES:
        for uid, book_id in book_by_uid.items():
            if uid is None:
                cr.execute('UPDATE "%s" SET book_id = %%s WHERE book_id IS NULL AND create_uid IS NULL' % table,
                           (book_id,))
            else:
                cr.execute('UPDATE "%s" SET book_id = %%s WHERE book_id IS NULL AND create_uid = %%s' % table,
                           (book_id, uid))
            if cr.rowcount:
                _logger.info("personal_budget : %s ligne(s) de %s -> budget %s", cr.rowcount, table, book_id)

    for table, parent_col, parent_table in DERIVED_BOOK_TABLES:
        cr.execute("""
            UPDATE "%(t)s" c SET book_id = p.book_id
              FROM "%(p)s" p
             WHERE p.id = c."%(col)s" AND c.book_id IS DISTINCT FROM p.book_id
        """ % {'t': table, 'p': parent_table, 'col': parent_col})

    # Écarts de visibilité : une ligne dérivée dont l'auteur n'est pas la
    # personne propriétaire du budget de son parent. Avant la montée, elle
    # n'était visible que de son auteur ; elle l'est désormais de la personne
    # propriétaire du budget. On le dit.
    for table, parent_col, parent_table in DERIVED_BOOK_TABLES:
        cr.execute("""
            SELECT count(*) FROM "%(t)s" c
              JOIN personal_budget_book b ON b.id = c.book_id
             WHERE c.create_uid IS DISTINCT FROM b.user_id
        """ % {'t': table})
        n = cr.fetchone()[0]
        if n:
            _logger.warning(
                "personal_budget : %s ligne(s) de %s ont un auteur différent de la "
                "personne propriétaire du budget de leur parent", n, table)
    cr.execute("""
        SELECT count(*) FROM personal_budget_transaction t
          JOIN personal_budget_contributor c ON c.id = t.contributor_id
         WHERE c.book_id != t.book_id
    """)
    n = cr.fetchone()[0]
    if n:
        _logger.warning("personal_budget : %s transaction(s) citent un contributeur d'un autre budget", n)

    # Verrou NOT NULL, puis relecture : ce qu'on croit posé se vérifie.
    for table in OWN_BOOK_TABLES:
        cr.execute('SELECT count(*) FROM "%s" WHERE book_id IS NULL' % table)
        if cr.fetchone()[0]:
            raise RuntimeError("personal_budget : %s garde des lignes sans budget" % table)
        cr.execute('ALTER TABLE "%s" ALTER COLUMN book_id SET NOT NULL' % table)
        cr.execute("""
            SELECT is_nullable FROM information_schema.columns
             WHERE table_name = %s AND column_name = 'book_id'
        """, (table,))
        if cr.fetchone()[0] != 'NO':
            raise RuntimeError("personal_budget : NOT NULL non posé sur %s.book_id" % table)

    env.invalidate_all()
    # Le solde courant du registre de partage se calcule désormais par budget.
    ShareLine = env['personal.budget.share.line']
    ShareLine._recompute_running_balances(ShareLine.search([]).book_id)
    _logger.info("personal_budget : %s budget(s) personnel(s) prêts", len(book_by_uid))
