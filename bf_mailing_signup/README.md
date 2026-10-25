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
| GET | `/infolettre/desabonner?e=&l=&j=&lang=` | Lien de chaque courriel de la séquence d'accueil. Si le jeton vaut, rend une page à un bouton « Me désabonner » ; ne désabonne RIEN. Sinon renvoie au formulaire. |
| POST | `/infolettre/desabonner` (`e`, `l`, `j`, `lang`) | Le bouton, ou le désabonnement en un clic d'un client de messagerie (RFC 8058, corps `List-Unsubscribe=One-Click`) : pose l'`opt_out` si le jeton vaut. Répond 200 avec la page « Vous êtes désabonné ». |

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

1. un renvoi étroit de `/infolettre`, de `/infolettre/confirmer` et, pour la séquence
   d'accueil, de `/infolettre/desabonner` vers Odoo (trois blocs à correspondance
   EXACTE), sur le modèle des blocs `/r/` et `/m/` déjà présents ;
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

## La séquence d'accueil

Odoo Community n'a pas l'automatisation marketing. Le module en tient la partie dont
une infolettre a besoin : à chaque personne qui CONFIRME par le lien, une suite de
courriels, le premier à la confirmation, les autres selon un délai en jours.

* **Les étapes** sont des enregistrements `bf.mailing.signup.step`, que le module ne
  montre dans aucune vue (il n'en ajoute pas) : on les pose par RPC ou par import.
  Une étape : liste, langue, rang, délai en jours depuis la confirmation, sujet et
  corps HTML complet. Le module n'en livre AUCUNE, et sans étape la tâche planifiée
  ne fait rien. Le corps DOIT contenir `{{desabonnement}}`, remplacé à l'envoi par le
  lien de la personne : une étape sans ce marqueur ne s'enregistre pas.
* **La langue** est celle du formulaire, retenue sur l'inscription (`bf_lang`), pas
  sur une liste de plus. Avec la date de confirmation (`bf_confirmed_at`), le rang du
  dernier courriel parti (`bf_step`) et sa date (`bf_step_sent_at`), le compte des échecs
  (`bf_failures`) et la date du dernier (`bf_attempt_at`), ce sont les champs que le
  module ajoute à `mailing.subscription`. 🔴 Un désabonnement EFFACE `bf_confirmed_at`,
  par n'importe quel chemin (lien de la séquence, page d'Odoo, import, case décochée) :
  une réinscription sans double consentement (route du site web, geste d'administrateur)
  ne relance donc jamais la séquence, seule une confirmation par le lien le fait.
* **Qui la reçoit** : seulement celles dont `bf_confirmed_at` est posé, donc qui ont
  confirmé par le lien. Un contact importé, ajouté à la main ou déjà abonné avant la
  pose du module n'en reçoit pas, tant qu'il n'a pas confirmé par le formulaire. Une
  réinscription après un désabonnement relance la séquence du début ; un lien de
  confirmation rechargé ne la relance pas.
* **Quand** : le premier courriel part au clic de confirmation ; la tâche planifiée
  (toutes les 15 minutes) envoie les suivants et reprend ce qui n'est pas parti. Après
  un retard (serveur arrêté), le courriel suivant ne part pas dans la foulée : l'écart
  prévu entre les deux étapes est compté depuis le dernier envoi réel, et la tâche
  n'envoie qu'un courriel par personne et par passage (cent au plus par passage).
* **Un échec ne passe pas pour un succès** : le rang n'avance que si le courriel est
  réellement parti, c'est-à-dire que le cœur a passé le `mail.mail` à l'état `sent`
  (lu dans `_postprocess_sent_message`, juste avant que le cœur ne l'efface : l'absence
  du courriel après l'envoi ne prouve rien). Sinon le rang reste, l'échec est compté, et
  l'envoi est retenté après 1, 2, 4, 8, 16 puis 24 heures (plafond) ; au huitième échec
  de suite (environ trois jours) la séquence s'arrête pour cette inscription (remettre `bf_failures`
  à 0 la relance). Rien ne distingue un refus propre à une adresse d'une panne du serveur sortant :
  l'alerte est la ligne ERROR du journal, une panne de plus de trois jours abandonne les personnes dues. Le rang est
  validé AVANT la note du fil du contact : une note qui échoue ne renvoie pas un courriel
  déjà parti. Un serveur SMTP qui coupe en plein envoi compte comme un échec (le courriel
  a pu partir : au pire un doublon, jamais un trou). La ligne `mail.mail` n'est jamais
  gardée : elle porte l'adresse.
* **Une étape retirée** au milieu (rangs 1, 2, 4) ne bloque personne : la séquence passe
  au prochain rang qui existe. Une liste archivée n'envoie plus rien.
* **Se désabonner** : chaque courriel porte le lien `/infolettre/desabonner` à jeton, un
  jeton par personne et par liste (HMAC du secret de la base, sans stockage, sans
  péremption : un courriel vieux de deux mois désabonne encore). Pas de connexion, pas
  d'adresse à saisir. La liste est DANS le lien (`l=`, couverte par le jeton) : changer le
  paramètre `list_id` ne rend pas mortes les liens déjà envoyés. Le désabonnement pose
  l'`opt_out` sur TOUTES les inscriptions de cette liste portant l'adresse
  (`mailing.contact` n'a pas de contrainte d'unicité sur le courriel), et seulement cette
  liste. Le lien est aussi dans `List-Unsubscribe`, avec
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058). 🔴 Ces deux en-têtes ne se
  plient JAMAIS : la politique SMTP d'Odoo plie tout en-tête non structuré de plus de 78
  caractères, et Python coupe alors une URL (sans espace) en mots encodés RFC 2047 que les
  clients de messagerie ne reconnaissent pas. `ir.mail_server.build_email` pose donc une
  politique qui les laisse sur une seule ligne. Un essai le vérifie sur les octets
  sérialisés, pas sur l'objet en mémoire. Pas de plafond par IP sur cette route : un
  fournisseur de messagerie poste les clics uniques de tous ses abonnés depuis quelques
  adresses, et refuser un désabonnement est la seule défaillance inacceptable. 🔴 Le GET ne
  désabonne pas : les passerelles de courriel ouvrent chaque lien avant la personne, et
  feraient taire sans qu'elle le sache tous les abonnés d'une entreprise qui en a une.
  Le POST (le bouton, ou le clic unique du client de messagerie) désabonne, et pose
  l'`opt_out` de la liste : l'inscription reste, son refus aussi. Le jeton de
  désabonnement ne vaut pas pour la confirmation, ni l'inverse.
