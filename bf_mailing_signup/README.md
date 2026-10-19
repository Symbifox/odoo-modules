# Inscription publique à une liste d'envoi

Une route qui accepte un formulaire HTML ordinaire et inscrit l'adresse à une
`mailing.list`, à double consentement, sans reCaptcha, sans script et sans
ressource tierce.

## Pourquoi pas le formulaire d'Odoo

`/website/form/` fait déjà ce travail, mais `google_recaptcha` le protège dès
qu'il est installé — et il l'est, avec de vraies clés, parce que les formulaires
de contact en ont besoin. Sur symbifox.com, charger le reCaptcha de Google
contredirait la promesse écrite en tête de la configuration du proxy, *aucune
page de ce site ne charge de ressource tierce*, qui est aussi un argument de
vente et que la CSP du domaine impose. Le désactiver globalement affaiblirait des
formulaires en production sur un autre domaine. Troisième voie, retenue à
l'analyse : ce module.

## Le double consentement n'est pas un raffinement

La page qui porte le formulaire est un fichier statique : pas de session Odoo,
donc **pas de jeton CSRF possible**. La route s'ouvre forcément sans.

Ce que ça laisserait ouvert, le double consentement le referme : l'inscription
naît `opt_out=True` et ne reçoit qu'un seul courriel, celui du lien de
confirmation. Seule la personne qui relève l'adresse peut confirmer. Une adresse
déjà désinscrite ne se réactive pas non plus par une resoumission du formulaire.
Et la confirmation laisse une preuve de consentement exprès datée au fil du
contact, ce dont une entreprise qui vend de la conformité a besoin.

## Ouvrir le lien ne confirme rien

Les passerelles de courriel (liens réécrits, antivirus) et les aperçus de
messagerie ouvrent chaque lien d'un message avant la personne, en `GET` ou en
`HEAD`. Tant que le `GET` confirmait, un tiers pouvait inscrire l'adresse d'une
entreprise dont la passerelle analyse les liens : le double consentement se
confirmait tout seul. Le lien ouvre donc une page qui porte un bouton, et seul
le `POST` de ce bouton confirme. La page n'a aucun script qui soumettrait le
formulaire de lui-même ; sinon, un robot qui exécute le JavaScript confirmerait.

Le limiteur de débit garde aussi ses blocages : passé 10 000 clés suivies, il
retire d'abord les clés échues, puis celles qui comptent le moins de requêtes
récentes. Les adresses IP qu'on fait défiler en comptent chacune une ou deux :
elles partent avant la source qu'elles voulaient débloquer.

## Le lien ne stocke rien

Le jeton est `HMAC(database.secret, "liste:adresse:quantième")`, tronqué à
32 caractères. La validité de sept jours s'obtient en réessayant les sept
derniers quantièmes : rien à écrire, rien à purger, rien à faire expirer par un
cron. Un lien plus vieux ne confirme plus, il faut refaire une demande.

## Routes

| Méthode | Route | Effet |
|---|---|---|
| POST | `/infolettre` | Crée l'inscription désactivée et envoie le lien. Redirige vers la page de remerciement, sauf deux cas : plafond GLOBAL atteint (page « occupé ») et `Origin` étranger (403). |
| GET | `/infolettre/confirmer?e=&j=&lang=` | Si le jeton vaut, rend une page à un bouton « Confirmer mon inscription » ; n'active RIEN. Sinon renvoie au formulaire. |
| POST | `/infolettre/confirmer` (`e`, `j`, `lang`) | Le bouton : lève l'`opt_out` si le jeton vaut. Sinon renvoie au formulaire. |

⚠️ Le POST rend la **même** réponse quoi qu'il arrive à une ADRESSE : adresse
connue, adresse refusée, pot de miel rempli, seau ou plafond de la boîte atteint.
Une réponse qui varierait ferait de la route un oracle : on saurait, en essayant
une adresse, si elle est sur la liste. La page « occupé » ne dit qu'un état
d'ensemble (le plafond global), le 403 qu'une origine étrangère.

## Plafonds

| Plafond | Tenu par | Au-delà |
|---|---|---|
| 5 envois par heure et par IP | seau en mémoire, **par worker** | « merci », refus journalisé |
| 3 confirmations par boîte et par jour | la base : `bf.mailing.signup.trace`, `UNIQUE(key, day, rank)`, `CHECK rank BETWEEN 1 AND 3` | « merci », sans envoi |
| 20 confirmations par heure, 100 par 24 h, toutes boîtes | compte en base des mêmes traces, sous verrou | page « occupé », un seul avis interne par fenêtre |
| `Origin` présent et étranger, ou `Sec-Fetch-Site: cross-site` | contrôle de la route | 403 |

