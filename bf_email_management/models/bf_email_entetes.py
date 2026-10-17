"""Rattrapage des en-têtes bruts.

Ni la passerelle ni le chatter n'écrivaient ``raw_headers``, et les
rattrapages du miroir IMAP ne les écrivaient pas non plus. Les signaux qui en
dépendent (désabonnement, envoi de masse, fuseau de l'expéditeur, règles sur
en-tête, plainte de pourriel) restaient muets sur ces lignes.

Trois sources, dans l'ordre du moins cher au plus cher :

1. **sur place** : la copie brute de la ligne (``raw_rfc822``), sinon l'original
   qu'Odoo joint au message de la passerelle (``original_email.eml``) ;
2. **par UID** : le dossier et l'UID notés sur la ligne, vérifiés par le
   Message-ID (un UID ne vaut que dans son dossier, et la boîte est archivée) ;
3. **par balayage** (``balayer=True``, à la main) : le Message-ID cherché dans
   les dossiers que relit la réconciliation, pour les lignes que l'UID n'a pas
   retrouvées.

Tout est en lecture seule côté serveur (``BODY.PEEK``) : aucun drapeau, aucun
déplacement. Côté Odoo, seuls ``raw_headers``, l'objet et l'expéditeur
(dépliés, lettres rendues) sont écrits ; ni le dossier ni l'UID de la ligne ne
bougent, parce que le miroir s'en sert pour décider de « Traité ».
"""

import base64
import logging
from datetime import timedelta

from odoo import api, fields, models

from . import bf_email_imap

_logger = logging.getLogger(__name__)

# Lignes relues par aller-retour IMAP.
LOT_IMAP = 200
# Le cron ne regarde que les lignes récentes : une ligne sans aucune source
# resterait sinon en tête de file chaque nuit, et la file n'avancerait plus.
CRON_JOURS = 7
CRON_LIMITE = 500

_SQL_OBJETS_A_REPARER = r"""
    SELECT id FROM bf_email
     WHERE subject ~ '[\r\n]'
        OR email_from ~ '[\r\n]'
        OR strpos(subject, U&'\FFFD') > 0
        OR subject ~ ('[ÂÃ][' || chr(128) || '-' || chr(191) || ']')
     ORDER BY id
"""


