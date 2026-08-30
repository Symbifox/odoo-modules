"""Où s'arrête le marquage « lu » automatique.

Odoo implémente ``web_search_read`` en ``records.web_read(...)``. Le marquage
« lu » vit dans ``web_read``, donc tout rendu de liste le déclenchait sur
chaque ligne affichée : le compteur de non-lus se vidait avant lecture, et
chaque client ouvert écrivait la même ligne au même instant.

Ces tests fixent la frontière : la liste lit, le formulaire marque.
"""
from .common import MobileApiCase


class TestMarkReadScope(MobileApiCase):

    SPEC = {"subject": {}, "status": {}}

    def test_liste_ne_marque_pas_lu(self):
        """Afficher la boîte en liste laisse les statuts intacts."""
        BfEmail = self.as_owner()
        result = BfEmail.web_search_read(
            [("id", "in", (self.inbound | self.with_attachment).ids)],
            self.SPEC,
        )

        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(self.inbound.status, "new")
        self.assertEqual(self.with_attachment.status, "new")
        # Le contenu retourné ne doit pas mentir non plus.
        self.assertEqual(
            {r["status"] for r in result["records"]}, {"new"},
            "la liste annonce « lu » un message qu'elle vient d'afficher",
        )

    def test_formulaire_marque_lu(self):
        """Ouvrir un message en formulaire le marque lu, comme avant."""
        BfEmail = self.as_owner()
        BfEmail.browse(self.inbound.id).web_read(self.SPEC)

        self.inbound.invalidate_recordset(["status"])
        self.assertEqual(self.inbound.status, "read")

    def test_liste_puis_formulaire(self):
        """La liste ne consomme pas le « non lu » que le formulaire attend."""
        BfEmail = self.as_owner()
        BfEmail.web_search_read([("id", "=", self.inbound.id)], self.SPEC)
        self.inbound.invalidate_recordset(["status"])
        self.assertEqual(self.inbound.status, "new")

        BfEmail.browse(self.inbound.id).web_read(self.SPEC)
        self.inbound.invalidate_recordset(["status"])
        self.assertEqual(self.inbound.status, "read")

    def test_deja_lu_reste_lu(self):
        """Un message déjà traité n'est pas ramené en arrière par une liste."""
        BfEmail = self.as_owner()
        BfEmail.web_search_read([("id", "=", self.outbound.id)], self.SPEC)
        self.outbound.invalidate_recordset(["status"])
        self.assertEqual(self.outbound.status, "read")
