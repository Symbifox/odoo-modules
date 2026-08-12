# Manuel d'utilisation - Module Vie privee (Loi 25)

## Suivi des consentements et destruction documentaire (Loi 25)

**Version:** 18.0.3.0.0
**Categorie:** Vie privee / Conformite
**Auteur:** Your Company Name

---

## Table des matières

1. [Introduction](#1-introduction)
2. [Acces au module](#2-acces-au-module)
3. [Tableau de bord](#3-tableau-de-bord)
4. [Operations](#4-operations)
   - 4.1 [Consentements](#41-consentements)
   - 4.2 [Demandes en attente](#42-demandes-en-attente)
   - 4.3 [Demandes de destruction](#43-demandes-de-destruction)
   - 4.4 [Registre de destruction](#44-registre-de-destruction)
   - 4.5 [Campagnes de destruction](#45-campagnes-de-destruction)
   - 4.6 [Evaluations d'anonymisation](#46-evaluations-danonymisation)
5. [Configuration](#5-configuration)
   - 5.1 [Finalites](#51-finalites)
   - 5.2 [Avis de consentement](#52-avis-de-consentement)
   - 5.3 [Preferences de contact](#53-preferences-de-contact)
   - 5.4 [Politiques de retention](#54-politiques-de-retention)
   - 5.5 [Calendrier de conservation](#55-calendrier-de-conservation)
   - 5.6 [Classifications documentaires](#56-classifications-documentaires)
   - 5.7 [Sequences de courriels](#57-sequences-de-courriels)
   - 5.8 [DocuSeal / LibreSign](#58-docuseal--libresign)
6. [Portail client](#6-portail-client)
7. [Integration avec les contacts](#7-integration-avec-les-contacts)
8. [Integration avec les projets](#8-integration-avec-les-projets)
9. [Cycle de vie d'un consentement](#9-cycle-de-vie-dun-consentement)
10. [Preuves et tracabilite](#10-preuves-et-tracabilite)
11. [Automatisations](#11-automatisations)
12. [Roles et permissions](#12-roles-et-permissions)
13. [Glossaire](#13-glossaire)

---

## 1. Introduction

### Objectif du module

Le module **Suivi des consentements (Loi 25)** permet de gérer la conformité aux exigences de la Loi 25 du Québec en matière de protection des renseignements personnels. Il offre une solution complète pour :

- **Collecter** les consentements de manière explicite, manifeste, éclairée et spécifique
- **Documenter** chaque consentement avec preuves à l'appui
- **Suivre** le cycle de vie complet des consentements
- **Automatiser** les rappels et les renouvellements
- **Gérer** les préférences de communication des contacts
- **Détruire** les données selon les politiques de rétention établies

### Exigences de la Loi 25

La Loi 25 exige que le consentement soit :

| Critère | Description |
|---------|-------------|
| **Manifeste** | Le consentement doit être donné de façon claire et non équivoque |
| **Libre** | Sans pression ou contrainte |
| **Éclairé** | La personne doit comprendre à quoi elle consent |
| **Spécifique** | Donné pour une finalité précise |
| **Granulaire** | Possibilité de consentir séparément à différentes finalités |

Ce module permet de répondre à toutes ces exigences.

---

## 2. Accès au module

### Menu principal

Accédez au module via le menu principal d'Odoo :

```
📁 Vie privée
├── 📊 Tableau de bord
├── 📋 Opérations
│   ├── Consentements
│   ├── Demandes en attente
│   └── Demandes de destruction
└── ⚙️ Configuration
    ├── Finalités
    ├── Avis de consentement
    ├── Préférences de contact
    ├── Politiques de rétention
    ├── Séquences de courriels
    └── DocuSeal
        ├── Configuration
        └── Modèles
```

### Niveaux d'accès requis

| Menu | Utilisateur | Gestionnaire | Responsable |
|------|:-----------:|:------------:|:-----------:|
| Tableau de bord | ✓ | ✓ | ✓ |
| Consentements | ✓ | ✓ | ✓ |
| Demandes en attente | ✓ | ✓ | ✓ |
| Demandes de destruction | ✗ | ✓ | ✓ |
| Configuration | ✗ | ✓ | ✓ |

---

## 3. Tableau de bord

### Vue d'ensemble

Le tableau de bord offre une vue en temps réel de l'état des consentements dans votre organisation.

### Indicateurs clés de performance (KPI)

#### Statuts des consentements

| Indicateur | Description | Action |
|------------|-------------|--------|
| **Demandes en attente** | Consentements en brouillon ou en attente de réponse | Cliquez pour voir la liste |
| **Consentements accordés** | Total des consentements actifs | Cliquez pour voir la liste |
| **Consentements refusés** | Total des refus | Cliquez pour voir la liste |
| **Consentements retirés** | Total des retraits | Cliquez pour voir la liste |
| **Consentements expirés** | Total des expirations | Cliquez pour voir la liste |

#### Alertes d'expiration

| Indicateur | Description |
|------------|-------------|
| **Expiration 30 jours** | Consentements expirant dans les 30 prochains jours |
| **Expiration 60 jours** | Consentements expirant entre 30 et 60 jours |
| **Expiration 90 jours** | Consentements expirant entre 60 et 90 jours |

#### Métriques de qualité

| Indicateur | Description |
|------------|-------------|
| **Taux de consentement** | Pourcentage de consentements accordés vs refusés |
| **Taux de preuve** | Pourcentage de consentements avec preuve documentée |
| **Contacts « Ne pas contacter »** | Nombre de contacts ayant activé cette option |
| **Destructions en attente** | Demandes de destruction non traitées |

### Actions rapides

Depuis le tableau de bord, vous pouvez :

- **Nouvelle demande** : Créer une nouvelle demande de consentement
- **Voir les expirations** : Accéder aux consentements qui vont expirer
- **Traiter les destructions** : Gérer les demandes de destruction en attente

---

## 4. Opérations

### 4.1 Consentements

#### Liste des consentements

La vue liste affiche tous les consentements avec un code couleur :

| Couleur | Statut |
|---------|--------|
| 🔵 Bleu (info) | En attente |
| 🟢 Vert (succès) | Accordé |
| 🟡 Jaune (avertissement) | Refusé |
| 🔴 Rouge (danger) | Retiré |
| ⚪ Gris (atténué) | Expiré |

#### Créer un consentement

**Méthode 1 : Depuis le menu Consentements**

1. Cliquez sur **Nouveau**
2. Remplissez les champs obligatoires :
   - **Contact** : Sélectionnez le contact concerné
   - **Finalité** : Choisissez la raison du consentement
   - **Méthode de collecte** : Indiquez comment le consentement sera obtenu
3. Optionnel : Sélectionnez un avis de consentement
4. Cliquez sur **Enregistrer**

**Méthode 2 : Via l'assistant de demande**

1. Depuis un contact ou un projet, cliquez sur **Demander un consentement**
2. L'assistant s'ouvre avec les destinataires pré-sélectionnés
3. Choisissez :
   - La **finalité**
   - L'**avis de consentement** (modèle)
   - La **méthode de collecte**
   - Si vous souhaitez **envoyer un courriel** de notification
4. Cliquez sur **Créer les demandes**

#### Champs d'un consentement

| Champ | Description |
|-------|-------------|
| **Contact** | La personne concernée |
| **Finalité** | La raison pour laquelle le consentement est demandé |
| **Version de l'avis** | La version exacte de l'avis présenté |
| **Statut** | Brouillon, En attente, Accordé, Refusé, Retiré, Expiré |
| **Méthode de collecte** | Portail, Courriel, Verbal, Écrit |
| **Date d'expiration** | Calculée automatiquement selon la finalité |
| **Projet** | Projet associé (optionnel) |

#### Actions disponibles

| Action | Condition | Description |
|--------|-----------|-------------|
| **Envoyer la demande** | Statut = Brouillon | Envoie un courriel et passe en « En attente » |
| **Accorder** | Statut = Brouillon ou En attente | Accorde le consentement manuellement |
| **Refuser** | Statut = En attente | Refuse le consentement manuellement |
| **Retirer** | Statut = Accordé | Ouvre l'assistant de retrait |
| **Renouveler** | Statut = Accordé, Expiré ou Retiré | Crée une nouvelle demande |
| **Remettre en brouillon** | Statut = En attente ou Refusé | Réinitialise pour modification |
| **Envoyer via DocuSeal** | DocuSeal configuré | Envoie pour signature électronique |

### 4.2 Demandes en attente

Cette vue filtre automatiquement les consentements ayant le statut :
- **Brouillon** : En préparation
- **En attente** : Envoyé, en attente de réponse

C'est votre liste de travail quotidienne pour le suivi des demandes.

### 4.3 Demandes de destruction

#### Cycle de vie d'une demande de destruction

```
En attente → Approuvée → Exécutée
     ↓
  Annulée
```

#### Créer une demande de destruction

Les demandes sont généralement créées automatiquement par :
- L'expiration d'un consentement (selon la politique de rétention)
- Le retrait d'un consentement (selon la politique de rétention)

Création manuelle :
1. Allez dans **Opérations > Demandes de destruction**
2. Cliquez sur **Nouveau**
3. Sélectionnez le **consentement** concerné
4. Choisissez la **méthode de destruction** :
   - **Anonymiser** : Remplace les données par des valeurs génériques
   - **Supprimer** : Supprime définitivement les données
   - **Archiver** : Déplace vers une archive sécurisée
   - **Manuel** : Nécessite une intervention humaine

#### Approuver une demande

1. Ouvrez la demande en attente
2. Vérifiez les informations
3. Cliquez sur **Approuver**
4. La demande passe au statut « Approuvée »

#### Exécuter une destruction

1. Ouvrez une demande approuvée
2. Cliquez sur **Exécuter**
3. Le système :
   - Exécute la méthode de destruction choisie
   - Génère un **certificat de destruction**
   - Passe au statut « Exécutée »

#### Certificat de destruction

Le certificat contient :
- Numero unique de certificat
- Informations sur le contact et le consentement
- Methode de destruction utilisee
- Dates pertinentes
- Base legale (Loi 25, RGPD)
- Empreinte SHA-256 pour verification d'integrite

### 4.4 Registre de destruction

**Menu :** Vie privee > Operations > Registre de destruction

Le registre de destruction est un journal **immuable** conforme a l'article 3.2 de la LPRPSP. Chaque destruction de renseignements personnels y est automatiquement consignee.

**Caracteristiques :**
- Les entrees sont en **lecture seule** : aucune modification (sauf les notes) ni suppression n'est possible
- Chaque entree contient une empreinte SHA-256 pour la verification d'integrite anti-falsification
- Numerotation automatique : `REG-YYYY-NNNNN`

**Informations enregistrees :**
- Date de destruction et auteur
- Approbateur (RPRP)
- Document detruit (modele, ID, nom au moment de la destruction)
- Categories de renseignements personnels concernees
- Nombre de sujets affectes
- Methode de destruction utilisee
- Base legale applicable
- Lien vers la demande de destruction ou la campagne

**Acces :** Responsable de la vie privee (Privacy Officer) uniquement.

### 4.5 Campagnes de destruction

**Menu :** Vie privee > Operations > Campagnes de destruction

Les campagnes permettent d'executer des purges periodiques de documents dont la periode de conservation a expire.

**Workflow :**

1. **Brouillon** : Creer la campagne, choisir la date limite et optionnellement une regle de conservation specifique
2. **Balayage** : Cliquer sur **Scanner** — le systeme identifie automatiquement tous les documents classifies dont la retention a expire avant la date limite
3. **Revision** : Examiner la liste des documents identifies, ignorer individuellement ceux a conserver
4. **Approbation** : Le responsable de la vie privee (RPRP) approuve la campagne
5. **Execution** : Chaque document est detruit selon la methode configuree, avec une entree au registre pour chaque destruction
6. **Terminee** : Le systeme affiche un resume (X detruits, Y echecs, Z ignores)

**Gestion des erreurs :** Si un document ne peut pas etre detruit, la ligne est marquee en echec avec un message d'erreur. Les autres documents continuent a etre traites normalement.

### 4.6 Evaluations d'anonymisation

**Menu :** Vie privee > Operations > Evaluations d'anonymisation

Les evaluations d'anonymisation permettent de valider si un jeu de donnees est effectivement anonyme selon les 3 criteres du Reglement sur l'anonymisation des renseignements personnels (A-2.1, r. 0.1, mai 2024).

**Workflow :**

1. **Brouillon** : Decrire le jeu de donnees a evaluer
2. **Analyse** : Evaluer chacun des 3 criteres :
   - **Individualisation** : Peut-on isoler ou distinguer une personne dans le jeu de donnees?
   - **Correlation** : Peut-on relier des jeux de donnees concernant la meme personne?
   - **Inference** : Peut-on deduire des renseignements personnels a partir d'autres donnees?
3. **Completee** : Une fois les 3 criteres evalues, le systeme calcule le risque global
4. **Reevaluation requise** : Signale automatiquement quand la reevaluation periodique est due

**Resultat :** Les donnees sont considerees comme **effectivement anonymes** uniquement si les 3 criteres presentent un risque **faible**. Dans le cas contraire, des mesures supplementaires sont necessaires.

**Reevaluation :** L'intervalle par defaut est de 12 mois. Un cron quotidien surveille les echeances et cree automatiquement une activite de rappel.

---

## 5. Configuration

### 5.1 Finalités

Les finalités définissent les raisons pour lesquelles vous collectez des consentements.

#### Finalités par défaut

| Code | Nom | Consentement exprès | Validité |
|------|-----|:------------------:|----------|
| `marketing` | Communications marketing | ✓ | 730 jours |
| `recording` | Enregistrement vidéo | ✓ | 365 jours |
| `recording_audio` | Enregistrement audio | ✓ | 365 jours |
| `transcription` | Transcription | ✓ | 365 jours |
| `reference` | Utilisation comme référence | ✗ | 1095 jours |
| `logo` | Utilisation du logo | ✗ | 730 jours |
| `case_study` | Étude de cas | ✓ | 730 jours |
| `service` | Communications de service | N/A | Illimité |
| `third_party` | Partage avec tiers | ✗ | 365 jours |

#### Créer une finalité

1. Allez dans **Configuration > Finalités**
2. Cliquez sur **Nouveau**
3. Remplissez les champs :

| Champ | Description |
|-------|-------------|
| **Code** | Identifiant unique (ex: `newsletter`) |
| **Nom** | Nom affiché (ex: « Inscription à l'infolettre ») |
| **Description** | Explication interne |
| **Résumé en langage clair (FR)** | Texte simple expliquant la finalité en français |
| **Résumé en langage clair (EN)** | Texte simple expliquant la finalité en anglais |
| **Consentement exprès requis** | Si coché, requiert un « opt-in » explicite |
| **Jours de validité par défaut** | Durée avant expiration (0 = illimité) |
| **Jours avant expiration auto** | Pour les demandes en attente sans réponse |
| **Canaux** | Email, Téléphone, SMS, Vidéo, En personne, Tous |
| **Contexte** | Marketing, Réunion, Projet, Général |

#### Résumé en langage clair

La Loi 25 exige que les informations soient présentées en termes simples et clairs. Exemple :

**Bon exemple :**
> « Nous souhaitons vous envoyer par courriel des nouvelles sur nos produits et des offres spéciales. Vous pouvez vous désabonner à tout moment en cliquant sur le lien dans nos courriels. »

**Mauvais exemple :**
> « Le responsable du traitement utilisera vos données personnelles conformément aux dispositions de l'article 53.1 de la Loi... »

### 5.2 Avis de consentement

Les avis sont les textes présentés aux personnes lors de la demande de consentement.

#### Gestion des versions

Chaque modification d'un avis crée une nouvelle **version** :
- Les versions sont **immuables** une fois utilisées
- Une **empreinte SHA-256** garantit l'intégrité
- L'historique complet est conservé

#### Créer un avis

1. Allez dans **Configuration > Avis de consentement**
2. Cliquez sur **Nouveau**
3. Remplissez :
   - **Nom** : Identifiant de l'avis
   - **Finalité** : Liée à quelle finalité
   - **Contenu (FR)** : Texte en français
   - **Contenu (EN)** : Texte en anglais
4. Cliquez sur **Créer une version**

#### Bonnes pratiques

- Utilisez un langage simple et accessible
- Expliquez clairement ce à quoi la personne consent
- Mentionnez la durée de conservation
- Indiquez comment retirer le consentement
- Identifiez qui contacter pour les questions

### 5.3 Préférences de contact

Les préférences permettent aux contacts de gérer leurs canaux de communication.

#### Canaux disponibles

| Canal | Description | Par défaut |
|-------|-------------|------------|
| **Courriels de service** | Communications essentielles (factures, confirmations) | Activé |
| **Courriels marketing** | Promotions, infolettres | Désactivé |
| **Appels téléphoniques** | Contact par téléphone | Activé |
| **Messages SMS** | Messages texte | Désactivé |

#### Option « Ne pas contacter »

Lorsqu'activée, cette option :
- Bloque **tous** les canaux de communication
- Ajoute le contact à la liste noire de marketing
- Enregistre la date et la raison du refus

#### Synchronisation avec le marketing

Les préférences sont synchronisées avec :
- Liste noire des courriels (`mail.blacklist`)
- Contacts de diffusion (`mailing.contact`)
- Abonnements aux listes (`mailing.contact.subscription`)

### 5.4 Politiques de rétention

Les politiques définissent combien de temps les données sont conservées et comment elles sont détruites.

#### Créer une politique

1. Allez dans **Configuration > Politiques de rétention**
2. Cliquez sur **Nouveau**
3. Configurez :

| Champ | Description |
|-------|-------------|
| **Finalité** | À quelle finalité s'applique cette politique |
| **Jours de rétention** | Combien de temps conserver après le déclencheur |
| **Déclencheur** | Expiration, Retrait, ou Les deux |
| **Méthode de destruction** | Anonymiser, Supprimer, Archiver, Manuel |

#### Exemple de politique

**Marketing :**
- Rétention : 90 jours après expiration ou retrait
- Méthode : Anonymiser
- Déclencheur : Les deux (expiration et retrait)

### 5.5 Calendrier de conservation

**Menu :** Vie privee > Configuration > Calendrier de conservation

Le calendrier de conservation definit les regles de retention par **type de document** (distinct des politiques de retention qui s'appliquent par consentement).

#### Creer une regle

1. Allez dans **Configuration > Calendrier de conservation**
2. Cliquez sur **Nouveau**
3. Configurez :

| Champ | Description |
|-------|-------------|
| **Code** | Identifiant unique (ex: `FIN-001`, `RH-003`) |
| **Nom** | Nom descriptif de la regle |
| **Type de document** | Contrat, facture, dossier RH, projet, etc. |
| **Base legale** | Reference juridique obligatoire (ex: Art. 2925 C.c.Q.) |
| **Conservation active** | Nombre d'annees de conservation active |
| **Conservation semi-active** | Nombre d'annees supplementaires |
| **Disposition finale** | Detruire, anonymiser, archiver en permanence, transferer |
| **Methode de destruction** | Anonymiser, supprimer, effacement securise, manuel |
| **Approbation requise** | Si le RPRP doit approuver (recommande) |

**Exemple :** Les contrats (CTR-001) sont conserves 6 ans en actif, puis detruits. Base legale : Art. 2925 C.c.Q. (prescription civile).

#### Bouton « Creer une campagne »

Depuis une regle du calendrier, cliquez sur **Creer une campagne** pour lancer une purge de tous les documents classifies sous cette regle dont la retention a expire.

### 5.6 Classifications documentaires

**Menu :** Vie privee > Configuration > Classifications documentaires

Les classifications permettent de taguer n'importe quel enregistrement Odoo avec les categories de renseignements personnels qu'il contient.

#### Classifier un document

1. Allez dans **Configuration > Classifications documentaires**
2. Cliquez sur **Nouveau**
3. Selectionnez le modele (ex: `res.partner`, `project.task`, `hr.employee`)
4. Selectionnez l'enregistrement specifique
5. Choisissez la categorie de RP et le niveau de sensibilite

**Categories de RP disponibles :**
- Identification (nom, NAS, courriel)
- Medical / sante
- Financier
- Biometrique
- Geolocalisation
- Antecedents judiciaires
- Opinions politiques / syndicales
- Origine ethnique / raciale
- Renseignements sur un mineur
- Autre

**Niveaux de sensibilite :**
- Public
- Interne
- Confidentiel
- Hautement confidentiel

**Date d'expiration :** Calculee automatiquement a partir de la date de classification + la duree de la regle de conservation associee.

**Modeles autorises :** Pour des raisons de securite, seuls certains modeles peuvent etre classifies (contacts, projets, taches, employes, factures, pieces jointes, etc.). Les modeles systeme sont exclus.

### 5.7 Sequences de courriels

Les sequences permettent d'envoyer des rappels automatiques aux personnes n'ayant pas repondu.

#### Creer une sequence

1. Allez dans **Configuration > Sequences de courriels**
2. Cliquez sur **Nouveau**
3. Configurez :

| Champ | Description |
|-------|-------------|
| **Finalite** | Pour quelle finalite |
| **Numero de sequence** | Ordre d'envoi (1, 2, 3...) |
| **Jours apres demande** | Delai avant envoi |
| **Modele de courriel** | Modele a utiliser |
| **Verifier l'ouverture** | N'envoie que si le precedent n'a pas ete ouvert |

#### Exemple de sequence

**Rappels pour consentement marketing :**
1. **Rappel 1** : 7 jours apres la demande initiale
2. **Rappel 2** : 14 jours apres la demande initiale
3. **Rappel final** : 21 jours apres la demande initiale

### 5.8 DocuSeal / LibreSign

DocuSeal permet d'obtenir des signatures électroniques pour les consentements formels.

#### Configuration initiale

1. Allez dans **Configuration > DocuSeal > Configuration**
2. Cliquez sur **Nouveau** ou modifiez l'existant
3. Remplissez :

| Champ | Description |
|-------|-------------|
| **URL de l'API** | `https://api.docuseal.co` (par défaut) |
| **Clé API** | Votre clé API DocuSeal (chiffrée) |
| **Secret du webhook** | Pour valider les notifications (chiffré) |
| **Courriel de l'expéditeur** | Adresse d'envoi par défaut |
| **Envoi automatique** | Envoie automatiquement les demandes |

4. Cliquez sur **Tester la connexion** pour valider

#### Modèles DocuSeal

1. Allez dans **Configuration > DocuSeal > Modèles**
2. Associez vos modèles DocuSeal aux finalités Odoo
3. Chaque finalité peut avoir son propre modèle de signature

#### Processus de signature

1. Ouvrez un consentement
2. Cliquez sur **Envoyer via DocuSeal**
3. Sélectionnez le modèle
4. Le contact reçoit le document par courriel
5. Une fois signé :
   - Le webhook met à jour Odoo
   - Le consentement est automatiquement accordé
   - Le document signé est ajouté comme preuve

---

## 6. Portail client

### Accès au portail

Les clients peuvent gérer leurs consentements via le portail :

```
/my/privacy                    → Centre de préférences
/my/privacy/preferences        → Modifier les préférences
/my/privacy/consents           → Historique des consentements
/my/privacy/consent/<id>       → Détail d'un consentement
```

### Centre de préférences

Les clients peuvent :
- Voir leurs consentements actifs
- Modifier leurs préférences de communication
- Activer/désactiver le « Ne pas contacter »

### Répondre à une demande

**Via le portail (authentifié) :**
1. Se connecter au portail
2. Aller dans **Mon compte > Vie privée**
3. Ouvrir la demande en attente
4. Cliquer sur **Accorder** ou **Refuser**

**Via le lien courriel (public) :**
1. Cliquer sur le lien dans le courriel reçu
2. Lire l'avis de consentement
3. Cliquer sur **Accorder** ou **Refuser**

### Retirer un consentement

1. Ouvrir un consentement accordé
2. Cliquer sur **Retirer mon consentement**
3. Sélectionner une raison :
   - Sur demande
   - Suppression des données
   - Erreur
   - Plus nécessaire
   - Autre
4. Ajouter des notes (optionnel)
5. Confirmer

### Renouveler un consentement

1. Ouvrir un consentement expiré ou retiré
2. Cliquer sur **Renouveler**
3. Un nouveau consentement est créé et lié à l'ancien

---

## 7. Intégration avec les contacts

### Badge de consentement

Sur la fiche contact, vous pouvez voir :

| Badge | Signification |
|-------|---------------|
| 🟢 Marketing | Consentement marketing actif |
| 🟢 Enregistrement | Consentement d'enregistrement actif |
| 🟢 Référence | Consentement de référence actif |

### Bouton « Demander un consentement »

Depuis la fiche contact :
1. Cliquez sur le bouton intelligent **Consentements**
2. Ou utilisez **Action > Demander un consentement**
3. L'assistant s'ouvre avec le contact pré-sélectionné

### Compteur de consentements

Le bouton intelligent affiche le nombre de consentements liés au contact.

### Préférences de contact

Chaque contact a un enregistrement de préférences accessible via :
- Le champ **Préférences de contact** sur la fiche
- Le menu **Vie privée > Préférences de contact**

---

## 8. Intégration avec les projets

### Statut de consentement du projet

Chaque projet affiche un statut global :

| Statut | Signification |
|--------|---------------|
| ⚪ Aucun | Aucun consentement requis |
| 🟡 En attente | Des demandes sont en attente |
| 🟠 Partiel | Certains consentements manquent |
| 🟢 Complet | Tous les consentements sont accordés |

### Demander des consentements pour un projet

1. Ouvrez le projet
2. Cliquez sur **Action > Demander un consentement**
3. Sélectionnez les contacts concernés
4. Choisissez la finalité (ex: Utilisation comme référence)
5. Les consentements sont créés et liés au projet

### Vue des consentements du projet

Le bouton intelligent **Consentements** sur le projet affiche tous les consentements liés.

---

## 9. Cycle de vie d'un consentement

### Diagramme d'états

```
                    ┌─────────────┐
                    │  Brouillon  │
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  │
┌───────────────┐  ┌───────────────┐          │
│  En attente   │  │   Accordé     │◄─────────┘
└───────┬───────┘  └───────┬───────┘   (direct)
        │                  │
   ┌────┴────┐        ┌────┴────┐
   │         │        │         │
   ▼         ▼        ▼         ▼
┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐
│Refusé│  │Expiré│  │Retiré│  │Expiré│
└──────┘  └──────┘  └──────┘  └──────┘
   │         │        │         │
   └─────────┴────────┴─────────┘
                 │
                 ▼
          ┌────────────┐
          │ Renouveler │
          └────────────┘
```

### Transitions d'état

| De | Vers | Action | Déclencheur |
|----|------|--------|-------------|
| Brouillon | En attente | Envoyer la demande | Manuel |
| Brouillon | Accordé | Accorder | Manuel |
| En attente | Accordé | Accorder | Manuel ou Portail |
| En attente | Refusé | Refuser | Manuel ou Portail |
| En attente | Expiré | Expiration automatique | Cron |
| Accordé | Retiré | Retirer | Manuel ou Portail |
| Accordé | Expiré | Expiration automatique | Cron |
| Refusé | Brouillon | Remettre en brouillon | Manuel |
| En attente | Brouillon | Remettre en brouillon | Manuel |

### Dates importantes

| Date | Description |
|------|-------------|
| `requested_at` | Quand la demande a été envoyée |
| `granted_at` | Quand le consentement a été accordé |
| `refused_at` | Quand le consentement a été refusé |
| `withdrawn_at` | Quand le consentement a été retiré |
| `expires_at` | Date d'expiration calculée |

### Chaîne de renouvellement

Chaque consentement conserve un lien vers :
- `renewed_from_id` : Le consentement précédent
- `renewed_to_id` : Le consentement suivant

Cela permet de tracer l'historique complet des renouvellements.

---

## 10. Preuves et traçabilité

### Types de preuves

| Type | Description |
|------|-------------|
| `pdf_signed` | Document signé via DocuSeal |
| `screenshot` | Capture d'écran |
| `document` | Document générique |
| `verbal_note` | Notes d'une confirmation verbale |
| `email` | Confirmation par courriel |
| `portal_log` | Journal d'activité du portail |
| `consent_granted` | Preuve d'accord automatique |
| `consent_refused` | Preuve de refus automatique |
| `consent_withdrawn` | Preuve de retrait automatique |
| `consent_renewed` | Preuve de renouvellement automatique |

### Données médico-légales collectées

Chaque action via le portail enregistre automatiquement :

| Donnée | Description |
|--------|-------------|
| **Adresse IP** | Adresse IP du visiteur (supporte les proxys) |
| **Agent utilisateur** | Navigateur et système d'exploitation |
| **ID de session** | Identifiant unique de session |
| **Langue acceptée** | Préférences de langue du navigateur |
| **URL de référence** | Page source |
| **Méthode HTTP** | GET ou POST |
| **Horodatage UTC** | Date et heure précises (ISO 8601) |
| **Action** | Accord, Refus, Retrait, Renouvellement |
| **Type d'accès** | Portail authentifié, Public, Lien courriel, API, Manuel |
| **Instantané du consentement** | Copie JSON de l'état au moment de l'action |
| **Empreinte** | Hash SHA-256 pour vérification d'intégrité |

### Ajouter une preuve manuellement

1. Ouvrez un consentement
2. Allez dans l'onglet **Preuves**
3. Cliquez sur **Ajouter une ligne**
4. Remplissez :
   - Type de preuve
   - Description
   - Pièce jointe (optionnel)
5. Les métadonnées sont remplies automatiquement

---

## 11. Automatisations

### Tâches planifiées (Cron)

| Tâche | Fréquence | Description |
|-------|-----------|-------------|
| **Vérifier les expirations** | Quotidienne | Crée des activités 30 et 7 jours avant expiration |
| **Marquer les expirations** | Quotidienne | Change le statut en « Expiré » à la date d'expiration |
| **Séquences de courriels** | Quotidienne | Envoie les rappels selon les séquences configurées |
| **Expiration des demandes** | Quotidienne | Expire les demandes en attente sans réponse |
| **Créer les destructions** | Quotidienne | Crée les demandes selon les politiques de rétention |
| **Exécuter les destructions** | Quotidienne | Exécute les destructions planifiées |

### Activités créées automatiquement

| Activite | Quand | Assigne a |
|----------|-------|-----------|
| **Consentement va expirer** | 30 jours avant | Utilisateur responsable |
| **Consentement expire bientot** | 7 jours avant | Utilisateur responsable |
| **Revoir la destruction** | Demande creee | Gestionnaire vie privee |
| **Supprimer dossier Nextcloud** | Destruction avec Nextcloud | Gestionnaire vie privee |
| **Reevaluation d'anonymisation due** | Date de reevaluation atteinte | Evaluateur original |

---

## 12. Rôles et permissions

### Groupes de sécurité

#### Utilisateur vie privee

**Peut :**
- Voir les finalites, avis et consentements
- Creer et modifier des consentements
- Creer et modifier des preuves
- Gerer les preferences de contact
- Consulter le registre de destruction (lecture seule)
- Consulter les classifications documentaires (lecture seule)

**Ne peut pas :**
- Supprimer des enregistrements
- Acceder a la configuration
- Gerer les destructions ou campagnes

#### Gestionnaire vie privee

**Inclut** toutes les permissions de l'Utilisateur, plus :

**Peut :**
- Configurer les finalites et avis
- Configurer les politiques de retention et le calendrier de conservation
- Gerer les sequences de courriels
- Creer et modifier les classifications documentaires
- Creer des demandes de destruction et les **approuver**
- Creer et gerer les campagnes de destruction
- Creer et modifier les evaluations d'anonymisation
- Configurer les modeles DocuSeal / LibreSign

**Ne peut pas :**
- Executer les destructions
- Approuver les campagnes ou evaluations
- Modifier ou supprimer des entrees du registre

#### Responsable vie privee (RPRP)

**Inclut** toutes les permissions du Gestionnaire, plus :

**Peut :**
- **Approuver et executer** les demandes de destruction
- **Approuver et executer** les campagnes de destruction en lot
- **Approuver** les evaluations d'anonymisation
- Creer des entrees au registre de destruction
- Administration complete du module
- Configurer DocuSeal / LibreSign (API, webhooks)
- Exporter toutes les donnees

### Règles d'accès

| Règle | Description |
|-------|-------------|
| **Multi-entreprise** | Chaque utilisateur ne voit que les données de sa(ses) entreprise(s) |
| **Portail - Consentements** | Les utilisateurs portail voient uniquement leurs propres consentements (lecture seule) |
| **Portail - Préférences** | Les utilisateurs portail peuvent modifier leurs propres préférences |

---

## 13. Glossaire

| Terme | Définition |
|-------|------------|
| **Consentement** | Accord donné par une personne pour un traitement spécifique de ses données |
| **Consentement exprès** | Consentement qui doit être donné de manière active (opt-in) |
| **Finalité** | Raison pour laquelle les données personnelles sont collectées ou traitées |
| **Avis de consentement** | Document expliquant à quoi la personne consent |
| **Loi 25** | Loi québécoise modernisant les dispositions législatives en matière de protection des renseignements personnels |
| **Politique de rétention** | Règles définissant combien de temps les données sont conservées |
| **Destruction** | Processus de suppression ou d'anonymisation des données |
| **Preuve** | Document ou enregistrement attestant du consentement |
| **Opt-in** | Action positive requise pour donner son consentement |
| **Opt-out** | Possibilité de retirer son consentement |
| **Ne pas contacter** | Demande d'une personne de ne plus recevoir aucune communication |
| **Portail** | Interface web permettant aux clients de gérer leurs informations |
| **DocuSeal** | Service de signature électronique intégré |
| **Webhook** | Notification automatique entre systèmes |
| **Empreinte (Hash)** | Code unique genere a partir d'un contenu pour verifier son integrite |
| **UUID** | Identifiant unique universel utilise pour les jetons d'acces |
| **Medico-legal** | Relatif a la collecte de preuves utilisables en contexte juridique |
| **Calendrier de conservation** | Ensemble de regles definissant la duree de retention par type de document |
| **Classification documentaire** | Attribution de categories de RP a un enregistrement Odoo |
| **Registre de destruction** | Journal immuable de toutes les destructions de RP (Art. 3.2 LPRPSP) |
| **Campagne de destruction** | Purge periodique de documents depasses selon le calendrier de conservation |
| **Evaluation d'anonymisation** | Evaluation des 3 criteres du Reglement A-2.1, r. 0.1 pour valider l'anonymisation |
| **Individualisation** | Critere d'anonymisation : peut-on isoler une personne dans le jeu de donnees? |
| **Correlation** | Critere d'anonymisation : peut-on relier des jeux de donnees? |
| **Inference** | Critere d'anonymisation : peut-on deduire des RP? |
| **RPRP** | Responsable de la protection des renseignements personnels (Privacy Officer) |
| **LPRPSP** | Loi sur la protection des renseignements personnels dans le secteur prive |
| **Art. 23 LPRPSP** | Obligation de detruire les RP une fois les fins de collecte atteintes |
| **Art. 28.1 LPRPSP** | Droit a l'effacement (droit a l'oubli) |
| **Art. 3.2 LPRPSP** | Cadre de gouvernance incluant la conservation et la destruction |
| **Droit a l'effacement** | Droit d'une personne de demander la suppression de ses donnees personnelles |
| **LibreSign** | Service auto-heberge de signature electronique integre |

---

## Annexe A : Modèles de courriels

### Demande de consentement initiale

Le modèle par défaut inclut :
- Logo de l'entreprise
- Salutation personnalisée
- Nom de la finalité
- Résumé en langage clair
- Bouton d'action vers le portail
- Coordonnées pour questions

### Rappels

Les rappels reprennent le format initial avec :
- Mention qu'il s'agit d'un rappel
- Numéro du rappel (1er, 2e, etc.)
- Urgence accrue pour les derniers rappels

### Confirmation de renouvellement

Envoyée après un renouvellement réussi :
- Confirmation du nouveau consentement
- Nouvelle date d'expiration
- Lien vers le portail

---

## Annexe B : Intégrations externes

### DocuSeal

- **URL API** : `https://api.docuseal.co`
- **Webhook** : `/privacy/docuseal/webhook`
- **Sécurité** : Clés chiffrées avec Fernet

### Marketing (mass_mailing)

- Synchronisation avec `mail.blacklist`
- Synchronisation avec `mailing.contact`
- Synchronisation avec `mailing.contact.subscription`

### Nextcloud (optionnel)

- Création d'activités pour suppression manuelle de dossiers
- Intégration via le module `hosting_management` si installé

---

## Annexe C : Conformité légale

### Loi 25 du Québec

Ce module aide à respecter les exigences suivantes :

| Article | Exigence | Fonctionnalité |
|---------|----------|----------------|
| 8.1 | Consentement manifeste | Collecte explicite via portail |
| 8.2 | Consentement libre et éclairé | Avis en langage clair |
| 8.3 | Consentement spécifique | Finalités granulaires |
| 14 | Droit de retrait | Fonction de retrait |
| 27 | Conservation limitée | Politiques de rétention |
| 28 | Destruction sécurisée | Certificats de destruction |

### RGPD (si applicable)

| Article | Exigence | Fonctionnalité |
|---------|----------|----------------|
| 7 | Conditions du consentement | Preuves documentées |
| 17 | Droit à l'effacement | Demandes de destruction |
| 30 | Registre des traitements | Journal complet |

---

## Annexe D : Dépannage

### Le courriel de demande n'est pas envoyé

1. Vérifiez la configuration du serveur de messagerie sortant
2. Vérifiez que le contact a une adresse courriel valide
3. Consultez le journal des courriels dans **Paramètres > Technique > Courriels**

### Le webhook DocuSeal ne fonctionne pas

1. Vérifiez l'URL du webhook dans DocuSeal
2. Vérifiez que le secret du webhook correspond
3. Consultez les journaux du serveur Odoo

### Les rappels ne sont pas envoyés

1. Vérifiez que les séquences de courriels sont configurées
2. Vérifiez que la tâche planifiée est active
3. Vérifiez les conditions (jours écoulés, ouverture du courriel)

### Un contact ne peut pas accéder au portail

1. Vérifiez que le contact a un utilisateur portail associé
2. Vérifiez les droits d'accès du groupe portail
3. Vérifiez que le jeton d'accès n'a pas expiré (pour les liens publics)

---

## Support

Pour toute question ou assistance :

- **Documentation technique** : Consultez les fichiers README dans le module
- **Support** : Contactez votre administrateur Odoo ou Your Company Name

---

*Document généré pour le module privacy_consent version 18.0.2.0.0*
*Your Company Name - Conformité Loi 25*
