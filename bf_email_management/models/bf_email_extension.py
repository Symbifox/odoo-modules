"""Ce que l'extension Thunderbird demande, et que l'API mobile n'avait pas.

Deux ajouts, pas plus. Tout le reste de l'extension passe par les routes qui
servent déjà le téléphone (`/handle`, `/snooze`, `/spawn`, `/route`, `/report`,
`/records`, `/logout`), et « Envoyer vers Gen » par l'API de `bf_claude_chat`,
qui reconnaît déjà le jeton d'appareil de la boîte courriel.

**L'appariement par Message-ID.** Thunderbird affiche un courriel ; il faut sa
fiche. L'API ne savait lire un courriel que par son identifiant Odoo, que le
client de courriel ne connaît pas. 🔴 Jamais par l'UID IMAP : le miroir du
module a déjà payé cette leçon (voir `_imap_writeback_*`), l'archivage laisse
l'UID périmé derrière lui. ⚠️ Les deux bords n'écrivent pas l'en-tête pareil,
Thunderbird sans chevrons et Odoo avec : les deux formes sont cherchées.

**Un seul aller-retour.** Le panneau a besoin du courriel, de son contexte
client et des réglages du moment. Trois routes feraient trois allers-retours en
série, et depuis un poste lointain chacun peut coûter plus de 200 ms :
l'écran s'ouvrirait en deux tiers de seconde de latence pure. Une route rend les
trois blocs.
"""
from datetime import timezone

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import email_normalize

from .bf_email_mobile import SPAWN_KINDS

# Ce que le panneau MONTRE d'une liste d'abord : de quoi décider, pas de quoi
# tout lire.
LIGNES_MAX = 5
# 🔴 Ce que la route ENVOIE, qui n'est pas la même chose, et confondre les deux
# a coûté un vrai défaut. Le panneau affichait « 5 » comme nombre de
# factures impayées d'un client qui en avait DIX-SEPT : ce compte était la
# longueur de la liste tronquée. Un chiffre faux est pire qu'un chiffre absent,
# parce qu'il se croit. Et le tri par échéance croissante achevait l'illusion :
# les cinq plus vieilles remplissaient la liste, et les douze plus récentes
# — et leur montant — n'existaient nulle part à l'écran. Trois choses
# distinctes depuis : les lignes qui voyagent (ce plafond), celles qui
# s'affichent avant le dépliage (LIGNES_MAX), et les totaux EXACTS, comptés en
# base, qui ne dépendent d'aucun plafond.
LIGNES_ENVOYEES = 20
# Combien de Message-ID un seul appel d'appariement en lot accepte. Au-delà, ils
# sont ignorés en silence plutôt que refusés : une sélection de mille messages
# est une fausse manœuvre, pas une demande, et rendre 400 ferait échouer le
# geste entier au lieu des seuls surnuméraires. ⚠️ La borne est aussi une garde :
# chaque en-tête devient des paramètres de requête SQL.
LOT_MAX = 200
# Les états fermés de `project.task`. Une tâche faite ou annulée n'est pas un
# « en cours » : l'afficher dans « tâches ouvertes » ferait rouvrir un dossier
# clos par réflexe.
ETATS_FERMES = ("1_done", "1_canceled")