🔴 **Le seau par IP ne borne pas le total.** Il vit dans la mémoire de chaque
worker (dix workers : environ 50 envois par heure et par IP) et repart à zéro à
chaque recyclage. On ne le garde pas en base, pour ne pas garder une donnée de plus
(l'adresse IP) : c'est le plafond global, avec son avis interne, qui borne le total.

La boîte est normalisée avant le compte : sans `+suffixe`, et sans les points chez
Gmail (`gmail.com`, `googlemail.com`) ; sinon `cible+0@` à `cible+7@` donnaient
huit confirmations à la même personne. La trace ne garde PAS l'adresse : un HMAC
de la boîte normalisée (secret de la base), le jour et un rang. Elle est effacée
au bout de 48 heures. Le perdant d'une course sur la contrainte lève avant tout
envoi, et le journal SQL est muet pendant l'écriture (la requête fautive porterait
sinon l'empreinte au journal, en ERROR, provoquée de l'extérieur).

🔴 **Le compte puis l'écriture sont sérialisés.** Sans verrou, 24 envois simultanés
comptaient tous 19 et écrivaient tous (23 lignes pour 20). Chaque
envoi prend donc le verrou consultatif des plafonds (`pg_advisory_xact_lock`, tenu
jusqu'à la fin de sa transaction), puis compte sur une transaction NEUVE, ouverte après
la prise du verrou : Odoo travaille en REPEATABLE READ, et un compte fait dans la
transaction de la requête, dont l'instantané date d'avant le verrou, ne verrait pas la
ligne que l'envoi précédent vient de valider. Le rang de la boîte est alors toujours
libre : la contrainte ne lève plus, et Postgres n'écrit plus de violation à son journal.

L'avis « plafond global atteint » part à `notify_email` une fois par fenêtre (heure
ou jour) : `bf_mailing_signup.plafond_avise` garde la dernière fenêtre avisée, et sa
ligne est prise sous verrou pour qu'un second worker ne le double pas. Il fait un
`set_param`, qui vide les caches de tous les workers : une fois par fenêtre au plus.

L'adresse doit passer une expression stricte : ASCII, ni virgule, ni point-virgule,
ni chevrons, ni guillemets, ni `=` ni `?` (un mot encodé RFC 2047 `=?…?=` se décode
en une AUTRE adresse chez le serveur).

## Champs attendus du formulaire

| Champ | Rôle |
|---|---|
| `courriel` | l'adresse. `type="email" required` côté navigateur suffit à écarter les fautes de frappe sans une ligne de script. |
| `lang` | `fr` ou `en`, choisit la langue du courriel et les pages de retour. |
| `site_web` | **pot de miel**. Sorti de l'écran par la feuille de style, `tabindex="-1"`, `aria-hidden`. Rempli, l'envoi est jeté en silence. |

## Le courriel porte la marque

La confirmation est le **premier** courriel que la personne reçoit : s'il arrive
en texte nu, elle doute d'avoir écrit à la bonne enseigne, et un lien qu'on ne
reconnaît pas ne se clique pas. Il part donc dans la même coquille que les
infolettres qui suivront — bandeau marine, sigle, bouton bleu, pied avec
l'adresse postale.

Trois choses, apprises ailleurs, y sont volontaires :

* **Le cadre extérieur est clair.** Plusieurs clients jettent le
  `background-color` de la carte intérieure ; sur un cadre sombre le texte
  foncé devient alors illisible. Les seuls aplats sombres sont posés en
  attribut `bgcolor`, que personne ne retire.
* **Le lien figure aussi en clair**, sous le bouton. Un bouton dont le fond
  disparaît ne se voit plus.
* **Tout est en tableaux et en style en ligne.** Les clients courriel jettent
  les `<style>`, ignorent `max-width` sur un `div` et ne connaissent ni flex ni
  grid.

## Paramètres système