* **Aucun pixel ni lien suivi** : les courriels portent `X-BF-Sans-Suivi` (voir plus bas).
  L'adresse d'expédition reste celle de l'instance, la seule alignée en SPF et DKIM : le courriel est
  créé avec `email_from` explicitement vide, sans quoi `mail.message` prêterait à l'adresse le nom
  d'affichage de l'usager qui l'envoie (celui de la tâche planifiée, `__system__`).
* **Pas de `base_url`, pas de courriel** : le lien de désabonnement s'écrit avec le paramètre
  `bf_mailing_signup.base_url` et n'a AUCUN site de repli. Sans lui rien ne part (journal en ERROR,
  rien de compté contre les inscriptions) : un lien qui ne marche pas serait pire que pas de courriel.
* Une adresse sur la liste noire ne reçoit rien. Chaque courriel envoyé laisse une note
  sur la fiche du contact, comme la preuve du consentement.
* ⚠️ **Un autre module qui garde une copie des courriels sortants** (un historique de courriels,
  comme `bf_email_management` chez Blue Fox) la garde aussi. Le courriel part en `auto_delete`
  pour qu'il n'en garde que l'enveloppe, jamais le corps ni le lien à jeton, mais l'enveloppe (adresse
  du destinataire, objet, date) reste tant que ce module-là ne l'écarte pas : le module qui envoie
  ne la voit pas et ne la purge pas. Les courriels envoyés par la tâche planifiée y laissent une
  ligne chacun (le premier, envoyé au clic de confirmation par l'usager public, aucune). Chez un
  locataire qui garde un historique, lui faire ignorer les courriels marqués `X-BF-Sans-Suivi`.
* Le courriel et le jeton sont dans l'URL du lien, donc dans les journaux d'accès d'Odoo et du
  proxy (inhérent à un lien sans connexion, et au POST en un clic de la RFC 8058, qui vise cette
  URL). Le jeton ne périme pas : il ne sert qu'à désabonner l'adresse qu'il nomme.
* Des rebonds ne reviennent jamais sur la fiche : le courriel est effacé et l'enveloppe d'erreur est
  l'adresse de retour de l'instance. Une adresse morte reçoit donc les dix courriels.
* **Effet sur les autres envois** : la politique qui garde `List-Unsubscribe` sur une seule ligne
  s'applique à TOUT courriel qui porte cet en-tête, donc aussi aux infolettres natives d'Odoo
  (`mass_mailing`), qui partaient avec l'en-tête encodé en RFC 2047 : leurs clients afficheront
  désormais leur bouton de désabonnement natif. Une URL de plus de 998 caractères reste une ligne
  trop longue (limite de la RFC 5322).

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

La 18.0.1.3.0 ajoute un modèle, six champs, une tâche planifiée et la route
`/infolettre/desabonner` : même séquence (`-u`, redémarrage), et un bloc `location =
/infolettre/desabonner` sur le proxy, sans quoi le lien des courriels tombe sur le site
statique. Les étapes se posent après, avec la tâche planifiée à l'œil : elle n'envoie
rien tant qu'aucune inscription n'a de date de confirmation.

## Journal

- **18.0.1.3.1** : le titre de la page de désabonnement ne s'accorde plus au genre de la personne
  (« Désabonnement confirmé », et non « Vous êtes désabonné »).
- **18.0.1.3.0** : séquence d'accueil (étapes par liste et par langue, tâche planifiée
  de 15 minutes, premier courriel au clic de confirmation), désabonnement à jeton par
  personne (`/infolettre/desabonner`, GET à bouton, POST et désabonnement en un clic),
  en-têtes `List-Unsubscribe` jamais pliés, aucun pixel ni lien suivi. Relecture adverse :
  en-tête `List-Unsubscribe` qui partait encodé en RFC 2047 ; réinscription sans double
  consentement qui relançait la séquence (la date de confirmation s'efface au
  désabonnement) ; désabonnement qui ne touchait qu'une des fiches en double ; rang non
  validé avant la note du fil (courriel renvoyé à chaque passage) ; échecs retentés sans fin
  (reprise à 1, 2, 4, 8 heures puis abandon) ; étape retirée qui bloquait ; liste archivée
  encore servie ; lien lié au paramètre de liste ; expression du pixel quadratique dans
  `build_email` (200 Ko en 14 secondes, appelable par tout usager connecté). Deuxième relecture :
  l'expression corrigée restait quadratique avec l'attribut répété dans une balise jamais fermée
  (464 Ko en 20 secondes) ; elle isole maintenant chaque balise puis cherche l'attribut ; un
  `_postprocess_sent_message` plus interne qui lève après un envoi réussi écrasait l'état `sent` et
  le courriel repartait (le premier état gagne) ; l'expéditeur prêtait le nom de l'usager de la tâche ;
  `l=²` rendait une 500 ; pas de repli sur un site pour le lien ; reprise après échec jusqu'à huit
  échecs avec attente plafonnée à 24 heures (cinq échecs abandonnaient tout le monde après 15 heures
  de panne).

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
