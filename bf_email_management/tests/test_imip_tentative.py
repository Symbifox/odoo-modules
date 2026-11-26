"""Une invitation reçue arrive sous son vrai titre, provisoire, et lisible.

Avant cette version, l'événement
naissait sous « [Tentatif] <titre> » : la marque vivait dans le seul champ que
tout le monde lit, et la lever demandait d'éditer le titre à la main. Sur une base
réelle, presque tous les événements nés d'une invitation portaient encore le
préfixe, la plupart portaient en même temps le statut « confirmée » estampé par
le `create()` de bf_calendar_invite, et les dernières reçues avaient été
corrigées à la main.

Le même lot verse ce que le `.ics` disait et qu'on jetait : organisateur nommé,
invités avec leur réponse, lien de visioconférence, fichier reçu en pièce.

🔴 Les deux choix retenus, et c'est eux
que la moitié de ces essais surveille :

* **aucun invité ne devient participant**, même appariable, même sans compte
  chez nous. Le propriétaire seul. C'est ce qui ferme d'un coup la lecture par
  la règle du portail et les quatre chemins d'envoi qui partaient de
  `partner_ids` ;
* **la description NOMME les invités sans écrire leur adresse** — des liens
  de partage au niveau « détails » servent déjà ce champ. Faute de `CN`,
  l'invité est nommé par son domaine. L'organisateur garde nom ET adresse.

Les trois gardes d'envoi restent, et ont chacune son essai : elles ne protègent
plus l'ingestion, qui n'inscrit plus personne, mais le cas où le propriétaire ajoute un
invité À LA MAIN sur l'une de ces rencontres.

Chaque essai part d'une invitation NEUVE.
"""
import base64
import email
import email.policy
import unittest

from odoo.tests import TransactionCase, tagged

from ..models import imip

ORGANISATEUR = "organisateur@ailleurs.test"
CONNUE = "connue@ailleurs.test"
INCONNU = "inconnu@ailleurs.test"
AVEC_COMPTE = "client.portail@ailleurs.test"
PROPRIO = "proprietaire@example.com"
MEET = "https://meet.google.com/tev-ariw-hyd"
# Le piège : un hôte connu dans la
# REQUÊTE d'une adresse qui n'est pas la sienne.
PIEGE = "https://phishing.test/go?r=https://meet.google.com/abc-defg-hij"


