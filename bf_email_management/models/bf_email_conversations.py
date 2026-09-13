"""La lecture par conversation au poste.

Le téléphone replie les fils depuis toujours (`get_mobile_threads`) ; le poste
affichait une ligne par message, avec un bouton « Fil » pour aller voir.

⚠️ Et la mesure dit de ne pas en attendre un miracle. Sur `une base réelle` le
2026-09-13, la boîte du jour comptait **1 528 lignes pour 1 462 fils** : le pli
en fait disparaître **66, soit 4,3 %**. Sur tout l'historique, 11 157 des
12 746 fils tiennent en UN message. C'est un confort de LECTURE, pas un gain de
volume, et c'est pour ça que le pli est une OPTION et non le défaut : changer
la forme de la liste de tout le monde pour 4 % de lignes en moins serait un
mauvais marché.

Là où il gagne, c'est sur les 42 fils actifs de la boîte, ceux qui portent la
conversation qui compte, et qu'on relisait message par message.
"""
from odoo import _, api, fields, models

MAX_PAGE = 500


class BfEmailConversations(models.Model):
    _inherit = "bf.email"

    @api.model
    def inbox_get_threads(self, folder="inbox", offset=0, limit=100,
                          search=None):
        """Une page de fils, du plus récemment actif au plus ancien.

        Le repliage se fait par `read_group` sur `thread_root_id` : c'est la
        base qui agrège et qui pagine, pas nous. Replier en Python aurait
        demandé de tout charger d'abord, et aurait menti sur le total dès que
        le dossier dépasse une page.
        """
        domaine = self._inbox_folder_domain(folder)
        domaine += self._search_domain_from_query(search)
        offset = max(0, int(offset or 0))
        limit = max(1, min(int(limit or 100), MAX_PAGE))

        groupes = self.read_group(
            domaine, ["date:max", "id:count"], ["thread_root_id"],
            offset=offset, limit=limit, orderby="date:max desc", lazy=False,
        )
        total = len(self.read_group(
            domaine, ["id:count"], ["thread_root_id"], lazy=False))

        lignes = []
        for groupe in groupes:
            racine = groupe.get("thread_root_id")
            dernier = self.search(
                domaine + [("thread_root_id", "=", racine)],
                order="date desc, id desc", limit=1)
            if not dernier:
                continue
            ligne = dernier._inbox_row()
            membres = self.search(
                domaine + [("thread_root_id", "=", racine)],
                order="date asc")
            ligne.update({
                "thread_root": racine or "",
                "thread_size": groupe.get("__count") or len(membres),
                "thread_ids": membres.ids,
                # Le nom qui s'affiche est celui des DEUX bouts quand le fil
                # va et vient : « moi et Line Antaki » dit plus que le dernier
                # expéditeur, qui est souvent moi.
                "participants": membres._thread_participants(),
                "unread_in_thread": len(
                    membres.filtered(lambda r: r.status == "new")),
            })
            lignes.append(ligne)

        return {
            "folder": folder,
            "threads": lignes,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def _thread_participants(self):
        """Les correspondants du fil, sans nous et sans doublon."""
        miens = set()
        proprietaire = self[:1].user_id or self.env.user
        try:
            miens = set(self[:1]._get_self_addresses(user=proprietaire))
        except Exception:  # noqa: BLE001
            miens = set()
        vus = []
        for ligne in self:
            nom = (ligne.partner_id.display_name
                   or ligne._inbox_display_name(ligne.email_from)
                   or ligne.email_from or "")
            adresse = (ligne.email_from or "").lower()
            if any(m in adresse for m in miens):
                continue
            if nom and nom not in vus:
                vus.append(nom)
        return vus[:3]
