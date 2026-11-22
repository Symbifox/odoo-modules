"""18.0.4.6.0 : le partage nomme, et les comptes qui n'en recoivent pas.

🔴 Ce que cette migration ne fait PAS, et pourquoi.

Le lot avait pose un parametre systeme, `bf_bloc_notes.comptes_exclus`, cense
tenir les identifiants des comptes non humains. Deux defauts, les deux mesures
en relecture : rien ne le posait, et pose, il ne
tenait pas (un `domain=` de champ n'est jamais applique par l'ORM a un
`create`). La protection vit maintenant sur la fiche de l'usager
(`res.users.bf_note_no_shared_notes`), une contrainte refuse la ligne nommee, et
la portee exclut le compte.

Reste a cocher les comptes connus du locataire. ⛔ Aucun identifiant d'usager
n'est code ici : un identifiant d'usager ne designe rien d'un locataire a
locataire. Et aucun discriminant mesure sur la base ne separe les comptes de
service des humains sans faux positif (mesure sur une copie de la
base reelle, ou la plupart des comptes internes actifs etaient de service) :

  - `share` ne separe que le portail ; les comptes de service sont
    internes ;
  - un employe `hr` : `hr` n'est pas une dependance, et un humain peut n'avoir
    aucune fiche d'employe ;
  - l'absence de mot de passe : un humain qui entre par SSO n'en a pas non plus ;
  - une cle d'API : un humain peut en avoir une ;
  - `notification_type = 'inbox'` : certains comptes de service seulement, et un humain
    peut le choisir ;
  - un telephone absent chez le partenaire : il peut separer les comptes d'une
    base, et c'est un accident, pas une construction — un humain sans
    telephone est ordinaire, et le faux positif serait SILENCIEUX (il ne
    recevrait plus de note partagee sans que personne ne le sache).

Donc : cette migration ne coche RIEN d'elle-meme. Elle reprend la valeur du
parametre si le locataire l'a pose, et c'est tout. Quand le parametre est
absent : les trois cases se cochent a la main sur la fiche de l'usager
(Parametres > Utilisateurs > Preferences > Bloc-notes).

⚠️ Idempotente (le `WHERE` ecarte ce qui est deja coche) et elle ne LEVE jamais :
une migration qui leve ne se rejoue pas, et le lot entier resterait a mi-chemin
pour un drapeau de confort.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    try:
        cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s",
                   ("bf_bloc_notes.comptes_exclus",))
        ligne = cr.fetchone()
        ids = []
        for morceau in (ligne[0] if ligne and ligne[0] else "").replace(";", ",").split(","):
            morceau = morceau.strip()
            if morceau.isdigit():
                ids.append(int(morceau))
        if not ids:
            _logger.info(
                "bf_bloc_notes 4.6.0 : parametre bf_bloc_notes.comptes_exclus absent ou "
                "vide, aucun compte coche « ne recoit pas de notes partagees ». "
                "A cocher a la main sur la fiche des comptes de service.")
            return
        # En SQL, comme chez le voisin (bf_gamification 3.0.0) : une ecriture ORM
        # sur res.users reveille les synchronisations des autres modules
        # (annuaires, SSO) pour un simple drapeau de bloc-notes.
        cr.execute(
            "UPDATE res_users SET bf_note_no_shared_notes = TRUE"
            " WHERE id IN %s AND bf_note_no_shared_notes IS NOT TRUE"
            " RETURNING login", (tuple(ids),))
        logins = [r[0] for r in cr.fetchall()]
        _logger.info("bf_bloc_notes 4.6.0 : %d compte(s) hors partage de notes : %s",
                     len(logins), ", ".join(logins) or "-")
    except Exception:  # noqa: BLE001 - une migration qui leve ne se rejoue pas
        _logger.exception(
            "bf_bloc_notes 4.6.0 : reprise du parametre bf_bloc_notes.comptes_exclus "
            "impossible ; aucune case cochee, le lot se pose quand meme.")
