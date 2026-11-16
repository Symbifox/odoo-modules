"""La moisson ciblée : aller chercher dans la boîte UN message de cette personne.

Décidé le 2026-11-11. L'enrichissement par signature ne lit que ce que la
synchronisation a déjà ingéré. Sur une base réelle, cette fenêtre couvrait dix
semaines : **33** des 929 contacts incomplets y avaient écrit. Les ~900 autres
n'étaient pas inaccessibles, ils étaient simplement plus vieux que la fenêtre.

La reprise intégrale a été écartée — une boîte de plusieurs années porte des
dizaines de milliers de messages, soit des heures de passes et autant de lignes
dans la base d'un client — au profit de cette moisson : pour une adresse
donnée, on demande au serveur ``FROM <adresse>``, on choisit **un ou deux**
messages, et on n'ingère que ceux-là.

Ce que la moisson ne fait pas, et pourquoi :

- ⛔ **rien ne sort de la boîte.** Les dossiers s'ouvrent en ``EXAMINE``
  (``readonly=True``), donc même le ``FETCH (RFC822)`` partagé ne peut pas
  poser ``\\Seen``. Et l'ingestion pose le contexte
  ``bf_email_rules_local_only`` : 🔴 sans lui, le moteur de règles qui tourne
  au ``create`` d'une ``bf.email`` peut (a) marquer la ligne traitée puis
  **archiver le message sur le serveur** (COPY + EXPUNGE) quand le compte a
  l'archivage bilatéral — c'est le défaut par lequel 55 courriels sont sortis
  d'une boîte réelle un soir de 2026 —, (b) **répondre une absence** au vieux
  correspondant, et (c) ingérer une invitation d'agenda : un CANCEL ancien
  **archive l'événement vivant** d'une série récurrente. Une moisson de 900
  fiches répondrait donc à 900 personnes. Le drapeau existe exactement pour ce
  cas-là (l'ajout d'un lot de vieux courriels retenus).
- ⛔ **aucun filigrane touché** : ``_ingest_rfc822`` est appelé directement, et
  c'est ``_sync_imap_folder`` — jamais appelé ici — qui avance
  ``last_uid_inbox``. La moisson ingère par UID choisi, elle ne rejoue pas
  l'histoire.
- ⛔ **le mot de passe ne sort pas d'Odoo** : il vit sur ``bf.email.account`` et
  n'est lu que par ``_ouvrir_imap``. La moisson tourne DANS Odoo, jamais depuis
  un script de l'hôte.

⛔ **Et la ligne moissonnée ne se marque PAS « traitée ».** La tentation est
forte — venue de l'INBOX, elle naît en travail neuf chez son propriétaire, et
un message de 2024 qui apparaît comme neuf est désagréable. Mais 🔴 une ligne
`is_handled` encore présente dans l'INBOX du serveur est **exactement** le
domaine du balayage horaire `_cron_imap_writeback_sweep` (actif par défaut,
toutes les heures) : il apparie par Message-ID, sans regarder ni le compte ni
l'UID, et **archive le message sur le serveur** (COPY + EXPUNGE). Une moisson
de 900 fiches déplacerait 900 messages dans les boîtes de clients — le défaut
même qu'on dit prévenir. `_apply_rules` retire d'ailleurs `is_handled` de ses
valeurs quand `bf_email_rules_local_only` est posé, et son commentaire le dit
mot pour mot. Le tic de boîte est donc le prix assumé.

⚠️ **On préfère un message qui n'est PAS une réponse.** C'est là que
l'expéditeur signe — et c'est l'absence de cette signature qui a contaminé deux
fiches.

⚠️ **Éteinte par défaut.** Le paramètre système
``bf_contact_enrichment.moisson_imap`` l'allume, locataire par locataire : une
montée ne doit pas se mettre à ouvrir les boîtes de cinq clients d'elle-même, et
la mesure qui l'a justifiée vient d'une base en particulier.
"""
import logging

from odoo import SUPERUSER_ID, api, fields, models
from odoo.addons.bf_email_management.models import bf_email_imap

from ..tools import identite

_logger = logging.getLogger(__name__)