| Clé | Défaut | Rôle |
|---|---|---|
| `bf_mailing_signup.list_id` | *(aucun)* | La `mailing.list` visée. **Sans elle, rien ne s'inscrit** et un avertissement est journalisé. |
| `bf_mailing_signup.base_url` | `https://symbifox.com` | Le domaine écrit dans le lien de confirmation. |
| `bf_mailing_signup.reply_to_fr` | *(aucun)* | Adresse de réponse du courriel français. |
| `bf_mailing_signup.reply_to_en` | *(aucun)* | Adresse de réponse du courriel anglais. |
| `bf_mailing_signup.brand_name` | *(le nom de la société)* | Le nom écrit dans le sujet, l'en-tête et le corps. |
| `bf_mailing_signup.brand_mark_url` | *(aucune)* | URL absolue du sigle, dans le bandeau. |
| `bf_mailing_signup.brand_logo_url` | *(aucune)* | URL absolue du logo de la société, dans le pied. |
| `bf_mailing_signup.company_url` | *(aucune)* | Le site de la société, en lien à côté de son nom dans le pied. `http` ou `https` seulement ; vide, le pied garde le nom seul. |
| `bf_mailing_signup.notify_email` | *(aucune)* | Qui prévenir d'une inscription. Vide, personne n'est prévenu. Plusieurs adresses séparées par des virgules. |

⚠️ Les deux URL d'images n'ont **aucune valeur par défaut**, et c'est délibéré :
un module publié qui pointerait sur les images de son auteur ferait relever les
ouvertures des abonnés de qui l'installe par le serveur de l'auteur. Sans elles,
l'en-tête garde son libellé et perd la vignette, ce qui ne casse rien.

Le reste de la coquille (palette, police) est en dur : c'est de l'habillage, pas
de la configuration, et sept paramètres de plus pour changer un bleu ne rendent
service à personne.

`email_from` n'est **pas** posé : `mail.mail` retombe sur l'adresse d'envoi par
défaut de l'instance, la seule dont le domaine est aligné en SPF et DKIM. En
poser une ici est le meilleur moyen de faire classer la confirmation en pourriel.

## L'avis interne

Deux moments distincts sont annoncés à `notify_email`, et ils ne disent pas la
même chose :

* **la demande**, quand quelqu'un remplit le formulaire. L'inscription est
  encore désactivée et le courriel de confirmation vient de partir. C'est le
  moment qui révèle une confirmation qui n'arrive jamais, donc un problème de
  délivrabilité — mais c'est aussi celui qu'un robot passé à travers le pot de
  miel peut déclencher ;
* **la confirmation**, quand le lien est suivi. C'est la seule vraie
  inscription, et l'avis ne part **que si l'état a changé** : recharger le lien
  n'est pas une deuxième inscription, et prévenir deux fois pour la même
  personne apprend à ignorer l'avis.

L'avis porte l'adresse, la langue, l'état, la liste visée et un lien direct
vers la fiche du contact dans Odoo.

⚠️ Il est posé **après** le travail utile et tout entier sous `try` : s'il lève,
la personne qui vient de s'inscrire ne doit ni voir d'erreur, ni perdre son
inscription, ni attendre. Un essai le vérifie en le faisant échouer exprès.

## Côté proxy

Le site est servi en statique. Deux réglages sont nécessaires sur l'hôte, et le
formulaire échoue en silence sans eux :

1. un renvoi étroit de `/infolettre` vers Odoo, sur le modèle des blocs `/r/` et
   `/m/` déjà présents ;
2. `form-action 'self'` dans la CSP — elle dit `'none'` par défaut, et un
   navigateur bloque alors l'envoi **sans rien afficher**.

🔴 Le bloc doit aussi envoyer `proxy_set_header X-Forwarded-Host …`. Odoo 18
n'applique ProxyFix (`proxy_mode`) que si cet en-tête arrive : sans lui,
`remote_addr` reste l'adresse du proxy, tous les visiteurs partagent UN seau, et
cinq envois par heure ferment le formulaire pour tout le monde. Le module le
signale une fois par processus au journal, et journalise chaque refus du seau.
`client_max_body_size 64k` (le module refuse lui-même au-delà de 64 Kio, en 413)
et `proxy_hide_header Set-Cookie` (Odoo poserait un témoin de session de 90 jours
sur le domaine du site) complètent le bloc.

⚠️ N'ajouter `X-Forwarded-Host` aux blocs de production qu'**après** la pose de la
18.0.1.2.2 : avant elle, `/infolettre` n'a ni plafond par boîte ni plafond global,
et c'est le seau unique, par accident, qui le bornait. Avec l'en-tête et l'ancien
code, chaque IP enverrait environ 50 confirmations par heure à n'importe quelle
adresse.

## Pages attendues du site appelant

`/infolettre-merci.html`, `/infolettre-confirme.html`, `/infolettre-occupe.html`,
et leurs jumelles sous `/en/`. Le module n'ajoute aucune vue : c'est tout son intérêt, la page reste
celle du site, avec sa charte et sa CSP.

