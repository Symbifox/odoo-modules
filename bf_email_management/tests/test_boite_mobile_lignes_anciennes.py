"""Le téléphone affichait une boîte vide au-dessus de celle du poste.

`is_muted` est arrivé avec la sourdine. Odoo n'écrit PAS la valeur par défaut
d'un booléen neuf dans les lignes qui existent déjà : pour l'ORM, NULL et faux
disent la même chose, et remplir la colonne coûterait une réécriture de la
table. Sur une base réelle, toutes les lignes d'avant la montée portaient donc
`is_muted` à NULL.

Le domaine Python (`('is_muted', '=', False)`) se traduit en
`is_muted IS NULL OR is_muted = false` : le poste voyait ses courriels. Le
filtre du téléphone est du SQL écrit à la main, `is_muted = false`, qui écarte
NULL : l'app répondait « Boîte de réception · 0 », pour tout le monde.

L'essai de parité de `test_dossier_disparu` comparait déjà les deux
transcriptions sur les mêmes lignes, et passait : ses lignes naissent par
l'ORM, qui écrit `false`. Il éprouvait une population qui n'existe pas en
production. Ceux-ci posent la population réelle.
"""
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestBoiteMobileLignesAnciennes(MobileApiCase):

    def setUp(self):
        super().setUp()
        self.env.flush_all()
        # Toutes les lignes du propriétaire telles qu'elles sont en production
        # le jour où la colonne arrive : jamais écrites.
        self.env.cr.execute(
            "UPDATE bf_email SET is_muted = NULL WHERE user_id = %s",
            [self.owner.id])
        # Et les deux autres formes du « faux » que l'ORM confond avec NULL :
        # un dossier vide plutôt qu'absent, un drapeau « traité » jamais écrit.
        self.env.cr.execute(
            "UPDATE bf_email SET imap_in_inbox = false, imap_folder = '' "
            "WHERE id = %s", [self.with_attachment.id])
        self.env.cr.execute(
            "UPDATE bf_email SET is_handled = NULL WHERE id = %s",
            [self.inbound.id])
        self.env.invalidate_all()

    def _boite_par_le_domaine(self):
        BfEmail = self.as_owner()
        return BfEmail.search(
            BfEmail._inbox_domain() + [("user_id", "=", self.owner.id)])

    def _ids_par_le_sql(self, name):
        where, params = self.as_owner()._mobile_filter_sql(name)
        self.env.cr.execute(
            "SELECT id FROM bf_email WHERE user_id = %%s AND active = true "
            "AND %s" % where, [self.owner.id] + list(params))
        return {r[0] for r in self.env.cr.fetchall()}

    def test_le_sql_du_telephone_compte_la_meme_boite_que_le_poste(self):
        par_le_domaine = set(self._boite_par_le_domaine().ids)
        self.assertEqual(
            {self.inbound.id, self.outbound.id, self.with_attachment.id},
            par_le_domaine,
            "le poste doit voir les trois lignes, sinon l'essai ne prouve rien")
        self.assertEqual(
            par_le_domaine, self._ids_par_le_sql("inbox"),
            "une ligne jamais écrite sort de la boîte du téléphone seulement")

    def test_l_app_liste_les_fils_que_le_poste_voit(self):
        page = self.as_owner().get_mobile_threads("inbox", limit=100)
        self.assertEqual(
            {t["thread_key"] for t in page["threads"]},
            {"<racine-1@test.invalid>", "id:%s" % self.with_attachment.id},
            "« Boîte de réception » vide au téléphone, pleine au poste")

    def test_la_pastille_compte_ce_que_la_liste_affiche(self):
        counts = self.as_owner()._mobile_counts()
        self.assertEqual(counts["inbox"], 2)
        self.assertEqual(self.as_owner()._mobile_counts(grouped=False)["inbox"], 3)

    def test_non_lus_et_non_classes_voient_aussi_les_lignes_jamais_ecrites(self):
        # `inbound` est neuf et jamais marqué traité : il est non lu, et il
        # n'est classé nulle part.
        self.assertIn(self.inbound.id, self._ids_par_le_sql("unread"))
        self.assertIn(self.inbound.id, self._ids_par_le_sql("unrouted"))

    def test_une_ligne_en_sourdine_reste_dehors(self):
        # Le correctif ne doit pas rendre la sourdine inopérante en voulant
        # attraper NULL.
        self.inbound.with_user(self.owner).action_mute_thread()
        self.env.flush_all()
        self.assertNotIn(self.inbound.id, self._ids_par_le_sql("inbox"))
        self.assertNotIn(self.outbound.id, self._ids_par_le_sql("inbox"))
        self.assertEqual(
            set(self._boite_par_le_domaine().ids), self._ids_par_le_sql("inbox"))