# post_install : `bf_event_status` et le champ d'UID viennent de modules chargés
# ailleurs. En at_install, le registre ne les connaît pas encore.
@tagged("post_install", "-at_install")
class ImipTentativeCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_email.auto_add_calendar_invites", "1")
        cls.owner = cls.env["res.users"].create({
            "name": "Propriétaire Boîte",
            "login": PROPRIO,
            "email": PROPRIO,
        })
        cls.account = cls.env["bf.email.account"].sudo().create({
            "name": "Épreuve",
            "user_id": cls.owner.id,
            "host": "imap.example.com",
            "port": 993,
            "login": PROPRIO,
            "password": "x",
        })
        # Un invité qui a DÉJÀ une fiche : avant ce choix, il
        # devenait participant. Il ne doit plus l'être.
        cls.connue = cls.env["res.partner"].create({
            "name": "Connue Invitée", "email": CONNUE})
        # Un invité qui a un compte PORTAIL : même règle, pour la même raison
        # en plus forte (la règle du portail lui donnerait la LECTURE).
        cls.connue_avec_compte = cls.env["res.users"].create({
            "name": "Client Portail",
            "login": AVEC_COMPTE,
            "email": AVEC_COMPTE,
            "groups_id": [(6, 0, [cls.env.ref("base.group_portal").id])],
        })
        # Un collègue qui peut TOUT lire : c'est lui qui presse le bouton dans
        # l'essai de la garde. ⚠️ Le cœur ne notifie jamais l'usager courant
        # (`_should_notify_attendee`), donc un envoi déclenché par le
        # propriétaire lui-même ne prouverait pas qu'il reste joignable.
        cls.collegue = cls.env["res.users"].create({
            "name": "Collègue Interne",
            "login": "collegue@example.com",
            "email": "collegue@example.com",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("base.group_system").id])],
        })
        cls.BfEmail = cls.env["bf.email"]
        cls.Event = cls.env["calendar.event"].sudo()
        cls.Mail = cls.env["mail.mail"].sudo()
        cls.Attachment = cls.env["ir.attachment"].sudo()
        cls._n = 0

    # -- outillage -----------------------------------------------------
    @classmethod
    def _ics(cls, uid, method="REQUEST", organizer=ORGANISATEUR, summary="Point trimestriel",
             start="20300901T140000Z", stop=None, dtstamp="20300101T000000Z",
             visio=True, bourrage=0, lien=None,
             invites=((PROPRIO, "Propriétaire Boîte", "NEEDS-ACTION"),
                      (CONNUE, "Connue Invitée", "ACCEPTED"),
                      (AVEC_COMPTE, "Client Portail", "TENTATIVE"),
                      (INCONNU, "", "DECLINED"))):
        # ⚠️ La virgule est ÉCHAPPÉE, comme dans un vrai « .ics » : une
        # virgule nue fait du TEXT une valeur MULTIPLE, et vobject n’en rend
        # alors que le premier morceau. Une première version de cet essai perdait
        # ainsi toute la description après « Bonjour » — et le lien de visio
        # avec elle, donc l’essai échouait pour la mauvaise raison.
        corps = ("Bonjour\\,&nbsp;voici l'ordre du jour.\\n"
                 "Participer : %s" % (lien or MEET)) if visio else "Sans visio."
        if stop is None:
            # Une relance déplaçait le début et laissait la fin : le cœur refuse
            # alors l'écriture (fin antérieure au début), l'ingestion avale
            # l'exception comme elle doit le faire, et l'essai échouait en aval
            # sur un titre jamais écrit.
            stop = "%sT%06d" % (start[:8], int(start[9:15]) + 10000)
        lignes = [
            "BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:%s" % method,
            "BEGIN:VEVENT", "UID:%s" % uid,
            "DTSTART:%s" % start, "DTEND:%sZ" % stop.rstrip("Z"),
            "SUMMARY:%s" % summary,
            "DTSTAMP:%s" % dtstamp,
            "SEQUENCE:2",
            "LOCATION:Google Meet",
            "DESCRIPTION:%s" % corps,
            "ORGANIZER;CN=Léa Organisatrice:mailto:%s" % organizer,
        ]
        for addr, cn, partstat in invites:
            lignes.append(
                "ATTENDEE%s;ROLE=REQ-PARTICIPANT;PARTSTAT=%s:mailto:%s"
                % (";CN=%s" % cn if cn else "", partstat, addr))
        # Du bourrage en X-propriétés : la pièce jointe grossit sans que la
        # description de l'événement grossisse avec elle.
        for i in range(bourrage):
            lignes.append("X-BF-BOURRAGE-%s:%s" % (i, "b" * 10000))
        return "\r\n".join(lignes + ["END:VEVENT", "END:VCALENDAR"])

    def _message(self, corps_ics, sender=ORGANISATEUR):
        """Un message d'une seule partie `text/calendar`."""
        type(self)._n += 1
        raw = (
            "From: %s\r\nTo: %s\r\nSubject: invitation\r\n"
            "Message-ID: <inv-%s@example.test>\r\nMIME-Version: 1.0\r\n"
            "Content-Type: text/calendar; charset=utf-8; method=REQUEST\r\n\r\n%s"
            % (sender, PROPRIO, self._n, corps_ics)
        )
        # ⚠️ En octets, comme l'IMAP les livre. Lu depuis un `str`, le module
        # `email` réencode la charge utile avec substitution et les accents
        # deviennent des « � » : le défaut était dans la base d'essai, pas dans le
        # code (une vraie invitation porte bien « Réservé par »).
        return email.message_from_bytes(raw.encode("utf-8"),
                                        policy=email.policy.default)

    def _message_multipart(self, corps_ics_list, sender=ORGANISATEUR):
        """Un message qui porte PLUSIEURS parties `text/calendar`."""
        type(self)._n += 1
        morceaux = [
            "From: %s\r\nTo: %s\r\nSubject: invitations\r\n"
            "Message-ID: <inv-%s@example.test>\r\nMIME-Version: 1.0\r\n"
            'Content-Type: multipart/mixed; boundary="FRONTIERE"\r\n\r\n'
            % (sender, PROPRIO, self._n)
        ]
        for corps in corps_ics_list:
            morceaux.append(
                "--FRONTIERE\r\nContent-Type: text/calendar; charset=utf-8; "
                "method=REQUEST\r\n\r\n%s\r\n" % corps)
        morceaux.append("--FRONTIERE--\r\n")
        return email.message_from_bytes(
            "".join(morceaux).encode("utf-8"), policy=email.policy.default)

    def _deliver(self, corps_ics, sender=ORGANISATEUR):
        self.BfEmail._maybe_ingest_calendar_invite(
            self._message(corps_ics, sender), self.account, "INBOX")

    def _invitation(self, **kw):
        type(self)._n += 1
        uid = "inv-essai-%s@example.test" % self._n
        self._deliver(self._ics(uid, **kw))
        event = self.Event.search([("x_imip_uid", "=", uid)])
        self.assertEqual(len(event), 1, "le flux légitime doit créer l'événement")
        return event, uid

    def _conference_de(self, **kw):
        """Ce que l'analyseur retient comme salle de visio, sans toucher l'ORM."""
        corps = self._ics("visio-%s@example.test" % id(kw), **kw)
        evenements = imip.parse_imip_events(self._message(corps))
        self.assertEqual(len(evenements), 1)
        return evenements[0]["conference"]

    def _pieces(self, event):
        return self.Attachment.search([
            ("res_model", "=", "calendar.event"), ("res_id", "=", event.id)])

    @staticmethod
    def _langues(env, prefixe):
        return env["res.lang"].sudo().search(
            [("code", "=like", prefixe + "%"), ("active", "=", True)], limit=1)

    # -- le titre --------------------------------------------
    def test_le_titre_est_celui_de_l_organisateur(self):
        event, _uid = self._invitation()
        self.assertEqual(event.name, "Point trimestriel",
                         "le titre ne porte plus aucune marque : c'est le "
                         "champ que tout le monde lit")

    def test_elle_nait_provisoire_et_ne_retient_pas_le_creneau(self):
        event, _uid = self._invitation()
        if "bf_event_status" not in event._fields:
            raise unittest.SkipTest("bf_calendar_invite requis")
        self.assertEqual(event.bf_event_status, "tentative",
                         "sans la CLÉ dans les valeurs, le create() de "
                         "bf_calendar_invite estampe « confirmée »")
        self.assertEqual(event.show_as, "free")

    def test_une_relance_de_l_organisateur_met_a_jour_sans_prefixe(self):
        event, uid = self._invitation()
        self._deliver(self._ics(uid, summary="Point trimestriel (déplacé)",
                                start="20300902T140000Z",
                                dtstamp="20300102T000000Z"))
        self.assertEqual(self.Event.search_count([("x_imip_uid", "=", uid)]), 1,
                         "une relance met à jour, elle ne duplique pas")
        self.assertEqual(event.name, "Point trimestriel (déplacé)")

    # -- choix A : aucun invité ne devient participant ------------
    def test_aucun_invite_ne_devient_participant_meme_appariable(self):
        """🔴 Le choix retenu, et la moitié de ces essais tient dessus.

        Le propriétaire seul. Un invité inscrit comme participant pourrait
        LIRE la rencontre par la règle du portail, et c'est aussi de
        `partner_ids` que partent le bouton « Envoyer un courriel aux
        participants », le POKE, les alarmes de type courriel et la
        pré-sélection du composeur. Ne pas l'inscrire ferme les cinq d'un
        coup, au lieu de les garder ouverts derrière des gardes.
        """
        Partner = self.env["res.partner"]
        avant = Partner.search_count([])
        event, _uid = self._invitation()
        self.assertEqual(event.partner_ids, self.owner.partner_id,
                         "le propriétaire, et personne d'autre : sa vue "
                         "agenda filtre par participant")
        self.assertNotIn(self.connue, event.partner_ids,
                         "un invité qui a DÉJÀ une fiche n'est pas inscrit "
                         "non plus")
        self.assertNotIn(self.connue_avec_compte.partner_id, event.partner_ids,
                         "et celui qui a un compte encore moins")
        self.assertEqual(Partner.search_count([]), avant,
                         "une invitation reçue ne peuple pas le carnet")

    # -- choix B : nommés, jamais adressés ------------------------
    def test_la_description_nomme_les_invites_sans_leur_adresse(self):
        """🔴 Des liens de partage servent déjà cette description.

        Un client de courriel, un abonnement Nextcloud, un agenda partagé la lisent au
        niveau « détails », et s'ouvrent avec leur seule adresse. Les adresses
        complètes restent dans le `.ics` joint, que le portail refuse.
        """
        event, _uid = self._invitation()
        corps = event.description or ""
        self.assertIn("Connue Invitée", corps, "l'invité est NOMMÉ")
        self.assertIn("Client Portail", corps)
        for adresse in (CONNUE, AVEC_COMPTE, INCONNU, PROPRIO):
            self.assertNotIn(adresse, corps,
                             "aucune adresse d'invité dans la description : "
                             "elle est servie par trois liens de partage")
        self.assertIn(ORGANISATEUR, corps,
                      "l'organisateur, lui, garde son adresse : c'est le "
                      "vis-à-vis de la rencontre, et c'est par là qu'on lui "
                      "répond")
        self.assertIn("Léa Organisatrice", corps)
        self.assertIn("a accepté", corps, "la réponse de chacun se lit")
        self.assertIn("a refusé", corps)
        self.assertIn('href="%s"' % MEET, corps)
        self.assertGreater(corps.count("<p>"), 1,
                           "🔴 le défaut d'origine : tout le corps tenait dans "
                           "UN SEUL <p>, donc les sauts de ligne tombaient")
        # ⚠️ « <br », pas « <br/> » : le champ est assaini à l'écriture et
        # l'assainisseur normalise la balise fermante.
        self.assertIn("<br", corps,
                      "les sauts de ligne de l'organisateur sont rendus")
        self.assertNotIn("&amp;nbsp;", corps,
                         "le &nbsp; livré par Google ne se réaffiche pas en clair")

    def test_un_invite_sans_nom_est_nomme_par_son_domaine(self):
        event, _uid = self._invitation()
        corps = event.description or ""
        self.assertIn("un invité chez ailleurs.test", corps,
                      "faute de CN, le domaine dit d'où vient la personne "
                      "sans livrer de quoi lui écrire")

    def test_un_cn_qui_est_une_adresse_ne_passe_pas_pour_un_nom(self):
        """⚠️ Beaucoup de clients écrivent `CN=<adresse>` faute de nom."""
        event, _uid = self._invitation(invites=(
            (PROPRIO, "Propriétaire Boîte", "NEEDS-ACTION"),
            (CONNUE, CONNUE, "ACCEPTED"),
        ))
        corps = event.description or ""
        self.assertNotIn(CONNUE, corps,
                         "l'adresse reversée par la porte du nom est une "
                         "adresse quand même")
        self.assertIn("un invité chez ailleurs.test", corps)

    def test_le_telephone_recoit_un_invite_par_ligne(self):
        """🔴 Le téléphone ne lit pas ce HTML, il lit sa version aplatie.

        `bf_dav_server` sert la description passée par `html2plaintext`, et
        cette fonction colle tous les `<li>` d'une liste sur une seule ligne.
        L'essai mesure donc ce que l'outil LIT, pas ce que l'écran rend.
        """
        from odoo.tools import html2plaintext
        event, _uid = self._invitation()
        aplati = html2plaintext(event.description or "")
        porteuses = [ligne for ligne in aplati.splitlines()
                     if "Connue Invitée" in ligne
                     or "un invité chez ailleurs.test" in ligne]
        self.assertEqual(len(porteuses), 2,
                         "un invité par ligne : une <ul> les collerait "
                         "ensemble sur le téléphone")

    # -- la langue ---------------------------------------------------
    def test_les_textes_de_la_description_sont_traduits(self):
        """🔴 Un dictionnaire de module ne se traduit pas à l'import.

        Les libellés sont construits À L'APPEL, source anglaise et français au
        catalogue : une base sans français
        lisait une description en français.
        """
        fr = self._langues(self.env, "fr")
        en = self._langues(self.env, "en")
        if not fr or not en:
            raise unittest.SkipTest("il faut une langue fr ET une en actives")
        event, _uid = self._invitation()
        corps_fr = self.BfEmail.with_context(lang=fr.code)._imip_description(
            {"organizer": ORGANISATEUR, "invites": [
                {"email": CONNUE, "cn": "Connue Invitée", "partstat": "ACCEPTED"}]})
        corps_en = self.BfEmail.with_context(lang=en.code)._imip_description(
            {"organizer": ORGANISATEUR, "invites": [
                {"email": CONNUE, "cn": "Connue Invitée", "partstat": "ACCEPTED"}]})
        self.assertIn("Organisateur :", corps_fr)
        self.assertIn("a accepté", corps_fr)
        self.assertIn("Organizer:", corps_en)
        self.assertIn("has accepted", corps_en)
        self.assertNotIn("a accepté", corps_en,
                         "un locataire anglophone ne lit pas du français")
        # Et la MARQUE, elle, ne bouge dans aucune des deux : c'est elle qui
        # permet de reconnaître nos descriptions plus tard.
        from ..models.bf_email import IMIP_MARQUE_NOYAU
        self.assertIn(IMIP_MARQUE_NOYAU, corps_fr)
        self.assertIn(IMIP_MARQUE_NOYAU, corps_en)
        self.assertTrue(
            self.BfEmail._imip_description_est_la_notre(event.description))

    # -- la salle de visio -------------------------
    def test_une_adresse_de_visio_piegee_n_est_pas_retenue(self):
        """🔴 Le défaut le plus grave de cette version.

        La liste d'hôtes était cherchée en SOUS-CHAÎNE dans l'URL entière, et
        seulement dans le lieu et la description : un hôte connu dans la
        requête ou dans le chemin suffisait, et `X-GOOGLE-CONFERENCE` comme
        `URL` passaient sans aucun contrôle. Le résultat est un bouton
        « Rejoindre » et un lien cliquable vers le site de l'inconnu qui a
        écrit l'invitation.
        """
        self.assertEqual(self._conference_de(lien=MEET), MEET,
                         "une vraie salle Meet passe")
        self.assertEqual(self._conference_de(lien=PIEGE), "",
                         "un hôte connu dans la REQUÊTE d'une autre adresse "
                         "n'est pas une salle")
        self.assertEqual(
            self._conference_de(lien="https://phishing.test/meet.google.com/abc"),
            "", "ni dans le CHEMIN")
        self.assertEqual(
            self._conference_de(lien="https://meet.google.com.phishing.test/abc"),
            "", "ni en préfixe de domaine")
        self.assertEqual(
            self._conference_de(lien="https://meet.google.com@phishing.test/abc"),
            "", "ni derrière un userinfo")
        self.assertEqual(
            self._conference_de(lien="https://us02web.zoom.us/j/123"),
            "https://us02web.zoom.us/j/123", "un vrai sous-domaine passe")

    def test_une_propriete_de_visio_quelconque_n_est_pas_retenue(self):
        """Les deux branches qui passaient sans aucun contrôle."""
        corps = self._ics("xprop@example.test", visio=False).replace(
            "SEQUENCE:2",
            "SEQUENCE:2\r\nX-GOOGLE-CONFERENCE:https://phishing.test/o365"
            "\r\nURL:https://phishing.test/facture.pdf")
        evenements = imip.parse_imip_events(self._message(corps))
        self.assertEqual(evenements[0]["conference"], "",
                         "`X-GOOGLE-CONFERENCE` et `URL` sont des champs "
                         "comme les autres, écrits par un inconnu")

    def test_le_lien_de_visio_va_dans_le_champ_visio(self):
        event, _uid = self._invitation()
        self.assertEqual(event.videocall_location, MEET,
                         "le bouton « Rejoindre » lit ce champ, et il part "
                         "dans LOCATION vers le téléphone")

    def test_sans_lien_le_champ_visio_reste_vide(self):
        event, _uid = self._invitation(visio=False)
        self.assertFalse(event.videocall_location)

    def test_une_relance_sans_lien_efface_le_champ_visio(self):
        """Un lien périmé se clique; un lien absent se demande."""
        event, uid = self._invitation()
        self.assertEqual(event.videocall_location, MEET)
        self._deliver(self._ics(uid, visio=False, dtstamp="20300103T000000Z"))
        self.assertFalse(event.videocall_location,
                         "la salle a été retirée de l'invitation : le bouton "
                         "« Rejoindre » ne doit pas garder l'ancienne")

    # -- la marque, et l'historique qu'elle doit reconnaître ---------
    def test_la_marque_reconnait_les_descriptions_deja_posees(self):
        """🔴 L'historique dit « Ajouté », ce lot dit « Ajoutée ».

        Sur une base réelle, presque tous les événements nés d'une
        invitation portent la forme masculine, et AUCUN la marque de ce lot.
        Comparer la marque entière rendait donc faux sur tout l'historique, et
        aucune relance de l'organisateur n'y aurait jamais rien rafraîchi.
        """
        historique = ("<p>Ajouté automatiquement depuis une invitation reçue "
                      "par courriel (à confirmer). Organisateur : "
                      "alex@ailleurs.test</p>")
        self.assertTrue(
            self.BfEmail._imip_description_est_la_notre(historique),
            "la forme masculine de l'historique est la nôtre aussi")
        self.assertFalse(
            self.BfEmail._imip_description_est_la_notre("<p>Mes notes</p>"))
        self.assertTrue(self.BfEmail._imip_description_est_la_notre(False),
                        "vide : il n'y a rien à préserver")
        # Et le comportement qui en dépend : une relance rafraîchit une
        # description posée par le code d'AVANT ce lot.
        event, uid = self._invitation()
        event.write({"description": historique})
        self._deliver(self._ics(uid, dtstamp="20300104T000000Z"))
        self.assertIn("Connue Invitée", event.description or "",
                      "une description de l'historique se laisse rafraîchir")

    # -- les boutons, et ce qu'ils pré-remplissent -------------------
    def test_les_deux_boutons_ne_pre_remplissent_que_le_proprietaire(self):
        """Le défaut bloquant : le composeur, et le POKE.

        Les deux lisent `partner_ids`. C'est pour ça que l'arbitrage a porté
        là, et pas sur une garde de plus.
        """
        event, _uid = self._invitation()
        for nom, action in (("EMAIL", "action_open_composer"),
                            ("POKE", "action_bf_poke")):
            contexte = getattr(event.with_user(self.collegue), action)()["context"]
            destinataires = self.env["res.partner"].browse(
                contexte["default_partner_ids"])
            self.assertEqual(destinataires, self.owner.partner_id,
                             "le bouton %s ne pré-remplit que le "
                             "propriétaire" % nom)

    # -- les gardes : le filet de l'invité ajouté À LA MAIN ----------
    def _avec_un_invite_a_la_main(self):
        """Une invitation reçue sur laquelle le propriétaire a ajouté un invité.

        C'est le SEUL cas où les gardes servent encore, depuis que
        l'ingestion n'inscrit plus personne — et c'est un cas réel : ajouter
        un collègue à une rencontre qu'on n'organise pas est un geste normal.
        """
        event, uid = self._invitation()
        event.write({"partner_ids": [(4, self.connue.id)]})
        return event, uid

    def test_le_bouton_qui_ecrit_aux_participants_n_atteint_que_le_proprietaire(self):
        """🔴 L'essai qui prouve VRAIMENT la garde.

        ⚠️ Une première version de cet essai déplaçait la rencontre et comptait
        les messages : elle passait aussi quand la garde était retirée, parce
        que `bf_calendar_invite` fait taire l'avis de déplacement du cœur bien
        avant qu'un destinataire soit choisi. Un essai qui ne meurt pas quand
        on casse ce qu'il surveille ne surveille rien. C'est le bouton
        « Envoyer un courriel aux participants » qui la tue : il écrit à TOUS
        les participants.
        """
        event, _uid = self._avec_un_invite_a_la_main()
        Message = self.env["mail.message"].sudo()
        avant = Message.search([]).ids
        event.with_user(self.collegue).action_sendmail()
        # ⚠️ Mesuré sur `mail.message`, pas sur `mail.mail` : le cœur notifie
        # par `message_notify`, et un usager INTERNE reçoit une cloche sans
        # aucun `mail.mail`.
        # Compter les courriels ne verrait donc jamais le propriétaire, et
        # l'essai ne pourrait pas dire s'il a été écarté par la garde ou par
        # son propre réglage de notification.
        notifies = Message.search([("id", "not in", avant)]).mapped(
            "partner_ids.email")
        self.assertNotIn(CONNUE, notifies,
                         "les invités d'une invitation REÇUE sont les "
                         "invités de quelqu'un d'autre, même ajoutés à la main")
        self.assertIn(PROPRIO, notifies,
                      "le propriétaire, lui, reste joignable : c'est son "
                      "agenda, et un rappel qu'il s'est posé doit arriver")

    def test_la_garde_ne_casse_pas_un_envoi_legitime(self):
        """La réciproque, et elle compte autant.

        Une rencontre ordinaire — pas née d'une invitation — doit continuer
        d'écrire à ses invités. Une garde qui attrape tout le monde serait
        trouvée par un client, pas par un essai.
        """
        cree = self.Event.with_context(
            no_mail_to_attendees=True, mail_create_nolog=True).create({
                "name": "Rencontre à nous",
                "start": "2030-09-20 14:00:00",
                "stop": "2030-09-20 15:00:00",
                "user_id": self.owner.id,
                "partner_ids": [(6, 0, [self.owner.partner_id.id,
                                        self.connue.id])],
            })
        # ⚠️ Relu SANS le contexte de création : `no_mail_to_attendees` y est
        # posé pour que la création reste muette, et il voyage avec le
        # recordset. Un essai qui presse le bouton sur ce recordset-là mesure
        # son propre silence, pas la garde.
        event = self.Event.browse(cree.id)
        Message = self.env["mail.message"].sudo()
        avant = Message.search([]).ids
        event.with_user(self.collegue).action_sendmail()
        notifies = Message.search([("id", "not in", avant)]).mapped(
            "partner_ids.email")
        self.assertIn(CONNUE, notifies,
                      "sur une rencontre qui est la nôtre, l'invité reçoit "
                      "bien le courriel")

    def test_un_deplacement_n_ecrit_a_personne(self):
        """Ce que ce chemin prouve, et ce qu'il ne prouve pas.

        Il tient par DEUX choses : la garde de ce module, et le fait que
        `bf_calendar_invite` fasse taire l'avis de déplacement du cœur pour
        envoyer le sien. Il reste utile — c'est le geste quotidien — mais la
        garde elle-même se prouve par l'essai du bouton, au-dessus.
        """
        event, _uid = self._avec_un_invite_a_la_main()
        avant = self.Mail.search([]).ids
        # Déplacée comme un usager la déplacerait : par l'ORM, sans aucun
        # contexte de silence. Le cœur écrit aux participants d'une rencontre
        # FUTURE dont `start` bouge.
        event.with_user(self.owner).write({"start": "2030-09-03 14:00:00",
                                           "stop": "2030-09-03 15:00:00"})
        adresses = set()
        for mail in self.Mail.search([("id", "not in", avant)]):
            adresses |= set((mail.email_to or "").replace(",", " ").split())
            adresses |= set(mail.recipient_ids.mapped("email"))
        for interdit in (CONNUE, ORGANISATEUR):
            self.assertNotIn(interdit, adresses,
                             "les invités d'une invitation REÇUE sont les "
                             "invités de quelqu'un d'autre")

    def test_aucun_avis_de_changement_ne_les_vise(self):
        event, _uid = self._avec_un_invite_a_la_main()
        if not hasattr(event, "_bf_notice_recipients"):
            raise unittest.SkipTest("bf_calendar_invite requis")
        self.assertFalse(event._bf_notice_recipients(),
                         "l'avis de changement de bf_calendar_invite ne "
                         "trouve personne à prévenir sur une invitation "
                         "reçue, même quand un invité y a été ajouté")

    # -- le `.ics` gardé en pièce ------------------------------------
    def test_le_ics_brut_est_garde_une_seule_fois(self):
        event, uid = self._invitation()
        pieces = self._pieces(event)
        self.assertEqual(len(pieces), 1, "le fichier reçu est gardé")
        self.assertTrue(pieces.name.endswith(".ics"))
        self.assertEqual(pieces.mimetype, "text/calendar")
        self._deliver(self._ics(uid))
        self.assertEqual(len(self._pieces(event)), 1,
                         "une relivraison du même message n'empile pas les copies")

    def test_chaque_evenement_recoit_le_fichier_qui_porte_SON_uid(self):
        """⚠️ Un message peut porter deux parties calendrier.

        Deux invitations dans le même courriel, ou un `REQUEST` suivi de son
        annulation : garder « la première partie » attachait au second
        événement le fichier du premier.
        """
        un = "deux-parties-un@example.test"
        deux = "deux-parties-deux@example.test"
        msg = self._message_multipart([
            self._ics(un, summary="Première", dtstamp="20300201T000000Z"),
            self._ics(deux, summary="Seconde", dtstamp="20300202T000000Z"),
        ])
        self.BfEmail._maybe_ingest_calendar_invite(msg, self.account, "INBOX")
        for uid, autre in ((un, deux), (deux, un)):
            event = self.Event.search([("x_imip_uid", "=", uid)])
            self.assertEqual(len(event), 1)
            pieces = self._pieces(event)
            self.assertEqual(len(pieces), 1)
            contenu = base64.b64decode(pieces.datas).decode("utf-8")
            self.assertIn(uid, contenu, "le fichier gardé est le SIEN")
            self.assertNotIn(autre, contenu,
                             "et pas celui de l'autre invitation du message")

    def test_un_ics_trop_gros_n_est_pas_garde(self):
        """Le `.ics` vient d'un inconnu, et il en arrive un par révision."""
        event, _uid = self._invitation(bourrage=60)
        self.assertEqual(len(self._pieces(event)), 0,
                         "au-delà du plafond, rien n'est gardé — et le "
                         "journal le dit")
        self.assertEqual(event.name, "Point trimestriel",
                         "l'événement, lui, est créé comme d'habitude")

    def test_le_nombre_de_pieces_est_plafonne(self):
        event, uid = self._invitation()
        plafond = self.BfEmail.IMIP_ICS_MAX_PIECES
        for i in range(plafond + 3):
            self._deliver(self._ics(uid, dtstamp="20300301T0000%02dZ" % i))
        self.assertEqual(len(self._pieces(event)), plafond,
                         "une relance qui déplace la rencontre vingt fois "
                         "n'écrit pas vingt fois dans le magasin de fichiers")

    # -- une relance de l'organisateur ne détruit rien ----------------
    def test_une_relance_ne_detruit_pas_les_notes_de_l_usager(self):
        """Ce que l'usager a écrit dans le corps n'est pas à l'organisateur."""
        event, uid = self._invitation()
        event.write({"description": "<p>Mes notes à moi</p>"})
        self._deliver(self._ics(uid, summary="Point trimestriel (déplacé)",
                                dtstamp="20300105T000000Z"))
        self.assertEqual(event.name, "Point trimestriel (déplacé)",
                         "le reste de la relance s'applique bien")
        self.assertIn("Mes notes à moi", event.description or "")

    def test_une_relance_rafraichit_la_description_restee_la_notre(self):
        event, uid = self._invitation()
        self._deliver(self._ics(
            uid, dtstamp="20300106T000000Z",
            invites=((PROPRIO, "Propriétaire Boîte", "NEEDS-ACTION"),
                     (CONNUE, "Connue Invitée", "DECLINED"))))
        corps = event.description or ""
        self.assertIn("a refusé", corps,
                      "la réponse changée d'un invité se relit")
        self.assertNotIn("un invité chez ailleurs.test", corps,
                         "un invité retiré de l'invitation en sort aussi")
