# -*- coding: utf-8 -*-
"""Liens de réservation personnels (2.42.0), et leur reprise (2.57.0)."""

import re
from ast import literal_eval
from datetime import timedelta
from unittest.mock import patch

from lxml import etree

from odoo import Command, fields
from odoo.osv import expression
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_onetime")
class TestOnetimeLink(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 lien", "attendance_ids": attendances, "tz": "UTC"})
        cls.resource = cls.env["resource.resource"].create({
            "name": "lien material", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC"})
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])]})
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type lien", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "is_public": True,
            "listed_on_landing": False,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id})],
        })
        cls.destinataire = cls.env["res.partner"].create({
            "name": "Destinataire", "email": "dest@test.invalid"})
        cls.invite = cls.env["res.partner"].create({
            "name": "Invité", "email": "invite@test.invalid"})

    def _wizard(self, **vals):
        base = {
            "type_id": self.booking_type.id,
            "partner_id": self.destinataire.id,
        }
        base.update(vals)
        return self.env["bf.appointment.onetime.wizard"].create(base)

    # -- Fabrication --------------------------------------------------------

    def test_wizard_creates_a_pending_booking_with_a_link(self):
        """Le lien n'a de sens que sur une réservation SANS créneau : c'est la
        personne qui choisira."""
        w = self._wizard()
        w.action_create_link()
        self.assertEqual(w.state, "done")
        booking = w.booking_id
        self.assertTrue(booking.exists())
        self.assertFalse(booking.start, "un lien de réservation ne fixe pas l'heure")
        self.assertNotEqual(booking.state, "confirmed")
        self.assertTrue(booking.access_token)
        self.assertEqual(booking.bf_source, "onetime")
        self.assertIn("/appointment/b/%d/%s/schedule" % (booking.id, booking.access_token),
                      w.url)

    def test_guests_are_attached_without_choosing_the_slot(self):
        w = self._wizard(guest_partner_ids=[Command.set([self.invite.id])])
        w.action_create_link()
        self.assertIn(self.destinataire, w.booking_id.partner_ids)
        self.assertIn(self.invite, w.booking_id.partner_ids)

    def test_overrides_are_applied(self):
        w = self._wizard(duration=2.5, location="Salle du fond")
        w.action_create_link()
        self.assertEqual(w.booking_id.duration, 2.5)
        self.assertEqual(w.booking_id.location, "Salle du fond")

    def test_expiry_is_set_from_days(self):
        w = self._wizard(expires_in_days=7)
        w.action_create_link()
        delta = w.booking_id.link_expires_at - fields.Datetime.now()
        self.assertGreater(delta, timedelta(days=6, hours=23))
        self.assertLess(delta, timedelta(days=7, minutes=1))

    def test_no_expiry_when_zero(self):
        w = self._wizard(expires_in_days=0)
        w.action_create_link()
        self.assertFalse(w.booking_id.link_expires_at)

    def test_non_public_type_is_refused_with_a_reason(self):
        """Un type non public rendrait un lien qui ne s'ouvre pas. Mieux vaut
        refuser à la fabrication qu'après l'envoi."""
        self.booking_type.is_public = False
        w = self._wizard()
        with self.assertRaises(UserError):
            w.action_create_link()

    # -- État du lien -------------------------------------------------------

    def test_fresh_link_is_active_and_usable(self):
        w = self._wizard()
        w.action_create_link()
        self.assertEqual(w.booking_id.link_state, "active")
        self.assertTrue(w.booking_id._link_is_usable())

    def test_expired_link_is_refused(self):
        w = self._wizard()
        w.action_create_link()
        w.booking_id.link_expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.assertEqual(w.booking_id.link_state, "expired")
        self.assertFalse(w.booking_id._link_is_usable())

    def test_single_use_link_closes_after_use(self):
        w = self._wizard(single_use=True)
        w.action_create_link()
        booking = w.booking_id
        self.assertTrue(booking._link_is_usable())
        booking._mark_link_used()
        self.assertEqual(booking.link_state, "used")
        self.assertFalse(booking._link_is_usable())

    def test_reusable_link_stays_open_after_use(self):
        w = self._wizard(single_use=False)
        w.action_create_link()
        booking = w.booking_id
        booking._mark_link_used()
        self.assertEqual(booking.link_state, "active",
                         "sans usage unique, le lien doit rester ouvert")
        self.assertTrue(booking._link_is_usable())

    def test_marking_twice_keeps_the_first_timestamp(self):
        w = self._wizard()
        w.action_create_link()
        booking = w.booking_id
        booking._mark_link_used()
        premier = booking.link_used_at
        booking._mark_link_used()
        self.assertEqual(booking.link_used_at, premier)

    def test_ordinary_bookings_are_not_link_governed(self):
        """🔴 L'invariant qui protège l'existant : une réservation prise sur le
        formulaire public n'a pas de lien à expirer, et les gardes ne doivent
        jamais s'appliquer à elle."""
        ordinaire = self.env["resource.booking"].create({
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([self.destinataire.id])],
        })
        self.assertEqual(ordinaire.link_state, "none")
        self.assertTrue(ordinaire._link_is_usable())
        ordinaire.link_expires_at = fields.Datetime.now() - timedelta(days=1)
        self.assertEqual(
            ordinaire.link_state, "none",
            "une expiration posée par erreur ne doit pas fermer une réservation "
            "ordinaire",
        )
        self.assertTrue(ordinaire._link_is_usable())

    def test_closed_link_template_exists(self):
        """La page qui explique doit exister : sans elle, le contrôleur lève."""
        self.assertTrue(self.env.ref(
            "bf_appointment.appointment_link_closed", raise_if_not_found=False))

    # -- Voir et filtrer les liens (2.68.0) ----------------------------------

    def _lien(self, **vals):
        """Un lien personnel semé directement, pour choisir son état."""
        base = {
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([self.destinataire.id])],
            "bf_source": "onetime",
        }
        base.update(vals)
        return self.env["resource.booking"].create(base)

    def _semis_des_quatre_etats(self):
        """Six liens qui couvrent les quatre états, préséance comprise."""
        passe = fields.Datetime.now() - timedelta(minutes=1)
        hier = fields.Datetime.now() - timedelta(days=1)
        self.ordinaire = self.env["resource.booking"].create({
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([self.destinataire.id])],
        })
        self.actif = self._lien()
        self.actif_servi = self._lien(link_single_use=False, link_used_at=hier)
        self.utilise = self._lien(link_single_use=True, link_used_at=hier)
        self.expire = self._lien(link_expires_at=passe)
        self.expire_et_servi = self._lien(
            link_expires_at=passe, link_single_use=True, link_used_at=hier)
        return (self.ordinaire | self.actif | self.actif_servi | self.utilise
                | self.expire | self.expire_et_servi)

    def _filtre(self, domaine, parmi):
        """Ce que le FILTRE rend, borné aux fiches semées par l'essai.

        🔴 Sans la borne, l'essai lirait aussi les liens de la base : sur un
        banc tiré de la production, une recherche nue rend des dizaines de
        fiches et l'essai passerait sans rien éprouver.
        """
        return self.env["resource.booking"].search(
            domaine + [("id", "in", parmi.ids)])

    def test_chaque_filtre_rend_exactement_ce_que_le_calcul_annonce(self):
        """🔴 Le contrôle qui compte : `search` est le MIROIR du calcul.

        Deux chemins écrivent la même règle — `_compute_link_state` en Python,
        `_bf_domaine_link_state` en SQL — et rien n'oblige le second à suivre
        le premier. Un essai qui appellerait le filtre pour compter ses lignes
        passerait avec un domaine faux ; ici chaque fiche semée est confrontée
        à SON état calculé, état par état, et le semis doit couvrir les quatre
        (sinon deux ensembles vides se ressembleraient).
        """
        semis = self._semis_des_quatre_etats()
        for etat in ("none", "active", "used", "expired"):
            attendu = semis.filtered(lambda b, e=etat: b.link_state == e)
            self.assertTrue(attendu, "le semis ne couvre pas l'état %s" % etat)
            self.assertEqual(
                self._filtre([("link_state", "=", etat)], semis), attendu,
                "le filtre « %s » ne rend pas ce que le calcul annonce" % etat)

    def test_un_lien_expire_et_servi_est_expire_des_deux_cotes(self):
        """La préséance du calcul doit se retrouver dans le domaine."""
        semis = self._semis_des_quatre_etats()
        self.assertEqual(self.expire_et_servi.link_state, "expired")
        self.assertIn(self.expire_et_servi,
                      self._filtre([("link_state", "=", "expired")], semis))
        self.assertNotIn(self.expire_et_servi,
                         self._filtre([("link_state", "=", "used")], semis))

    def test_un_lien_reutilisable_deja_servi_reste_actif(self):
        """Le piège du 2026-09-10 doit rester VISIBLE, donc filtrable."""
        semis = self._semis_des_quatre_etats()
        self.assertEqual(self.actif_servi.link_state, "active")
        self.assertIn(self.actif_servi,
                      self._filtre([("link_state", "=", "active")], semis))

    def test_la_negation_rend_le_complement(self):
        semis = self._semis_des_quatre_etats()
        for etat in ("none", "active", "used", "expired"):
            attendu = semis.filtered(lambda b, e=etat: b.link_state != e)
            self.assertEqual(
                self._filtre([("link_state", "!=", etat)], semis), attendu,
                "la négation de « %s » n'est pas son complément" % etat)
        self.assertEqual(
            self._filtre([("link_state", "in", ["used", "expired"])], semis),
            semis.filtered(lambda b: b.link_state in ("used", "expired")))
        self.assertEqual(
            self._filtre([("link_state", "not in", ["none"])], semis),
            semis.filtered(lambda b: b.link_state != "none"))

    def test_un_filtre_sans_valeur_ne_designe_personne(self):
        """Aucune réservation n'est « sans état » : chacune en porte un."""
        semis = self._semis_des_quatre_etats()
        self.assertFalse(self._filtre([("link_state", "=", False)], semis))
        self.assertEqual(self._filtre([("link_state", "!=", False)], semis),
                         semis)

    def test_un_operateur_non_pris_en_charge_le_dit_lisiblement(self):
        """Mieux vaut refuser que rendre une liste vide qui a l'air d'un fait.

        ⚠️ Depuis que le champ porte `search=`, il apparaît dans « Ajouter un
        filtre personnalisé » : choisir « contient » est un geste d'USAGER, pas
        un défaut de programmeur, et doit rendre un message, pas un traceback.
        L'état inconnu, lui, ne peut venir que du code : il garde sa levée nue.
        """
        with self.assertRaises(UserError):
            self.env["resource.booking"].search([("link_state", "ilike", "act")])
        with self.assertRaises(NotImplementedError):
            self.env["resource.booking"].search([("link_state", "=", "ouvert")])

    def test_les_etats_du_chercheur_sont_ceux_du_champ(self):
        """Une cinquième valeur ajoutée au champ et oubliée ici serait refusée."""
        champ = self.env["resource.booking"]._fields["link_state"]
        self.assertEqual(
            sorted(self.env["resource.booking"].ETATS_DU_LIEN),
            sorted(code for code, _libelle in champ.selection),
            "le chercheur et le champ ne connaissent pas les mêmes états")

    def test_les_filtres_d_etat_s_ajoutent_au_defaut_au_lieu_de_le_contredire(self):
        """🔴 Quatre filtres sur six rendaient ZÉRO, par construction.

        Odoo combine les filtres en OU dans un groupe et en ET entre les
        groupes (`search_model.js` : `Domain.or` par groupe, puis ET). Chaque
        filtre d'état isolé par un `<separator/>` s'ajoutait donc en ET à la
        facette par défaut « en attente, sans créneau » — qu'il CONTREDIT :
        « déjà utilisé » n'existe qu'après la confirmation d'un créneau,
        « rendez-vous déjà pris » exige un `start`, « annulés » un état annulé.
        L'écran rendait zéro ligne et affichait « Aucun lien… » sur des
        réservations bien réelles (3 sur BF au 2026-11-11).

        L'essai tient les deux bouts : le ET est bien vide — donc la structure
        de la vue décide — et l'arch ne porte AUCUN séparateur entre le défaut
        et le dernier filtre d'état, donc le client les met en OU.
        """
        vue = self.env.ref("bf_appointment.resource_booking_view_search_onetime")
        recherche = etree.fromstring(vue.arch)
        etats = ["liens_ouverts", "lien_actif", "lien_utilise", "lien_expire",
                 "lien_rdv_pris", "lien_annule"]

        rangs, separateurs = {}, []
        for i, noeud in enumerate(recherche):
            if noeud.tag == "filter" and noeud.get("name") in etats:
                rangs[noeud.get("name")] = i
            elif noeud.tag == "separator":
                separateurs.append(i)
        self.assertEqual(sorted(rangs), sorted(etats),
                         "un filtre d'état a disparu de la vue")
        debut, fin = min(rangs.values()), max(rangs.values())
        self.assertFalse(
            [i for i in separateurs if debut < i < fin],
            "un séparateur s'est glissé entre les filtres d'état : chacun "
            "s'ajouterait alors en ET à la facette par défaut, et ceux qui la "
            "contredisent rendraient zéro quelles que soient les données")

        defaut = literal_eval(
            recherche.xpath("//filter[@name='liens_ouverts']")[0].get("domain"))
        utilise = literal_eval(
            recherche.xpath("//filter[@name='lien_utilise']")[0].get("domain"))

        # ⚠️ Le semis doit avoir la forme que la PRODUCTION produit, pas une
        # forme commode : `_mark_link_used` n'est appelé qu'APRÈS la
        # confirmation du créneau (controllers/main.py), donc un lien « déjà
        # utilisé » porte toujours un créneau et n'est jamais « en attente ».
        # Un lien marqué utilisé sans créneau — ce que `_semis_des_quatre_etats`
        # fabrique pour éprouver le calcul — rendrait le ET non vide et cet
        # essai ne surveillerait plus rien.
        dort = self._lien()
        utilise_reel = self._lien(
            combination_id=self.combination.id,
            combination_auto_assign=False,
            duration=1.0,
            start=(fields.Datetime.now() + timedelta(days=5)).replace(
                minute=0, second=0, microsecond=0),
            link_single_use=True,
            link_used_at=fields.Datetime.now() - timedelta(hours=1),
        )
        self.assertEqual(utilise_reel.link_state, "used")
        self.assertNotEqual(utilise_reel.state, "pending")
        semis = dort | utilise_reel

        self.assertFalse(
            self._filtre(expression.AND([defaut, utilise]), semis),
            "le ET du défaut et de « déjà utilisé » ne peut rien rendre : "
            "c'est pourquoi ces filtres doivent s'ajouter en OU")
        self.assertEqual(
            self._filtre(expression.OR([defaut, utilise]), semis), semis,
            "le OU doit montrer le lien qui dort ET le lien déjà utilisé")

    def test_les_filtres_d_affinage_gardent_leur_propre_groupe(self):
        """L'envers du contrôle précédent : ceux-là doivent rester en ET.

        « Encore ouverts et toujours valides » et « Expire dans les 7 jours »
        RESSERRENT ce qui est montré. Glissés dans le groupe des états, ils
        l'élargiraient en silence — et une mutation l'a vérifié : les essais
        restaient verts.
        """
        recherche = etree.fromstring(self.env.ref(
            "bf_appointment.resource_booking_view_search_onetime").arch)
        enfants = list(recherche)
        rang = {n.get("name"): i for i, n in enumerate(enfants) if n.tag == "filter"}
        separateurs = [i for i, n in enumerate(enfants) if n.tag == "separator"]
        for avant, apres in (("lien_annule", "liens_ouverts_vivants"),
                             ("liens_ouverts_vivants", "lien_expire_bientot")):
            self.assertIn(avant, rang, "le filtre « %s » a disparu" % avant)
            self.assertIn(apres, rang, "le filtre « %s » a disparu" % apres)
            self.assertTrue(
                [i for i in separateurs if rang[avant] < i < rang[apres]],
                "« %s » doit rester séparé de « %s » : sans séparateur il "
                "élargit au lieu de resserrer" % (apres, avant))

    def test_les_vues_du_menu_ne_volent_pas_le_defaut_du_modele(self):
        """🔴 Une vue PRIMAIRE en priorité 16 devient le défaut du modèle.

        `ir.ui.view._order` vaut `priority, name, id` : à priorité égale c'est
        le NOM qui tranche, et « resource.booking.search.bf_appointment… »
        passe avant « resource.booking.view.search » d'OCA. L'écran
        « Bookings », dont l'action n'épingle aucune recherche, perdait alors
        sa vue de recherche, sa facette « Involving me » ET les filtres de
        consentement de Blue Fox — qui sont des extensions de la vue d'OCA,
        donc l'écran par lequel on trouve les réservations sans consentement.

        Rien ne regardait la priorité : c'est par là que le défaut est passé.
        """
        for nom in ("resource_booking_view_list_onetime",
                    "resource_booking_view_search_onetime",
                    "resource_booking_view_form_onetime"):
            vue = self.env.ref("bf_appointment.%s" % nom)
            self.assertEqual(
                vue.mode, "primary",
                "%s n'est plus une vue primaire : les attributs qu'elle pose "
                "s'appliqueraient alors à la vue d'OCA elle-même" % nom)
            self.assertGreater(
                vue.priority, 16,
                "%s est en priorité %d : elle peut voler le défaut du modèle"
                % (nom, vue.priority))

        for type_vue, xmlid in (
                ("list", "resource_booking.resource_booking_view_tree"),
                ("form", "resource_booking.resource_booking_view_form"),
                ("search", "resource_booking.resource_booking_view_search")):
            _arch, retenue = self.env["resource.booking"]._get_view(
                view_type=type_vue)
            self.assertEqual(
                retenue, self.env.ref(xmlid),
                "le défaut du modèle pour « %s » n'est plus celui d'OCA, mais "
                "« %s » : tout écran qui ne pinne pas sa vue le subit"
                % (type_vue, retenue.name))

    def test_le_formulaire_du_menu_n_offre_ni_creation_ni_suppression(self):
        """🔴 La liste le refusait ; le formulaire le rendait à côté.

        `getActiveActions` ne lit que l'arch de la vue COURANTE
        (`web/views/utils.js`) : le contexte de l'action n'y change rien. En
        cliquant une ligne, on atteignait le formulaire d'OCA avec son bouton
        « Nouveau » et son « Supprimer », et la réservation née là sortait de
        la liste par son domaine — exactement ce que la vue disait empêcher.

        L'essai lit l'arch COMBINÉE, pas la surcharge : c'est elle que le
        client reçoit, et elle doit garder les extensions BF du formulaire.
        """
        action = self.env.ref("bf_appointment.resource_booking_action_onetime")
        formulaire = self.env.ref(
            "bf_appointment.resource_booking_view_form_onetime")
        self.assertIn(
            ("form", formulaire.id),
            {(v.view_mode, v.view_id.id) for v in action.view_ids},
            "l'action n'impose pas sa variante de formulaire")

        combinee = etree.fromstring(
            self.env["resource.booking"].get_view(formulaire.id, "form")["arch"])
        self.assertEqual(combinee.get("create"), "0")
        self.assertEqual(combinee.get("delete"), "0")
        self.assertTrue(
            combinee.xpath("//field[@name='one_time_url']"),
            "la variante a perdu les extensions BF du formulaire")

    def test_le_filtre_ne_lit_aucune_fiche(self):
        """Le domaine doit être pur SQL, sur des champs STOCKÉS.

        `bf_consent_state` paie un `search` par couple (demandeur, type) ;
        celui-ci ne doit rien payer du tout, sinon la liste s'écroule le jour
        où les liens se comptent par milliers. Les QUATRE états sont regardés,
        et une feuille qui viserait un champ non stocké est refusée elle aussi :
        elle ferait lever la recherche au premier clic.
        """
        Res = self.env["resource.booking"]
        for etat in Res.ETATS_DU_LIEN:
            for feuille in Res._bf_domaine_link_state(etat):
                if not isinstance(feuille, (list, tuple)):
                    continue  # un opérateur « | » ou « & »
                self.assertNotEqual(
                    feuille[0], "id",
                    "le domaine de « %s » passe par une liste d'ids : il a lu "
                    "des fiches" % etat)
                self.assertTrue(
                    Res._fields[feuille[0]].store,
                    "le domaine de « %s » vise %s, qui n'est pas stocké"
                    % (etat, feuille[0]))

    def test_l_action_du_menu_vise_les_liens_et_s_ouvre_sur_ceux_qui_dorment(self):
        """L'écran promis par la napkin : le menu, son action, son défaut."""
        action = self.env.ref("bf_appointment.resource_booking_action_onetime")
        self.assertEqual(action.res_model, "resource.booking")
        self.assertEqual(literal_eval(action.domain),
                         [("bf_source", "=", "onetime")])
        self.assertEqual(
            literal_eval(action.context).get("search_default_liens_ouverts"), 1,
            "l'écran doit s'ouvrir sur les liens qui dorment")
        menu = self.env.ref("bf_appointment.menu_appointment_onetime_links")
        self.assertEqual(menu.action, action,
                         "le menu ne pointe pas sur l'action des liens")
        self.assertTrue(self.env.ref(
            "bf_appointment.menu_appointment_onetime",
            raise_if_not_found=False),
            "l'assistant garde son propre menu (arbitrage du 2026-11-11)")

    def test_le_defaut_lu_DANS_la_vue_ne_montre_que_les_liens_qui_dorment(self):
        """🔴 Le domaine est lu dans la vue livrée, pas retapé ici.

        Un essai qui recopie le domaine du filtre éprouve sa propre copie :
        changer la vue ne le ferait pas rougir. Celui-ci lit l'arch servi par
        la base, donc il tombe si le filtre est renommé ou retouché.
        """
        vue = self.env.ref("bf_appointment.resource_booking_view_search_onetime")
        filtres = etree.fromstring(vue.arch).xpath(
            "//filter[@name='liens_ouverts']")
        self.assertTrue(filtres, "le filtre par défaut a disparu de la vue")
        domaine = literal_eval(filtres[0].get("domain"))

        dort = self._lien()
        pris = self._lien(
            combination_id=self.combination.id,
            combination_auto_assign=False,
            duration=1.0,
            start=(fields.Datetime.now() + timedelta(days=3)).replace(
                minute=0, second=0, microsecond=0),
        )
        self.assertEqual(pris.state, "scheduled", "le semis n'a pas réservé")
        rendu = self._filtre(domaine, dort | pris)
        self.assertEqual(rendu, dort,
                         "le défaut doit montrer le lien qui dort, et lui seul")

    def test_le_defaut_tient_le_cas_du_creneau_sans_evenement(self):
        """🔴 La seconde feuille du défaut (`start = False`) est une GARDE.

        Mesuré le 2026-11-12, uid 2 : aucune réservation de BF ni de PMEC n'est
        « en attente » avec un créneau, archivées comprises. OCA dérive l'état
        du `meeting_id` (`_compute_state`), jamais du `start`. Une mutation qui
        retire cette feuille survit donc à tout semis ordinaire — et ça ne dit
        pas que la garde est morte, ça dit qu'aucun contrôle ne la regarde.

        Le cas qu'elle protège existe : un événement d'agenda supprimé sous une
        réservation laisse le créneau en base et l'état retombé à « en
        attente ». On le FORGE en SQL, parce que l'ORM refuse de le produire —
        `_compute_start` relirait l'événement absent et effacerait le créneau.
        L'écran, lui, ne doit pas présenter ce lien comme endormi.
        """
        vue = self.env.ref("bf_appointment.resource_booking_view_search_onetime")
        filtres = etree.fromstring(vue.arch).xpath(
            "//filter[@name='liens_ouverts']")
        self.assertTrue(filtres, "le filtre par défaut a disparu de la vue")
        domaine = literal_eval(filtres[0].get("domain"))

        dort = self._lien()
        orphelin = self._lien(
            combination_id=self.combination.id,
            combination_auto_assign=False,
            duration=1.0,
            start=(fields.Datetime.now() + timedelta(days=3)).replace(
                minute=0, second=0, microsecond=0),
        )
        self.assertTrue(orphelin.meeting_id, "le semis n'a pas réservé")
        creneau = orphelin.start
        # 🔴 Vider les écritures en attente AVANT le SQL brut : sans ce vidage,
        # la première lecture d'après déclenche le flush de l'ORM, qui réécrit
        # la ligne avec ses valeurs en mémoire — « scheduled » — et chasse le
        # semis forgé. L'essai rougissait en annonçant un semis raté alors que
        # c'était la lecture qui l'avait défait.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE resource_booking SET meeting_id = NULL, state = 'pending', "
            "start = %s WHERE id = %s", (creneau, orphelin.id))
        orphelin.invalidate_recordset()
        self.assertEqual(orphelin.state, "pending", "le semis forgé a raté")
        self.assertEqual(orphelin.start, creneau, "le créneau devait survivre")

        rendu = self._filtre(domaine, dort | orphelin)
        self.assertEqual(
            rendu, dort,
            "un lien dont le créneau survit à son événement ne dort pas : le "
            "défaut de l'écran ne doit pas le présenter comme tel")

    def test_la_liste_du_menu_n_offre_ni_creation_ni_suppression(self):
        """Un « Nouveau » d'ici rendrait une réservation hors de la liste."""
        vue = self.env.ref("bf_appointment.resource_booking_view_list_onetime")
        liste = etree.fromstring(vue.arch)
        self.assertEqual(liste.get("create"), "0")
        self.assertEqual(liste.get("delete"), "0")