#: Combien de boîtes la moisson ouvre au plus pour une adresse. Elle s'arrête
#: à la première qui rend quelque chose.
_MAX_COMPTES = 3
#: Combien de dossiers d'une boîte elle fouille au plus (INBOX puis archives,
#: de la plus récente à la plus vieille).
_MAX_DOSSIERS = 6
#: Combien d'UID le serveur peut rendre avant qu'on se limite aux plus récents.
_MAX_UIDS_LUS = 30
#: Au bout de combien de jours une adresse qui n'a rien donné se recherche.
_JOURS_AVANT_DE_REESSAYER = 30
#: Les préfixes qui disent « ceci est une réponse », donc « probablement sans
#: signature ». Comparés sans accent ni casse, et ``Re[2]:`` compte aussi.
_PREFIXES_REPONSE = (
    "re", "rep", "ref", "res", "tr", "fw", "fwd", "transf", "antw", "sv",
    "odp", "vs", "aw", "wg", "rif", "enc",
)


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_bf_moisson_date = fields.Datetime(
        string="Last mailbox harvest", copy=False, readonly=True,
        help="When the mailboxes were last searched for a message from this "
             "address. Keeps a fruitless search from being repeated at every "
             "pass.",
    )

    # ── Permission, et quand on réessaie ────────────────────────────

    @api.model
    def _bf_moisson_permise(self):
        """Le paramètre système ``bf_contact_enrichment.moisson_imap``.

        ⚠️ **Faux par défaut.** Une montée du module ne doit pas allumer, chez
        cinq locataires d'un coup, une fonction qui se connecte aux boîtes.
        """
        valeur = self.env["ir.config_parameter"].sudo().get_param(
            "bf_contact_enrichment.moisson_imap", "False")
        return str(valeur).strip().lower() in ("1", "true", "vrai", "on", "yes", "oui")

    def _bf_moisson_a_retenter(self):
        """Faux si on a déjà cherché cette adresse récemment.

        🔴 Sans cette mémoire, les ~870 fiches dont la boîte ne porte rien
        payaient le plein tarif — jusqu'à trois ouvertures IMAP, autant de
        ``LIST``, dix-huit ``SELECT``+``SEARCH`` — à CHAQUE passe, pour
        retrouver le même néant.
        """
        self.ensure_one()
        if not self.x_bf_moisson_date:
            return True
        try:
            jours = int(self.env["ir.config_parameter"].sudo().get_param(
                "bf_contact_enrichment.moisson_jours",
                _JOURS_AVANT_DE_REESSAYER))
        except (TypeError, ValueError):
            # ⚠️ Un paramètre posé à « trente » ou à vide levait ici, hors du
            # `try` de l'appelant : trace brute à l'usager par l'assistant.
            jours = _JOURS_AVANT_DE_REESSAYER
        return (fields.Datetime.now() - self.x_bf_moisson_date).days >= jours

    def _bf_comptes_pour_moisson(self):
        """Les boîtes que CE geste a le droit d'ouvrir.

        🔴 Deux chemins, et la différence est une question de vie privée, pas
        de commodité : le lot passe par le cron, **sous uid 1**, et c'est ce
        qui lui donne la boîte de la personne qui reçoit le plus de courrier
        (sur une base réelle, la quasi-totalité des lignes venaient d'une seule
        boîte). Un geste à la main, lui, n'ouvre que les boîtes de celui qui le
        fait : personne ne fouille la boîte d'un collègue en cliquant
        « Enrichir ».
        """
        Compte = self.env["bf.email.account"].sudo()
        # 🔴 Borné à la société du locataire. Sans ça, le lot sous uid 1 élisait
        # les trois comptes les plus récemment synchronisés, **boîtes
        # personnelles comprises** et appartenant à une AUTRE société : il
        # serait allé chercher le message d'un contact d'affaires dans la boîte
        # privée de quelqu'un, et la ligne créée aurait porté la société du
        # compte, pas celle du contact.
        societes = (self.company_id or self.env.company) | self.env.company
        domaine = [("active", "=", True), ("host", "!=", False),
                   ("login", "!=", False), ("user_id", "!=", False),
                   ("company_id", "in", societes.ids)]
        if not (self.env.su or self.env.uid == SUPERUSER_ID):
            domaine.append(("user_id", "=", self.env.uid))
        # ⚠️ `nulls last` : en DESC, PostgreSQL met les NULL en PREMIER, donc un
        # compte jamais synchronisé prenait un créneau avant la boîte la plus
        # active.
        return Compte.search(domaine, order="last_sync_date desc nulls last",
                             limit=_MAX_COMPTES)

    # ── La moisson ──────────────────────────────────────────────────

    def _bf_moissonner_par_adresse(self, par_adresse=2):
        """Ingère au plus ``par_adresse`` messages de cette personne.

        Rend le nombre de lignes ``bf.email`` créées. Ne lève jamais : une
        boîte injoignable est un enrichissement qui n'a rien trouvé, pas une
        passe en échec (le lot en traite des centaines).
        """
        self.ensure_one()
        adresse = identite.adresse(self.email)
        if not adresse or not self._bf_moisson_a_retenter():
            return 0
        try:
            adresse.encode("ascii")
        except UnicodeEncodeError:
            # Un SEARCH sur une valeur non ASCII demande un CHARSET et une
            # négociation ; aucune adresse du corpus n'est dans ce cas.
            return 0
        # La date se pose quoi qu'il arrive : une recherche sans résultat est
        # une recherche faite, et c'est ce qu'on ne veut pas refaire demain.
        self.sudo().x_bf_moisson_date = fields.Datetime.now()
        crees = 0
        for compte in self._bf_comptes_pour_moisson():
            try:
                conn = compte._ouvrir_imap()
            except Exception:  # noqa: BLE001 — une boîte fermée n'arrête rien
                _logger.info(
                    "Moisson ciblée : boîte %s injoignable", compte.display_name,
                    exc_info=True)
                continue
            try:
                crees += self._bf_moisson_dans_compte(
                    conn, compte, adresse, par_adresse)
            except Exception:  # noqa: BLE001
                _logger.warning(
                    "Moisson ciblée : échec sur %s pour %s",
                    compte.display_name, adresse, exc_info=True)
            finally:
                try:
                    conn.logout()
                except Exception:  # noqa: BLE001
                    pass
            if crees:
                break
        if crees:
            _logger.info(
                "Moisson ciblée : %d message(s) ingéré(s) pour %s (partenaire %s)",
                crees, adresse, self.id)
        return crees

    def _bf_moisson_dans_compte(self, conn, compte, adresse, par_adresse):
        """La moisson dans UNE boîte déjà ouverte. Rend le nombre de créations."""
        self.ensure_one()
        # L'ingestion tourne dans l'environnement du PROPRIÉTAIRE de la boîte :
        # c'est de là que `create` tire user_id et company_id, comme la
        # synchronisation (`_sync_account`). Sans ça, la ligne naîtrait au nom
        # de l'opérateur et la règle propriétaire la cacherait à son vrai
        # destinataire. `bf_email_rules_local_only` : voir l'en-tête du fichier
        # — c'est lui qui garantit que rien ne sort.
        BfEmail = self.env["bf.email"].with_user(compte.user_id).with_context(
            allowed_company_ids=(
                compte.company_id or compte.user_id.company_id).ids,
            bf_email_rules_local_only=True,
        )
        crees = 0
        for dossier in self._bf_dossiers_a_fouiller(conn, compte):
            # readonly=True → EXAMINE : rien ne peut sortir ni changer d'état.
            if not bf_email_imap.select_folder(conn, dossier, readonly=True):
                continue
            uids = self._bf_uids_de_l_adresse(conn, adresse)
            if not uids:
                continue
            for uid in self._bf_choisir_les_uids(conn, uids, adresse, par_adresse):
                brut = bf_email_imap.fetch_rfc822(conn, uid)
                if not brut:
                    continue
                try:
                    with self.env.cr.savepoint():
                        # Sans `bf_email_ingest_guard` : une moisson est un
                        # rattrapage VOULU, et la garde des vieux courriels ne
                        # doit pas retenir ce qu'on est allé chercher.
                        if BfEmail._ingest_rfc822(brut, uid, dossier, compte):
                            crees += 1
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "Moisson ciblée : UID %s de %r non ingéré",
                        uid, dossier, exc_info=True)
                if crees >= par_adresse:
                    return crees
            if crees:
                return crees
        return crees

    # ── Les trois choix : où chercher, quoi chercher, quoi garder ───

    @staticmethod
    def _bf_dossiers_a_fouiller(conn, compte):
        """INBOX, puis les archives de la plus récente à la plus vieille.

        ⚠️ L'INBOX ne suffit pas : l'archivage bilatéral a déjà déplacé du
        courrier par le passé (55 courriels sortis d'une boîte réelle un soir
        de 2026), et un message rangé n'est plus là où la fenêtre l'a vu.
        """
        gabarit = (compte.archive_folder or "").strip()
        prefixe = gabarit.split("{")[0].strip().rstrip("/.").lower()
        archives = []
        for dossier in bf_email_imap.list_folders(conn):
            nom = dossier.get("name") or ""
            if dossier.get("noselect") or nom.upper() == "INBOX":
                continue
            if prefixe and nom.lower().startswith(prefixe):
                archives.append(nom)
        # « Archives/2026 » avant « Archives/2025 » : le plus récent d'abord.
        archives.sort(reverse=True)
        return ["INBOX"] + archives[:_MAX_DOSSIERS - 1]

    @staticmethod
    def _bf_uids_de_l_adresse(conn, adresse):
        """Les UID des messages venus de cette adresse, les plus récents d'abord.

        ⚠️ ``SEARCH FROM`` est un match de SOUS-CHAÎNE sur l'en-tête (RFC 3501) :
        ce que le serveur rend est un sur-ensemble, et c'est
        ``_bf_choisir_les_uids`` qui exige l'égalité exacte.
        """
        valeur = bf_email_imap.imap_reject_crlf(adresse, "FROM")
        # Chaîne IMAP entre guillemets : le guillemet et la barre oblique
        # inverse ne peuvent pas y entrer (une adresse n'en porte pas).
        valeur = valeur.replace('"', "").replace("\\", "")
        statut, data = conn.uid("SEARCH", None, "FROM", '"%s"' % valeur)
        if statut != "OK" or not data or not data[0]:
            return []
        brut = data[0]
        if isinstance(brut, bytes):
            brut = brut.decode("ascii", errors="ignore")
        uids = sorted((int(x) for x in brut.split() if x.isdigit()), reverse=True)
        return uids[:_MAX_UIDS_LUS]

    @staticmethod
    def _bf_choisir_les_uids(conn, uids, adresse, par_adresse):
        """Les UID à ingérer : la bonne adresse, et d'abord ce qui n'est pas une réponse.

        🔴 Deux filtres, et les deux comptent :

        - **l'expéditeur doit être exactement la personne.** ``SEARCH FROM``
          étant une sous-chaîne, une adresse courte (``ric@x.test``) ou un
          champ mal formé (``@acme.com``, fréquent en base réelle) ramenait
          les messages d'un inconnu — qu'on aurait ingérés dans la base d'un
          client et attribués à qui l'appariement voudrait.
        - **un premier message avant une réponse.** C'est là que l'expéditeur
          signe ; moissonner une réponse referait exactement le défaut que
          cette version corrige.
        """
        entetes = bf_email_imap.fetch_headers_bulk(conn, uids)
        premiers, ensuite = [], []
        for uid in uids:
            lu = entetes.get(uid)
            if not lu or lu[0] is None:
                continue
            msg = lu[0]
            de = bf_email_imap.first_header(msg, "From") or ""
            if identite.adresse(de) != adresse:
                continue
            sujet = (bf_email_imap.first_header(msg, "Subject") or "").strip()
            tete = sujet.split(":", 1)[0].strip().lower()
            tete = tete.split("[", 1)[0].strip()
            if tete in _PREFIXES_REPONSE:
                ensuite.append(uid)
            else:
                premiers.append(uid)
        return (premiers + ensuite)[:par_adresse]
