# Pages de liens (`bf_linkpage`)

Une page publique qui rassemble les liens d'une personne sous une adresse
courte, et le QR à poser dans une signature courriel.

## Ce que ça ajoute par rapport à un service de pages de liens

Les liens existent déjà dans la base. Le lien de prise de rendez-vous, la page
de dépôt sécurisé, le téléphone et le courriel sont des enregistrements, pas
des chaînes recopiées à la main. La page les **résout à l'affichage**.

Conséquence recherchée : quand le slug de rendez-vous d'une personne change, sa
page suit sans que personne n'y touche, et le QR déjà imprimé dans sa signature
continue de pointer au bon endroit. C'est la seule chose qu'un service externe
ne peut pas faire.

## Les trois décisions qui gouvernent le module

### 1. Un slug inconnu rend un 404 franc

Le module voisin `bf_appointment` redirige en silence vers son index quand le
slug ne résout pas. C'est acceptable là où l'adresse est cliquée depuis un
courriel qu'on peut corriger et renvoyer. Ici l'adresse part dans un **QR
imprimé** : elle ne se corrige plus. Une redirection donnerait une page qui
s'affiche, donc l'apparence du succès, et personne ne saurait que le QR pointe
à côté.

Corollaire : tous les refus rendent le même 404. Un slug inexistant, une page
en brouillon, fermée, archivée ou expirée sont indiscernables de l'extérieur,
sans quoi l'adresse deviendrait un oracle qui confirme à un visiteur anonyme
quels slugs existent.

### 2. Une source qui ne résout pas fait disparaître le lien

Elle ne rend pas une adresse approximative et n'envoie personne vers une page
d'accueil. Un lien mort atteint par un QR déjà imprimé coûte plus cher qu'un
lien absent. L'écart entre « Liens » et « Liens affichés » au back-office est
la seule façon de voir qu'une source est devenue muette.

### 3. Une page ponctuelle porte une expiration, armée à la création

Une page publique sans propriétaire que personne ne révoque est le même angle
mort qu'un partage éternel. L'expiration est une **date lue à l'affichage**, pas
un état à maintenir : aucun cron n'a besoin de tourner pour qu'une page se
ferme. Le délai par défaut est de 90 jours, réglable par le paramètre système
`bf_linkpage.oneoff_expiry_days`.

## Le préfixe d'URL

Les pages sont servies sous `/l/<slug>`, jamais à la racine. Un slug servi à
`/<slug>` entre en collision avec le routage du site (pages website, `/shop`,
`/blog`), et le préfixe de langue est retiré avant le routage, ce qui rend la
collision intermittente donc difficile à voir. Le préfixe dédié coûte deux
caractères dans le QR et supprime la classe entière de pannes.

## Les sources

| Code | Résout | Fournisseur requis |
| --- | --- | --- |
| `manual` | L'adresse saisie dans le lien | aucun |
| `appointment` | La page de rendez-vous publique de la personne, par sa ressource | `bf_appointment` |
| `securetransfer` | La page de dépôt `/to/<slug>` | `bf_securetransfer` |
| `partner_email` | `mailto:` de la fiche | aucun |
| `partner_phone` | `tel:` de la fiche, ponctuation retirée | aucun |
| `partner_website` | Le site web de la fiche | aucun |

**Aucun import vers un module fournisseur.** Le module ne dépend ni de
`bf_appointment` ni de `bf_securetransfer` : il vérifie la présence du modèle au
registre et se tait sinon. Un import ferait échouer l'installation là où le
fournisseur est absent.

Un module satellite ajoute une source en surchargeant `_sources()` sur le
modèle abstrait `bf.linkpage.source` et en définissant la méthode
`_resolve_<code>` correspondante.

## Le QR

`GET /l/<slug>/qr.png` — réservé aux usagers connectés membres du module. Le QR
n'encode qu'une adresse publique, mais composer une image à la demande sur une
route publique est un levier commode pour saturer le serveur ; il se télécharge
une fois, par la personne qui monte sa signature.

- `?branded=0` — sans le logo de la société.
- `&size=6` — rendu plus compact (4 à 20).

Le QR à la marque est produit en **correction d'erreur de niveau H** parce que
le logo masque des modules du code, et le logo est borné à 22 % du côté.
Réduire ce niveau donne un QR qui se lit à l'écran puis échoue une fois imprimé
petit dans une signature.

## Hors portée délibérément

**Le domaine personnalisé.** Chaque domaine demande un host de proxy et un
certificat posés à la main, plus la configuration `website.domain` côté Odoo.
C'est de la corvée d'hébergement récurrente qui n'ajoute rien au module. Un
client qui le demande explicitement est traité comme du travail
d'hébergement, pas comme une fonctionnalité.

## Ce que le QA du 2026-08-30 a établi

75 tests, verts en installation neuve avec ET sans les modules fournisseurs.
Chaque invariant a été soumis à une mutation : on casse la règle dans le code
et on exige que la suite rougisse. 18 mutations sur 19 rougissent.

**La seule qui ne rougit pas**, et c'est assumé : remettre le compteur de
visites en lire-modifier-écrire au lieu de l'incrément fait par la base. La
perte de visites sous charge n'est pas démontrable dans une transaction
unique. L'incrément SQL reste une précaution non couverte par un test.

Quatre défauts trouvés à cette occasion, tous corrigés :

- **La photo ne s'affichait pas.** Servie par `/web/image/bf.linkpage/<id>/avatar`,
  elle arrivait au visiteur sous forme d'image de remplacement — 6078 octets de
  silhouette générique là où la photo en fait 77 — parce que l'usager public n'a
  aucun droit de lecture sur le modèle et que `/web/image` répond alors **200**
  au lieu d'une erreur. La photo passe maintenant par `/l/<slug>/avatar`.
- **Le doublon de slug remontait une erreur de base de données.** La contrainte
  SQL s'applique au INSERT, donc avant toute contrainte Python : le contrôle a
  été déplacé dans `create()` et `write()`.
- **Un paramètre `oneoff_expiry_days` mal saisi empêchait de créer une page.**
  Il retombe sur 90 jours.
- **`_compute_linkpage_count` n'avait pas de `@api.depends`**, donc n'était
  jamais invalidé.

Et un constat qu'il faut lire avec sa correction : le QA a d'abord cru trouver
une **redirection ouverte** par la source « site web du contact ». Vérification
faite, `res.partner.website` normalise à l'écriture — `//exemple.invalide/x`
devient `http://exemple.invalide/x` — donc aucune des six sources actuelles ne
peut faire sortir un schéma exécutable. Le filtre `_safe_url` a été ajouté quand
même : il couvre l'adresse RÉSOLUE, là où la contrainte d'écriture ne voyait que
le champ `url`, et une source ajoutée plus tard n'aura pas ce garde-fou par
accident.

## Lancer les tests

⚠️ Le banc d'essai a `list_db = False`. Une requête **anonyme** ne peut alors
résoudre une base que si le `dbfilter` n'en désigne qu'une seule. Avec
`--db-filter='.*'`, toutes les routes publiques rendent un 404 werkzeug nu, et
les assertions « doit rendre 404 » passent **sans rien discriminer**. Toujours
épingler le filtre sur la base d'essai :

```sh
docker exec odoo-staging odoo -d dryrun_linkpage -i bf_linkpage \
    --test-enable --test-tags /bf_linkpage --stop-after-init \
    --http-port=8199 --db-filter='^dryrun_linkpage$'
```

C'est `test_page_publiee_repond_200` qui rend les tests de refus significatifs :
il prouve que la donnée est visible du serveur.