@tagged("bf_appointment", "bf_appointment_cancel_link")
class TestCancelLinkIsReachable(TransactionCase):
    """Le lien « Annuler » de l'ICS se clique depuis l'agenda, donc en GET."""

    def test_cancel_url_in_ics_points_at_a_reachable_route(self):
        """🔴 L'ICS écrit « Annuler : <url> » : cette URL doit aboutir.

        Elle rendait un 405 brut de Werkzeug — la route n'acceptait que POST.
        Signalé le 2026-08-20. Le contrôle vérifie ici que la route déclare
        bien GET; le comportement de la page est éprouvé par sonde HTTP.
        """
        from odoo.addons.bf_appointment.controllers.main import AppointmentController

        route = AppointmentController.appointment_cancel.original_routing
        self.assertIn("GET", route["methods"],
                      "le lien d'annulation d'un courriel se clique en GET")
        self.assertIn("POST", route["methods"],
                      "la mutation doit rester possible en POST")

    def test_cancel_confirm_template_exists(self):
        self.assertTrue(self.env.ref(
            "bf_appointment.appointment_cancel_confirm", raise_if_not_found=False))


@tagged("bf_appointment", "bf_appointment_quicklink")
class TestQuickBookingLink(TransactionCase):
    """Raccourcis : depuis le compositeur de courriel et depuis un contact."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        att = [Command.create({"name": "j%s" % d, "dayofweek": str(d),
                               "hour_from": 0.0, "hour_to": 24.0,
                               "day_period": "morning"}) for d in range(7)]
        cal = cls.env["resource.calendar"].create({
            "name": "24/7 quick", "attendance_ids": att, "tz": "UTC"})
        res = cls.env["resource.resource"].create({
            "name": "quick mat", "calendar_id": cal.id,
            "resource_type": "material", "tz": "UTC"})
        combo = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([res.id])]})
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type rapide", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cal.id, "is_public": True,
            "listed_on_landing": True,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": combo.id})]})
        cls.env.company.appointment_quick_link_type_id = cls.booking_type
        cls.client = cls.env["res.partner"].create({
            "name": "Client rapide", "email": "rapide@test.invalid"})

    def _composer(self, **vals):
        base = {"partner_ids": [Command.set([self.client.id])],
                "body": "<p>Bonjour,</p>"}
        base.update(vals)
        return self.env["mail.compose.message"].create(base)

    def test_composer_appends_a_personal_link(self):
        c = self._composer()
        c.action_bf_insert_booking_link()
        self.assertIn("Bonjour", c.body, "le texte déjà écrit doit survivre")
        booking = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        self.assertIn(booking.access_token, c.body, "le lien n'est pas dans le corps")
        self.assertIn(self.client, booking.partner_ids)
        self.assertFalse(booking.start, "un lien ne fixe pas l'heure")

    def test_link_is_not_escaped(self):
        """🔴 Le défaut le plus visible : les balises en clair dans le courriel.

        `body` est un `Markup` ; y concaténer une `str` échappe la `str`, et le
        rédacteur voit « &lt;p&gt;&lt;a href… » au lieu d'un lien. L'écriture
        réussit, aucune exception : seul un contrôle sur le contenu l'attrape.
        """
        c = self._composer()
        c.action_bf_insert_booking_link()
        corps = str(c.body)
        self.assertNotIn("&lt;", corps, "des balises ont été échappées")
        self.assertNotIn("&gt;", corps, "des balises ont été échappées")
        # Le banc sert en http, la prod en https : on vérifie la BALISE, pas
        # le protocole, sinon le test échoue là où le code est bon.
        self.assertIn('<a href="http', corps, "le lien n'est pas du HTML")
        self.assertRegex(corps, r'<a href="[^"]+/appointment/b/\d+/[^"]+/schedule"')

    def test_link_lands_before_the_signature(self):
        """Sous la signature, le lien se lit comme une note de bas de page."""
        signature = '<div><table style="border:0"><tr><td>Jane Doe</td></tr></table></div>'
        self.env.user.signature = signature
        c = self._composer(body="<p>Bonjour,</p><br>" + signature)
        c.action_bf_insert_booking_link()
        corps = str(c.body)
        i_lien = corps.find("/appointment/b/")
        i_sig = corps.find("Jane Doe")
        self.assertGreater(i_lien, 0, "lien absent")
        self.assertGreater(i_sig, 0, "signature absente")
        self.assertLess(i_lien, i_sig, "le lien est passé SOUS la signature")
        self.assertTrue(corps.startswith("<p>Bonjour,</p>"),
                        "le texte déjà écrit doit rester en tête")

    def test_short_signature_is_still_found(self):
        """🔴 Une signature courte doit être trouvée comme une longue.

        Les tailles de préfixe essayées étaient 400, 200, 100 et 60, chacune
        sautée si la signature est plus courte : une signature compactée sous
        60 caractères ne déclenchait aucun essai et le lien retombait à la fin,
        en silence. Invisible en production, où les signatures font des
        milliers de caractères.
        """
        courte = "<div><b>Jane Doe</b></div>"
        self.env.user.signature = courte
        c = self._composer(body="<p>Salut,</p><br>" + courte)
        c.action_bf_insert_booking_link()
        corps = str(c.body)
        self.assertLess(corps.find("/appointment/b/"), corps.find("Jane Doe"),
                        "une signature courte n'a pas été repérée")

    def test_link_lands_before_a_quoted_reply(self):
        c = self._composer(
            body='<p>Ma réponse</p><blockquote>Le message d\'origine</blockquote>')
        c.action_bf_insert_booking_link()
        corps = str(c.body)
        self.assertLess(corps.find("/appointment/b/"), corps.find("<blockquote"),
                        "le lien doit précéder l'historique cité")

    def test_link_appended_when_there_is_no_signature(self):
        self.env.user.signature = False
        c = self._composer(body="<p>Court message</p>")
        c.action_bf_insert_booking_link()
        corps = str(c.body)
        self.assertTrue(corps.startswith("<p>Court message</p>"))
        self.assertIn("/appointment/b/", corps)

    def test_composer_never_overwrites_the_body(self):
        c = self._composer(body="<p>Texte important</p>")
        c.action_bf_insert_booking_link()
        self.assertTrue(c.body.startswith("<p>Texte important</p>"),
                        "le lien doit s'AJOUTER, jamais remplacer")

    def test_composer_without_recipient_says_so(self):
        c = self.env["mail.compose.message"].create({"body": "<p>x</p>"})
        with self.assertRaises(UserError):
            c.action_bf_insert_booking_link()

    def test_composer_recipient_without_email_is_refused(self):
        muet = self.env["res.partner"].create({"name": "Sans courriel"})
        c = self._composer(partner_ids=[Command.set([muet.id])])
        with self.assertRaises(UserError):
            c.action_bf_insert_booking_link()

    def test_configured_type_wins_over_the_first_listed(self):
        autre = self.booking_type.copy({
            "name": "Autre type", "slug": "autre-type-quick", "sequence": 1})
        self.env.company.appointment_quick_link_type_id = autre
        c = self._composer()
        c.action_bf_insert_booking_link()
        booking = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        self.assertEqual(booking.type_id, autre,
                         "le réglage de société doit primer")

    def test_falls_back_to_first_public_type(self):
        self.env.company.appointment_quick_link_type_id = False
        c = self._composer()
        c.action_bf_insert_booking_link()
        booking = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        self.assertTrue(booking.type_id.is_public)

    def test_partner_button_opens_the_copy_dialog(self):
        action = self.client.action_bf_booking_link()
        self.assertEqual(action["res_model"], "bf.appointment.onetime.wizard")
        assistant = self.env["bf.appointment.onetime.wizard"].browse(action["res_id"])
        self.assertEqual(assistant.state, "done")
        booking = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        self.assertIn(booking.access_token, assistant.url)

    def test_copy_button_leaves_the_body_untouched(self):
        """Copier n'écrit rien dans le message : c'est tout son intérêt."""
        c = self._composer(body="<p>Mon texte</p>")
        avant = str(c.body)
        c.action_bf_copy_booking_link()
        self.assertEqual(str(c.body), avant, "le corps a été modifié")
        self.assertIn("/appointment/b/", c.bf_booking_link_url)
        self.assertIn(c.bf_booking_id.access_token, c.bf_booking_link_url)

    def test_copy_button_keeps_the_composer_open(self):
        """🔴 Le courriel en cours ne doit PAS se fermer.

        Le bouton rendait la fenêtre du lien, et le client retire le dialogue
        courant avant d'ouvrir le suivant : le brouillon disparaissait de
        l'écran, la personne le tenait pour perdu. Signalé le 2026-08-25.
        """
        c = self._composer(body="<p>Mon texte</p>")
        action = c.action_bf_copy_booking_link()
        self.assertEqual(action["res_model"], "mail.compose.message",
                         "l'action doit ramener au courriel, pas ailleurs")
        self.assertEqual(action["res_id"], c.id, "un AUTRE brouillon a été ouvert")
        self.assertEqual(action["target"], "new")

    def test_insert_button_keeps_the_composer_open(self):
        c = self._composer(body="<p>Mon texte</p>")
        action = c.action_bf_insert_booking_link()
        self.assertEqual(action["res_model"], "mail.compose.message")
        self.assertEqual(action["res_id"], c.id)

    def test_copy_button_reuses_its_link_on_a_second_click(self):
        """Deux clics ne doivent pas laisser deux réservations en attente.

        Le lien reste maintenant à l'écran : recliquer est naturel, et sans
        garde chaque clic fabriquait une réservation de plus.
        """
        c = self._composer()
        c.action_bf_copy_booking_link()
        premier = c.bf_booking_id
        avant = self.env["resource.booking"].search_count(
            [("bf_source", "=", "onetime")])
        c.action_bf_copy_booking_link()
        self.assertEqual(c.bf_booking_id, premier, "le lien a changé sans raison")
        self.assertEqual(
            self.env["resource.booking"].search_count(
                [("bf_source", "=", "onetime")]),
            avant, "un deuxième clic a créé une réservation de plus")

    def test_copy_button_refreshes_the_link_for_a_new_recipient(self):
        """Le lien est PERSONNEL : changer de destinataire doit en refaire un."""
        c = self._composer()
        c.action_bf_copy_booking_link()
        premier = c.bf_booking_id
        autre = self.env["res.partner"].create(
            {"name": "Autre client", "email": "autre@test.invalid"})
        c.partner_ids = [Command.set([autre.id])]
        c.action_bf_copy_booking_link()
        self.assertNotEqual(c.bf_booking_id, premier,
                            "le lien du premier destinataire a été recyclé")
        self.assertIn(autre, c.bf_booking_id.partner_ids)

    def test_composer_form_carries_the_link_zone(self):
        """🔴 Deux harnais valent mieux qu'un : le modèle PUIS l'écran.

        Le modèle peut être juste et l'écran rester muet — un xpath qui ne
        s'applique plus, et le lien se fabrique sans que personne le voie.
        """
        arch = self.env["mail.compose.message"].get_view(
            self.env.ref("mail.email_compose_message_wizard_form").id, "form")["arch"]
        self.assertIn("bf_booking_link_url", arch,
                      "la zone du lien a disparu du compositeur")
        self.assertIn("CopyClipboardURL", arch,
                      "sans le widget natif, il n'y a plus de bouton copier")
        self.assertIn("action_bf_copy_booking_link", arch)

    def test_copy_button_announces_the_expiry(self):
        c = self._composer()
        c.action_bf_copy_booking_link()
        self.assertTrue(c.bf_booking_link_expires,
                        "l'expiration doit être annoncée dans le courriel")
        self.assertEqual(c.bf_booking_link_expires,
                         c.bf_booking_id.link_expires_at)

    def test_both_buttons_share_the_same_sequence(self):
        """🔴 Insérer et copier doivent produire le MÊME lien.

        Deux copies de la séquence divergeraient au premier ajustement.
        """
        c1 = self._composer()
        c1.action_bf_insert_booking_link()
        par_insertion = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        c2 = self._composer()
        c2.action_bf_copy_booking_link()
        par_copie = c2.bf_booking_id
        self.assertNotEqual(par_insertion, par_copie, "deux liens distincts attendus")
        for champ in ("type_id", "bf_source", "link_single_use"):
            self.assertEqual(par_insertion[champ], par_copie[champ], champ)
        self.assertEqual(par_insertion.partner_ids, par_copie.partner_ids)

    def test_partner_dialog_shows_the_expiry(self):
        """Depuis une fiche de contact, la fenêtre reste le bon véhicule :
        il n'y a pas de brouillon à préserver derrière."""
        action = self.client.action_bf_booking_link()
        assistant = self.env["bf.appointment.onetime.wizard"].browse(action["res_id"])
        self.assertTrue(assistant.expires_display)

    def test_shared_factory_is_the_single_source(self):
        """🔴 L'assistant, le compositeur et le contact doivent produire la
        MÊME chose. Trois copies de la séquence divergeraient au premier
        ajustement."""
        w = self.env["bf.appointment.onetime.wizard"].create({
            "type_id": self.booking_type.id, "partner_id": self.client.id})
        w.action_create_link()
        par_assistant = w.booking_id
        c = self._composer()
        c.action_bf_insert_booking_link()
        par_courriel = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        for champ in ("bf_source", "link_single_use", "type_id"):
            self.assertEqual(par_assistant[champ], par_courriel[champ], champ)
        self.assertFalse(par_assistant.start)
        self.assertFalse(par_courriel.start)


