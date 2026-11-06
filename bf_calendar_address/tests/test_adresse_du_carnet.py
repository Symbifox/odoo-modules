# -*- coding: utf-8 -*-
"""Le lieu d'une rencontre, pris dans le carnet d'adresses.

Les comptes et les adresses sont FICTIFS. Le scénario : une visite chez un
membre dont l'usine a plus d'une adresse au carnet.
"""

from datetime import timedelta

from odoo import Command, fields
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_calendar_address")
class TestAdresseDuCarnet(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, no_mail_to_attendees=True,
            skip_nc_sync=True))
        cls.Event = cls.env["calendar.event"]
        cls.Partner = cls.env["res.partner"]
        cls.ca = cls.env.ref("base.ca")
        Etat = cls.env["res.country.state"]
        cls.qc = Etat.search(
            [("country_id", "=", cls.ca.id), ("code", "=", "QC")], limit=1)
        if not cls.qc:
            cls.qc = Etat.create({
                "name": "Québec", "code": "QC", "country_id": cls.ca.id})
        cls.env.company.country_id = cls.ca

        cls.membre = cls.Partner.create({
            "name": "Fonderie Bellechasse",
            "is_company": True,
            "street": "128 rue des Forges",
            "city": "Sherbrooke",
            "state_id": cls.qc.id,
            "zip": "J1H 4M2",
            "country_id": cls.ca.id,
        })
        cls.plant3 = cls.Partner.create({
            "name": "Usine 3",
            "type": "other",
            "parent_id": cls.membre.id,
            "street": "4000 chemin des Cantons",
            "city": "Magog",
            "state_id": cls.qc.id,
            "zip": "J1X 0B1",
            "country_id": cls.ca.id,
        })
        cls.contact = cls.Partner.create({
            "name": "Lambert Nadeau",
            "parent_id": cls.membre.id,
            "email": "lambert.nadeau@example.invalid",
        })
        cls.interne = cls.env["res.users"].create({
            "name": "Conseiller du essai",
            "login": "conseiller.essai",
            "street": "1 rue Interne",
            "city": "Québec",
            "state_id": cls.qc.id,
            "zip": "G1R 1A1",
            "country_id": cls.ca.id,
        }).partner_id

    SIEGE = "128 rue des Forges, Sherbrooke QC J1H 4M2"
    USINE3 = "4000 chemin des Cantons, Magog QC J1X 0B1"

    def _rencontre(self, **vals):
        base = {
            "name": "Visite du essai",
            "start": "2026-11-18 14:00:00",
            "stop": "2026-11-18 15:30:00",
        }
        base.update(vals)
        return self.Event.create(base)

    # ------------------------------------------------------------ remplissage

    def test_le_lieu_vient_du_carnet_a_la_creation(self):
        """Le chemin du lien de réservation : les invités sont dans les vals."""
        event = self._rencontre(partner_ids=[Command.set([self.contact.id])])
        self.assertEqual(event.location, self.SIEGE)
        self.assertEqual(
            event.bf_address_partner_id, self.membre,
            "c'est l'adresse de la société, et c'est elle qu'on nomme",
        )

    def test_la_meme_adresse_ne_sort_quune_fois(self):
        """Odoo recopie l'adresse d'une société sur ses contacts.

        Le contact et sa société portent donc le même texte : l'offrir deux
        fois dans le menu ne donne aucun choix, et laisse deviner laquelle des
        deux fiches a servi.
        """
        self.assertEqual(
            self.contact.bf_address_one_line(), self.SIEGE,
            "le contact porte l'adresse recopiée de sa société",
        )
        candidats = self.contact._bf_address_candidats()
        self.assertEqual(candidats, self.membre | self.plant3)
        self.assertNotIn(self.contact, candidats)

    def test_le_pays_de_la_maison_ne_se_repete_pas(self):
        etranger = self.Partner.create({
            "name": "Membre d'ailleurs",
            "is_company": True,
            "street": "12 Rue de la Paix",
            "city": "Paris",
            "zip": "75002",
            "country_id": self.env.ref("base.fr").id,
        })
        self.assertNotIn("Canada", self.membre.bf_address_one_line(),
                         "« Canada » à la fin de chaque lieu est du bruit")
        self.assertIn("France", etranger.bf_address_one_line(),
                      "hors du pays, c'est la partie qui compte")

    def test_une_ville_sans_rue_nest_pas_une_adresse(self):
        sans_rue = self.Partner.create({
            "name": "Membre sans rue",
            "is_company": True,
            "city": "Victoriaville",
            "state_id": self.qc.id,
            "country_id": self.ca.id,
        })
        self.assertEqual(sans_rue.bf_address_one_line(), "")
        event = self._rencontre(partner_ids=[Command.set([sans_rue.id])])
        self.assertFalse(event.location, "une ville seule n'envoie personne nulle part")
        self.assertFalse(event.bf_address_partner_id)

    def test_defaut_du_contexte_vu_malgre_tout(self):
        """Ouvrir une rencontre depuis une fiche contact : l'invité est un défaut."""
        event = self.Event.with_context(
            default_partner_ids=[Command.set([self.contact.id])]
        ).create({
            "name": "Visite du essai (défaut)",
            "start": "2026-11-18 14:00:00",
            "stop": "2026-11-18 15:30:00",
        })
        self.assertEqual(event.partner_ids, self.contact)
        self.assertEqual(
            event.location, self.SIEGE,
            "un on-change ne voit jamais les défauts du contexte",
        )
        self.assertEqual(event.bf_address_partner_id, self.membre)

    # --------------------------------------------------------- ce qu'on laisse

    def test_un_lieu_fourni_nest_jamais_remplace(self):
        event = self._rencontre(
            partner_ids=[Command.set([self.contact.id])],
            location="Chez le notaire, 5 rue Principale",
        )
        self.assertEqual(event.location, "Chez le notaire, 5 rue Principale")
        self.assertFalse(event.bf_address_partner_id)

    def test_un_lieu_par_defaut_du_contexte_nest_jamais_remplace(self):
        event = self.Event.with_context(
            default_location="Salle du conseil",
        ).create({
            "name": "Visite du essai (lieu par défaut)",
            "start": "2026-11-18 14:00:00",
            "stop": "2026-11-18 15:30:00",
            "partner_ids": [Command.set([self.contact.id])],
        })
        self.assertEqual(
            event.location, "Salle du conseil",
            "écrire notre lieu annulerait en silence le défaut du contexte",
        )

    def test_une_visio_nest_pas_un_endroit_ou_se_rendre(self):
        event = self._rencontre(
            partner_ids=[Command.set([self.contact.id])],
            videocall_location="https://meet.example.invalid/essai",
        )
        self.assertFalse(event.location)
        self.assertFalse(event.bf_address_partner_id)

    def test_nos_propres_adresses_ne_sont_pas_le_lieu(self):
        event = self._rencontre(partner_ids=[Command.set([self.interne.id])])
        self.assertFalse(event.location, "l'adresse d'un collègue n'est pas une destination")
        event2 = self._rencontre(
            partner_ids=[Command.set([self.env.company.partner_id.id])])
        self.assertFalse(event2.location)

    def test_une_adresse_de_facturation_est_offerte_jamais_choisie(self):
        """Elle est une vraie adresse, mais ce n'est pas là qu'on va."""
        siege_social = self.Partner.create({
            "name": "Coopérative Yamaska",
            "is_company": True,
            "city": "Granby",
            "state_id": self.qc.id,
            "country_id": self.ca.id,
        })
        facturation = self.Partner.create({
            "name": "Comptes payables",
            "type": "invoice",
            "parent_id": siege_social.id,
            "street": "C.P. 4000, succursale Centre-ville",
            "city": "Granby",
            "state_id": self.qc.id,
            "zip": "J2G 9H1",
            "country_id": self.ca.id,
        })
        candidats = siege_social._bf_address_candidats()
        self.assertIn(facturation, candidats, "le menu l'offre")
        event = self._rencontre(partner_ids=[Command.set([siege_social.id])])
        self.assertFalse(
            event.location,
            "une case postale n'est pas un lieu de rencontre : à l'usager de la choisir",
        )

    # ------------------------------------------------------------- à l'écran

    def test_le_choix_de_lusine_tient_quand_un_invite_arrive(self):
        """Le cas pour lequel le menu existe : l'usine n'est pas le siège."""
        with Form(self.Event) as form:
            form.name = "Visite du essai (écran)"
            form.partner_ids.add(self.contact)
            self.assertEqual(form.location, self.SIEGE,
                             "le siège social est proposé tout seul")
            form.bf_address_partner_id = self.plant3
            self.assertEqual(form.location, self.USINE3,
                             "choisir une adresse la pose comme lieu")
            form.partner_ids.add(self.interne)
            self.assertEqual(form.location, self.USINE3,
                             "un invité de plus ne renvoie personne au siège social")
        event = form.record
        self.assertEqual(event.location, self.USINE3)
        self.assertEqual(event.bf_address_partner_id, self.plant3)

    def test_le_choix_de_lusine_tient_apres_enregistrement(self):
        """Ce que l'écran renvoie, pas ce que le serveur avait calculé.

        Ce qui dit « ce lieu est le nôtre » doit revenir du client : un champ
        caché posé par un on-change ne revient pas, et la règle se tairait au
        pire moment — le choix de l'usine se ferait écraser à l'invité suivant.
        """
        autre = self.Partner.create({
            "name": "Atelier Kamouraska",
            "is_company": True,
            "street": "77 route du Fleuve",
            "city": "La Pocatière",
            "state_id": self.qc.id,
            "zip": "G0R 1Z0",
            "country_id": self.ca.id,
        })
        with Form(self.Event) as form:
            form.name = "Visite du essai (choix tenu)"
            form.partner_ids.add(self.contact)
            form.bf_address_partner_id = self.plant3
        event = form.record
        with Form(event) as form:
            form.partner_ids.add(autre)
        self.assertEqual(
            event.location, self.USINE3,
            "l'usine choisie à la main survit à l'enregistrement et à l'invité suivant",
        )

    def test_le_lieu_quon_a_mis_suit_le_client_quon_change(self):
        autre = self.Partner.create({
            "name": "Scierie Etchemin",
            "is_company": True,
            "street": "9 rang Saint-Gilles",
            "city": "Lac-Etchemin",
            "state_id": self.qc.id,
            "zip": "G0R 1S0",
            "country_id": self.ca.id,
        })
        with Form(self.Event) as form:
            form.name = "Visite du essai (client changé)"
            form.partner_ids.add(self.contact)
        event = form.record
        self.assertEqual(event.location, self.SIEGE)
        with Form(event) as form:
            form.partner_ids.remove(id=self.contact.id)
            form.partner_ids.add(autre)
        self.assertEqual(event.location, "9 rang Saint-Gilles, Lac-Etchemin QC G0R 1S0")
        self.assertEqual(event.bf_address_partner_id, autre)

    def test_linvite_retire_emporte_le_lieu_quon_avait_mis(self):
        with Form(self.Event) as form:
            form.name = "Visite du essai (retrait)"
            form.partner_ids.add(self.contact)
            self.assertEqual(form.location, self.SIEGE)
            form.partner_ids.remove(id=self.contact.id)
            self.assertFalse(
                form.location,
                "un lieu laissé par un invité parti est un lieu que personne n'a vérifié",
            )
            self.assertFalse(form.bf_address_partner_id)

    def test_un_lieu_ecrit_a_la_main_survit_a_un_nouvel_invite(self):
        with Form(self.Event) as form:
            form.name = "Visite du essai (à la main)"
            form.location = "Garage municipal, porte 4"
            form.partner_ids.add(self.contact)
            self.assertEqual(form.location, "Garage municipal, porte 4")

    def test_un_lieu_reecrit_par_dessus_le_notre_nest_plus_le_notre(self):
        autre = self.Partner.create({
            "name": "Métallurgie Nicolet",
            "is_company": True,
            "street": "250 boulevard Industriel",
            "city": "Nicolet",
            "state_id": self.qc.id,
            "zip": "J3T 1A1",
            "country_id": self.ca.id,
        })
        with Form(self.Event) as form:
            form.name = "Visite du essai (réécrit)"
            form.partner_ids.add(self.contact)
            form.location = "128 rue des Forges, porte 2 (quai de réception)"
            form.partner_ids.add(autre)
            self.assertEqual(
                form.location, "128 rue des Forges, porte 2 (quai de réception)",
                "la précision ajoutée à la main est la seule chose que personne ne sait",
            )

    def test_ajouter_puis_retirer_nemporte_pas_ladresse_chez_lautre(self):
        """L'ordre des deux gestes ne doit rien changer.

        En retirant d'abord, le lieu passe par le vide et se refait. En
        ajoutant d'abord, rien ne passe par le vide : c'est le geste qui a
        envoyé l'adresse d'un client dans l'invitation d'un autre.
        """
        autre = self.Partner.create({
            "name": "Scierie Etchemin",
            "is_company": True,
            "street": "9 rang Saint-Gilles",
            "city": "Lac-Etchemin",
            "state_id": self.qc.id,
            "zip": "G0R 1S0",
            "country_id": self.ca.id,
        })
        with Form(self.Event) as form:
            form.name = "Visite du essai (ordre inverse)"
            form.partner_ids.add(self.contact)
            self.assertEqual(form.location, self.SIEGE)
            form.partner_ids.add(autre)
            form.partner_ids.remove(id=self.contact.id)
            self.assertNotIn(
                "128 rue des Forges", form.location or "",
                "l'adresse de la Fonderie partirait dans l'invitation de la Scierie",
            )
            self.assertEqual(form.location, "9 rang Saint-Gilles, Lac-Etchemin QC G0R 1S0")
            self.assertEqual(form.bf_address_partner_id, autre)

    def test_un_contact_classe_sous_notre_societe_nest_pas_une_destination(self):
        """Odoo recopie NOTRE adresse sur les fiches classées sous la nôtre."""
        sous_traitant = self.Partner.create({
            "name": "Sous-traitant interne",
            "parent_id": self.env.company.partner_id.id,
            "email": "sous.traitant@example.invalid",
        })
        self.assertFalse(sous_traitant.user_ids)
        event = self._rencontre(partner_ids=[Command.set([sous_traitant.id])])
        self.assertFalse(
            event.location,
            "le lieu de la rencontre deviendrait notre propre bureau",
        )

    def test_une_copie_et_une_occurrence_gardent_la_source(self):
        """Sans la source, une occurrence porte un lieu que plus rien ne suit."""
        event = self._rencontre(partner_ids=[Command.set([self.contact.id])])
        copie = event.copy()
        self.assertEqual(copie.location, self.SIEGE)
        self.assertEqual(copie.bf_address_partner_id, self.membre,
                         "une copie sans source est un lieu que personne ne peut tracer")
        serie = self._rencontre(
            name="Visite récurrente du essai",
            partner_ids=[Command.set([self.contact.id])],
            recurrency=True, rrule_type="weekly", interval=1,
            end_type="count", count=3, wed=True, event_tz="America/Toronto",
        )
        occurrences = serie.recurrence_id.calendar_event_ids
        self.assertTrue(len(occurrences) > 1)
        self.assertTrue(
            all(o.bf_address_partner_id == self.membre for o in occurrences),
            "chaque occurrence doit pouvoir rendre le lieu quand l'invité part",
        )

    def test_un_lieu_vide_a_la_main_nest_pas_rerempli(self):
        """Vider le lieu est une décision : la visite est ailleurs, à confirmer."""
        with Form(self.Event) as form:
            form.name = "Visite du essai (lieu vidé)"
            form.partner_ids.add(self.contact)
            form.bf_address_partner_id = self.plant3
            form.location = ""
            form.partner_ids.add(self.interne)
            self.assertFalse(
                form.location,
                "le lieu vidé s'était fait remplir avec le siège social",
            )
            self.assertEqual(form.bf_address_partner_id, self.plant3)

    def test_passer_la_rencontre_en_visio_rend_ladresse(self):
        event = self._rencontre(partner_ids=[Command.set([self.contact.id])])
        self.assertEqual(event.location, self.SIEGE)
        event.write({"videocall_location": "https://meet.example.invalid/essai"})
        self.assertFalse(
            event.location,
            "une adresse de rue sur une rencontre en ligne est un lieu où personne n'ira",
        )
        self.assertFalse(event.bf_address_partner_id)

    def test_un_lieu_ecrit_a_la_main_survit_au_passage_en_visio(self):
        event = self._rencontre(
            partner_ids=[Command.set([self.contact.id])],
            location="Chez le notaire, 5 rue Principale")
        event.write({"videocall_location": "https://meet.example.invalid/essai-b"})
        self.assertEqual(event.location, "Chez le notaire, 5 rue Principale")

    def test_ladresse_se_reconnait_dans_une_autre_langue(self):
        """Deux lecteurs, deux langues, le même lieu.

        Le nom du pays est traduit : si la comparaison était exacte, le lieu
        cesserait d'être le nôtre dès qu'un collègue d'une autre langue ouvre
        la rencontre, et ne serait plus jamais repris.
        """
        etranger = self.Partner.create({
            "name": "Membre d'ailleurs",
            "is_company": True,
            "street": "12 Rue de la Paix",
            "city": "Paris",
            "zip": "75002",
            "country_id": self.env.ref("base.fr").id,
        })
        en_fr = etranger.with_context(lang="fr_CA").bf_address_one_line()
        self.assertTrue(etranger.with_context(lang="en_US").bf_address_correspond(en_fr),
                        "écrit en français (%s), relu en anglais" % en_fr)
        self.assertFalse(etranger.bf_address_correspond("12 Rue de la Paix, porte 4"))

    def test_la_synchro_dune_reservation_ne_vide_pas_notre_lieu(self):
        """Le chemin du lien de réservation, de bout en bout.

        `resource_booking._sync_meeting` repousse TOUS les champs de la
        rencontre à chaque écriture sur la réservation, dont le lieu du type —
        vide ici. Sans garde, l'adresse posée à la création disparaissait
        quelques secondes plus tard, dans la même requête web.
        """
        if "resource.booking" not in self.env:
            self.skipTest("resource_booking n'est pas installé")
        calendrier = self.env["resource.calendar"].create({
            "name": "9-17 essai", "tz": "UTC",
            "attendance_ids": [Command.create({
                "name": "J%s" % j, "dayofweek": str(j), "hour_from": 9.0,
                "hour_to": 17.0, "day_period": "morning"}) for j in range(7)],
        })
        ressource = self.env["resource.resource"].create({
            "name": "Conseiller du essai", "calendar_id": calendrier.id,
            "resource_type": "material", "tz": "UTC"})
        combinaison = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([ressource.id])]})
        type_rdv = self.env["resource.booking.type"].create({
            "name": "Visite de membres", "duration": 1.5,
            "slot_duration": 1.5, "modifications_deadline": 0.0,
            "combination_assignment": "sorted",
            "resource_calendar_id": calendrier.id,
            "combination_rel_ids": [Command.create({
                "sequence": 0, "combination_id": combinaison.id})],
        })
        self.assertFalse(type_rdv.location, "le type n'a pas de lieu fixe")
        debut = (fields.Datetime.now() + timedelta(days=3)).replace(
            hour=10, minute=0, second=0, microsecond=0)
        reservation = self.env["resource.booking"].create({
            "partner_ids": [Command.set([self.contact.id])],
            "type_id": type_rdv.id, "combination_id": combinaison.id,
            "start": debut,
        })
        rencontre = reservation.meeting_id
        self.assertTrue(rencontre, "la réservation crée bien sa rencontre")
        self.assertEqual(rencontre.location, self.SIEGE,
                         "le lien de réservation pose l'adresse du membre")
        reservation.write({"name": "Visite chez Fonderie Bellechasse"})
        self.assertEqual(
            rencontre.location, self.SIEGE,
            "la synchro de la réservation a effacé l'adresse",
        )

    def test_la_comparaison_repond_meme_quand_la_fiche_est_cloisonnee(self):
        """La synchro d'une réservation tourne sous l'usager public.

        Lue sans `sudo`, l'adresse de la fiche nommée sort en AccessError dès
        qu'une règle de société la cache à qui écrit — et une AccessError
        hérite de UserError : la réservation publique tomberait en erreur, sur
        une comparaison purement technique qui ne montre rien à personne.
        """
        autre_societe = self.env["res.company"].create({"name": "Société 2"})
        cloisonne = self.Partner.create({
            "name": "Membre cloisonné",
            "is_company": True,
            "company_id": autre_societe.id,
            "street": "3 rue Cloisonnée",
            "city": "Rimouski",
            "state_id": self.qc.id,
            "zip": "G5L 1A1",
            "country_id": self.ca.id,
        })
        event = self._rencontre(partner_ids=[Command.set([cloisonne.id])])
        self.assertTrue(event.location)
        dehors = self.env["res.users"].create({
            "name": "Écrivain d'une autre société",
            "login": "ecrivain.essai",
            "company_id": self.env.company.id,
            "company_ids": [Command.set([self.env.company.id])],
            "groups_id": [Command.set([self.env.ref("base.group_user").id])],
        })
        self.assertFalse(
            cloisonne.with_user(dehors).has_access("read"),
            "la fiche doit bien être hors de portée de ce compte",
        )
        # ⚠️ Rien n'est en `sudo` ici : avec le `sudo` du module en moins,
        # les deux gestes doivent tomber en AccessError, sinon l'essai est
        # vert pour la mauvaise raison.
        event.user_id = dehors
        # ⚠️ Sans vider le cache, la fiche déjà lue par l'administrateur est
        # servie sans repasser par les règles : l'essai mesurerait le cache.
        self.env.invalidate_all()
        vu = event.with_user(dehors)
        self.assertTrue(
            vu._bf_address_est_le_notre(),
            "la comparaison doit répondre, pas refuser",
        )
        # Le chemin réel : la synchro d'une réservation repousse un lieu vide.
        vu.with_context(syncing_booking_ids=[1]).write({"location": False})
        self.assertEqual(
            event.location, "3 rue Cloisonnée, Rimouski QC G5L 1A1",
            "le lieu posé a été effacé par la synchro",
        )

    def test_une_visite_hybride_garde_ladresse_choisie(self):
        """Un lien de visio qui n'a pas changé n'est pas une rencontre qui passe en ligne.

        La synchro d'une réservation repousse le lien qu'elle avait déjà à
        chaque écriture. Jugée sur ce que l'écriture *dit* plutôt que sur ce
        qui *change*, la règle « pas d'adresse sur une rencontre en ligne »
        effaçait l'adresse choisie à la main pour ceux qui s'y rendent — et
        défaisait, dans la même requête, le lieu que l'autre garde venait de
        remettre.
        """
        event = self._rencontre(
            partner_ids=[Command.set([self.contact.id])],
            videocall_location="https://meet.example.invalid/hybride")
        self.assertFalse(event.location, "rien n'est posé d'office sur une visio")
        with Form(event) as form:
            form.bf_address_partner_id = self.plant3
        self.assertEqual(event.location, self.USINE3, "choisi à la main, malgré la visio")
        event.with_context(syncing_booking_ids=[1]).write({
            "location": False,
            "videocall_location": "https://meet.example.invalid/hybride",
            "name": "Visite hybride du essai",
        })
        self.assertEqual(
            event.location, self.USINE3,
            "le lien inchangé a été pris pour un passage en ligne",
        )
        self.assertEqual(event.bf_address_partner_id, self.plant3)

    def test_un_membre_archive_nemporte_pas_le_lieu_de_sa_visite(self):
        """Archiver une fiche n'est pas retirer un invité.

        Mesuré le 2026-11-03 : un contact archivé disparaît de `partner_ids`
        (la lecture d'un many2many filtre les inactifs). Sans précaution, un
        ménage dans le carnet — adhésion finie, doublon fusionné — effaçait le
        lieu d'une visite encore au calendrier, côté serveur, sans que
        personne n'ait retiré personne.

        À l'écran, c'est Odoo lui-même qui retire le contact archivé de la
        liste des invités : là, le lieu part avec lui, et l'usager voit les
        deux disparaître ensemble.
        """
        event = self._rencontre(partner_ids=[Command.set([self.membre.id])])
        self.assertEqual(event.location, self.SIEGE)
        self.membre.action_archive()
        event.invalidate_recordset()
        self.assertFalse(event.partner_ids, "Odoo masque l'invité archivé")
        event._bf_address_rafraichir()
        self.assertEqual(
            event.location, self.SIEGE,
            "le ménage du carnet a effacé le lieu d'une visite à venir",
        )
        self.assertEqual(event.bf_address_partner_id, self.membre)

    def test_une_precision_ajoutee_au_bout_reste_a_lusager(self):
        """« Notre ligne, porte 2 » n'est pas notre ligne."""
        self.assertFalse(
            self.membre.bf_address_correspond(self.SIEGE + ", porte 2 (quai)"),
            "le segment de trop n'est toléré que si c'est le nom du pays",
        )
        with Form(self.Event) as form:
            form.name = "Visite du essai (précision au bout)"
            form.partner_ids.add(self.contact)
            form.location = self.SIEGE + ", porte 2 (quai)"
            form.partner_ids.remove(id=self.contact.id)
            self.assertEqual(
                form.location, self.SIEGE + ", porte 2 (quai)",
                "la précision ajoutée à la main s'est fait effacer",
            )

    def test_un_lieu_explicitement_vide_est_une_reponse(self):
        """Une chaîne vide dit « pas de lieu » ; `False` dit « le type n'en a pas »."""
        importe = self._rencontre(
            name="Événement lu d'un agenda externe",
            partner_ids=[Command.set([self.contact.id])],
            location="")
        self.assertFalse(
            importe.location,
            "un événement importé sans lieu ne doit pas en gagner un",
        )
        self.assertFalse(importe.bf_address_partner_id)
        reservation = self._rencontre(
            name="Rencontre née d'une réservation",
            partner_ids=[Command.set([self.contact.id])],
            location=False)
        self.assertEqual(reservation.location, self.SIEGE,
                         "le type sans lieu fixe, lui, se fait remplir")

    def test_un_appelant_peut_demander_quon_le_laisse_tranquille(self):
        event = self.Event.with_context(bf_address_skip=True).create({
            "name": "Import du essai",
            "start": "2026-11-18 14:00:00",
            "stop": "2026-11-18 15:30:00",
            "partner_ids": [Command.set([self.contact.id])],
        })
        self.assertFalse(event.location)

    # --------------------------------------------------------- jusqu'à l'ICS

    def test_ladresse_part_dans_linvitation(self):
        event = self._rencontre(partner_ids=[Command.set([self.contact.id])])
        ics = event._get_ics_file().get(event.id) or b""
        self.assertIn(b"128 rue des Forges", ics,
                      "c'est dans l'agenda du téléphone que l'adresse sert")
