# Copyright 2026 Les services de consultation Blue Fox, Inc.
# License LGPL-3
"""La garde du tableau des activités.

Sans la garde, les quatre premiers essais lèvent
`ValueError: Wrong value for mail.activity.related_model_instance`, et, en vrai,
font tomber la liste du tableau et son formulaire pour tout le monde.

⚠️ Les formes fautives se fabriquent **en base**, pas par l'ORM : `mail.activity.create`
appelle `message_subscribe` sur la fiche visée (donc elle doit être un `mail.thread`),
et supprimer une fiche supprime ses activités. Aucune de ces formes ne peut donc naître
d'un `create`, et ce sont pourtant celles qu'on relève en production : activités posées
en XML-RPC sur un `mail.thread` sans `mail.activity.mixin`, et colonnes `res_model`
jamais calculées.

⚠️ Ces essais ne discriminent que sur un `mail_activity_board` d'ORIGINE. Sur une base
dont le fichier OCA a été patché en place, ils passent avec ou sans la garde : c'est le
patch qui les fait passer. Mesurer le parc d'une base demande un script d'exploitation,
pas un essai : un essai livré ne doit pas dépendre des données de celui qui l'exécute.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestGardeTableauActivites(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.type_todo = cls.env.ref("mail.mail_activity_data_todo")
        # Un modèle hors sélection, lu depuis `ir.model` et non depuis la
        # sélection du champ : l'attendu ne vient pas du livrable.
        cls.hors_selection = "ir.ui.view"
        assert not cls.env["ir.model"].search([
            ("model", "=", cls.hors_selection), ("is_mail_activity", "=", True),
        ]), f"l'essai suppose que {cls.hors_selection} n'a pas mail.activity.mixin"
        cls.cible_hors = cls.env["ir.ui.view"].search([], limit=1)

    def _activite(self, fiche):
        """Une activité normale, sur une fiche qui l'accepte."""
        return self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get(fiche._name).id,
            "res_id": fiche.id,
            "activity_type_id": self.type_todo.id,
            "summary": "garde du tableau",
            "date_deadline": "2026-11-30",
            "user_id": self.env.user.id,
        })

    def _partenaire(self, nom):
        return self.env["res.partner"].create({"name": f"Garde du tableau : {nom}"})

    def _repointer(self, activite, modele=None, res_id=None, modele_nul=False):
        """Repointer l'activité EN BASE, comme la prod la porte vraiment."""
        activite.flush_recordset()
        morceaux, valeurs = [], []
        if modele is not None:
            morceaux += ["res_model_id = %s", "res_model = %s"]
            valeurs += [self.env["ir.model"]._get(modele).id, modele]
        if modele_nul:
            morceaux.append("res_model = NULL")
        if res_id is not None:
            morceaux.append("res_id = %s")
            valeurs.append(res_id)
        self.env.cr.execute(
            f"UPDATE mail_activity SET {', '.join(morceaux)} WHERE id = %s",
            valeurs + [activite.id],
        )
        activite.invalidate_recordset()
        return activite

    def _lire(self, activites):
        """Le chemin exact du plantage : le `read` que fait la vue."""
        return activites.read(["related_model_instance", "res_model", "res_id"])

    def test_modele_hors_selection_se_lit_sans_lever(self):
        activite = self._repointer(
            self._activite(self._partenaire("hors sélection")),
            modele=self.hors_selection, res_id=self.cible_hors.id,
        )
        lu = self._lire(activite)[0]
        self.assertFalse(lu["related_model_instance"])
        # La ligne garde de quoi se nommer : c'est ce que le formulaire du
        # tableau affiche à la place, par la vue de ce module.
        self.assertEqual(lu["res_model"], self.hors_selection)
        self.assertEqual(lu["res_id"], self.cible_hors.id)

    def test_un_fautif_ne_fait_plus_tomber_le_lot(self):
        """C'est la vraie panne : UNE ligne fautive, et toute la vue tombe."""
        partenaire = self._partenaire("lot")
        bonne = self._activite(partenaire)
        fautive = self._repointer(
            self._activite(self._partenaire("lot fautif")),
            modele=self.hors_selection, res_id=self.cible_hors.id,
        )
        lu = self._lire(bonne | fautive)
        self.assertEqual(len(lu), 2)
        valeurs = {ligne["id"]: ligne["related_model_instance"] for ligne in lu}
        self.assertEqual(valeurs[bonne.id], f"res.partner,{partenaire.id}")
        self.assertFalse(valeurs[fautive.id])

    def test_activite_sans_modele_du_tout(self):
        """Une forme relevée en production : `res_model` nul en base.

        Sans la garde, le calcul écrit « False,<id> » et lève.
        """
        activite = self._repointer(
            self._activite(self._partenaire("sans modèle")), modele_nul=True)
        self.assertFalse(activite.res_model)
        self.assertFalse(self._lire(activite)[0]["related_model_instance"])

    def test_cible_disparue_rend_vide_comme_avant(self):
        """Modèle permis, fiche disparue : Odoo rend déjà vide, sans lever.

        La garde ne doit pas changer ce chemin (`convert_to_cache` fait
        `.exists()`), sinon elle masquerait une suppression derrière un refus.
        """
        self.env.cr.execute("SELECT max(id) + 1000000 FROM res_partner")
        fantome = self.env.cr.fetchone()[0]
        activite = self._repointer(
            self._activite(self._partenaire("cible disparue")), res_id=fantome)
        self.assertFalse(self.env["res.partner"].browse(fantome).exists())
        self.assertFalse(self._lire(activite)[0]["related_model_instance"])

    def test_modele_avec_mixin_garde_sa_reference(self):
        """La garde ne vide pas ce qui marchait."""
        partenaire = self._partenaire("référence")
        activite = self._activite(partenaire)
        self.assertEqual(activite.related_model_instance, partenaire)
        self.assertEqual(
            self._lire(activite)[0]["related_model_instance"],
            f"res.partner,{partenaire.id}",
        )
