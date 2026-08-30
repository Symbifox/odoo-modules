"""Les deux sources qui justifient le module, exécutées contre de vrais fournisseurs.

Ces tests SE SAUTENT quand `bf_appointment` ou `bf_securetransfer` n'est pas
installé — c'est justement ce qui se produisait au banc d'essai, où les deux
résolveurs n'avaient donc JAMAIS tourné. Un saut annoncé vaut mieux qu'une
couverture qu'on croit avoir.
"""

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("bf_linkpage", "post_install", "-at_install")
class TestProviders(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Titulaire", "login": "linkpage.prov@test.invalid",
            "groups_id": [Command.set([cls.env.ref("base.group_user").id])]})
        cls.page = cls.env["bf.linkpage"].create({
            "name": "Titulaire", "slug": "titulaire-prov", "kind": "owner",
            "partner_id": cls.user.partner_id.id, "user_id": cls.user.id,
            "state": "published"})

    def _besoin(self, model):
        """Le fournisseur doit être là ET sa table doit suivre son code.

        Le deuxième contrôle n'est pas théorique : un banc alimenté depuis une
        copie porte des modules dont le `-u` n'a jamais tourné. Le code y
        déclare des champs que la table n'a pas, et la moindre lecture casse
        sur un `UndefinedColumn`. Sans ce garde, bf_linkpage rougirait pour la
        panne d'un AUTRE module, ce qui est le meilleur moyen de faire ignorer
        une suite de tests.
        """
        if model not in self.env:
            self.skipTest("%s absent : la source n'est pas vérifiable ici" % model)
        M = self.env[model]
        self.env.cr.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (M._table,),
        )
        colonnes = {row[0] for row in self.env.cr.fetchall()}
        manquantes = sorted(
            n for n, f in M._fields.items()
            if f.store and f.column_type and n not in colonnes
        )
        if manquantes:
            self.skipTest(
                "%s a un schéma en retard sur son code (colonnes absentes : %s). "
                "Le fournisseur n'est pas à jour sur ce banc ; la source n'est "
                "pas vérifiable ici." % (model, ", ".join(manquantes[:3]))
            )

    def _link(self, code, **vals):
        return self.env["bf.linkpage.link"].create(
            dict({"page_id": self.page.id, "name": "L", "source_code": code}, **vals))

    def _booking_type(self, slug, public=True):
        cal = self.env["resource.calendar"].create({
            "name": "24/7 %s" % slug, "tz": "UTC",
            "attendance_ids": [Command.create({
                "name": "j%d" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning"}) for d in range(7)]})
        resource = self.env["resource.resource"].create({
            "name": "Ressource %s" % slug, "calendar_id": cal.id,
            "resource_type": "user", "user_id": self.user.id, "tz": "UTC"})
        combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([resource.id])]})
        return self.env["resource.booking.type"].create({
            "name": "Type %s" % slug, "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cal.id, "is_public": public, "slug": slug,
            "combination_rel_ids": [Command.create({
                "sequence": 0, "combination_id": combo.id})]})

    def test_rendez_vous_suit_le_slug(self):
        """LA promesse du module : le QR imprimé survit au renommage."""
        self._besoin("resource.booking.type")
        booking_type = self._booking_type("rdv-suivi")
        link = self._link("appointment")
        self.assertTrue(link.resolved_url.endswith("/appointment/rdv-suivi"))
        booking_type.slug = "rdv-renomme"
        link.invalidate_recordset()
        self.assertTrue(
            link.resolved_url.endswith("/appointment/rdv-renomme"),
            "le lien doit suivre le slug, sinon le module n'a pas de raison d'être",
        )

    def test_rendez_vous_non_publie_ne_resout_pas(self):
        self._besoin("resource.booking.type")
        self._booking_type("rdv-interne", public=False)
        self.assertFalse(self._link("appointment").resolved_url,
                         "un type non publié n'a rien à faire sur une page publique")

    def test_rendez_vous_sans_utilisateur_ne_resout_pas(self):
        self._besoin("resource.booking.type")
        self._booking_type("rdv-orphelin")
        page = self.env["bf.linkpage"].create({
            "name": "Ponctuelle", "slug": "ponctuelle-prov", "kind": "oneoff"})
        link = self.env["bf.linkpage.link"].create({
            "page_id": page.id, "name": "L", "source_code": "appointment"})
        self.assertFalse(link.resolved_url)

    def test_depot_securise(self):
        self._besoin("secure.transfer.brand")
        self.env["secure.transfer.brand"].create({
            "name": "Marque", "slug": "titulaire-prov"})
        self.assertTrue(self._link("securetransfer").resolved_url
                        .endswith("/to/titulaire-prov"))

    def test_reference_douce_prime_sur_la_recherche(self):
        self._besoin("resource.booking.type")
        self._booking_type("rdv-trouve")
        designe = self._booking_type("rdv-designe")
        link = self._link("appointment", source_res_model="resource.booking.type",
                          source_res_id=designe.id)
        self.assertTrue(link.resolved_url.endswith("/appointment/rdv-designe"))

    def test_reference_douce_vers_un_disparu_ne_resout_pas(self):
        """Une référence douce n'a pas de contrainte d'intégrité : la cible peut
        disparaître sans que rien ne le signale."""
        self._besoin("resource.booking.type")
        booking_type = self._booking_type("rdv-a-supprimer")
        link = self._link("appointment", source_res_model="resource.booking.type",
                          source_res_id=booking_type.id)
        self.assertTrue(link.resolved_url)
        booking_type.unlink()
        link.invalidate_recordset()
        self.assertFalse(link.resolved_url)

    def test_une_reference_douce_vers_un_type_NON_PUBLIE_ne_resout_pas(self):
        """Le chemin que la recherche ne protège pas.

        Trouvé par mutation le 2026-08-30. La recherche filtre déjà
        `is_public = True`, donc le garde-fou final semblait redondant : le
        retirer ne faisait rougir aucun test. Il ne l'est pas — il est le SEUL
        rempart sur le chemin de la référence désignée, où un gestionnaire
        pointe explicitement un type. Sans lui, un type de rendez-vous interne
        atterrit sur une page publique.
        """
        self._besoin("resource.booking.type")
        interne = self._booking_type("rdv-interne-designe", public=False)
        link = self._link("appointment", source_res_model="resource.booking.type",
                          source_res_id=interne.id)
        self.assertFalse(
            link.resolved_url,
            "un type NON PUBLIÉ désigné à la main ne doit pas sortir en public",
        )

    def test_une_reference_douce_vers_un_type_publie_resout_bien(self):
        """Sans elle, le test précédent passerait aussi si la référence douce
        avait cessé de fonctionner tout court."""
        self._besoin("resource.booking.type")
        publie = self._booking_type("rdv-publie-designe")
        link = self._link("appointment", source_res_model="resource.booking.type",
                          source_res_id=publie.id)
        self.assertTrue(link.resolved_url.endswith("/appointment/rdv-publie-designe"))