@tagged("bf_appointment", "bf_appointment_reprise")
class TestRepriseDuLienPersonnel(TransactionCase):
    """Le lien personnel actif d'une personne est repris, pas doublé (2.57.0).

    Le courriel qui porte un lien personnel porte AUSSI celui de la signature :
    la personne qui clique le second réservait jusqu'ici à côté de son propre
    lien, sans le titre écrit pour elle et en laissant le lien ouvert derrière.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 reprise", "attendance_ids": attendances, "tz": "UTC"})
        cls.resource = cls.env["resource.resource"].create({
            "name": "reprise material", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC"})
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])]})
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type reprise", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "is_public": True,
            "listed_on_landing": False, "video_provider": "none",
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id})],
        })
        cls.autre_type = cls.env["resource.booking.type"].create({
            "name": "Autre type reprise", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "is_public": True,
            "listed_on_landing": False, "video_provider": "none",
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id})],
        })
        cls.destinataire = cls.env["res.partner"].create({
            "name": "Destinataire reprise", "email": "reprise@test.invalid"})
        cls.etranger = cls.env["res.partner"].create({
            "name": "Quelqu'un d'autre", "email": "autre@test.invalid"})

    def _reprise(self, booking_type=None, partner=None):
        return self.env["resource.booking"]._bf_find_onetime_to_reuse(
            booking_type or self.booking_type, partner or self.destinataire)

    def test_le_lien_actif_est_repris(self):
        lien = self.booking_type._bf_create_onetime_link(self.destinataire)
        self.assertEqual(self._reprise(), lien)

    def test_le_plus_recent_prime(self):
        """L'organisateur qui refait un lien vient de corriger le précédent."""
        self.booking_type._bf_create_onetime_link(self.destinataire)
        second = self.booking_type._bf_create_onetime_link(self.destinataire)
        self.assertEqual(self._reprise(), second)

    def test_le_lien_expire_n_est_pas_repris(self):
        lien = self.booking_type._bf_create_onetime_link(self.destinataire)
        lien.link_expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.assertFalse(self._reprise())

    def test_le_lien_a_usage_unique_deja_consomme_n_est_pas_repris(self):
        lien = self.booking_type._bf_create_onetime_link(
            self.destinataire, single_use=True)
        lien._mark_link_used()
        self.assertFalse(self._reprise())

    def test_un_autre_type_ne_donne_rien(self):
        """Le lien vaut pour SON type : disponibilités, durée, courriels."""
        self.booking_type._bf_create_onetime_link(self.destinataire)
        self.assertFalse(self._reprise(booking_type=self.autre_type))

    def test_une_autre_personne_ne_donne_rien(self):
        self.booking_type._bf_create_onetime_link(self.destinataire)
        self.assertFalse(self._reprise(partner=self.etranger))

    def test_une_reservation_ordinaire_n_est_jamais_reprise(self):
        """🔴 L'invariant qui protège l'existant : une réservation prise au
        formulaire public n'est pas un lien, et ne doit jamais être resservie
        au visiteur suivant qui donne la même adresse."""
        self.env["resource.booking"].create({
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([self.destinataire.id])],
        })
        self.assertFalse(self._reprise())

    def test_un_lien_deja_planifie_n_est_pas_repris(self):
        """Une fois le créneau choisi, le lien a produit sa rencontre : la
        reprendre écraserait un rendez-vous qui existe."""
        lien = self.booking_type._bf_create_onetime_link(self.destinataire)
        lien.start = fields.Datetime.now() + timedelta(days=1)
        self.assertTrue(lien.meeting_id, "un créneau choisi crée l'événement")
        self.assertFalse(self._reprise())