## Aucun pixel de suivi, relevés effacés à sept jours

La confirmation et l'avis interne (qui nomme l'adresse) portent l'en-tête interne
`X-BF-Sans-Suivi`. `ir.mail_server.build_email` le retire, avec l'image de suivi
d'ouverture que le module OCA `mail_tracking` colle à tout courriel sortant et les
en-têtes qu'il poserait (`X-Odoo-MailTracking-ID`, `X-Odoo-Database`), quel que soit
l'ordre de chargement des deux modules. Un autre module peut marquer ses courriels de
la même façon.

🔴 `build_email` est une méthode publique : tout usager connecté, portail compris,
l'appelle par `/web/dataset/call_kw`. Elle n'écrit donc RIEN (une version antérieure
notait en `sudo` tout identifiant de relevé lu dans le corps reçu, et un usager
portail faisait effacer n'importe quel relevé de l'instance). Les relevés `mail.tracking.email`
se notent dans `bf.mailing.signup.tracking` à l'envoi, dans
`mail.mail._postprocess_sent_message` (privée), par le `mail_id` des courriels marqués
du lot : jamais à partir d'un identifiant reçu. La tâche quotidienne efface ces relevés
au bout de sept jours : le module OCA les garderait sans limite, adresse comprise. La
désinstallation du module les efface tout de suite (`uninstall_hook`) : plus personne
ne les purgerait.

## Contacts jamais confirmés, effacés à sept jours

Le formulaire crée un contact et son inscription désactivée ; si le lien n'est jamais
suivi, ils restaient à vie. La même tâche efface au bout de sept jours, comme le lien
qui seul pouvait les confirmer, les seuls contacts :

* créés par la route publique (créateur membre de `base.group_public`) ;
* dont TOUTES les inscriptions (au moins une) sont nées désactivées et n'ont jamais
  bougé depuis (`opt_out_datetime` égale à leur date de création, sans raison de
  désinscription), et ont plus de sept jours.

Un abonné, même désinscrit (son refus doit rester), un contact créé à la main ou par
import, un contact sans inscription ne sont jamais visés. Un lien suivi plus tard
recrée le contact.

## Déploiement

Depuis la 18.0.1.2.2, le module a des modèles (traces, relevés), des données et une
tâche planifiée : un `-u` est nécessaire, PUIS un redémarrage (les contrôleurs ne se
rechargent qu'au démarrage). Libellés en anglais, traduits dans `i18n/fr_CA.po`.

## Journal

- **18.0.1.2.2, troisième passe** (relecture adverse) : `build_email` n'écrit plus rien
  (un usager portail y faisait noter n'importe quel relevé) ; les relevés se notent à
  l'envoi par le `mail_id` des courriels marqués ; plafonds comptés sous verrou
  consultatif, sur une transaction neuve (24 envois simultanés : 20 lignes, plus 23) ;
  avis interne sous `savepoint` ; contacts jamais confirmés effacés à sept jours ;
  relevés notés effacés à la désinstallation.
- **18.0.1.2.2, deuxième passe** (relecture adverse) : plafonds en base (3 par boîte
  normalisée et par jour, en HMAC ; 20 par heure, 100 par 24 h, page « occupé » et un
  avis par fenêtre) ; `Origin` étranger et `Sec-Fetch-Site: cross-site` refusés ; `=` et
  `?` refusés dans la partie locale ; relevés `mail_tracking` notés et effacés à sept
  jours, avis interne compris.
- **18.0.1.2.2, suite** (relecture adverse) : jeton validé (`[0-9a-f]{32}`)
  avant la comparaison, qui levait sur un caractère non ASCII (une 500 pour une adresse
  inscrite seulement, donc un oracle) ; un champ envoyé comme fichier ne rend plus de
  500 ; corps limité à 64 Kio ; refus du seau journalisés ; proxy sans
  `X-Forwarded-Host` signalé ; plus de pixel de suivi sur la confirmation.
- **18.0.1.2.2** : le pied peut porter le site de la société, en lien à côté
  de son nom (`company_url`). L'adresse postale passe sur sa propre ligne, plus petite,
  dans le même gris : la LCAP la veut présentée clairement, et un lien ne la remplace pas.
- **18.0.1.2.1** : ouvrir le lien de confirmation ne confirme plus
  rien. Les passerelles de courriel et les aperçus de liens ouvrent chaque lien
  en GET ou en HEAD avant la personne, et confirmaient l'inscription à sa place.
  Le lien rend un bouton ; seul son POST active.
