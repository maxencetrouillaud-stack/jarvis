# Jarvis — version 0.5 (application Windows à mises à jour automatiques)

Assistant personnel local : cerveau multi-modèles, mémoire, centre de connaissances, chercheur autonome,
mode vocal, sphère 3D. La version 0.5 en fait une vraie application : installée une fois, elle se met ensuite
à jour d'elle-même, sans archive à télécharger.
Les agents et le contrôle complet de Windows viennent aux étapes suivantes (voir « Ce qui n'existe pas encore »).

## Installer (une seule fois)

Prérequis : Python 3.9 ou plus récent (https://www.python.org/downloads/, cocher « Add python.exe to PATH »).
Aucune autre installation : le moteur n'utilise que la bibliothèque standard de Python.

Dans PowerShell (menu Démarrer › « PowerShell »), coller :

    irm https://raw.githubusercontent.com/maxencetrouillaud-stack/jarvis/main/install.ps1 | iex

Ou, depuis un dossier de Jarvis déjà extrait : double-cliquer sur `Installer Jarvis.bat`.

L'installation copie Jarvis dans `%LOCALAPPDATA%\Programs\Jarvis`, crée les raccourcis « Jarvis » sur le Bureau
et dans le menu Démarrer (icône bleue, sans fenêtre noire), l'inscrit dans « Applications installées » de Windows
(désinstallation possible depuis Paramètres), puis le lance. Au premier démarrage : nom de l'assistant, votre prénom,
puis une clé API (Claude, GPT ou Gemini) ou Ollama pour un modèle local hors ligne.

Fonctionnement de l'application : un clic sur le raccourci ouvre Jarvis dans sa propre fenêtre ; fermer la fenêtre
arrête le moteur au bout de 2 minutes (sauf indexation ou recherche en cours, qui se terminent d'abord).
Journal du moteur : `%APPDATA%\Jarvis\jarvis.log`. `Lancer Jarvis.bat` reste utilisable (avec la fenêtre noire).

## Mises à jour

Jarvis vérifie au démarrage puis toutes les 6 heures s'il existe une version plus récente sur
https://github.com/maxencetrouillaud-stack/jarvis. Si oui, un bandeau propose « Installer la mise à jour » :
- seuls les fichiers modifiés sont téléchargés ;
- l'empreinte SHA-256 de chacun est vérifiée avant de remplacer quoi que ce soit (un fichier altéré annule tout) ;
- la version actuelle est gardée : Réglages › Mises à jour › « Revenir à la version … » ;
- Jarvis redémarre tout seul et la fenêtre se recharge ;
- vos données (`%APPDATA%\Jarvis`) ne sont jamais touchées.
Réglages › Mises à jour : vérifier maintenant, désactiver la vérification automatique.

Vos données sont dans `%APPDATA%\Jarvis` (base `jarvis.db`, réglages, clés, sauvegardes).

## Ce qui est terminé et testé

**Cerveau**
- Quatre connecteurs : Claude (Anthropic), GPT (OpenAI), Gemini (Google), modèles locaux (Ollama), réponses en flux.
- Changement de modèle à tout moment, même au milieu d'une conversation, sans perdre l'historique ni la mémoire.
- Sélection automatique du modèle selon la tâche (programmation, raisonnement, rédaction, question rapide, général).
  La reconnaissance de la tâche repose sur des mots-clés et la longueur du message ; c'est vous qui associez un modèle à chaque type.
- Bascule sur le modèle par défaut si le modèle choisi automatiquement échoue.
- Réflexion approfondie : plan en étapes, réponse, vérification par un second appel, correction si des erreurs sont relevées.
- Nom, personnalité, température et longueur de réponse réglables.
- Ajouter un futur fournisseur = une classe dans `jarvis_core/providers.py`.

**Mémoire**
- Conversations et souvenirs dans SQLite, sur le PC.
- Souvenirs créés automatiquement après chaque échange (un appel supplémentaire au modèle), sans doublons.
- Catégories, importance de 1 à 5 (5 = toujours rappelé), souvenirs temporaires avec date d'expiration.
- Consultation, ajout, modification, suppression, recherche.
- Recherche plein texte (FTS5) + vecteurs stockés dans la base. Par défaut, vecteurs locaux par mots
  et trigrammes (hors ligne). Recherche par le sens en option, avec un modèle d'embeddings Ollama, OpenAI ou Gemini.
- Rappel des passages d'anciennes conversations en rapport avec la question ; recherche dans les conversations.

**Interface**
- Chat, Artefacts, Navigateur, Images, Projets, Mémoire, Connaissances, Modèles, Tableau de bord, Paramètres.
  Indicateur d'activité relié à l'état réel du moteur.
- Interruption d'une réponse en cours (le texte déjà reçu est conservé).
- Chaque réponse indique le modèle utilisé, le type de tâche, les souvenirs transmis et le résultat de la vérification.

**Sécurité**
- Serveur accessible uniquement depuis ce PC (127.0.0.1), protégé par un jeton de session.
- Clés API chiffrées par Windows (DPAPI) ; elles ne sont jamais renvoyées à l'interface.
- Le texte des modèles n'est jamais exécuté comme du HTML ; les images distantes ne sont pas chargées.
- Le contenu de la mémoire et des documents importés est transmis au modèle comme des données, avec la consigne
  de ne pas en suivre les instructions. C'est une atténuation, pas une garantie.
- Sauvegarde automatique quotidienne (.zip), sauvegarde manuelle, restauration.

## Espace de travail : Artefacts, Navigateur, Images, missions multi-outils (version 0.7)

Une seule instruction suffit : le cerveau central choisit les outils, les confie à des agents spécialisés, les enchaîne en
transmettant les résultats d'un agent au suivant, puis assemble tout dans la conversation et le projet.

**Architecture** (modules indépendants, reliés par des interfaces communes) :
- `workspace.py` — **socle commun** : projets (un seul système : les projets nommés dans la mémoire en font partie), ressources
  (images, fichiers, sources, téléchargements) rangées dans `files/` et dédupliquées, journal des agents, **bus d'événements**
  partagé (missions, navigateur, artefacts, images) diffusé à l'interface en direct (`/api/events`).
- `tools.py` — **registre des outils** : même interface pour tous (contexte de mission, résultats standard : texte, sources,
  images, artefacts), avec durée maximale, nouvel essai borné, **autorisation obligatoire** pour les actions sensibles, journal.
- `missions.py` — **orchestrateur multi-outils** : reconnaissance de la demande (règles, puis le modèle utilitaire seulement si
  c'est ambigu), plan (modèle-type, ou plan établi par le modèle pour les missions qui combinent plusieurs outils, contrôlé :
  outils existants, outils nécessaires présents, contrôle après chaque création), exécution en arrière-plan, **reprise à
  l'étape en échec**, arrêt, synthèse finale avec les sources. Agents : chercheur, concepteur, développeur, ingénieur, vérificateur.
- `artifacts.py`, `browser.py`, `images.py` — les trois modules, utilisables seuls (onglets dédiés) ou par les agents.

**Artefacts** : applications HTML/CSS/JavaScript, composants React, schémas SVG, diagrammes Mermaid, documents et présentations.
- Affichés à droite de la conversation (ou dans l'onglet Artefacts) : **Aperçu réel** / **Code**, versions avec retour arrière,
  **sélection d'un élément** de l'aperçu pour le faire modifier, **Modifier** en discutant, export HTML autonome ou archive,
  plein écran, rangement par projet. Toute modification crée une version et l'aperçu se met à jour seul.
- **Isolation** : l'aperçu est servi avec une politique de sécurité « sandbox » (origine opaque) : le code généré ne peut ni lire
  l'interface de Jarvis, ni appeler son API, ni envoyer de données (aucune connexion réseau autorisée). Seules des bibliothèques
  de cdnjs, jsDelivr et unpkg peuvent être chargées (il faut donc Internet pour React, Chart.js, three.js…).
- Le **vérificateur** contrôle le code (images manquantes, ressources interdites, inachevé) et, si le moteur Chromium est
  installé, **exécute l'artefact dans un navigateur isolé** pour relever les erreurs JavaScript, puis fait corriger (borné).

**Navigateur** : onglets, adresse ou recherche, précédent/suivant/actualiser/accueil, favoris, historique, téléchargements,
plein écran, « Demander à Jarvis » sur la page ouverte.
- **Mode lecture** (sans installation) : la page est téléchargée par Jarvis et affichée nettoyée (aucun script de la page ne
  s'exécute) ; les images passent par un relais signé. Ne convient pas aux sites entièrement construits en JavaScript ni aux pages
  qui demandent une connexion.
- **Mode Chromium** (Paramètres › Installer le moteur Chromium : `pip install playwright`, puis Microsoft Edge du PC est piloté ;
  sinon Chromium est téléchargé) : vrai navigateur sans fenêtre, affiché en direct dans Jarvis, vos clics, défilements et frappes
  lui sont transmis. Vous pouvez vous connecter vous-même à un site ; Jarvis ne saisit jamais de mot de passe.
- **Agent web** : recherche, ouverture des pages dans des onglets « IA » visibles en temps réel, **citations vérifiées mot pour
  mot dans les pages**, comparaison (« confirmée » exige deux sources différentes, contradictions signalées), réponse avec les
  sources [W1]… Il respecte robots.txt, ne remplit aucun formulaire, ne se connecte nulle part, et ne télécharge rien sans votre
  accord (la mission attend votre clic). Les fichiers exécutables ne sont jamais téléchargés.
- **Recherche web générale : il faut une clé Brave Search** (offre gratuite, à saisir dans Connaissances › Chercheur). Sans elle,
  les recherches se limitent à Wikipédia et aux sources scientifiques (HAL, OpenAlex, arXiv) ; une adresse précise se lit toujours.

**Génération d'images** : description en français, images de référence, format (carré, portrait, paysage, vertical, proportions
au choix parmi celles du modèle), style, taille 1K/2K/4K, 1 à 4 variantes, modification d'une image (« ajoute… », « retire… »,
« passe en aquarelle »), version haute résolution régénérée par le modèle, galerie (seules les images enregistrées sont gardées ;
les autres sont effacées après le délai réglé), utilisation dans un artefact (`jarvis-image:N`).
- **Fournisseur gratuit (par défaut depuis la 0.7.1) : Cloudflare Workers AI**, modèles FLUX. Compte Cloudflare gratuit,
  identifiant de compte et jeton d'API « Workers AI » à saisir dans Paramètres › Génération d'images (le jeton est chiffré et
  jamais renvoyé à l'interface). Gratuit dans la limite de 10 000 « neurones » par jour, remise à zéro à minuit UTC : environ
  100 images par jour avec FLUX.2 [klein] 4B (crée et modifie), davantage avec FLUX.1 [schnell] (crée seulement). Pour modifier
  une image, Cloudflare exige une image de moins de 512 px : Jarvis la réduit avec le module Pillow (bouton « Installer Pillow »).
- **Fournisseur payant : Google Gemini** (« Nano Banana »), avec la clé Gemini de l'onglet Modèles. La génération d'images n'est
  pas incluse dans l'offre gratuite de l'API Gemini : la facturation doit être activée. Ajouter un autre fournisseur = une
  classe dans `jarvis_core/images.py`.
- Dans la conversation, « génère-moi… », « crée un logo… », « transforme cette pièce en rendu réaliste », « modifie cette image… »
  déclenchent le générateur sans changer d'onglet.

**Projets** : objectif, notes, état, missions avec le détail des étapes et des agents, conversations, recherches et sources (envoi
d'une source à la bibliothèque technique sur votre clic), images, fichiers, artefacts, souvenirs du projet, export en archive.
Rattacher une conversation à un projet y range aussi ce qu'elle a déjà produit. Les sauvegardes
contiennent les artefacts (dans la base) et les images et fichiers gardés ; les images temporaires n'y sont pas. Rien n'entre dans la mémoire permanente sans
passer par les règles habituelles de la mémoire.

**Paramètres** : choix automatique des outils, recherche web automatique, **validation de chaque plan avant exécution**, contrôle
à l'exécution, limites (étapes, durée, pages lues, corrections), moteurs du navigateur, modèle et taille des images, conservation.

## Cerveau central et mémoire intelligente (version 0.6)

**Cerveau central** (`jarvis_core/orchestrator.py`) — chaque message passe par lui :
- **Analyse** : objectif, type de tâche, complexité (simple, moyenne, complexe, avec les raisons), ressources à consulter,
  outils envisagés, question déjà posée dans la conversation. Faite par des règles : aucun appel au modèle, rapide et explicable.
- **Stratégie** : réponse directe, réponse avec contexte, réflexion approfondie (plan, réponse, vérification, correction)
  ou **tâche décomposée** : le modèle utilitaire découpe la demande en 2 à 6 étapes ; chaque étape est exécutée dans l'ordre
  et reçoit le résultat des étapes dont elle dépend ; une synthèse finale est rédigée puis vérifiée par un second appel.
- **Contrôles avant présentation** (sans appel au modèle) : source citée qui n'existe pas, action annoncée (« j'ai envoyé… »)
  alors qu'aucune action n'a été faite, réponse identique à la précédente.
- **Garde-fous** : nombre d'étapes, d'appels aux modèles et durée plafonnés ; une étape qui répète une étape précédente est
  écartée ; après deux étapes en échec, retour à une réponse en un seul passage.
- **Outils** : quand une demande vise une application de votre liste autorisée, un bouton propose de la lancer — rien ne
  part sans votre clic. La recherche sur des sources externes reste proposée, jamais lancée seule.
- **Trace** : chaque réponse garde son analyse, ses étapes, ses contrôles ; chaque exécution est historisée (stratégie,
  appels, durée, résultat). Visible sous la réponse (« Détail ») et dans le tableau de bord, avec des chiffres réels.

**Mémoire intelligente**
- Nature de chaque souvenir : personnelle, projet, technique ou temporaire ; rattachement à un projet nommé. Les anciens
  souvenirs reçoivent leur nature à la migration.
- **Historique** : toute modification garde l'ancienne version ; chaque version est restaurable ; un souvenir supprimé ou
  expiré reste récupérable (en bas de la vue Mémoire).
- **Mises à jour contrôlées** : quand une conversation corrige un souvenir existant (déménagement, changement d'avis…),
  la correction vous est **proposée** et le souvenir ne change qu'après « Accepter » (réglage : mise à jour automatique,
  ancienne version gardée).
- **Formulations différentes** : la recherche compare des racines de mots et des groupes de synonymes (« boulot » retrouve
  « travail »). Si une question personnelle ne trouve rien, le modèle utilitaire propose d'autres mots et la recherche est
  relancée (un appel de plus, seulement dans ce cas). Les préférences importantes accompagnent chaque demande.
- **Habitudes observées** : statistiques calculées sur vos messages (moments de la journée, types de demandes, longueur,
  usage de la réflexion approfondie). Ce ne sont pas des souvenirs et rien n'est deviné ; à partir de 20 messages, un
  résumé chiffré est transmis au modèle (désactivable).

Limites honnêtes : la complexité est estimée par des règles (longueur, listes, enchaînements, mots comme « plan complet ») ;
une demande difficile mais courte ne sera pas découpée automatiquement (activez alors la réflexion approfondie). La qualité
du découpage et des étapes dépend du modèle utilisé ; tout a été testé avec des modèles simulés.

## Base de connaissances (version 0.2)

Fichier `knowledge.db`, **séparé** de la mémoire personnelle (`jarvis.db`). Pendant une conversation, Jarvis
interroge les deux.

**Choix d'architecture : SQLite et non PostgreSQL + pgvector.** Jarvis se lance sans rien installer ; PostgreSQL
imposerait d'installer et d'administrer un serveur. Toute la couche d'accès aux données est dans
`jarvis_core/knowledge.py` : c'est le seul fichier à réécrire pour passer à PostgreSQL.
Limite de volume : la comparaison vectorielle porte sur tous les passages jusqu'à 6 000 ; au-delà, elle ne porte
que sur les 80 meilleurs candidats de la recherche plein texte. Mesuré ici : environ 60 ms pour une recherche parmi
3 000 passages. Le plein texte de SQLite tient des millions de passages, mais **rien n'a été mesuré au-delà de
quelques milliers**.

**Terminé et testé**
- Import de fichiers : PDF (avec numéro de page), Word .docx (titres et tableaux), Excel .xlsx (feuilles, valeurs
  et formules), CSV, HTML, Markdown, texte, fichiers techniques. Le fichier d'origine est conservé.
  Les PDF demandent le module facultatif `pypdf` (`py -m pip install pypdf`).
- Import d'une page web par son adresse, et d'articles de Wikipédia (recherche + import, licence CC BY-SA et date
  de révision enregistrées).
- Chaque document porte : titre, description, catégorie, mots-clés, source, licence, date de publication,
  date d'import, date de dernière vérification, niveau de fiabilité (1 à 5), références.
- 6 domaines et 44 sous-catégories créés d'office (ce sont des rayonnages vides, pas des connaissances) ;
  vous pouvez en ajouter.
- Indexation en arrière-plan avec suivi (en file, en cours, terminé, erreur, doublon).
- Doublons : un texte identique est refusé ; un texte presque identique est signalé.
- Recherche hybride : plein texte + vecteurs, fusion des deux classements, reclassement, filtre par catégorie.
- Réponses sourcées : les passages pertinents sont transmis au modèle, qui les cite [S1], [S2]… ; l'interface
  affiche les sources citées, et un clic mène au passage d'origine (avec la page pour un PDF).
  Si la base n'a rien sur une question, la réponse est marquée « sans source documentaire ».
- Graphe de connaissances : relations extraites des documents par le modèle utilitaire (une relation n'est gardée
  que si ses deux concepts figurent dans le texte), ajout et suppression à la main, visualisation, et relations
  transmises au modèle quand un concept est nommé dans la question.
- Classement automatique des documents par le modèle utilitaire (désactivable).
- Actualisation des documents web : si la source a changé, l'ancienne version est archivée et consultable.
- Enrichissement : les questions restées sans source et les catégories vides déclenchent, à votre demande, une
  recherche sur Wikipédia ; les articles trouvés sont des propositions, importées seulement après votre accord
  (réglable).
- Journal des modifications, sauvegarde et restauration (`knowledge.db` est dans les sauvegardes).
- Recherche globale (Ctrl+K) dans les connaissances, la mémoire et les conversations.
- Sécurité : les documents ne sont jamais exécutés ; archives piégées et XML suspect refusés ; le téléchargement
  refuse les adresses du réseau local ; le contenu des documents est transmis au modèle comme des données.

**Ce que la base ne fait pas**
- **Elle est livrée vide.** Aucun corpus n'a été importé : la machine qui a construit Jarvis n'a pas accès à
  Internet. L'onglet Importer propose une liste de 79 titres d'articles de Wikipédia à importer d'un clic depuis votre
  PC ; cette liste n'a pas pu être vérifiée, un titre introuvable s'affichera en erreur.
- Pas de recherche libre sur Internet : seulement une adresse que vous donnez, ou Wikipédia.
- Pas de lecture des PDF scannés ni des images (pas de reconnaissance de caractères) ; pas d'extraction d'images.
- La fiabilité est un niveau que vous réglez. Par défaut : 4 pour quelques sites officiels reconnus par leur
  adresse, 3 pour Wikipédia et vos fichiers, 2 pour les autres sites. Jarvis ne vérifie pas l'exactitude d'un
  document.
- Détecter des contradictions entre sources repose sur le modèle qui lit les passages : c'est une consigne, pas un
  contrôle garanti.
- L'enrichissement ne tourne pas tout seul : il se lance avec un bouton.
- Les fichiers d'origine (`knowledge/originals`) ne sont pas dans les sauvegardes .zip.
- Pas de permissions par agent : les agents n'existent pas encore. La recherche accepte déjà une restriction par
  catégorie, prévue pour cela.

## Cerveau étendu (version 0.4)

Les quatre modules partagent **une seule base** (`knowledge.db`, plus `jarvis.db` pour la mémoire personnelle) et les mêmes index.
Onglet « Connaissances » : Tableau de bord, Bibliothèque, Importer, Recherche, Chercheur, Sources, Graphe, À traiter, Indexation, Journal.

**1. Bibliothèque technique**
- Formats lus : PDF (avec `pypdf`), Word, Excel, **PowerPoint (.pptx), OpenDocument (.odt, .ods), RTF**, CSV, HTML, Markdown, texte, code.
- **Formules, tableaux et données chiffrées relevés à l'import** : égalités (`σ = F / S`), LaTeX, formules de tableur, tableaux,
  et données « grandeur : valeur unité » (`module de Young de l'acier : 210 GPa`). Affichés dans la fiche du document et
  cherchables (Recherche › Formules / Données / Tableaux). Rien n'est déduit : uniquement ce qui est écrit.
- **Classement sans modèle** : si aucun modèle ne répond, le document est classé d'après son vocabulaire (lexique par
  sous-catégorie). Sans vocabulaire reconnaissable, il reste non classé plutôt que d'être classé au hasard.
- **Anciennes versions** : deux documents de même titre (au numéro de version ou à l'année près) et de contenu proche mais
  différent sont signalés ; l'ancien n'est archivé (écarté des réponses, mais conservé) que sur votre décision.
- **Données contradictoires** : même grandeur, même unité, écart de plus de 10 % entre deux documents (À traiter).
- **Dossier de la bibliothèque** : indiquez un dossier du PC ; tous les fichiers lisibles sont indexés, puis seulement les
  nouveaux ou modifiés (analyse à la demande, ou toutes les 10 minutes si vous l'activez). Un fichier retiré du dossier
  n'est jamais effacé de la base.
- Nouvelle sous-catégorie « Logiciels de CAO (FreeCAD, CATIA, SolidWorks) ». Les tâches en échec peuvent être relancées.

**2. Mémoire vectorielle**
- Recherche hybride : plein texte (FTS5) + vecteurs, fusion des classements, puis reclassement (MMR) pour éviter les redites.
- **Index de voisinage approché** (signatures binaires, 62 bits par passage) : au-delà de 6 000 passages, la question n'est
  comparée exactement qu'aux candidats proches. Testé sur une base forcée dans ce mode ; pas mesuré sur des millions de documents.
- Vecteurs stockés sur le PC, indépendants du modèle qui répond.
- **Limite à connaître** : sans modèle d'embeddings, les vecteurs reposent sur les mots et leurs variantes, pas sur le sens.
  Pour retrouver une idée sans ses mots, réglez un modèle d'embeddings (onglet Modèles ; Ollama `nomic-embed-text` est gratuit
  et local), puis Indexation › Réindexer. L'interface indique « par le sens » uniquement quand un tel modèle a servi.
- À chaque import, le nouveau document est relié aux documents et souvenirs au contenu proche (connexions visibles dans la sphère).

**3. Chercheur autonome**
- Sources : Wikipédia, HAL, OpenAlex, arXiv (API publiques, sans clé ; clé OpenAlex gratuite facultative), et le web général
  via l'API Brave Search **si vous fournissez une clé** (sinon cette source est annoncée comme non interrogée).
- Déroulement visible étape par étape : domaine, requêtes, sources trouvées, lecture, extraction, vérification.
- Règles appliquées par le code, pas seulement demandées au modèle :
  une affirmation n'est gardée que si sa citation est retrouvée mot pour mot dans la source ;
  « vérifié » = au moins deux sites indépendants ; une seule source = « à confirmer » ; source en désaccord = « contradictoire » ;
  un article rétracté (OpenAlex) est écarté ; les pages web sous droits d'auteur ne sont pas copiées (courts extraits cités).
- **Validation humaine** (activée par défaut) : rien n'entre dans la base sans votre accord ; vous pouvez reformuler ou refuser.
  Validation désactivée : seules les affirmations vérifiées, et qui ne contredisent aucune donnée de la base, sont intégrées.
- Sans modèle disponible : les sources sont collectées et seules les données chiffrées sont relevées.
- Mode approfondi (plus de requêtes, en français et en anglais, plus de sources), historique, veille sur des sujets
  (publications récentes d'abord), sujets insuffisamment documentés, suivi des sources avec fiabilité réglable et blocage.
- En conversation : quand la base ne sait pas, un bouton « Lancer une recherche » apparaît. Aucune recherche ne part sans clic.

**4. Centre de connaissances**
- Tableau de bord : connaissances, documents, souvenirs, relations, catégories, dernières connaissances, dernières recherches,
  documents à actualiser, erreurs d'indexation, espace utilisé, état de la mémoire vectorielle.
- Exploration par catégorie et par module, recherche dans tout le cerveau, gestion des sources et des indexations,
  export (archive zip : textes et métadonnées en JSON, base SQLite, fichiers d'origine) et sauvegarde.

**Connexion au cerveau** — pour chaque message, Jarvis choisit les modules à consulter : un simple « merci » ne déclenche aucune
recherche documentaire, une question personnelle élargit la recherche en mémoire, une question technique identifie le domaine.
Le détail d'une réponse liste les modules consultés ; deux sources en désaccord sur une valeur sont signalées au modèle et à vous.
Route `/api/hub/query` : la même interface, prévue pour les futurs agents (qui n'existent pas encore).

**Connexion à la sphère** — chaque nœud porte son module ; filtre « Modules », couleurs par module, zones de la mémoire
vectorielle (regroupements calculés sur les vecteurs, à partir de 12 éléments), nœuds « Recherche » reliés à leurs connaissances
validées, et apparition progressive des nouvelles connaissances (le nœud grandit avec une onde).

## Mode vocal (version 0.3)

Bouton micro dans la zone de saisie. Reconnaissance et synthèse vocales : celles du navigateur (Edge ou Chrome),
rien à installer.

**À savoir avant de l'utiliser**
- Pendant l'écoute, l'audio du micro est envoyé au service de Microsoft (Edge) ou de Google (Chrome) pour être
  transcrit. Il faut une connexion Internet. En mode mains libres, le micro reste ouvert tant que la fenêtre de Jarvis
  est ouverte.
- La qualité de la voix dépend des voix françaises installées dans Windows et le navigateur. Edge propose en général
  des voix « Natural » nettement meilleures ; le choix se fait dans Réglages → Mode vocal.

**Terminé et testé (avec une reconnaissance vocale simulée, voir plus bas)**
- Conversation continue : un clic sur le micro, puis Jarvis réécoute après chaque réponse sans nouveau clic.
- Fin de phrase détectée par le silence (durée réglable).
- Réponses lues phrase par phrase pendant qu'elles s'écrivent, sans les symboles, le code ni les renvois de sources.
- Interruption : parler pendant qu'il parle, dire « stop », ou cliquer sur le micro. Jarvis ignore ce qui ressemble à
  sa propre phrase captée par le micro ; avec des haut-parleurs ce filtre peut se tromper, un casque règle le problème.
- Mains libres (désactivé par défaut) : veille sur un mot-clé personnalisable, puis écoute ; retour en veille après
  un délai réglable.
- États visibles : veille, écoute (halo et niveaux réels du micro), réflexion, parole, erreur (micro refusé, pas de
  micro, pas de connexion) avec un message clair.
- Commandes traitées sans passer par le modèle : « nouvelle conversation », « ouvre la mémoire / les connaissances /
  les réglages / le tableau de bord / les modèles », « mode silencieux », « mode vocal », « stop ».
- Lancement d'applications : « lance Excel », « ouvre la calculatrice ». Seules les applications de la liste
  autorisée (Réglages) peuvent être lancées, sans paramètre ; chaque lancement est noté dans « Dernières actions ».
  Liste d'origine : Bloc-notes, Calculatrice, Explorateur de fichiers, Excel, Word, Paramètres.

**Ce que le mode vocal ne fait pas**
- Pendant la parole, la visualisation suit le rythme des mots prononcés, pas le signal sonore : le navigateur ne donne
  pas accès au son de la synthèse.
- Pas d'écoute quand la fenêtre de Jarvis est fermée ; pas de mot-clé reconnu hors ligne.
- « Lancer des missions » : une demande vocale est envoyée au modèle comme un message. Les missions en plusieurs
  étapes sur le PC n'existent pas encore.

## Sphère 3D de la mémoire (version 0.3)

L'onglet Mémoire s'ouvre sur la sphère ; le bouton « Liste » ramène à la liste des souvenirs de la version précédente.

**Moteur écrit en WebGL, sans Three.js** (non disponible hors ligne au moment du développement). Un appel de dessin
pour tous les nœuds, un pour tous les liens.

**Terminé et testé**
- Chaque nœud est une ligne réelle de la base : souvenir, document, concept du graphe, famille ou catégorie. Une base
  vide n'affiche que les familles. Le globe filaire et la poussière lumineuse sont un décor, non cliquable.
- 11 familles avec leur couleur : Sciences (bleu), Mathématiques (vert), Ingénierie (orange), Informatique (violet),
  Intelligence artificielle (cyan), Histoire et culture générale (jaune), Vie quotidienne, Mes documents,
  Mémoire personnelle (rose), Projets (blanc), Non classé.
- Positions calculées, pas aléatoires : une région de la sphère par famille, une zone par sous-catégorie, et dans
  chaque zone une projection du contenu (deux textes proches sont voisins). Les concepts sont à l'intérieur de la
  sphère, du côté de leurs documents.
- Liens : hiérarchie, relations du graphe, concept → document source, contenus proches (mots rares en commun).
- Souris : tourner, zoomer, déplacer, cliquer. Zoom animé vers l'élément choisi.
- Panneau de détail : nom, description, catégorie, mots-clés, relations, source, date d'ajout, date de vérification ;
  ouvrir la fiche complète, modifier, demander à Jarvis de présenter le sujet, supprimer (toujours avec confirmation).
- « Explorer plus loin » étend la surbrillance d'un pas à chaque clic.
- Six vues : globale, catégories, relations, mémoire personnelle, projets, documents. Recherche et filtre par famille.
- Synchronisation : la sphère vérifie toutes les 4 secondes si la base a changé et se recharge.
- Recentrer, rotation automatique, zoom, plein écran, mode simplifié (manuel, ou automatique si l'affichage ralentit).

**Limites**
- Au plus 6 000 éléments affichés ; au-delà, les plus récents et les plus reliés (un bandeau l'indique).
- Vitesse : mesurée seulement en rendu logiciel, sans carte graphique : 11 images par seconde avec 3 500 nœuds.
  Sur un PC avec carte graphique ce sera plus rapide, mais ce n'est pas mesuré.
- Les « projets » sont les souvenirs de la catégorie « projets » : il n'y a pas encore de système de projets.
- La proximité de contenu repose sur les mots, pas sur le sens, tant qu'aucun modèle d'embeddings n'est réglé.

## Comment c'est testé — et ce qui ne l'est pas

- `python -m unittest discover -s tests` : 157 tests du moteur (dont 29 pour l'espace de travail de la 0.7 et 22 pour le cerveau central et la mémoire intelligente de la 0.6) (formats des 4 API, mémoire, routage, vérification, sécurité, sauvegardes,
  extraction de documents, base de connaissances, collecte, réponses sourcées, migration, sphère, lancement d'applications,
  et les 4 modules de la 0.4 : relevé de formules, versions, dossier surveillé, index approché, chercheur, validation, export).
- `node tests/e2e.mjs` : parcours complet de l'interface dans un vrai navigateur (demande `npm install` et Playwright).
- Ces tests utilisent des **faux serveurs** qui reproduisent le format des API des modèles, d'un site web, de Wikipédia,
  de HAL, d'OpenAlex, d'arXiv et de Brave Search.
  Côté tests, aucun appel n'a été fait à un vrai modèle ni aux vrais Wikipédia, HAL, OpenAlex, arXiv ou Brave : le
  **chercheur n'a donc jamais interrogé Internet pour de vrai**. Les formats ont été relus dans la documentation de ces API
  en novembre 2026 ; un premier essai sur votre PC dira si chacune répond (une source en échec est signalée, les autres continuent). Sur votre PC, la version 0.1 a démarré et a bien
  joint les API de Google et d'Anthropic (qui ont répondu par des erreurs de quota et de crédit).
- Tests exécutés sous Linux. Les versions 0.3 à 0.5 n'ont pas encore tourné sur Windows : les raccourcis, l'icône,
  l'inscription dans « Applications installées » et le lancement sans console n'ont donc pas été vus sur un vrai Windows.
- Mises à jour et installation testées contre un faux serveur GitHub (téléchargement partiel, fichier altéré, chemin
  dangereux refusé, retour arrière), redémarrage et arrêt à la fermeture de la fenêtre testés en vrai sous Linux.
- La qualité du relevé de formules et de données n'est testée que sur des textes simples : dans un PDF mal structuré,
  des formules seront manquées et quelques relevés seront faux. Ils restent liés au passage d'origine pour vérification.
- **Mode vocal : aucun essai avec un vrai micro.** Le navigateur de test n'a ni micro ni service de transcription : la
  reconnaissance et la synthèse y sont simulées. La logique (fin de phrase, mot-clé, interruption, commandes) est testée ;
  la qualité d'écoute réelle, l'écho et les voix ne le sont pas.
- Le lancement d'applications est testé avec un lanceur factice : aucun programme Windows n'a réellement été lancé.
- **Espace de travail (0.7)** : missions, passage des résultats entre agents, autorisation, reprise, isolation des artefacts,
  mode lecture, robots.txt, téléchargements refusés, recherche Brave et images testés contre des faux serveurs ; le **moteur
  Chromium et le contrôle d'exécution des artefacts ont tourné pour de vrai** (Chromium de Playwright, sous Linux), sur des pages
  locales. **Aucune image n'a été générée par le vrai Cloudflare** : l'adaptateur suit la documentation de Cloudflare (relue en
  novembre 2026) et a été testé contre un faux serveur au même format. Avec Gemini, votre essai réel a bien joint Google, qui a
  répondu « quota dépassé » (pas de génération d'images sans facturation). Côté Gemini : le format des requêtes suit la documentation de Google relue
  en novembre 2026 (avec repli automatique sur l'ancien format de configuration) ; le premier essai avec votre clé dira s'il
  répond. Le pilotage de Microsoft Edge sous Windows et l'installation de Playwright depuis Jarvis n'ont pas été vus sur Windows.

## Ce qui n'existe pas encore

Rien de ceci n'est présent dans l'interface, pas même sous forme de bouton :
vérification des faits et des calculs, niveaux de certitude, série de tests de qualité (phase 2), tâches planifiées et
agents permanents (phase 3 : les missions de la 0.7 s'exécutent à la demande), choix automatique du modèle selon le coût et
suivi des dépenses (phase 4), contrôle du PC (fichiers, exécution de code hors artefacts), remplissage de formulaires ou achats
par l'agent web (volontairement absents), fournisseurs d'images autres que Gemini, base PostgreSQL / pgvector
(la base reste SQLite, sans installation), plugins, installateur `.exe`, démarrage automatique, mises à jour automatiques, mot de passe à l'ouverture.
L'analyse d'images (décrire une photo, lire un PDF scanné) n'est pas gérée : les images servent de référence au générateur.

## Organisation

```
jarvis_core/   moteur Python (providers, brain, orchestrator, memory, knowledge, library, vectors, hub, research, extract, collect,
               kb_api, sphere, keystore, backup, updater, server ; 0.7 : workspace, tools, missions, artifacts, browser,
               images, studio_api)
install.py     installation comme application (raccourcis, désinstallation) ; install.ps1 : installation en une commande
tools/        publish.py : prépare manifest.json pour publier une version
ui/            interface React + TypeScript (sources)
web/           interface compilée, servie par le moteur
tests/         tests du moteur et de l'interface
```

Recompiler l'interface après modification : `npm install` puis `npm run build`.

## Passer d'une version en dossier (0.3 ou 0.4) à l'application

Fermez l'ancienne version, puis installez la 0.5 comme indiqué plus haut. Les données sont partagées : rien n'est perdu.
Vous pouvez ensuite supprimer les anciens dossiers et les zips téléchargés.

## Mettre à jour depuis la version 0.3

Fermez d'abord l'ancienne version (fenêtre noire), puis lancez celle-ci : le lanceur refuse de démarrer si une autre version
tourne encore. Au premier démarrage, la base est mise à niveau sans rien perdre (nouvelles colonnes, signatures de l'index
calculées en arrière-plan). Les formules, tableaux et données des documents déjà importés sont relevés pendant cette mise à niveau.

## Mettre à jour depuis la version 0.1

Fermez Jarvis, remplacez le dossier `jarvis` par celui-ci, relancez. Vos conversations, votre mémoire, vos réglages
et vos clés sont dans `%APPDATA%\Jarvis` et ne sont pas touchés. Les documents importés avec la 0.1 (rangés alors
dans la mémoire) sont déplacés automatiquement dans la base de connaissances au premier démarrage.
