import json
import logging
import re

from lxml import html

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

#: La classe que pose l'outil qui cite le courriel reçu
#: sur le bloc cité.
#: ⚠️ Une CLASSE, pas un attribut `data-*` : le nettoyeur du champ Html jette les
#: `data-*` qu'il ne connaît pas, et la classe survit à l'aller-retour en base.
#: On accepte la classe au milieu d'autres (`class="x bf_citation y"`) : chercher
#: `class="bf_citation"` au caractère raterait une citation parfaitement valide.
RE_CITATION = re.compile(r'class="[^"]*\bbf_citation\b[^"]*"')
#: La signature, posée par l'intégration qui rédige, en mode `brouillon`. La citation va
#: SOUS elle, comme le fait « Répondre à tous » d'un client de courriel.
MARQUEUR_SIGNATURE = "o_bf_signature"


class MailScheduledMessage(models.Model):
    _inherit = "mail.scheduled.message"

    # ------------------------------------------------------------------
    # Brouillon vs envoi différé
    # ------------------------------------------------------------------
    # Le modèle du noyau ne connaît qu'une chose : une date d'envoi. « Parquer
    # un brouillon » se faisait donc en posant une date lointaine à la main
    # (sentinelle ~2031) et en envoyant soi-même le moment venu. Ce drapeau dit
    # ce que la date ne dit pas — que la ligne est un BROUILLON, pas un envoi
    # que quelqu'un attend — et c'est lui, pas la date, qui décide du tri, de
    # la date affichée et du refus d'auto-envoi.
    bf_is_draft = fields.Boolean(
        string="Brouillon",
        default=False,
        index=True,
        help="Un brouillon ne part jamais tout seul : le cron l'ignore, quelle "
             "que soit sa date. Il attend « Envoyer maintenant ».",
    )

    # ------------------------------------------------------------------
    # Le fil cité attendu
    # ------------------------------------------------------------------
    # Une réponse à un courriel reçu doit porter le message cité sous la
    # signature, comme l'aurait fait « Répondre à tous ». La règle vivait dans
    # une consigne de rédaction — un texte qu'un seul des assistants
    # lisait. Gen, qui écrit sur les mêmes dossiers, ne la lisait pas
    # et postait des réponses sans fil cité.
    #
    # D'où ce drapeau, posé par l'intégration qui rédige quand elle reconnaît le courriel
    # reçu auquel le brouillon répond, et vérifié ICI à l'envoi : la porte ne
    # dépend plus de ce que l'auteur du brouillon a lu.
    #
    # ⚠️ Deux temps, et c'est voulu : la citation se pose APRÈS le brouillon,
    # par l'outil de citation, qui a besoin de son identifiant. Refuser
    # à la CRÉATION refuserait tous les brouillons. La porte est à l'ENVOI, là
    # où un refus ne coûte qu'une commande à lancer.
    bf_citation_attendue = fields.Boolean(
        string="Fil cité attendu",
        default=False,
        index=True,
        help="Ce brouillon répond à un courriel reçu : il ne peut pas partir "
             "sans le message cité sous la signature. Décocher si la citation "
             "n'a pas lieu d'être.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Le composeur marque ses brouillons par le contexte.

        ⚠️ Pas par un ``default_bf_is_draft`` : ``create`` du noyau passe par
        ``clean_context``, qui retire justement toutes les clés ``default_*``.
        Une clé qui ne commence pas par ``default_`` survit, elle.
        """
        if self.env.context.get("bf_save_as_draft"):
            vals_list = [dict(vals, bf_is_draft=True) for vals in vals_list]
        return super().create(vals_list)

    @api.model
    def _post_messages_cron(self, limit=50):
        """Un brouillon ne part jamais tout seul, même quand sa date arrive.

        Le cron du noyau balaie sur la SEULE date (``scheduled_date <= now``).
        La sentinelle lointaine suffit à le tenir à distance aujourd'hui, mais
        elle finit par arriver : sans ce filtre, tous les brouillons parqués
        partiraient d'un coup le jour venu, à des destinataires qui ne les
        attendent plus. Six lignes recopiées du noyau, pour une clause de plus.
        """
        domain = [("scheduled_date", "<=", fields.Datetime.now()),
                  ("bf_is_draft", "=", False)]
        messages_to_post = self.search(domain, limit=limit)
        messages_to_post.with_context(
            mail_notify_force_send=True)._post_message(raise_exception=False)
        # 🔴 Un message que la porte du fil cité bloque RESTE en place : c'est
        # le contrat de `raise_exception=False`, qui ne lève pas et le retire du
        # lot. `search_count` le recompterait donc à chaque passe et le cron se
        # re-déclencherait SANS FIN sur un message qu'il ne peut pas poster. On
        # ne se re-déclenche que s'il reste du travail POSTABLE. Essai :
        # `test_le_cron_ne_se_redeclenche_pas_sur_un_bloque`.
        #
        # ⚠️ La question se pose sur TOUTE la table, pas sur la fenêtre de
        # `limit` : un bloqué n'est jamais supprimé, il garde donc sa place en
        # tête (l'ordre est l'id, ce modèle n'a pas de `_order`), et au-delà de
        # `limit` bloqués la fenêtre ne contiendrait plus qu'eux. Le balayage
        # complet ne coûte que lorsqu'il reste quelque chose, et il ne lit que
        # ce que la passe suivante aurait posté de toute façon.
        if self.search_count(domain, limit=1):
            bloques, _motifs = self.search(domain)._bf_bloques_du_fil_cite()
            if self.search_count([*domain, ("id", "not in", bloques.ids)], limit=1):
                self.env.ref("mail.ir_cron_post_scheduled_message")._trigger()

    # ------------------------------------------------------------------
    # Ce qu'un envoi programmé garde de son composeur
    # ------------------------------------------------------------------
    # 🔴 Le composeur écrit TOUT ce qu'il enverrait dans
    # ``notification_parameters`` — la copie conforme et la copie cachée de
    # ``mail_composer_cc_bcc`` comprises — mais le noyau ne relit que les clés
    # de sa liste blanche, et ces deux-là n'y sont pas. Un brouillon ou un envoi
    # programmé partait donc SANS son Cc ni son Cci, au poste comme au
    # téléphone, sans un mot : le chatter montrait le message, personne ne
    # voyait qu'une copie manquait. Relevé en, corrigé ici.
    _BF_EXTRA_PARAMETERS = frozenset({
        "recipient_cc_ids",
        "recipient_bcc_ids",
        # La portée des envois du téléphone (S-M6), écrite par
        # ``mail.compose.message._prepare_mail_values`` en programmant.
        "bf_notify_explicit_only",
        # Les abonnés retirés dans le composeur du poste : sans cette
        # clé, programmer un envoi rendrait au cron une liste de destinataires
        # dont le retrait a disparu, et les abonnés décochés recevraient
        # quand même, le seul signe étant un courriel de plus, chez eux.
        "bf_abonnes_retires",
    })

    @api.model
    def _notification_parameters_whitelist(self):
        return super()._notification_parameters_whitelist() | self._BF_EXTRA_PARAMETERS

    def _bf_copy_partners(self):
        """(Cc, Cci) rangés dans les paramètres, en fiches encore vivantes."""
        self.ensure_one()
        try:
            params = json.loads(self.notification_parameters or "{}")
        except (ValueError, TypeError):
            params = {}
        if not isinstance(params, dict):
            params = {}
        Partner = self.env["res.partner"].sudo()

        def fiches(key):
            ids = []
            for raw in params.get(key) or []:
                try:
                    ids.append(int(raw))
                except (TypeError, ValueError):
                    continue
            return Partner.browse(ids).exists()

        return fiches("recipient_cc_ids"), fiches("recipient_bcc_ids")

    def _bf_bloques_du_fil_cite(self):
        """Les messages marqués dont le fil cité manque, et leurs motifs. MUET.

        Séparé de la garde parce que le cron a besoin de SAVOIR sans JOURNALISER :
        `_post_messages_cron` s'interroge une seconde fois après sa passe, pour
        décider s'il se re-déclenche, et un second avertissement par passe
        noierait le journal.
        """
        bloques, motifs = self.browse(), []
        for message in self:
            # Une NOTE interne ne répond pas à un courriel : rien ne la marque
            # aujourd'hui (`schedule_chatter_email` force `is_note=False`), mais
            # le formulaire laisse cocher la case, et une note marquée serait
            # alors impostable à jamais. Garde à un sou.
            if message.is_note or not message.bf_citation_attendue:
                continue
            motif = message._bf_motif_citation_manquante()
            if motif:
                bloques |= message
                objet = message.subject or _("(sans objet)")
                motifs.append(f"#{message.id} « {objet} » : {motif}")
        return bloques, motifs

    def _bf_motif_citation_manquante(self):
        """Le motif si le fil cité manque ou est mal placé, sinon ``None``.

        ⚠️ On ne compte PAS les marqueurs. Le courriel cité en porte un lui aussi
        quand il a été posé par l'outil de citation : exiger exactement un
        marqueur refuserait une réponse à une réponse, qui est le cas courant.
        Ce qui se vérifie est qu'il y en a AU MOINS un, et que le PREMIER vient
        après la signature. C'est l'invariant que l'outil de citation écrit.
        """
        self.ensure_one()
        cites, signature_sous_le_cite = self._bf_lire_le_fil_cite(self.body or "")
        if not cites:
            return _("aucun fil cité dans le corps")
        if signature_sous_le_cite:
            return _("le premier fil cité est au-dessus de la signature")
        return None

    def _bf_lire_le_fil_cite(self, corps):
        """``(combien de fils cités, notre signature est-elle sous le premier)``.

        🔴 Une SEULE lecture pour les deux questions, et c'est l'ARBRE qui
        répond, pas la chaîne. Deux observations l'imposent.

        La signature ne se cherche pas au caractère : des `bf.email` ENTRANTS
        d'une base réelle portent déjà `o_bf_signature` dans leur corps, le correspondant ayant
        répondu en citant notre courriel, signature comprise. Celle-là n'est pas
        la nôtre, elle est DANS le bloc cité. La prendre pour la nôtre refusait
        un brouillon conforme sans signature propre — usager sans signature, ou
        locataire en `signature_placement = envoi`, qui est le repli par défaut
        de l'intégration — et le refus était sans issue, l'outil de citation
        portant la même lecture au caractère.

        La présence ne se décide pas davantage au caractère : un corps où le
        marqueur n'apparaît qu'ÉCHAPPÉ (`&lt;div class="bf_citation"&gt;`) ou
        dans un commentaire satisfaisait la porte SANS porter aucun fil cité,
        tout en montrant du source au client. L'échappement du champ Html n'est
        pas théorique ici.

        ⚠️ Un corps que lxml ne sait pas lire ne fait refuser NI sur l'ordre, ni
        sur la présence au-delà de ce que la chaîne montre : on ne refuse jamais
        sur une analyse qui a échoué.

        ⚠️ LIMITE CONNUE, mesurée : un fil cité mal imbriqué (une balise
        orpheline, artefact courant d'Outlook) peut faire sortir du bloc cité la
        signature qu'il contenait. Elle paraît alors nôtre et sous le cité, donc
        l'ordre est refusé à tort. Il y faut trois choses à la fois : un cité mal
        formé, qui porte une signature, et un brouillon qui n'a pas la sienne —
        impossible là où l'intégration pose toujours la
        signature dans le corps. Le refus reste rattrapable en décochant la case
        au formulaire, et c'est pour ça que le refus la nomme.
        """
        try:
            arbre = html.fragment_fromstring(corps, create_parent="div")
        except Exception:  # noqa: BLE001 — corps illisible : voir la note ci-dessus
            return len(RE_CITATION.findall(corps)), False
        cites, signature_vue, sous = 0, False, False
        for element in arbre.iter():
            classes = (element.get("class") or "").split()
            if "bf_citation" in classes:
                cites += 1
                continue
            if signature_vue or MARQUEUR_SIGNATURE not in classes:
                continue
            if any("bf_citation" in (parent.get("class") or "").split()
                   for parent in element.iterancestors()):
                continue          # la signature du courriel CITÉ, pas la nôtre
            signature_vue, sous = True, cites > 0
        return cites, sous

    def _bf_garde_du_fil_cite(self, raise_exception=True):
        """Les messages de ``self`` qui ont le droit de partir.

        🔴 ``raise_exception=False`` est le contrat du cron du noyau : il balaie
        un lot et ne veut pas qu'un message fasse tomber les autres. On n'y lève
        donc rien — on retire le message du lot et on le dit au journal. Lever
        ici aurait coupé l'envoi des messages SUIVANTS du même lot, ce qui est
        exactement ce que la porte ne doit pas faire. (Un brouillon n'entre pas
        dans ce cron, ``_post_messages_cron`` l'exclut ; la garde tient quand
        même pour un envoi programmé marqué.)
        """
        bloques, motifs = self._bf_bloques_du_fil_cite()
        if not bloques:
            return self
        if not raise_exception:
            _logger.warning(
                "Fil cité attendu et absent : %d message(s) retiré(s) du lot. %s. "
                "Pour débloquer : répondre depuis le courriel reçu, ce qui pose "
                "le fil cité, ou décocher « Fil cité attendu » au formulaire.",
                len(bloques), " ; ".join(motifs))
            return self - bloques
        raise UserError(_(
            "Ce brouillon répond à un courriel reçu et ne porte pas le message "
            "cité. Il n'est pas parti.\n\n%(motifs)s\n\n"
            "Deux façons de continuer :\n"
            "  • répondre avec le fil cité : dans le chatter de la fiche, sur le "
            "courriel reçu, choisir « Répondre » ou « Répondre à tous » dans le "
            "menu « … ». La réponse porte alors le message cité sous la "
            "signature. Y recopier le texte de ce brouillon, puis supprimer ce "
            "brouillon.\n"
            "  • ou décocher « Fil cité attendu » sur le brouillon, si la "
            "citation n'a pas lieu d'être. Au formulaire du poste, ou depuis le "
            "téléphone quand c'est un brouillon.",
            motifs="\n".join(f"  - {m}" for m in motifs),
        ))

    def _bf_garde_du_droit_d_envoyer(self):
        """Le refus d'ACL du noyau, mais AVANT le premier envoi du lot.

        🔴 Même raisonnement que la garde du fil cité, et c'est un second
        examen qui l'a vu : `post_message()` du noyau lève
        « You are not allowed to send this scheduled message » quand
        `create_uid` n'est pas l'usager et qu'il n'est pas admin. Levé au
        MILIEU d'une boucle, ce refus annule les suppressions des brouillons
        déjà postés — dont les messages sont partis — et les rend renvoyables,
        donc doublés chez le client. Le domaine de la boîte filtre sur
        `author_id`, pas sur `create_uid`, et `create_uid` n'est pas toujours
        réparé : le cas est atteignable.
        """
        if self.env.is_admin():
            return
        orphelins = self.filtered(lambda m: m.create_uid.id != self.env.uid)
        if orphelins:
            raise UserError(_(
                "Ce lot contient %(nb)d brouillon(s) qu'un autre usager a "
                "créé(s) : le noyau refuserait de les envoyer, et le refus "
                "tomberait au milieu du lot. Rien n'est parti. Identifiants : "
                "%(ids)s.",
                nb=len(orphelins),
                ids=",".join(str(i) for i in orphelins.ids)))

    def _post_message(self, raise_exception=True):
        """Poster avec le Cc et le Cci, comme le composeur l'aurait fait.

        Les identifiants passent dans les valeurs du message (liste blanche
        ci-dessus) ; les ENVOIS, eux, se décident dans le contexte :
        ``mail_composer_cc_bcc`` lit ``partner_cc_ids`` / ``partner_bcc_ids``
        pour poser les en-têtes et les destinataires, exactement ce que
        ``_action_send_mail_comment`` met en place à l'envoi immédiat.

        Un message sans copie passe par le chemin du noyau, inchangé.
        """
        envoyables = self._bf_garde_du_fil_cite(raise_exception=raise_exception)
        if not envoyables:
            return None
        simples = self.browse()
        for message in envoyables:
            cc, bcc = message._bf_copy_partners()
            if not (cc or bcc):
                simples |= message
                continue
            super(MailScheduledMessage, message.with_context(
                is_from_composer=True,
                partner_cc_ids=cc,
                partner_bcc_ids=bcc,
                # 🔴 Envoi FORCÉ : sans lui, `mail_post_defer` reporte la
                # notification de trente secondes, le cron la rejoue sans ce
                # contexte, et la copie conforme disparaît de nouveau. Le cron
                # du noyau force déjà ; « Envoyer maintenant » (poste et
                # téléphone) ne le faisait pas. Relecture adverse du 2026-09-16.
                mail_notify_force_send=True,
                # Voir `mail.thread.message_notify` : l'avis d'ÉCHEC que le
                # noyau envoie à l'auteur ne doit pas hériter des copies.
                bf_copies_programmees=True,
            ))._post_message(raise_exception=raise_exception)
        if simples:
            return super(MailScheduledMessage, simples)._post_message(
                raise_exception=raise_exception)
        return None

    record_name = fields.Char(
        string="Enregistrement",
        compute="_compute_record_name",
        search="_search_record_name",
    )

    @api.depends("model", "res_id")
    def _compute_record_name(self):
        by_model = {}
        for rec in self:
            if rec.model and rec.res_id:
                by_model.setdefault(rec.model, set()).add(rec.res_id)

        names = {}
        for model, ids in by_model.items():
            if model not in self.env:
                continue
            try:
                records = self.env[model].sudo().browse(list(ids)).exists()
                for r in records:
                    names[(model, r.id)] = r.display_name or ""
            except Exception:
                continue

        for rec in self:
            rec.record_name = names.get((rec.model, rec.res_id), "")

    def _search_record_name(self, operator, value):
        if operator not in ("=", "!=", "like", "ilike", "not like", "not ilike"):
            return [("id", "in", [])]
        matches = self.search([])
        positive = operator in ("=", "like", "ilike")
        needle = (value or "").lower()
        ids = []
        for rec in matches:
            name = (rec.record_name or "").lower()
            if operator in ("=", "!="):
                hit = name == needle
            else:
                hit = needle in name
            if hit == positive:
                ids.append(rec.id)
        return [("id", "in", ids)]

    def action_open_record(self):
        self.ensure_one()
        if not self.model or not self.res_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": self.model,
            "res_id": self.res_id,
            "views": [[False, "form"]],
            "target": "current",
        }

    def action_send_now(self):
        # 🔴 Tout le lot est validé AVANT d'en poster le premier. Le noyau
        # commite chaque message posté mais ne supprime les brouillons qu'à la
        # fin : un refus au troisième laisserait les deux premiers postés ET
        # encore en liste, donc renvoyables en double.
        self._bf_garde_du_fil_cite(raise_exception=True)
        self._bf_garde_du_droit_d_envoyer()
        for rec in self:
            rec.post_message()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Brouillons envoyés",
                "message": f"{len(self)} message(s) posté(s).",
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }
