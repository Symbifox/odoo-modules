"""La porte du fil cité à l'envoi.

Pourquoi une porte et pas une consigne : la règle « une réponse porte le message
cité sous la signature » vivait dans une consigne de rédaction, un texte
qu'un seul des assistants lisait. Gen, qui
écrit sur les mêmes dossiers avec les mêmes outils, ne la lisait pas et postait
des réponses sans fil cité pendant quinze jours. Un texte que l'auteur relit
après avoir rédigé ne contraint rien ; `_post_message` contraint.

🔴 Le témoin positif est le premier essai du fichier, à dessein : une garde dont
on ne vérifie que les refus passe par construction, même si elle refuse tout.
"""
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

MARQUEUR_SIGNATURE = "o_bf_signature"
SIGNATURE = f'<div class="{MARQUEUR_SIGNATURE}">Alex Exemple</div>'
CITATION = ('<div class="bf_citation"><br/><p>Le lundi 3 novembre 2026, '
            'a@b.test a écrit :</p><blockquote>bonjour</blockquote></div>')


@tagged("post_install", "-at_install")
class TestPorteFilCite(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dossier = cls.env["res.partner"].create({"name": "Dossier d'essai"})
        cls.destinataire = cls.env["res.partner"].create(
            {"name": "Destinataire", "email": "destinataire@essai.invalid"})

    def _brouillon(self, corps, attendue=True, objet="Re: question"):
        """Un brouillon réel, pas un `new()` : il doit pouvoir être posté.

        (`new()` en `setUpClass` perd ses valeurs; et `post_message` du noyau
        relit `create_uid`, donc la ligne doit exister en base.)
        """
        return self.env["mail.scheduled.message"].create({
            "subject": objet,
            "body": corps,
            "model": "res.partner",
            "res_id": self.dossier.id,
            "partner_ids": [(6, 0, self.destinataire.ids)],
            "scheduled_date": "2031-01-01 12:00:00",
            "author_id": self.env.user.partner_id.id,
            "is_note": False,
            "bf_is_draft": True,
            "bf_citation_attendue": attendue,
        })

    # ------------------------------------------------------------------
    # Le témoin positif
    # ------------------------------------------------------------------
    def test_temoin_un_brouillon_cite_part(self):
        """Signature puis citation : la porte laisse passer, et le message est posté."""
        brouillon = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        avant = self.env["mail.message"].search_count(
            [("model", "=", "res.partner"), ("res_id", "=", self.dossier.id)])
        brouillon.post_message()
        apres = self.env["mail.message"].search_count(
            [("model", "=", "res.partner"), ("res_id", "=", self.dossier.id)])
        self.assertEqual(apres, avant + 1, "le brouillon cité n'a pas été posté")
        self.assertFalse(brouillon.exists(), "le brouillon posté devrait avoir disparu")

    def test_un_brouillon_non_marque_part_sans_citation(self):
        """La porte ne vise QUE les brouillons marqués : un premier envoi passe."""
        brouillon = self._brouillon("<p>Bonjour.</p>" + SIGNATURE,
                                    attendue=False, objet="Offre de service")
        brouillon.post_message()
        self.assertFalse(brouillon.exists())

    # ------------------------------------------------------------------
    # Les refus
    # ------------------------------------------------------------------
    def test_sans_citation_la_porte_refuse(self):
        brouillon = self._brouillon("<p>Merci.</p>" + SIGNATURE)
        with self.assertRaises(UserError) as capture:
            brouillon.post_message()
        self.assertIn("ne porte pas le message cité", str(capture.exception))
        message = str(capture.exception)
        # Le refus dit quoi faire DANS L'INTERFACE : les deux issues y sont
        # nommées, et aucun outil interne.
        self.assertIn("Répondre à tous", message)
        self.assertIn("« Fil cité attendu »", message)
        for interne in ("scripts/", ".py", "python3", "MCP", "Claude"):
            self.assertNotIn(interne, message,
                             "le refus ne doit nommer aucun outil interne")

    def test_citation_au_dessus_de_la_signature_refusee(self):
        """L'ordre compte : la citation va SOUS la signature, comme le ferait
        « Répondre à tous » d'un client de courriel.
        """
        brouillon = self._brouillon("<p>Merci.</p>" + CITATION + SIGNATURE)
        with self.assertRaises(UserError) as capture:
            brouillon.post_message()
        self.assertIn("au-dessus de la signature", str(capture.exception))

    def test_le_brouillon_refuse_reste_en_place(self):
        """Un refus ne doit pas consommer le brouillon.

        ⚠️ Vérifié dans un essai à part : `assertRaises` annule ce que le bloc a
        écrit, donc lire l'état après le bloc de l'essai précédent ne prouverait
        rien. Ici le brouillon est créé avant, et relu après l'annulation.
        """
        brouillon = self._brouillon("<p>Merci.</p>" + SIGNATURE)
        identifiant = brouillon.id
        with self.assertRaises(UserError) as capture:
            brouillon.post_message()
        # 🔴 `AccessError` HÉRITE de `UserError` : viser le type seul laisserait
        # passer un refus d'ACL pour un refus de la porte. On vise la phrase.
        self.assertIn("ne porte pas le message cité", str(capture.exception))
        self.assertTrue(
            self.env["mail.scheduled.message"].browse(identifiant).exists(),
            "le brouillon refusé a disparu")

    # ------------------------------------------------------------------
    # Les deux pièges mesurés
    # ------------------------------------------------------------------
    def test_une_reponse_a_une_reponse_passe(self):
        """🔴 Deux marqueurs, et c'est normal : le courriel cité en porte un.

        Exiger EXACTEMENT un marqueur — ce que fait l'assertion de
        l'outil de citation, limite connue et non corrigée — refuserait le cas
        le plus courant : répondre à une réponse.
        """
        corps = "<p>Merci.</p>" + SIGNATURE + CITATION.replace(
            "<blockquote>bonjour</blockquote>",
            "<blockquote>bonjour" + CITATION + "</blockquote>")
        brouillon = self._brouillon(corps)
        self.assertEqual(corps.count('class="bf_citation"'), 2, "le montage est faux")
        brouillon.post_message()
        self.assertFalse(brouillon.exists())

    def test_la_classe_au_milieu_d_autres_est_reconnue(self):
        """`class="x bf_citation y"` est une citation valide.

        Chercher `class="bf_citation"` au caractère refuserait un corps que le
        nettoyeur du champ Html a parfaitement le droit de rendre comme ça.
        """
        brouillon = self._brouillon(
            "<p>Merci.</p>" + SIGNATURE
            + '<div class="autre bf_citation encore"><p>a écrit :</p></div>')
        brouillon.post_message()
        self.assertFalse(brouillon.exists())

    def test_sans_signature_la_citation_seule_suffit(self):
        """En mode `envoi`, le corps n'a pas de signature : l'ordre n'a rien à dire."""
        brouillon = self._brouillon("<p>Merci.</p>" + CITATION)
        brouillon.post_message()
        self.assertFalse(brouillon.exists())

    def test_une_signature_dans_le_fil_cite_ne_fait_pas_un_faux_refus(self):
        """🔴 La signature du courriel CITÉ n'est pas la nôtre.

        Sur une base réelle, des `bf.email` entrants portent déjà
        `o_bf_signature`, parce que le correspondant a répondu en citant notre
        courriel, signature comprise. Un brouillon sans signature à lui — usager
        sans signature, ou locataire en `signature_placement = envoi` — n'avait
        alors que cette occurrence, forcément sous le cité, et se faisait refuser
        « le premier fil cité est au-dessus de la signature ». Sans issue :
        l'outil de citation porte la même lecture au caractère.
        """
        corps = "<p>Merci.</p>" + CITATION.replace(
            "<blockquote>bonjour", "<blockquote>bonjour" + SIGNATURE)
        self.assertIn(MARQUEUR_SIGNATURE, corps, "le montage est faux")
        brouillon = self._brouillon(corps)
        brouillon.post_message()
        self.assertFalse(brouillon.exists(), "faux refus sur la signature du cité")

    def test_une_note_interne_marquee_part_quand_meme(self):
        """La porte ne vise que les courriels : une note ne répond à personne."""
        brouillon = self._brouillon("<p>Note.</p>", objet="Re: question")
        brouillon.is_note = True
        brouillon.post_message()
        self.assertFalse(brouillon.exists(), "une note marquée est restée bloquée")

    def test_un_lot_dont_un_membre_bloque_ne_poste_RIEN(self):
        """🔴 Un refus en milieu de lot laisserait des doublons chez le client.

        Le noyau commite après chaque `message_post` mais ne supprime les
        brouillons qu'à la fin : si le dernier du lot est refusé, le rollback
        annule les suppressions alors que les messages sont partis, et les
        brouillons redeviennent renvoyables. `action_send_now` valide donc le lot
        ENTIER avant d'en poster le premier.
        """
        valide = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        bloque = self._brouillon("<p>Merci.</p>" + SIGNATURE, objet="Re: autre")
        avant = self.env["mail.message"].search_count(
            [("model", "=", "res.partner"), ("res_id", "=", self.dossier.id)])
        with self.assertRaises(UserError) as capture:
            (valide | bloque).action_send_now()
        self.assertIn("ne porte pas le message cité", str(capture.exception))
        self.assertEqual(
            self.env["mail.message"].search_count(
                [("model", "=", "res.partner"), ("res_id", "=", self.dossier.id)]),
            avant, "un message du lot est parti avant le refus")
        self.assertTrue(valide.exists(), "le brouillon valide a été consommé")

    def test_un_marqueur_seulement_en_texte_echappe_est_refuse(self):
        """🔴 La présence du fil cité se décide sur l'ARBRE, pas sur la chaîne.

        Un corps où le marqueur n'apparaît qu'échappé ne porte AUCUN fil cité,
        mais satisfaisait la porte quand elle lisait la chaîne — tout en montrant
        du source au client. L'échappement du champ Html n'est pas théorique ici.
        """
        brouillon = self._brouillon(
            '<p>Merci.</p><p>&lt;div class="bf_citation"&gt;</p>' + SIGNATURE)
        with self.assertRaises(UserError) as capture:
            brouillon.post_message()
        self.assertIn("aucun fil cité", str(capture.exception))

    def test_un_lot_dont_un_brouillon_est_d_un_autre_ne_poste_RIEN(self):
        """🔴 Le refus d'ACL du noyau se voit aussi AVANT le premier envoi.

        `post_message()` du noyau refuse un brouillon dont `create_uid` n'est pas
        l'usager. Levé au milieu d'une boucle, ce refus annule les suppressions
        des brouillons déjà postés — dont les messages sont partis — et les rend
        renvoyables, donc doublés chez le client. Même raisonnement que la garde du
        fil cité.
        """
        # ⚠️ `group_partner_manager` en plus de `group_user` : `mail.scheduled.message`
        # du noyau appelle `_check` à la création, qui exige le droit d'ÉCRIRE sur la
        # fiche porteuse — ici un contact. Sans ce groupe, le montage de l'essai
        # tombe en `AccessError` sur `res.partner` avant de rien mesurer.
        autre = self.env["res.users"].create({
            "name": "Collègue d'essai", "login": "collegue.porte@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])],
        })
        cite = "<p>Merci.</p>" + SIGNATURE + CITATION
        mien = self._brouillon(cite)
        sien = self.env["mail.scheduled.message"].with_user(autre).create({
            "subject": "Re: question", "body": cite, "model": "res.partner",
            "res_id": self.dossier.id, "partner_ids": [(6, 0, self.destinataire.ids)],
            "scheduled_date": "2031-01-01 12:00:00",
            "author_id": autre.partner_id.id, "is_note": False,
            "bf_is_draft": True, "bf_citation_attendue": True,
        })
        avant = self.env["mail.message"].search_count(
            [("model", "=", "res.partner"), ("res_id", "=", self.dossier.id)])
        lot = (mien | sien).with_user(autre)
        with self.assertRaises(UserError) as capture:
            lot.action_send_now()
        self.assertIn("qu'un autre usager a", str(capture.exception))
        self.assertEqual(
            self.env["mail.message"].search_count(
                [("model", "=", "res.partner"), ("res_id", "=", self.dossier.id)]),
            avant, "un message du lot est parti avant le refus")
        self.assertTrue(mien.exists() and sien.exists())

    def test_le_telephone_refuse_d_effacer_le_fil_cite(self):
        """🔴 Le téléphone ne doit pas pouvoir DÉTRUIRE la citation.

        `_mobile_compose_body` en texte brut remplace tout le corps : retoucher
        d'une phrase un brouillon marqué et cité en effaçait la citation, et le
        lot transformait cette perte en refus PERMANENT, la case n'étant pas
        exposée au téléphone. C'est le lot qui crée le cul-de-sac, donc c'est à
        lui de ne pas laisser détruire la citation.
        """
        brouillon = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        with self.assertRaises(UserError) as capture:
            self.env["bf.email"].mobile_draft_save(
                brouillon.id, body="Une phrase de plus.")
        self.assertIn("effacerait", str(capture.exception))
        self.assertTrue(
            self.env["mail.scheduled.message"].browse(brouillon.id)
            ._bf_lire_le_fil_cite(brouillon.body)[0],
            "la citation a été détruite malgré le refus")

    def test_le_telephone_retouche_un_brouillon_non_marque(self):
        """Témoin positif : la garde du téléphone ne vise QUE les marqués."""
        brouillon = self._brouillon("<p>Bonjour.</p>" + SIGNATURE,
                                    attendue=False, objet="Offre de service")
        self.env["bf.email"].mobile_draft_save(
            brouillon.id, body="Une phrase de plus.")
        self.assertIn("Une phrase de plus.", brouillon.body)

    # ------------------------------------------------------------------
    # Le contrat du cron
    # ------------------------------------------------------------------
    def test_le_cron_ne_leve_jamais_et_poste_les_autres(self):
        """🔴 `raise_exception=False` : un message bloqué ne doit pas emporter le lot.

        C'est le contrat du cron du noyau. Lever ici aurait coupé l'envoi des
        messages SUIVANTS, ce qui est exactement ce qu'une porte ne doit pas faire.
        """
        bloque = self._brouillon("<p>Merci.</p>" + SIGNATURE)
        passant = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION,
                                  objet="Re: autre question")
        lot = bloque | passant
        lot._post_message(raise_exception=False)
        self.assertTrue(bloque.exists(), "le message bloqué a été posté quand même")
        self.assertFalse(passant.exists(), "le message valide n'est pas parti")

    def _echu(self, programme):
        """Faire arriver l'heure d'un programmé.

        🔴 Par SQL, et non par `write` : le noyau porte
        `@api.constrains("scheduled_date")` → `_check_scheduled_date`, qui refuse
        d'ÉCRIRE une date passée (`ValidationError`). Or c'est exactement l'état
        dans lequel le cron trouve un programmé. Deux essais du lot écrivaient la
        date à la main et levaient avant d'avoir rien mesuré : corrigé, avec le
        même remède que `test_mobile_compositeur`.
        """
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE mail_scheduled_message SET scheduled_date = now() at time zone "
            "'UTC' - interval '1 minute' WHERE id = %s", (programme.id,))
        programme.invalidate_recordset(["scheduled_date"])

    def _isoler_le_cron(self, *gardes):
        """Sortir du domaine du cron tout ce qui n'est pas à cet essai.

        🔴 La base d'essai peut être le clone d'une base réelle, et `_post_messages_cron`
        balaie TOUTE la table en posant `mail_notify_force_send=True` : un envoi
        programmé d'un vrai dossier, dont la date serait passée, partirait pour
        de vrai à de vrais destinataires depuis une base d'essai. Et l'essai dépendrait
        d'un état qu'il ne pose pas lui-même. On reparque donc les autres en
        brouillon, dans la transaction, qui est annulée à la fin.

        Rend le nombre de lignes écartées, pour que l'essai puisse le dire.
        """
        gardes_ids = [m.id for garde in gardes for m in garde]
        autres = self.env["mail.scheduled.message"].search([
            ("scheduled_date", "<=", fields.Datetime.now()),
            ("bf_is_draft", "=", False),
            ("id", "not in", gardes_ids),
        ])
        autres.write({"bf_is_draft": True})
        return len(autres)

    def test_le_cron_des_brouillons_les_ignore_toujours(self):
        """La porte ne change rien au filtre d'avant : un brouillon n'auto-part pas.

        🔴 L'essai porte un TÉMOIN POSITIF, sans quoi il ne mesure rien : une
        fois les voisins écartés, le domaine du cron serait vide et l'essai
        passerait avec un `_post_messages_cron` réduit à `pass`. Le témoin est un
        programmé échu, non-brouillon, marqué ET cité : il doit partir dans la
        même passe.
        """
        brouillon = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION)
        self._echu(brouillon)
        temoin = self._brouillon("<p>Merci.</p>" + SIGNATURE + CITATION,
                                 objet="Re: le témoin du cron")
        temoin.write({"bf_is_draft": False})
        self._echu(temoin)
        self._isoler_le_cron(brouillon, temoin)
        self.env["mail.scheduled.message"]._post_messages_cron()
        self.assertFalse(temoin.exists(),
                         "le cron n'a rien posté : l'essai ne mesure rien")
        self.assertTrue(brouillon.exists(), "un brouillon est parti tout seul")

    def test_le_cron_ne_se_redeclenche_pas_sur_un_bloque(self):
        """🔴 Le cron ne doit pas tourner sans fin sur un message qu'il refuse.

        Le noyau finit `_post_message` par `self.unlink()` : un message qui
        échoue est supprimé, donc le `search_count` du cron retombe à zéro. La
        porte, elle, GARDE le message bloqué — c'est le contrat. Sans cette
        retouche, le cron se re-déclenchait à chaque passe, pour toujours.
        """
        bloque = self._brouillon("<p>Merci.</p>" + SIGNATURE)
        # Ce n'est plus un brouillon, et son heure est arrivée : le cron le VOIT.
        bloque.write({"bf_is_draft": False})
        self._echu(bloque)
        self._isoler_le_cron(bloque)
        cron = self.env.ref("mail.ir_cron_post_scheduled_message")
        Declencheur = self.env["ir.cron.trigger"]
        # 🔴 Compté APRÈS les écritures : `write` du noyau déclenche lui-même le
        # cron quand la date change. Ce qu'on mesure est la passe, pas l'écriture.
        avant = Declencheur.search_count([("cron_id", "=", cron.id)])
        self.env["mail.scheduled.message"]._post_messages_cron()
        self.assertTrue(bloque.exists(), "le message bloqué a été posté ou supprimé")
        self.assertEqual(
            Declencheur.search_count([("cron_id", "=", cron.id)]), avant,
            "le cron s'est re-déclenché sur un message qu'il ne peut pas poster")