@tagged("post_install", "-at_install", "bf_appointment", "bf_appointment_reprise")
class TestRepriseParLeFormulairePublic(HttpCase):
    """Le vrai chemin : un POST sur la page publique, pas un appel de méthode.

    C'est là que le défaut est né — le contrôleur créait sans regarder ce que
    la personne détenait déjà.
    """

    def setUp(self):
        super().setUp()
        # ⚠️ Routes `website=True` : sans le module `website`, rien n'est routé
        # et tout rend 404. Le module n'en dépend pas volontairement.
        if not self.env["ir.module.module"].sudo().search_count(
                [("name", "=", "website"), ("state", "=", "installed")]):
            self.skipTest(
                "module `website` absent : les routes `website=True` ne sont "
                "pas routées, ce test n'a rien à mesurer")
        attendances = [
            Command.create({
                "name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        calendrier = self.env["resource.calendar"].create({
            "name": "24/7 reprise HTTP", "attendance_ids": attendances, "tz": "UTC"})
        ressource = self.env["resource.resource"].create({
            "name": "reprise HTTP material", "calendar_id": calendrier.id,
            "resource_type": "material", "tz": "UTC"})
        combinaison = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([ressource.id])]})
        self.type_rdv = self.env["resource.booking.type"].create({
            "name": "Type reprise HTTP", "slug": "type-reprise-http",
            "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": calendrier.id, "video_provider": "none",
            "requires_recording_consent": False,
            "sends_intake_acknowledgement": False,
            "is_public": True, "listed_on_landing": False,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": combinaison.id})],
        })
        self.destinataire = self.env["res.partner"].create({
            "name": "Reprise HTTP", "email": "reprise-http@test.invalid"})
        self.lien = self.type_rdv._bf_create_onetime_link(self.destinataire)
        self.lien.name = "Revue des principes | Client x Blue Fox"
        self.env.cr.flush()

    def test_le_formulaire_public_renvoie_le_lien_par_courriel_et_garde_le_titre(self):
        Reservation = self.env["resource.booking"]
        avant = Reservation.search_count([("type_id", "=", self.type_rdv.id)])
        page = self.url_open("/appointment/%s" % self.type_rdv.slug)
        self.assertEqual(page.status_code, 200)
        jeton = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
        self.assertTrue(jeton, "le formulaire doit porter son jeton CSRF")
        envois = []
        Modele = type(self.env["resource.booking"])

        def _envoi(rec, template, attach_ics=True, recipient=None):
            envois.append(rec.id)

        with patch.object(Modele, "_send_appointment_email", _envoi):
            reponse = self.url_open(
                "/appointment/%s/book" % self.type_rdv.slug,
                data={
                    "csrf_token": jeton.group(1),
                    "name": "Reprise HTTP",
                    "email": "reprise-http@test.invalid",
                    "bf_consent": "on",
                },
            )
        self.assertEqual(reponse.status_code, 200)
        # 🔴 l'adresse seule ne livre plus le lien (il vaut jeton).
        self.assertNotIn(self.lien.access_token, reponse.url,
                         "le jeton du lien ne sort jamais sur une simple adresse")
        self.assertNotIn(self.lien.access_token, reponse.text)
        self.assertEqual(envois, [self.lien.id],
                         "le lien part dans la boîte de l'adresse au dossier")
        self.assertEqual(
            avant, Reservation.search_count([("type_id", "=", self.type_rdv.id)]),
            "aucune réservation neuve à côté du lien")
        self.lien.invalidate_recordset()
        self.assertEqual(self.lien.name, "Revue des principes | Client x Blue Fox",
                         "le titre écrit par l'organisateur survit au formulaire")