class BfEmail(models.Model):
    _inherit = "bf.email"

    # ------------------------------------------------------------------
    # Appariement
    # ------------------------------------------------------------------
    @api.model
    def _extension_header_forms(self, header):
        """Les écritures possibles d'un même Message-ID.

        ⚠️ Thunderbird rend « abc@x.ca », Odoo stocke « <abc@x.ca> ». Chercher
        une seule forme ne trouve jamais rien, et le panneau dirait « absent de
        Symbifox » sur une boîte entière.
        """
        # Les caractères non imprimables sautent ici : un octet NUL dans le
        # paramètre arrivait jusqu'à psycopg2, qui refuse (« A string literal
        # cannot contain NUL »). La route rendait bien 400, mais chaque appel
        # fautif laissait une ERREUR dans le journal du serveur.
        brut = "".join(c for c in str(header or "") if c.isprintable() and not c.isspace())
        nu = brut.strip("<>")
        # 🔴 Borné : la RFC plafonne une ligne d'en-tête à 998 octets, et rien
        # au-dessus n'est un Message-ID. Sans borne, un appelant authentifié
        # pousse un mégaoctet dans le paramètre d'une requête SQL à chaque
        # appel, pour un résultat toujours vide.
        if not nu or len(nu) > 998:
            return []
        return [nu, f"<{nu}>"]

    @api.model
    def _extension_find(self, header):
        """La fiche de CE courriel, pour l'appelant seulement.

        Le domaine porte `user_id` : la contrainte d'unicité du module est
        `(message_id_header, company_id, user_id)`, donc le même courriel
        reçu par deux personnes existe deux fois. Sans ce filtre, un membre de
        `group_email_admin` (qui lit toutes les boîtes dans l'ORM) ouvrirait la
        fiche de quelqu'un d'autre depuis son propre Thunderbird.
        """
        formes = self._extension_header_forms(header)
        if not formes:
            return self.browse()
        return self.search(
            [("message_id_header", "in", formes), ("user_id", "=", self.env.uid)],
            # Le plus récent d'abord : une même conversation multi-société peut
            # porter deux fiches, et c'est celle qu'on vient de recevoir qui
            # intéresse la personne.
            order="date desc, id desc", limit=1,
        )

    # ------------------------------------------------------------------
    # La route unique du panneau
    # ------------------------------------------------------------------
    @api.model
    def extension_open(self, header):
        """Le courriel, son contexte et les réglages du moment, ou False."""
        rec = self._extension_find(header)
        if not rec:
            return False
        return {
            "email": rec._extension_email_dict(),
            "context": rec._extension_context(),
            "config": self._extension_config(),
        }

    def _extension_email_dict(self):
        """Le même vocabulaire que l'API mobile, plus l'adresse web de la fiche.

        Réutilisé plutôt que recopié : deux descriptions du même courriel
        divergeraient au premier champ ajouté.
        """
        self.ensure_one()
        data = self._mobile_message_dict(full=False)
        data.update({
            "is_awaiting_reply": self.is_awaiting_reply,
            "url": self._extension_url("bf.email", self.id),
        })
        return data

    @api.model
    def _extension_url(self, model, res_id):
        """L'adresse web d'une fiche, pour un client qui n'est pas un navigateur.

        ⚠️ `web.base.url` peut être faux (il se fige au premier appel du
        serveur). On ne l'invente pas pour autant : vide, le panneau fabrique
        l'adresse depuis l'instance qu'il interroge, qui est forcément la bonne.
        """
        base = (self.env["ir.config_parameter"].sudo()
                .get_param("web.base.url") or "").rstrip("/")
        if not base or not model or not res_id:
            return ""
        return f"{base}/odoo/{model}/{res_id}"

    # ------------------------------------------------------------------
    # Le contexte client
    # ------------------------------------------------------------------
    def _extension_debut_du_jour(self):
        """Minuit aujourd'hui dans le fuseau de l'APPELANT, rendu en UTC naïf.

        🔴 Le seuil du retard d'une tâche est une DATE, pas un instant, et
        `project.task.date_deadline` est un `timestamp` en Odoo 18 — qu'un
        module de fuseau peut normaliser à 06 h locale (`bf_timezone`). Trois pièges d'un coup :

        * comparer un datetime à `fields.Date.context_today` lève
          `TypeError: can't compare datetime.datetime to datetime.date`. Trouvé
          par l'essai des tâches en retard ; sur une vraie base, le panneau serait
          tombé en 500 sur le premier client ayant une échéance posée, c'est-à-
          dire à peu près tous ;
        * comparer à « maintenant » rendrait rouge, dès 06 h 01, une tâche due
          AUJOURD'HUI ;
        * le fuseau est celui de l'appelant, pas celui du serveur : un usager
          à l'autre bout du monde est peut-être déjà demain.

        ⚠️ Le MÊME seuil sert au drapeau de chaque ligne et au compte des
        retards. Deux seuils, et le titre dirait « 1 en retard » au-dessus d'une
        liste où aucune ligne n'est marquée.
        """
        local = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        minuit = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return minuit.astimezone(timezone.utc).replace(tzinfo=None)

    def _extension_partner(self):
        """Le correspondant, et à défaut la fiche qui porte son adresse.

        Un courriel reçu d'une adresse jamais rattachée n'a pas de
        `partner_id` : la fiche existe parfois quand même, et c'est elle qui
        porte le dossier du client.
        """
        self.ensure_one()
        if self.partner_id:
            return self.partner_id
        adresse = email_normalize(self.email_from or "")
        if not adresse:
            return self.env["res.partner"]
        return self.env["res.partner"].search(
            [("email_normalized", "=", adresse)], order="id asc", limit=1)

    def _extension_context(self):
        """Ce qu'il faut savoir du client avant de répondre au courriel.

        🔴 Chaque bloc est lu avec les droits de l'APPELANT, jamais en sudo : le
        panneau ne doit pas montrer ce qu'Odoo lui cacherait. Et un bloc refusé
        n'est pas un bloc vide : il est nommé dans `restricted`, sinon « aucune
        tâche ouverte » voudrait dire deux choses opposées.
        """
        self.ensure_one()
        refuses = []
        partner = self._extension_partner()
        client = partner.commercial_partner_id or partner
        contexte = {
            "partner": self._extension_partner_dict(partner),
            "linked": self._extension_linked_dict(),
            "tasks": [],
            "invoices": [],
            "hour_bank": None,
            "restricted": refuses,
            # ⚠️ Clé NEUVE, à côté des listes et non à leur place : une
            # extension déjà posée lit `invoices` comme une liste. Changer sa
            # forme aurait cassé les postes restés en 0.3.1 — ils ignorent une
            # clé qu'ils ne connaissent pas, ils ne survivent pas à un objet là
            # où ils attendent un tableau.
            "totals": {},
        }
        if not client:
            return contexte
        for cle, lecteur, compteur in (
                ("tasks", self._extension_tasks, self._extension_tasks_total),
                ("invoices", self._extension_invoices, self._extension_invoices_total),
                ("hour_bank", self._extension_hour_bank, None)):
            try:
                valeur = lecteur(client)
                total = compteur(client) if compteur else None
            except AccessError:
                # Le refus est la mesure : le dire, et continuer les autres blocs.
                refuses.append(cle)
                continue
            # 🔴 Rien n'est posé avant que les DEUX lectures soient passées. Un
            # total posé avant une liste refusée afficherait « 17 factures » en
            # titre d'une liste vide, c'est-à-dire le défaut d'aujourd'hui à
            # l'envers.
            contexte[cle] = valeur
            if total is not None:
                contexte["totals"][cle] = total
        return contexte

    def _extension_partner_dict(self, partner):
        if not partner:
            return None
        societe = partner.commercial_partner_id
        return {
            "id": partner.id,
            "name": partner.display_name or "",
            "email": partner.email or "",
            "company_name": (societe.name or "") if societe != partner else "",
            "url": self._extension_url("res.partner", partner.id),
        }

    def _extension_linked_dict(self):
        """Là où ce courriel est déjà classé, s'il l'est."""
        self.ensure_one()
        ref = self._mobile_record_ref()
        if not ref:
            return None
        return {**ref, "url": self._extension_url(ref["model"], ref["id"])}

    def _extension_tasks_domain(self, client):
        """🔴 Un seul domaine pour la liste ET pour le compte.

        Deux copies divergent le jour où l'une change : le titre dirait douze
        et la liste montrerait autre chose. C'est la même raison qui fait que
        `_extension_invoices_domain` existe.
        """
        return [("partner_id", "child_of", client.id),
                ("state", "not in", ETATS_FERMES)]

    def _extension_tasks(self, client):
        """Les tâches ouvertes du client, les plus pressées d'abord."""
        if "project.task" not in self.env:
            return []
        taches = self.env["project.task"].search(
            self._extension_tasks_domain(client),
            # Une échéance nulle passe après une échéance posée : c'est l'ordre
            # dans lequel on veut les voir avant de répondre à un client.
            order="date_deadline asc, priority desc, id desc", limit=LIGNES_ENVOYEES)
        etats = dict(taches._fields["state"]._description_selection(self.env))
        seuil = self._extension_debut_du_jour()
        return [{
            "id": t.id,
            "name": t.display_name or "",
            "project": t.project_id.display_name or "",
            "deadline": t.date_deadline or "",
            "state_label": etats.get(t.state, ""),
            # Calculé ici et non dans le panneau : le poste et le serveur ne
            # sont pas dans le même fuseau, et « en retard » comparé à la date
            # du poste rougit une tâche due aujourd'hui quand l'usager est en
            # avance d'un jour sur le serveur.
            "overdue": bool(t.date_deadline and t.date_deadline < seuil),
            "url": self._extension_url("project.task", t.id),
        } for t in taches]

    def _extension_tasks_total(self, client):
        """Combien de tâches ouvertes exactement, et combien sont en retard."""
        if "project.task" not in self.env:
            return None
        Task = self.env["project.task"]
        base = self._extension_tasks_domain(client)
        return {
            "count": Task.search_count(base),
            "overdue_count": Task.search_count(
                base + [("date_deadline", "<", self._extension_debut_du_jour())]),
        }

    def _extension_invoices_domain(self, client):
        """Les factures de vente encore dues. Pas les brouillons, pas les avoirs.

        🔴 Un seul domaine pour la liste ET pour les totaux, pour la raison
        dite plus haut : deux copies finissent par ne plus compter la même
        chose, et c'est le total qu'on croit.
        """
        return [("commercial_partner_id", "=", client.id),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("payment_state", "not in", ("paid", "reversed", "invoicing_legacy")),
                ("amount_residual", ">", 0)]

    def _extension_invoices(self, client):
        """Les factures dues, la plus pressée d'abord."""
        if "account.move" not in self.env:
            return []
        factures = self.env["account.move"].search(
            self._extension_invoices_domain(client),
            order="invoice_date_due asc, id desc", limit=LIGNES_ENVOYEES)
        aujourdhui = fields.Date.context_today(self)
        return [{
            "id": f.id,
            "name": f.name or "",
            "residual": f.amount_residual,
            "currency": f.currency_id.name or "",
            "date_due": f.invoice_date_due or "",
            "overdue": bool(f.invoice_date_due and f.invoice_date_due < aujourdhui),
            # ⚠️ « En paiement » n'est pas « impayée » : le paiement est saisi,
            # il n'est pas lettré. Le résidu reste pourtant plein, et un compte
            # coché « à lettrer » après coup le gèle là pour de bon. Réclamer
            # une de celles-là à un client qui a payé est le pire courriel que
            # ce panneau puisse faire écrire : il est donc dit, et dit à part.
            "in_payment": f.payment_state == "in_payment",
            "url": self._extension_url("account.move", f.id),
        } for f in factures]

    def _extension_invoices_total(self, client):
        """Ce qui est dû en tout, par devise, et ce qui le nuance.

        🔴 Groupé par DEVISE, jamais sommé à plat. Additionner des montants de
        devises différentes rend un nombre qui n'existe pas, et il s'afficherait
        avec l'air d'un total — exactement la faute qu'on corrige ici, en plus
        grave, parce qu'elle serait indétectable à l'œil.

        ⚠️ Compté en base, c'est tout l'objet du bloc : les lignes sont
        plafonnées, le total ne l'est pas. `_read_group` fait le même contrôle
        d'accès et passe par les mêmes `ir.rule` que la recherche, donc un total
        ne peut pas révéler ce que la liste cacherait.
        """
        if "account.move" not in self.env:
            return None
        base = self._extension_invoices_domain(client)
        aujourdhui = fields.Date.context_today(self)
        tout = self._extension_par_devise(base)
        echu = self._extension_par_devise(
            base + [("invoice_date_due", "<", aujourdhui)])
        paiement = self._extension_par_devise(
            base + [("payment_state", "=", "in_payment")])
        devises = []
        for nom in sorted(tout):
            compte, residu = tout[nom]
            e_compte, e_residu = echu.get(nom, (0, 0.0))
            p_compte, p_residu = paiement.get(nom, (0, 0.0))
            devises.append({
                "currency": nom,
                "count": compte,
                "residual": residu,
                "overdue_count": e_compte,
                "overdue_residual": e_residu,
                "in_payment_count": p_compte,
                "in_payment_residual": p_residu,
            })
        return {
            "count": sum(d["count"] for d in devises),
            "overdue_count": sum(d["overdue_count"] for d in devises),
            "in_payment_count": sum(d["in_payment_count"] for d in devises),
            # Les comptes s'additionnent entre devises, les montants NON : eux
            # restent dans `currencies`, chacun avec la sienne.
            "currencies": devises,
        }

    def _extension_par_devise(self, domaine):
        """{nom de devise: (compte, résidu)} pour ce domaine.

        Une devise sans nom (fiche incomplète) tombe sous "" plutôt que de
        faire lever : le panneau montrera un montant sans symbole, ce qui est
        encore une information, là où une exception ne montrerait rien du tout.
        """
        groupes = self.env["account.move"]._read_group(
            domaine, ["currency_id"], ["__count", "amount_residual:sum"])
        return {(devise.name or ""): (compte, residu or 0.0)
                for devise, compte, residu in groupes}

    def _extension_hour_bank(self, client):
        """Le solde de la banque d'heures du client, quand le module est là.

        ⚠️ `current_balance` est un champ CALCULÉ : le lire sur un
        `search_read` le calcule. Une seule fiche à la fois, donc, et seulement
        celle du client affiché.
        """
        if "hour.bank.client" not in self.env:
            return None
        banque = self.env["hour.bank.client"].search(
            [("partner_id", "=", client.id)], order="id desc", limit=1)
        if not banque:
            return None
        return {
            "id": banque.id,
            "balance": banque.current_balance,
            "url": self._extension_url("hour.bank.client", banque.id),
        }

    # ------------------------------------------------------------------
    # L'appariement EN LOT, pour la pastille et la sélection multiple
    # ------------------------------------------------------------------
    @api.model
    def extension_resolve(self, headers):
        """Apparier plusieurs Message-ID d'un coup, et dire l'essentiel de chacun.

        🔴 Une route à part de `/extension/open`, et pour une raison chiffrée.
        Le contexte complet d'un gros client pèse une dizaine de kilo-octets. Le client de courriel en
        a besoin deux fois dans des cas où il ne veut PAS tout ça :

        * la pastille du bouton, peinte à chaque courriel affiché — onze
          kilo-octets par courriel survolé, pour afficher un chiffre ;
        * les gestes sur une sélection de vingt messages — vingt allers-retours
          en série, soit plusieurs secondes de latence pure depuis un
          poste lointain (plus de 200 ms l'aller-retour).

        Celle-ci rend deux cents octets par courriel, et un seul aller-retour
        pour toute la sélection.

        ⛔ Elle ne marque RIEN comme lu et ne change rien : c'est une lecture.

        ⚠️ Le compte des retards est celui du CLIENT, pas du courriel, et il est
        calculé une fois par client même si dix courriels viennent de lui.
        """
        # 🔴 Les échéances de report voyagent AVEC la réponse, recalculées à
        # chaque appel. Elles sont ABSOLUES : un client qui les garderait
        # proposerait demain « dans 1 heure » pour une heure passée, et le
        # courriel reviendrait en boîte au moment même où on le reporte. Même
        # raison que dans `_extension_config`, et c'est pour ça qu'elles ne
        # sont pas servies une fois à l'appariement de l'appareil.
        formes = {}
        for header in (headers or [])[:LOT_MAX]:
            for forme in self._extension_header_forms(header):
                formes.setdefault(forme, str(header))
        if not formes:
            return {"emails": [], "snooze_presets": self._mobile_snooze_presets()}
        # 🔴 Bornée à l'appelant, comme `_extension_find` : la règle de lecture
        # totale de `group_email_admin` laisserait sinon un administrateur
        # recevoir la copie d'une collègue du même Message-ID.
        courriels = self.search([
            ("message_id_header", "in", list(formes)),
            ("user_id", "=", self.env.uid),
        ])
        # Le compte des retards, une fois par client : dix courriels du même
        # correspondant ne doivent pas coûter dix fois la même requête.
        retards = {}
        sorties = []
        for courriel in courriels:
            partner = courriel._extension_partner()
            client = (partner.commercial_partner_id or partner) if partner else partner
            cle = client.id if client else 0
            if cle not in retards:
                retards[cle] = courriel._extension_retards(client)
            sorties.append({
                "header": formes.get(courriel.message_id_header or "", ""),
                "id": courriel.id,
                "subject": courriel.subject or "",
                "status": courriel.status or "",
                "direction": courriel.direction or "",
                "is_handled": bool(courriel.is_handled),
                "partner_name": (client.display_name or "") if client else "",
                **retards[cle],
            })
        return {"emails": sorties, "snooze_presets": self._mobile_snooze_presets()}

    def _extension_retards(self, client):
        """Ce qui est EN RETARD chez ce client, en deux comptes et pas un octet de plus.

        🔴 `None` et non `0` quand les droits refusent : la pastille du bouton
        est un cri, et crier « 0 » pour « je n'ai pas pu regarder » est
        exactement la faute que la 18.0.11.73.0 vient de corriger ailleurs.
        """
        if not client:
            return {"overdue_invoices": 0, "overdue_tasks": 0, "partial": False}
        sortie = {"overdue_invoices": None, "overdue_tasks": None, "partial": False}
        try:
            total = self._extension_invoices_total(client)
            sortie["overdue_invoices"] = (total or {}).get("overdue_count", 0)
        except AccessError:
            sortie["partial"] = True
        try:
            total = self._extension_tasks_total(client)
            sortie["overdue_tasks"] = (total or {}).get("overdue_count", 0)
        except AccessError:
            sortie["partial"] = True
        return sortie

    # ------------------------------------------------------------------
    # Les signatures, pour le client de courriel du poste
    # ------------------------------------------------------------------
    @api.model
    def extension_identities(self):
        """Les identités d'envoi de l'appelant, avec la signature à poser.

        Sert au client de courriel du poste, qui porte ses propres identités :
        il apparie sur l'adresse normalisée et recopie la signature. Symbifox
        fait autorité, le poste suit.

        🔴 `_usable_for` et pas une recherche à la main : il filtre déjà sur
        `verified` ET `active`, et c'est le garde qui empêche d'écrire au nom
        d'une adresse qu'on n'a pas prouvé posséder. Une identité non vérifiée
        poussée vers le poste y poserait une signature pour une adresse que le
        serveur refuserait d'envoyer : le poste promettrait ce que Symbifox
        interdit.

        ⚠️ La signature vient de `_signature_for`, donc avec son repli sur
        celle de la fiche utilisateur. Deux identités sans signature propre
        reçoivent la même, et c'est voulu : c'est déjà ce qui part quand on
        écrit depuis Odoo.

        ⛔ Aucun secret ici. Le modèle porte `mail_server_id`, et un serveur
        sortant traîne des identifiants : on ne rend que ce qui s'affiche dans
        un courriel.
        """
        identities = self.env["bf.email.identity"]._usable_for(self.env.user)
        return {"identities": [{
            "id": identity.id,
            "email": identity.email or "",
            "email_normalized": identity.email_normalized or "",
            "name": identity.name or "",
            "signature_html": identity._signature_for(),
            "is_default": bool(identity.is_default),
        } for identity in identities if identity.email]}

    # ------------------------------------------------------------------
    # Les réglages du moment
    # ------------------------------------------------------------------
    @api.model
    def _extension_config(self):
        """Ce que le panneau a besoin de savoir à CHAQUE ouverture.

        🔴 Les reports (`snooze_presets`) portent des échéances ABSOLUES,
        calculées maintenant : une extension qui garderait cette liste
        proposerait demain « dans 1 heure » pour une heure passée, et le
        courriel reviendrait en boîte aussitôt reporté. C'est pour ça que ces
        réglages voyagent avec le contexte au lieu d'être servis une fois à
        l'appariement.

        Taillé pour le panneau, pas une copie de `get_mobile_config` : la
        config du téléphone porte les identités d'envoi, les comptes, la marque
        et les pastilles, dont rien ici ne se sert.
        """
        return {
            "user_name": self.env.user.name or "",
            "snooze_presets": self._mobile_snooze_presets(),
            "routable_models": self._mobile_routable_models(),
            "spawn_kinds": sorted(
                genre for genre, (model, _methode) in SPAWN_KINDS.items()
                if model in self.env
            ),
        }
