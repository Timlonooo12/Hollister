# 👕 Stockwatch — alerte Telegram dès qu'une taille revient en stock

Bot Telegram qui surveille une fiche produit **en continu (1 vérification par
seconde)** et prévient **à la seconde** où une taille — XS et S par défaut —
redevient disponible.

Conçu pour la fiche Hollister
[Icon Henley](https://www.hollisterco.com/shop/eu-fr/p/icon-henley-63586319-2),
mais l'URL et les tailles se changent depuis Telegram (`/produit`, `/tailles`) :
le lecteur de stock ne dépend d'aucun sélecteur codé en dur.

```
🚨🚨 DISPO — TAILLE XS 🚨🚨

👕 Hollister — Icon Henley
✅ En stock : XS
➕ Autres tailles dispo : M, L
🕒 Détecté à 14:07:41 (le 14/09/2026)
⚡ Vérification toutes les 1 s

Commander maintenant
[ 🛒 Ouvrir le produit ]
```

---

## 1. Démarrage rapide — une seule commande

**macOS / Linux** — copie-colle ce bloc dans ton terminal :

```bash
git clone https://github.com/Timlonooo12/Hollister.git stockwatch && cd stockwatch && bash setup.sh
```

**Windows** — dans PowerShell :

```powershell
git clone https://github.com/Timlonooo12/Hollister.git stockwatch; cd stockwatch; powershell -ExecutionPolicy Bypass -File setup.ps1
```

Le script installe les dépendances, **te demande ton token** (colle-le, Entrée),
vérifie que le stock est lisible et démarre le bot. Il ne reste qu'à envoyer
**`/start`** à ton bot sur Telegram pour recevoir les alertes.

Le token vient de [@BotFather](https://t.me/BotFather) : `/newbot`, tu choisis un
nom, il te répond `123456789:AAE-xxxxxxxxxxxxxxxxxxxx`.

### Prérequis

- **Python 3.11 ou plus récent.** Sur macOS, le Python fourni par Apple est en
  3.9 : installe le `.pkg` depuis
  [python.org/downloads/macos](https://www.python.org/downloads/macos/)
  (double-clic, Suivant, terminé). Sur Debian/Ubuntu :
  `sudo apt install -y python3 python3-venv python3-pip`.
- **`git`** uniquement pour la commande ci-dessus. Sur un Mac neuf il n'est pas
  installé — soit tu acceptes le dialogue « outils de ligne de commande », soit
  tu passes par le ZIP :

### Sans git (ou dépôt privé) : par le ZIP

Sur la page du dépôt, bouton vert **Code** → **Download ZIP**, puis :

```bash
cd ~/Downloads && unzip -oq Hollister-main.zip && cd Hollister-main && bash setup.sh
```

C'est le chemin le plus court si le dépôt est privé : pas de token GitHub à
configurer.

<details>
<summary>Installation manuelle (si tu préfères)</summary>

```bash
python3 -m venv .venv && source .venv/bin/activate     # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # puis renseigne STOCKWATCH_BOT_TOKEN
python -m stockwatch
```

</details>

Relancer plus tard : `bash setup.sh` (il garde le token déjà enregistré) ou
directement `.venv/bin/python -m stockwatch`.

> Tant que personne n'a fait `/start` et que `STOCKWATCH_CHAT_IDS` est vide, le
> bot surveille mais n'a personne à prévenir — il le signale dans les logs.

Python **3.11 ou plus récent** est requis.

---

## 2. Vérifier la lecture du stock (à faire une fois)

Le stock est lu depuis la page du produit. Avant de compter dessus, demande au
bot ce qu'il voit :

```bash
python -m stockwatch diagnose
```

```
Source      : https://www.hollisterco.com/shop/eu-fr/p/icon-henley-63586319-2
HTTP        : 200  en 412 ms  (1486203 caractères)
Produit id  : 63586319
Blocs JSON  : 7
Stratégie   : json-focused

Tailles détectées :
  ❌ épuisée   XS
  ❌ épuisée   S
  ✅ DISPO     M
  ✅ DISPO     L
```

- **Les tailles apparaissent** → tout est bon, lance `python -m stockwatch`.
- **« Aucune taille détectée »** → la sortie dit quoi faire. Deux cas :
  - *page de blocage* (Akamai/captcha) : voir §5 ;
  - *structure changée* : envoie-moi la sortie de
    `python -m stockwatch diagnose --save page.html`, l'analyse se corrige dans
    `stockwatch/parsing.py`.

L'option `--url` analyse une autre page, `--file` relit un fichier déjà
téléchargé (pratique pour tester sans requêter le site).

---

## 3. Commandes Telegram

| Commande | Effet |
|---|---|
| `/start` | S'abonner aux alertes dans ce chat |
| `/stop` | Se désabonner |
| `/status` | Produit, tailles, stock actuel, latence, statistiques, erreurs |
| `/check` | Vérification immédiate, réponse en direct |
| `/tailles XS,S` | Changer les tailles surveillées (`xs`, `Small`, `X-Small`… acceptés) |
| `/produit <url>` | Changer le produit surveillé |
| `/variante <id>` | Choisir le coloris quand la page en contient plusieurs |
| `/intervalle 1` | Délai entre deux vérifications, en secondes |
| `/pause` / `/reprendre` | Suspendre ou relancer la surveillance |
| `/id` | Afficher l'identifiant du chat (pour `STOCKWATCH_CHAT_IDS`) |
| `/aide` | Rappel des commandes |

Renseigne `STOCKWATCH_OWNER_ID` pour que **toi seul** puisses changer le
produit, les tailles et l'intervalle : les autres abonnés ne peuvent que
recevoir les alertes et consulter l'état.

Les réglages faits depuis Telegram sont persistés : ils survivent à un
redémarrage.

---

## 4. Comment l'alerte « à la seconde » fonctionne

- une requête HTTP par seconde, sur une **connexion maintenue ouverte**
  (pas de poignée de main TLS à chaque tour) : un contrôle coûte ~100–400 ms ;
- l'alerte Telegram part **avant** l'écriture d'état, dans une tâche séparée :
  la boucle ne perd pas un tour à attendre Telegram ;
- une alerte par **transition** épuisé → disponible. Tant que la taille reste
  dispo, plus rien (sauf `STOCKWATCH_REPEAT_ALERT_MINUTES`) ; si elle repart et
  revient, tu es prévenu à nouveau ;
- l'état est enregistré dans `stockwatch-state.json`, donc un redémarrage ne
  rejoue pas une alerte déjà envoyée.

**Le délai réel** entre la remise en stock et ton téléphone = intervalle de
vérification (≤ 1 s) + temps de réponse du site + latence Telegram, soit en
pratique **1 à 3 secondes** — auxquelles s'ajoute le cache du CDN du marchand,
sur lequel aucun bot n'a de prise. Descendre sous 1 s n'améliore donc rien et
augmente le risque de blocage ; le plancher est fixé à 0,2 s.

Si la page devient illisible (blocage, panne, refonte), le bot **ne conclut
jamais « épuisé »** : il compte les échecs, ralentit progressivement
(backoff exponentiel jusqu'à `STOCKWATCH_MAX_BACKOFF`) et t'envoie un message
« surveillance dégradée », puis « surveillance rétablie » quand ça repart.

---

## 5. Si le site bloque les requêtes

Les grandes enseignes (Hollister = plateforme Abercrombie & Fitch) sont
derrière un pare-feu applicatif. Symptôme : `HTTP 403`, « Access Denied » ou
« Reference # » dans `diagnose`. Dans l'ordre :

1. **Ralentis** : `STOCKWATCH_POLL_INTERVAL=2` (ou 5). Une alerte 4 s plus tard
   vaut mieux qu'un bot banni.
2. **Copie un cookie de navigateur** : ouvre la fiche produit dans Chrome →
   F12 → onglet *Network* → clic sur la requête du document → *Request
   Headers* → copie la valeur de `Cookie` dans `STOCKWATCH_COOKIE`.
3. **Change d'IP** : `STOCKWATCH_PROXY_URL=http://user:pass@host:port`
   (un proxy résidentiel passe là où un IP de datacenter est refusée).
4. Ajuste `STOCKWATCH_USER_AGENT` pour coller exactement à ton navigateur.

---

## 5 bis. Lire le stock de la bonne fiche, et du bon coloris

Deux messages possibles quand la lecture ne donne rien — ils disent tous les
deux quoi faire plutôt que d'inventer une disponibilité.

### « La page contient plusieurs produits »

Une fiche produit embarque aussi ses **autres coloris** et ses recommandations,
chacun avec ses propres tailles. Fusionner leurs stocks ferait sonner l'alerte
pour le mauvais coloris : le bot refuse, et `diagnose` affiche le stock lu pour
chaque identifiant :

```
   Stock lu pour chacun — repère celui qui correspond à ce que montre le site :
     • produit 63503980
         dispo    : aucune
         épuisées : XS, S, M, L
     • produit 63503981
         dispo    : M, L
         épuisées : XS, S
```

Compare avec la page, puis désigne le bon : `/variante 63503980` sur Telegram
(ou `STOCKWATCH_PRODUCT_ID=63503980` dans `.env`).

### « Les tailles sont listées sans état de stock »

Message rencontré sur **hollisterco.com** : la page HTML contient bien les
tailles, mais leur disponibilité est chargée ensuite en JavaScript. Le HTML seul
ne peut donc pas y répondre — et le bot **refuse de deviner** : supposer
« bouton affiché = disponible » ferait sonner l'alerte sur un produit épuisé.
Il signale « surveillance dégradée » au lieu d'inventer une disponibilité.

La solution est de lui donner l'appel réseau qui porte réellement le stock :

1. Ouvre la fiche produit dans Chrome ou Safari.
2. **F12** (ou clic droit → Inspecter) → onglet **Réseau** → filtre **Fetch/XHR**.
3. Recharge la page, puis clique une taille.
4. Cherche une réponse **JSON** qui contient `XS`, `inStock`, `inventory` ou
   `availability` (l'onglet *Aperçu/Preview* les montre).
5. Clic droit sur cette requête → **Copier** → **Copier l'adresse du lien**.
6. Colle-la dans `.env` :

```bash
STOCKWATCH_API_URL=https://www.hollisterco.com/api/…
```

7. Vérifie : `python -m stockwatch diagnose` doit maintenant lister les tailles
   avec ✅/❌, puis relance le bot.

Si l'endpoint exige des en-têtes particuliers, ajoute-les dans
`STOCKWATCH_EXTRA_HEADERS` (JSON) et le cookie dans `STOCKWATCH_COOKIE`.

En cas de doute, `python -m stockwatch diagnose` affiche une section
**« Pistes »** qui montre où les tailles apparaissent dans le JSON de la page :
ces quelques lignes suffisent à écrire le lecteur exact.

---

## 6. Configuration

Tout se règle par variables d'environnement (ou `.env`). `.env.example` liste
les valeurs commentées ; les principales :

| Variable | Défaut | Rôle |
|---|---|---|
| `STOCKWATCH_BOT_TOKEN` | — | **Obligatoire.** Token @BotFather |
| `STOCKWATCH_CHAT_IDS` | vide | Chats prévenus même sans `/start` |
| `STOCKWATCH_OWNER_ID` | vide | Seul autorisé à modifier la surveillance |
| `STOCKWATCH_PRODUCT_URL` | Icon Henley | Page surveillée |
| `STOCKWATCH_SIZES` | `XS,S` | Tailles surveillées |
| `STOCKWATCH_PRODUCT_ID` | vide | Identifiant du coloris, si la page en contient plusieurs |
| `STOCKWATCH_POLL_INTERVAL` | `1.0` | Secondes entre deux vérifications |
| `STOCKWATCH_ALERT_ON_FIRST_SEEN` | `true` | Alerter si déjà dispo au démarrage |
| `STOCKWATCH_REPEAT_ALERT_MINUTES` | `0` | Rappel tant que c'est dispo (0 = aucun) |
| `STOCKWATCH_COOKIE` / `STOCKWATCH_PROXY_URL` | vide | Contournement d'un blocage |
| `STOCKWATCH_CONDITIONAL_REQUESTS` | `true` | ETag : ne retélécharge la page que si elle a changé |
| `STOCKWATCH_DAILY_BUDGET_MB` | `0` | Plafond de données par jour (0 = illimité) |
| `STOCKWATCH_FAILURE_GRACE` | `3` | Échecs tolérés à cadence normale avant de ralentir |
| `STOCKWATCH_STATE_FILE` | `stockwatch-state.json` | Mémoire du stock et des abonnés |

Le token, le cookie et le proxy sont chargés dans des `SecretStr` : ils
n'apparaissent ni dans les logs ni dans un `repr()`.

---

## 6 bis. Mettre à jour

```bash
cd ~/Downloads/Hollister-main && bash update.sh
```

Le script récupère la dernière version, remplace le code et réinstalle les
dépendances. Ton `.env` et la mémoire du stock (`stockwatch-state.json`) ne sont
pas dans l'archive : ils restent en place. Dépôt privé → crée un token
(github.com/settings/tokens, portée « repo ») puis
`GITHUB_TOKEN=ton_token bash update.sh`.

---

## 7. Déploiement 24/7

Le bot **n'écoute sur aucun port** : il ne fait que des requêtes sortantes. Il
cohabite donc sans réglage avec n'importe quel autre service — il lui faut
seulement son propre dossier, son propre environnement virtuel et son propre
token Telegram.

**systemd** (VPS, Raspberry Pi) — le fichier est fourni :

```bash
# Python 3.11+ requis : Debian 12 l'a, Ubuntu 22.04 non (3.10).
python3 --version

sudo useradd --system --home-dir /opt/stockwatch stockwatch
sudo mkdir -p /opt/stockwatch && sudo cp -r . /opt/stockwatch && cd /opt/stockwatch
sudo rm -rf .venv                   # un venv créé ailleurs n'est pas portable
sudo python3 -m venv .venv && sudo .venv/bin/pip install -q -r requirements.txt
sudo chown -R stockwatch:stockwatch /opt/stockwatch && sudo chmod 600 /opt/stockwatch/.env

sudo -u stockwatch .venv/bin/python -m stockwatch diagnose   # le VPS voit-il le stock ?

sudo cp deploy/stockwatch.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now stockwatch
journalctl -u stockwatch -f
```

### Quand le site refuse une partie des requêtes

Constaté depuis un VPS OVH : environ une requête sur trois revient en `403`,
les autres passent normalement. Le bot ne ralentit donc pas au premier refus —
il retente à la cadence normale pendant `STOCKWATCH_FAILURE_GRACE` échecs
(3 par défaut) avant de commencer à espacer. Ralentir dès le premier 403
ferait rater exactement le réassort qu'on surveille. `/status` affiche la part
de requêtes refusées.

### Quand l'IP du serveur est bloquée en permanence

Constaté sur un VPS OVH : `HTTP 403`, corps de 149 octets, « Bad Request /
Reference ID » — le pare-feu applicatif du marchand refuse les adresses de
datacenter alors que la même requête passe depuis une connexion résidentielle.
Ce n'est pas un problème de configuration, et aucun réglage d'en-têtes ne le
règle de façon fiable. Par ordre de coût :

1. **Faire tourner le bot depuis chez toi.** Un Mac suffit :
   `deploy/com.stockwatch.bot.plist` est un LaunchAgent prêt à l'emploi
   (redémarrage automatique inclus) ; pense à empêcher la veille.
2. **Passer par un proxy résidentiel** : `STOCKWATCH_PROXY_URL=http://user:pass@hôte:port`.
   Quelques euros par mois, et le VPS redevient utilisable.
3. **Coller un cookie de navigateur** dans `STOCKWATCH_COOKIE` : parfois
   suffisant, mais le cookie est lié à l'empreinte du navigateur et à son IP,
   donc il expire vite. Dépannage, pas solution.

### Consommation de données

Le bot ne retélécharge pas la page tant qu'elle n'a pas changé : il renvoie
l'`ETag` de la dernière réponse et le serveur répond « 304 Not Modified ».
Brotli est négocié quand le serveur le propose. Mesuré sur une page de 750 Ko
(comptage en-têtes compris, dans les deux sens) :

| | par vérification | à 1 s | à 30 s |
|---|---|---|---|
| page entière à chaque fois | 353 Ko | 29 Go/jour | 1 Go/jour |
| requête conditionnelle *(défaut)* | **878 o** | **72 Mo/jour** | 2,4 Mo/jour |

Soit **411 fois moins de données**, sans rien perdre en réactivité : un 304
coûte un aller-retour d'en-têtes, et dès que la page bouge le corps complet
arrive normalement. `python -m stockwatch diagnose` refait la requête une
seconde fois et affiche ce que le site répond vraiment — c'est la seule façon
de savoir si un marchand donné honore les requêtes conditionnelles.

Si ce n'est pas le cas chez lui, il reste l'intervalle (`STOCKWATCH_POLL_INTERVAL`)
et, derrière un proxy facturé au volume, le garde-fou :

```bash
STOCKWATCH_DAILY_BUDGET_MB=500      # au-delà, le bot ralentit tout seul
STOCKWATCH_THROTTLED_INTERVAL=300   # et prévient une fois sur Telegram
```

`/status` affiche en permanence le volume du jour, la projection mensuelle et
le pourcentage de réponses « inchangé ».

### Coût CPU d'une vérification par seconde

Analyser une fiche de 670 Ko coûte ~0,2 s de CPU, soit **~20 % d'un cœur** à
une vérification par seconde. Les réponses « 304 » n'étant pas ré-analysées,
ce coût ne se paie en pratique que lorsque la page change réellement. Sur un
petit VPS partagé avec un autre service, `STOCKWATCH_POLL_INTERVAL=3` le divise
par trois sans perte notable — le CDN du marchand ne rafraîchit pas sa réponse
à la milliseconde.

**Docker** :

```bash
cp .env.example .env   # renseigne le token
docker compose up -d --build
docker compose logs -f
```

L'état est monté dans un volume : le conteneur peut redémarrer sans réémettre
d'alerte déjà envoyée.

---

## 8. Architecture

```
stockwatch/
├── __main__.py   CLI : `run` (défaut) et `diagnose`
├── app.py        câblage : bot Telegram + boucle de surveillance
├── bot.py        commandes Telegram
├── monitor.py    boucle, détection des transitions, backoff, /status
├── parsing.py    lecture du stock (JSON embarqué, ld+json, repli HTML)
├── client.py     client HTTP (connexion persistante, empreinte navigateur)
├── notifier.py   envoi Telegram (retries, désabonnement des chats bloqués)
├── config.py     configuration
└── state.py      persistance (abonnés, stock connu, réglages)
```

`parsing.py` ne cible **aucun** sélecteur CSS ni aucun chemin d'API : il
collecte tous les documents JSON de la page (état embarqué type
`__INITIAL_STATE__` / `__NEXT_DATA__`, données structurées `ld+json`, réponse
d'API brute) et retient les objets qui portent **à la fois** une taille
(`XS`, `X-Small`, `Taille S`…) et un signal de stock (`inStock`, `soldOut`,
`availability`, `quantity`…). Les signaux d'un même objet doivent concorder
(`inStock: true` + `quantity: 0` ⇒ épuisé). Quand la page identifie le produit surveillé, chaque lecture est rattachée au
produit **le plus proche** dans la structure, jamais à un ancêtre : sur un cache
Apollo, le produit affiché est nommé à la racine et « tout ce qui descend de
lui » engloberait les autres coloris. Faute d'un rattachement net, le parseur
refuse de conclure plutôt que de fusionner des stocks qui ne sont pas les
siens. Quand les tailles et les stocks vivent dans deux structures distinctes, ils sont
recoupés par identifiant de variante (sku). Si aucun JSON n'est exploitable, les
boutons de taille du HTML sont lus en dernier recours — et **uniquement** si la
page marque réellement l'indisponibilité quelque part (attribut `disabled`,
classe « sold out »…) ; sinon la lecture est déclarée impossible plutôt que
devinée.

C'est ce qui permet au bot de survivre à une refonte du site sans changer de
code, et `diagnose` sert à le vérifier en une commande.

Tests :

```bash
pip install -r requirements-dev.txt
python -m pytest -q        # 97 tests
python -m ruff check .
```

---

## 9. Honnêteté technique

- **Le HTML de hollisterco.com ne porte pas le stock** (constaté en conditions
  réelles) : les tailles y sont listées, leur disponibilité est chargée ensuite
  en JavaScript. Tant que `STOCKWATCH_API_URL` ne pointe pas vers l'appel qui
  porte le stock (voir §5 bis), le bot dira « surveillance dégradée » — c'est
  volontaire : il ne devine pas. Une première version devinait, et a annoncé
  « XS, S disponibles » sur un produit intégralement épuisé ; c'est corrigé et
  couvert par un test de non-régression.
- **Le lecteur n'a pas été validé contre la vraie page depuis l'environnement de
  développement** : `hollisterco.com` y était bloqué (sortie réseau filtrée).
  Les 97 tests couvrent chaque format de réponse géré ; `diagnose` sert à
  confirmer le format réellement servi et fournit les « Pistes » nécessaires
  pour écrire le lecteur manquant.
- **Le stock affiché n'est pas une réservation.** Le bot te prévient, il
  n'achète rien : sur une pièce très demandée, la taille peut repartir entre
  l'alerte et ton passage en caisse.
- **Un seul produit surveillé à la fois** (`/produit` bascule de l'un à
  l'autre). Le suivi simultané de plusieurs fiches n'est pas implémenté.
- Respecte les conditions d'utilisation du site : garde une cadence raisonnable
  et n'utilise ce bot que pour ton usage personnel.