class BfEmailEntetes(models.Model):
    _inherit = "bf.email"

    # ------------------------------------------------------------------
    # Écriture
    # ------------------------------------------------------------------
    def _bf_poser_entetes(self, headers):
        """Écrire ``headers`` sur la ligne, et réparer l'objet au passage.

        Rend ``True`` si la ligne a été écrite. Ne lève jamais.
        """
        self.ensure_one()
        if not headers:
            return False
        vals = {"raw_headers": headers}
        vals.update(self._bf_vals_objet_repare(headers))
        try:
            with self.env.cr.savepoint():
                self.write(vals)
        except Exception:  # noqa: BLE001
            _logger.warning("bf.email #%s : en-têtes non écrits", self.id,
                            exc_info=True)
            return False
        return True

    def _bf_vals_objet_repare(self, headers=None):
        """``{subject, email_from}`` à réécrire, seulement ce qui change."""
        self.ensure_one()
        headers = self.raw_headers if headers is None else headers
        vals = {}
        subject = self._bf_objet_repare(self.subject or "", headers or "")
        if subject != (self.subject or ""):
            vals["subject"] = subject
        email_from = bf_email_imap.unfold_display(self.email_from or "")
        if email_from != (self.email_from or ""):
            vals["email_from"] = email_from
            # `category` est calculé sur `email_from` mais modifiable (règle,
            # geste) : réécrire l'expéditeur le recalculerait et la ligne
            # changerait de dossier. On écrit la valeur qu'elle a.
            vals["category"] = self.category
        return vals

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------
    def _bf_entetes_locaux(self):
        """En-têtes tirés d'une copie gardée ici, ou "" : rien sur le réseau."""
        self.ensure_one()
        raw_b64 = self.with_context(bin_size=False).raw_rfc822
        if raw_b64:
            try:
                headers = bf_email_imap.headers_text_from_bytes(
                    base64.b64decode(raw_b64))
            except Exception:  # noqa: BLE001 - copie illisible
                headers = ""
            if headers:
                return headers
        return self._bf_entetes_du_message(self.mail_message_id)

    def _bf_comptes_de_la_ligne(self):
        """Le compte qui a vu la ligne, sinon les comptes actifs du propriétaire."""
        self.ensure_one()
        if self.account_id:
            return self.account_id
        return self.env["bf.email.account"].sudo().search([
            ("user_id", "=", self.user_id.id), ("active", "=", True),
        ])

    # ------------------------------------------------------------------
    # La passe
    # ------------------------------------------------------------------
    @api.model
    def _bf_rattraper_entetes(self, imap=True, balayer=False, limite=None,
                              depuis=None, ids=None):
        """Donner leurs en-têtes aux lignes qui n'en ont pas. Idempotent.

        ``imap`` : relire le serveur par UID ; ``balayer`` : chercher ensuite
        le Message-ID dans tous les dossiers (lent, à la main) ; ``limite`` :
        nombre de lignes au plus ; ``depuis`` : ne regarder que les lignes
        créées après cette date ; ``ids`` : ne regarder que ces lignes.

        Ne valide rien (pas de ``commit``) : l'appelant découpe en lots.
        Rend les compteurs de la passe.
        """
        Rows = self.sudo().with_context(active_test=False,
                                        tracking_disable=True)
        domain = ["|", ("raw_headers", "=", False), ("raw_headers", "=", "")]
        if depuis:
            domain.append(("create_date", ">=", depuis))
        if ids:
            domain.append(("id", "in", list(ids)))
        rows = Rows.search(domain, order="id desc", limit=limite)
        stats = {"vues": len(rows), "sur_place": 0, "par_uid": 0,
                 "par_balayage": 0, "sans_source": 0}

        reste = Rows.browse()
        for row in rows:
            if row._bf_poser_entetes(row._bf_entetes_locaux()):
                stats["sur_place"] += 1
            else:
                reste |= row

        if imap and reste:
            trouvees = Rows._bf_rattraper_par_uid(reste)
            stats["par_uid"] = len(trouvees)
            reste -= trouvees
        if imap and balayer and reste:
            trouvees = Rows._bf_rattraper_par_balayage(reste)
            stats["par_balayage"] = len(trouvees)
            reste -= trouvees
        stats["sans_source"] = len(reste)
        _logger.info("bf.email en-têtes : %s", stats)
        return stats

    @api.model
    def _bf_rattraper_par_uid(self, rows):
        """Relire chaque ligne dans le dossier et à l'UID qu'elle porte."""
        trouvees = self.browse()
        par_compte = {}
        for row in rows:
            if not (row.imap_uid and row.imap_folder
                    and str(row.imap_uid).isdigit()
                    and row.message_id_header and row.account_id):
                continue
            par_compte.setdefault(row.account_id, {}).setdefault(
                row.imap_folder, {})[int(row.imap_uid)] = row
        for account, dossiers in par_compte.items():
            conn = self._bf_ouvrir(account)
            if not conn:
                continue
            try:
                for dossier, par_uid in dossiers.items():
                    if not bf_email_imap.select_folder(conn, dossier,
                                                       readonly=True):
                        continue
                    _ecrites, rates = self._bf_ecrire_entetes_imap(
                        conn, par_uid, account)
                    for uid, row in par_uid.items():
                        if uid not in rates and row.raw_headers:
                            trouvees |= row
                    self._bf_valider_le_lot()
            except Exception:  # noqa: BLE001 - réseau : le compte suivant
                _logger.warning("bf.email en-têtes (%s) : passe par UID "
                                "interrompue", account.display_name,
                                exc_info=True)
            finally:
                self._bf_fermer(conn)
        return trouvees

    @api.model
    def _bf_rattraper_par_balayage(self, rows):
        """Chercher le Message-ID de chaque ligne dans tous les dossiers.

        Lit les Message-ID de chaque dossier par lots (quatre champs, sans le
        corps), puis les en-têtes complets des seuls messages voulus.
        """
        trouvees = self.browse()
        voulues = {}
        for row in rows:
            mid = bf_email_imap.normalize_message_id(row.message_id_header)
            if not mid:
                continue
            for account in row._bf_comptes_de_la_ligne():
                voulues.setdefault(account, {}).setdefault(mid, row)
        for account, par_mid in voulues.items():
            par_mid = {m: r for m, r in par_mid.items() if not r.raw_headers}
            if not par_mid:
                continue
            conn = self._bf_ouvrir(account)
            if not conn:
                continue
            try:
                # Les dossiers de la réconciliation : ni corbeille, ni
                # « Migadu Backups », ni le « Tous les messages » de Gmail.
                dossiers = account._reconcile_folder_names(
                    served=bf_email_imap.list_folders(conn))
                for dossier in dossiers:
                    if not par_mid:
                        break
                    if not bf_email_imap.select_folder(conn, dossier,
                                                       readonly=True):
                        continue
                    uids = bf_email_imap.search_uids_in_range(conn)
                    a_lire, vus = {}, set()
                    for start in range(0, len(uids), LOT_IMAP):
                        entetes = bf_email_imap.fetch_headers_bulk(
                            conn, uids[start:start + LOT_IMAP])
                        for uid, (msg, _seen) in entetes.items():
                            mid = bf_email_imap.normalize_message_id(
                                msg.get("Message-ID", ""))
                            # Un seul UID par message : le premier vu.
                            if mid in par_mid and mid not in vus:
                                vus.add(mid)
                                a_lire[int(uid)] = par_mid[mid]
                    if not a_lire:
                        continue
                    self._bf_ecrire_entetes_imap(conn, a_lire, account)
                    for row in a_lire.values():
                        if row.raw_headers:
                            trouvees |= row
                            par_mid.pop(bf_email_imap.normalize_message_id(
                                row.message_id_header), None)
                    self._bf_valider_le_lot()
            except Exception:  # noqa: BLE001
                _logger.warning("bf.email en-têtes (%s) : balayage "
                                "interrompu", account.display_name,
                                exc_info=True)
            finally:
                self._bf_fermer(conn)
        return trouvees

    @api.model
    def _bf_valider_le_lot(self):
        """Valider après chaque dossier, seulement quand on le demande.

        La reprise de la prod (``bf_entetes_commit``) tourne longtemps : une
        panne au dernier dossier ne doit pas défaire les précédents. Le cron
        et les essais ne le demandent pas.
        """
        if self.env.context.get("bf_entetes_commit"):
            self.env.cr.commit()  # pylint: disable=invalid-commit

    @api.model
    def _bf_ouvrir(self, account):
        if not (account.host and account.login and account.password):
            return None
        try:
            return account._ouvrir_imap()
        except Exception as exc:  # noqa: BLE001 - un compte injoignable
            # n'arrête pas la passe : ImapConnectionError, mais aussi un
            # OSError ou un imaplib.error qui passerait au travers.
            _logger.warning("bf.email en-têtes (%s) : %s",
                            account.display_name, exc)
            return None

    @api.model
    def _bf_fermer(self, conn):
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass

    @api.model
    def _bf_reparer_objets(self):
        """Déplier objets et expéditeurs, rendre les lettres perdues.

        Pour les lignes qui ont déjà leurs en-têtes, ou n'en auront jamais.
        Rend le nombre de lignes réécrites.
        """
        self.env.cr.execute(_SQL_OBJETS_A_REPARER)
        ids = [r[0] for r in self.env.cr.fetchall()]
        Rows = self.sudo().with_context(active_test=False,
                                        tracking_disable=True)
        n = 0
        for row in Rows.browse(ids):
            vals = row._bf_vals_objet_repare()
            if not vals:
                continue
            try:
                with self.env.cr.savepoint():
                    row.write(vals)
                n += 1
            except Exception:  # noqa: BLE001
                _logger.warning("bf.email #%s : objet non réparé", row.id,
                                exc_info=True)
        _logger.info("bf.email objets réparés : %s", n)
        return n

    @api.model
    def _cron_rattraper_entetes(self):
        """Filet de nuit : les lignes de la semaine encore sans en-têtes.

        Le miroir les donne au passage ; ce filet rattrape celles qu'il a
        manquées (réseau, ligne ancrée avant d'avoir son Message-ID). Par UID
        seulement, borné.
        """
        depuis = fields.Datetime.now() - timedelta(days=CRON_JOURS)
        return self._bf_rattraper_entetes(imap=True, balayer=False,
                                          limite=CRON_LIMITE, depuis=depuis)
