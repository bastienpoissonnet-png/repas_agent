# 🍱 Bot Discord — Repas & Courses Étudiants Monoprix

Système complet d'organisation des repas et d'optimisation logistique des courses pour étudiant, piloté par un bot Discord et connecté aux promotions de **Monoprix** (`courses.monoprix.fr`).

---

## 🎯 1. Contexte & Contraintes Métier

* **Rythme de vie :** Présent en appartement étudiant **5 jours par semaine** (du lundi soir au vendredi midi = 4 dîners et 4 déjeuners).
* **Règle des tupperwares (cruciale) :** L'étudiant ne cuisine **jamais le midi**. Chaque dîner préparé est obligatoirement cuisiné en **double portion** (une part pour le soir même, une part en tupperware pour le déjeuner du lendemain).
* **Gestion des restes du week-end (anti-gaspillage) :** Les plats préparés et ingrédients rapportés de chez les parents le dimanche soir sont intégrés en **priorité absolue** (lundi et mardi) pour garantir leur fraîcheur et éviter tout gaspillage.
* **Budget & promotions Monoprix :** Les courses d'appoint s'appuient sur les promotions actives de la semaine chez Monoprix (-30%, 2e à -50%, cagnottage carte M') pour réduire le panier au strict nécessaire.

---

## 📁 2. Architecture du Projet

```text
repas_agent/
├── bot.py                  # Bot Discord (discord.py), écoute de canal, Embeds riches
├── monoprix_client.py      # Client HTTP (httpx) interrogeant courses.monoprix.fr avec fallback résilient
├── meal_planner.py         # Moteur de planification : tupperwares, scoring promo & déduplication
├── test_system.py          # Suite de tests unitaires et de validation du système
├── requirements.txt        # Dépendances (discord.py, httpx, python-dotenv)
├── .env.example            # Gabarit des variables d'environnement
├── .env                    # Fichier de configuration local (ignoré par git)
├── .gitignore              # Exclusion des fichiers locaux et virtuels
└── README.md               # Guide d'installation et documentation
```

---

## 🌐 3. Promotions Nationales Monoprix (Catalogue Global)

Le bot fonctionne en **mode national / global** :
* **Aucun `MONOPRIX_STORE_ID` requis** : Idéal si votre magasin Monoprix local n'est pas éligible au drive ou au click & collect.
* Le script interroge directement les offres promotionnelles globales et nationales de l'enseigne via l'URL générique `courses.monoprix.fr/promotions` et ses flux publics.
* Vous profitez ainsi des remises nationales de la semaine (-30%, 2e à -50%, cagnottages carte M') applicables dans tous les magasins Monoprix de France.

*(Note : pour les utilisateurs souhaitant forcer un magasin spécifique éligible au drive, le paramètre `store_id` reste disponible en option dans `MonoprixClient(store_id="...")` ou via la variable optionnelle `MONOPRIX_STORE_ID`).*

---

## 🚀 4. Installation et Démarrage Local

### Prérequis
* Python **3.10+** (Python 3.11 recommandé)
* Un compte Discord et un bot créé sur le [Discord Developer Portal](https://discord.com/developers/applications)

### Étape 1 : Cloner le dépôt et créer l'environnement virtuel

```bash
cd /home/bastien/repas_agent

# Création du venv
python3 -m venv .venv

# Activation du venv
source .venv/bin/activate
```

### Étape 2 : Installer les dépendances

```bash
pip install -r requirements.txt
```

### Étape 3 : Configurer les variables d'environnement

Copiez le template `.env.example` en `.env` :

```bash
cp .env.example .env
```

Éditez le fichier `.env` avec vos identifiants Discord :

```env
# Token secret de votre bot Discord
DISCORD_TOKEN=votre_token_discord_ici

# ID numérique du salon Discord dédié aux repas
DISCORD_CHANNEL_ID=1556984067080327168
```

> ⚠️ **Important pour Discord :**
> Dans le **Discord Developer Portal** > votre application > onglet **Bot** :
> Assurez-vous d'activer l'option **Message Content Intent** (Privileged Gateway Intents) afin que le bot puisse lire le contenu de la commande `!planning`.

### Étape 4 : Lancer la suite de tests

Vérifiez que tous les modules fonctionnent correctement :

```bash
python test_system.py
```
*Tous les tests (client Monoprix, logique tupperware, déduplication, Embeds) doivent afficher `OK`.*

### Étape 5 : Lancer le bot Discord

```bash
python bot.py
```

Le bot se connecte et affiche :
```text
[INFO] repas_bot: Bot connecté en tant que RepasBot (ID: ...)
[INFO] repas_bot: Écoute restreinte au salon ID: 1556984067080327168
[INFO] repas_bot: Démarrage du bot avec le magasin Monoprix ID: 00452
```

---

## 💬 5. Utilisation dans Discord

Le bot écoute **exclusivement** dans le salon configuré (`DISCORD_CHANNEL_ID`).

### 1. Commande principale : `!planning`
Postez ce que vous ramenez de votre week-end :

```text
!planning Ce week-end j'ai : 1 part de quiche, du poulet rôti, des courgettes
```

**Le bot répond avec deux messages structurés (Embeds Discord) :**

1. **🍱 Planning des Repas :**
   * **Lundi soir (Dîner) :** Plat maison du week-end : *1 part de quiche* (réchauffage immédiat)
   * **Mardi midi (Déjeuner) :** Reste du week-end : *du poulet rôti* (prêt à emporter, 0 min de cuisine)
   * **Mardi soir (Dîner) :** *Poêlée de poulet au curry doux, courgettes & riz basmati* (Cuisiné en double portion)
   * **Mercredi midi (Déjeuner) :** *Tupperware : Poêlée de poulet au curry doux...* (Reste de la veille)
   * **Mercredi soir (Dîner) :** *Gratin de Penne à la Mozzarella di Bufala et coulis Mutti* (Double portion)
   * **Jeudi midi (Déjeuner) :** *Tupperware : Gratin de Penne...*
   * **Jeudi soir (Dîner) :** *Poêlée mexicaine de poulet aux poivrons tricolores & riz* (Double portion)
   * **Vendredi midi (Déjeuner) :** *Tupperware : Poêlée mexicaine...*

2. **🛒 Liste de Courses Optimisée Monoprix :**
   * Classée par **rayon** (*Boucherie*, *Poissonnerie*, *Fruits & Légumes*, *Crèmerie*, *Épicerie salée*).
   * **Déduplication automatique :** Les courgettes rapportées de chez les parents ne sont **pas** sur la liste de courses !
   * **Mise en avant des promos :**
     * `🍗 [Boucherie] Filets de poulet fermier d'Auvergne (500g) — [🏷️ -30% (7.45 €)]`
     * `🥦 [Fruits & Légumes] Poivrons tricolores filet (500g) — [🏷️ -30% (2.80 €)]`
     * `🧀 [Crèmerie] Mozzarella di Bufala Campana AOP (125g) — [🏷️ 30% cagnotté (2.89 €)]`
     * `🍝 [Épicerie salée] Penne Rigate Barilla (1kg) — [🏷️ 2e à -50% (2.30 €)]`
   * Bilan budget estimé et total d'économies.

### 2. Commande `!promos`
Permet de visualiser les promotions actives de la semaine chez Monoprix :
```text
!promos
```

### 3. Commande `!aide`
Affiche le manuel d'utilisation rapide et les rappels des règles logistiques :
```text
!aide
```

---

## ⚙️ 6. Résilience et Sécurité

* **Gestion réseau httpx :** `monoprix_client.py` intègre un catalogue de référence réaliste Monoprix si le réseau coupe, si l'API est indisponible ou bloquée par un pare-feu applicatif. Le bot **ne plante jamais**.
* **Asynchronisme :** Les requêtes HTTP et le calcul de planning sont exécutés de façon non bloquante (`asyncio.run_in_executor`) pour préserver la réactivité du bot Discord.
* **Sécurité des tokens :** Le token Discord et les identifiants sensibles sont isolés dans `.env` et strictement exclus par `.gitignore`.

