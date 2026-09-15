"""La pastille de l'onglet Courriel : les non-lus de la boîte, rien d'autre.

``unread`` compte ``status = 'new'`` hors des traités : la sourdine, et tout ce
qui a été rangé hors de l'INBOX du serveur, y entrent. La pastille de l'onglet
affichait donc bien plus que ce que la boîte montre. ``inbox_unread`` est la
clause « boîte » ET non lu ET entrant, comme l'app définit « non lu ».

⚠️ Même piège qu'avec `is_muted` : le SQL doit dire ce que l'ORM écrit, pas ce que le
domaine a l'air de dire. Les lignes d'essai portent donc des NULL là où la
production en a (sourdine et « traité » jamais écrits), et la parité se mesure
contre un ``search`` de l'ORM sur le même jeu, pas contre un texte.
"""
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestPastilleBoite(MobileApiCase):

    def setUp(self):
        super().setUp()
        BfEmail = self.as_owner()

        def ligne(uid, **extra):
            vals = self._vals(subject="Pastille %s" % uid, sender="x@acme.test",
                              direction="in", status="new", root=False,
                              body="Corps.", uid=uid)
            vals.update(extra)
            return BfEmail.create(vals)

        self.compte = {
            # Comptent : non lus, entrants, dans la boîte.
            "neuf_null": ligne("201"),
            "chatter": ligne("202", source="chatter", imap_in_inbox=False,
                             imap_folder=False),
            "fil_a": ligne("203", thread_root_id="<fil-pastille@test.invalid>"),
            "fil_b": ligne("204", thread_root_id="<fil-pastille@test.invalid>"),
        }
        self.ecarte = {
            "sourdine": ligne("211", is_muted=True),
            "hors_boite": ligne("212", imap_in_inbox=False,
                                imap_folder="Archives/2026"),
            "sortant": ligne("213", direction="out"),
            "lu": ligne("214", status="read"),
            "traite": ligne("215", is_handled=True),
        }
        self.env.flush_all()
        # La population réelle : des booléens jamais écrits.
        self.env.cr.execute(
            "UPDATE bf_email SET is_muted = NULL, is_handled = NULL WHERE id = %s",
            [self.compte["neuf_null"].id])
        self.env.cr.execute(
            "UPDATE bf_email SET is_muted = NULL WHERE id IN %s",
            [(self.ecarte["hors_boite"].id, self.ecarte["sortant"].id)])
        self.env.invalidate_all()

    def _par_l_orm(self):
        BfEmail = self.as_owner()
        return BfEmail.search(BfEmail._inbox_domain() + [
            ("user_id", "=", self.owner.id),
            ("status", "=", "new"),
            ("direction", "=", "in"),
        ])

    def _par_le_sql(self):
        where, params = self.as_owner()._mobile_filter_sql("inbox_unread")
        self.env.cr.execute(
            "SELECT id FROM bf_email WHERE user_id = %%s AND active = true "
            "AND %s" % where, [self.owner.id] + list(params))
        return {r[0] for r in self.env.cr.fetchall()}

    def test_le_jeu_d_essai_discrimine(self):
        """Sans cette vérification, une parité à zéro passerait pour un succès."""
        orm = set(self._par_l_orm().ids)
        for nom, rec in self.compte.items():
            self.assertIn(rec.id, orm, nom)
        for nom, rec in self.ecarte.items():
            self.assertNotIn(rec.id, orm, nom)

    def test_le_sql_compte_les_memes_lignes_que_l_orm(self):
        self.assertEqual(self._par_le_sql(), set(self._par_l_orm().ids))

    def test_la_pastille_a_plat_et_repliee(self):
        orm = self._par_l_orm()
        BfEmail = self.as_owner()
        self.assertEqual(BfEmail._mobile_counts(grouped=False)["inbox_unread"],
                         len(orm))
        fils = {r.thread_root_id or "id:%s" % r.id for r in orm}
        self.assertEqual(BfEmail._mobile_counts(grouped=True)["inbox_unread"],
                         len(fils))
        self.assertLess(len(fils), len(orm),
                        "le fil de deux messages doit se replier en un")

    def test_la_cle_n_est_pas_celle_de_unread(self):
        """Le défaut d'origine : « unread » compte la sourdine et le hors-boîte."""
        comptes = self.as_owner()._mobile_counts(grouped=False)
        self.assertGreater(comptes["unread"], comptes["inbox_unread"])

    def test_la_cle_descend_partout_ou_les_compteurs_descendent(self):
        BfEmail = self.as_owner()
        self.assertIn("inbox_unread", BfEmail.get_mobile_config()["counts"])
        apres = BfEmail.mobile_mark_read([self.compte["neuf_null"].id], grouped=False)
        self.assertEqual(apres["inbox_unread"], len(self._par_l_orm()))
