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
  - *page de blocage* (Akamai/captcha) : voir §7 ;
  - *structure changée* : envoie-moi la sortie de
    `python -m stockwatch diagnose --save page.html`, l'analyse se corrige dans
    `stockwatch/parsing.py`.

L'option `--url` analyse une autre page, `--file` relit un fichier déjà
téléchargé (pratique pour tester sans requêter le site).

---

## 3. Le bot au quotidien

Tout se pilote depuis **/menu** : un seul message, mis à jour sur place, avec
des boutons. Pas de commandes à retenir, pas de fil qui se remplit.

```
🤖 Surveillance de stock

👕 Icon Henley — Blanc
🎯 Tailles suivies : XS, S
📦 ❌ XS  ❌ S
🕒 Dernier contrôle : 02:44:11 (23 ms)
⚡ Une vérification toutes les 1 s
🌙 Veille : 20h00 → 07h00

[🔎 Vérifier maintenant] [🔄 Actualiser]
[📏 Tailles]             [🎨 Coloris]
[⚡ Cadence]             [🌙 Veille]
[⏸ Mettre en pause]     [📊 Détails]
[🛒 Ouvrir la fiche]     [❓ Aide]
```

- **📏 Tailles** — coche et décoche XXS à XXL d'un doigt.
- **🎨 Coloris** — un bouton par coloris détecté dans la page, avec son nom
  (« Blanc », « Vert sauge »), pas un identifiant à huit chiffres.
- **⚡ Cadence** — de 1 seconde à 5 minutes.
- **🌙 Veille** — désactivée, 20h→7h, 22h→8h ou 0h→8h.

## 4. Veille nocturne

```bash
STOCKWATCH_QUIET_START=20
STOCKWATCH_QUIET_END=7
STOCKWATCH_TIMEZONE=Europe/Paris
```

Pendant la plage, le bot **ne fait plus aucune requête** : pas d'alerte, pas de
trafic, pas de CPU. Il s'endort et se réveille tout seul, en te prévenant une
fois à chaque fois. La plage peut traverser minuit (20 → 7), et le fuseau est
le tien : un serveur tourne en UTC, où « 20 h » n'est pas 20 h chez toi.

`/veille 20 7` et `/veille off` font la même chose au clavier. Le bouton
« 🔎 Vérifier maintenant » reste actif pendant la veille, pour un contrôle
ponctuel.

## 5. Commandes Telegram

| Commande | Effet |
|---|---|
| `/menu` | Le tableau de bord à boutons |
| `/start` | S'abonner aux alertes dans ce chat |
| `/stop` | Se désabonner |
| `/status` | Produit, tailles, stock actuel, latence, statistiques, erreurs |
| `/check` | Vérification immédiate, réponse en direct |
| `/tailles XS,S` | Changer les tailles surveillées (`xs`, `Small`, `X-Small`… acceptés) |
| `/produit <url>` | Changer le produit surveillé |
| `/couleur Blanc` | Choisir le coloris par son nom — le plus sûr |
| `/variante <id>` | Choisir le coloris par son identifiant interne |
| `/intervalle 1` | Délai entre deux vérifications, en secondes |
| `/veille 20 7` | Ne rien vérifier entre 20h et 7h (`/veille off` pour arrêter) |
| `/pause` / `/reprendre` | Suspendre ou relancer la surveillance |
| `/id` | Afficher l'identifiant du chat (pour `STOCKWATCH_CHAT_IDS`) |
| `/aide` | Rappel des commandes |

Renseigne `STOCKWATCH_OWNER_ID` pour que **toi seul** puisses changer le
produit, les tailles et l'intervalle : les autres abonnés ne peuvent que
recevoir les alertes et consulter l'état.

Les réglages faits depuis Telegram sont persistés : ils survivent à un
redémarrage.

---

## 6. Comment l'alerte « à la seconde » fonctionne

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

## 7. Si le site bloque les requêtes

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

## 8. Lire le stock de la bonne fiche, et du bon coloris

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

`diagnose` donne le nom de chaque coloris quand la page le porte :

```
     • Blanc (produit 63492467)
         dispo    : aucune
         épuisées : XXS, XS, S, M, L, XL, XXL
     • Vert sauge (produit 63503980)
         dispo    : XXL
         épuisées : XXS, XS, S, M, L, XL
```

Désigne alors le tien par son nom : `/couleur Blanc` sur Telegram (ou
`STOCKWATCH_PRODUCT_COLOR=Blanc` dans `.env`). Le français fonctionne même quand
la page nomme ses données en anglais — « blanc » retrouve `white`, « bleu
clair » retrouve `light blue` — parce que le libellé affiché est traduit alors
que la donnée ne l'est pas. L'identifiant numérique reste
possible via `/variante`, mais un nom de coloris se vérifie d'un coup d'œil sur
la fiche, pas un nombre à huit chiffres.

### « Les tailles sont listées sans état de stock »

Message rencontré sur **hollisterco.com** : la page HTML contient bien les
tailles, mais leur disponibilité est chargée ensuite en JavaScript. Le HTML seul
ne peut donc pas y répondre — et le bot **refuse de deviner** : supposer
« bouton affiché = disponible » ferait sonner l'alerte sur un produit épuisé.
Il signale « surveillance dégradée » au lieu d'inventer une disponibilité.

La solution est de lui donner l'appel réseau qui porte réellement le stock :

### Le renouveler tout seul (recommandé)

Un cookie de contrôle expire en quelques heures : le recopier à la main à
chaque fois n'est pas tenable. Installe un navigateur headless, une fois :

```bash
sudo /opt/stockwatch/.venv/bin/pip install playwright
sudo /opt/stockwatch/.venv/bin/playwright install-deps chromium
sudo PLAYWRIGHT_BROWSERS_PATH=/opt/stockwatch/browsers \
     /opt/stockwatch/.venv/bin/playwright install chromium
sudo chown -R stockwatch:stockwatch /opt/stockwatch
```

Le chemin explicite compte : installé avec `sudo` sans lui, Chromium atterrit
dans le dossier personnel de `root`, tandis que le service le cherche dans
celui de son propre utilisateur. L'unité systemd fournie pointe déjà sur
`/opt/stockwatch/browsers`, donc recopie-la après mise à jour :

```bash
sudo cp /opt/stockwatch/deploy/stockwatch.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl restart stockwatch
```

Le bot ouvre alors la page dans ce navigateur dès qu'une lecture échoue, et à
titre préventif toutes les trois heures. Un vrai navigateur obtient un cookie
valide à chaque visite — c'est l'objet même du contrôle — et les milliers de
vérifications qui suivent restent de simples requêtes HTTP. Le cookie obtenu
est gardé dans le fichier d'état, jamais réécrit dans ton `.env`, et réutilisé
au redémarrage.

Pour vérifier l'installation, ou forcer un renouvellement :

```bash
python -m stockwatch cookie --auto     # en ligne de commande
/cookie                                 # ou depuis Telegram (bouton 🍪)
```

`/status` indique l'âge du cookie et le nombre de renouvellements.

### Se passer de cookie : imiter un vrai navigateur

Avant de bricoler des cookies, il vaut la peine d'essayer ceci :

```bash
STOCKWATCH_IMPERSONATE=safari17_0
```

Les filtres anti-bot comparent la **signature TLS** du client à celles des
navigateurs connus. httpx — comme toute bibliothèque Python — s'y distingue au
premier coup d'œil, quels que soient les en-têtes envoyés ; c'est ce qui
explique qu'un site serve une page amputée à un script et la page complète au
même en-tête depuis Safari. `curl_cffi` reproduit l'empreinte d'un vrai
navigateur, sans en lancer aucun.

Valeurs utiles : `safari17_0`, `safari18_0`, `chrome124`, `chrome131`,
`firefox133`. Vérifie avec `diagnose` que la page revient complète ; si oui, le
cookie devient inutile et la question du renouvellement disparaît.

### Savoir qu'il a expiré

Un site ne dit jamais « ton cookie a expiré » : il refuse la requête, ou sert
une version amputée de la page. Le bot reconnaît les deux et envoie une alerte
explicite, avec un bouton de renouvellement :

```
🍪 Cookie expiré
Le site ne reconnaît plus la session (obtenue il y a 6 h) : il refuse la
requête ou sert une page amputée, et je ne peux plus lire le stock.

Aucune alerte ne partira tant qu'il n'est pas renouvelé.
```

Elle n'arrive qu'après plusieurs échecs consécutifs — un refus isolé est
courant et se rattrape tout seul — et se réarme dès qu'une lecture réussit.

### Quand le serveur ne peut pas en obtenir

Certains sites refusent toute nouvelle session venant d'une adresse de
datacenter — le navigateur reçoit un `403` ou un `418` avant même la page,
quel que soit son déguisement. Le cookie doit alors être créé ailleurs. Deux
montages, au choix :

**Une machine de confiance le dépose.** `deploy/mac-cookie-courier.sh` obtient
un cookie depuis ton ordinateur et le copie sur le serveur ; le bot relit le
fichier dès qu'il change, sans redémarrage.

Préparation du serveur, une seule fois — un dossier de dépôt que ton compte SSH
peut écrire et que le service peut lire, pour qu'aucun `sudo` distant ne soit
nécessaire ensuite :

```bash
sudo install -d -o ubuntu -g stockwatch -m 2770 /opt/stockwatch/incoming
echo STOCKWATCH_COOKIE_FILE=/opt/stockwatch/incoming/cookie.txt | sudo tee -a /opt/stockwatch/.env
sudo systemctl restart stockwatch
```

Sur ton ordinateur, Playwright une fois, puis le dépôt :

```bash
.venv/bin/pip install playwright && .venv/bin/playwright install chromium
bash deploy/mac-cookie-courier.sh ubuntu@mon-serveur
```

Avec le LaunchAgent fourni (`deploy/com.stockwatch.courier.plist`), c'est
automatique toutes les deux heures — et l'ordinateur n'a pas besoin de rester
allumé en permanence, chaque passage prolongeant la validité de plusieurs
heures.

**Un proxy résidentiel, pour la seule visite du navigateur.**

```bash
STOCKWATCH_BROWSER_PROXY_URL=http://utilisateur:motdepasse@hôte:port
```

Seule cette visite y passe : quelques centaines de kilo-octets toutes les trois
heures, là où faire transiter la surveillance entière coûterait des gigaoctets.
Le serveur redevient alors totalement autonome.

### Le fournir à la main

Le plus simple : clic droit sur la ligne du **document** dans l'onglet Réseau →
**Copier en tant que cURL**, puis sur le serveur :

```bash
python -m stockwatch cookie      # colle, puis Ctrl-D
```

La commande extrait le cookie du collage, l'écrit dans `.env` en `600` et ne
l'affiche jamais en clair — ni les `;` à échapper, ni les guillemets à gérer.

Pour trouver cette ligne :

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

## 9. Configuration

Tout se règle par variables d'environnement (ou `.env`). `.env.example` liste
les valeurs commentées ; les principales :

| Variable | Défaut | Rôle |
|---|---|---|
| `STOCKWATCH_BOT_TOKEN` | — | **Obligatoire.** Token @BotFather |
| `STOCKWATCH_CHAT_IDS` | vide | Chats prévenus même sans `/start` |
| `STOCKWATCH_OWNER_ID` | vide | Seul autorisé à modifier la surveillance |
| `STOCKWATCH_PRODUCT_URL` | Icon Henley | Page surveillée |
| `STOCKWATCH_SIZES` | `XS,S` | Tailles surveillées |
| `STOCKWATCH_PRODUCT_COLOR` | vide | Nom du coloris suivi (« Blanc ») — prioritaire |
| `STOCKWATCH_PRODUCT_ID` | vide | Identifiant interne du coloris, à défaut de nom |
| `STOCKWATCH_POLL_INTERVAL` | `1.0` | Secondes entre deux vérifications |
| `STOCKWATCH_ALERT_ON_FIRST_SEEN` | `true` | Alerter si déjà dispo au démarrage |
| `STOCKWATCH_REPEAT_ALERT_MINUTES` | `0` | Rappel tant que c'est dispo (0 = aucun) |
| `STOCKWATCH_COOKIE` / `STOCKWATCH_PROXY_URL` | vide | Contournement d'un blocage |
| `STOCKWATCH_IMPERSONATE` | vide | Imiter la signature TLS d'un navigateur (`safari17_0`…) |
| `STOCKWATCH_AUTO_COOKIE` | `true` | Renouveler le cookie seul (navigateur headless) |
| `STOCKWATCH_COOKIE_REFRESH_MINUTES` | `180` | Âge au-delà duquel on en cherche un neuf |
| `STOCKWATCH_CONDITIONAL_REQUESTS` | `true` | ETag : ne retélécharge la page que si elle a changé |
| `STOCKWATCH_DAILY_BUDGET_MB` | `0` | Plafond de données par jour (0 = illimité) |
| `STOCKWATCH_FAILURE_GRACE` | `3` | Échecs tolérés à cadence normale avant de ralentir |
| `STOCKWATCH_QUIET_START` / `_END` | vide | Plage sans aucune vérification (ex. 20 et 7) |
| `STOCKWATCH_TIMEZONE` | `Europe/Paris` | Fuseau des horaires de veille |
| `STOCKWATCH_STATE_FILE` | `stockwatch-state.json` | Mémoire du stock et des abonnés |

Le token, le cookie et le proxy sont chargés dans des `SecretStr` : ils
n'apparaissent ni dans les logs ni dans un `repr()`.

---

## 10. Mettre à jour

```bash
cd ~/Downloads/Hollister-main && bash update.sh
```

Le script récupère la dernière version, remplace le code et réinstalle les
dépendances. Ton `.env` et la mémoire du stock (`stockwatch-state.json`) ne sont
pas dans l'archive : ils restent en place. Dépôt privé → crée un token
(github.com/settings/tokens, portée « repo ») puis
`GITHUB_TOKEN=ton_token bash update.sh`.

---

## 11. Déploiement 24/7

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

### Quand le site sert une page incomplète

Hollister renvoie par moments une variante allégée de la fiche — 368 Ko au lieu
de 653 — qui ne contient pas les données de stock. Ce n'est ni un blocage ni un
changement de structure, et la requête suivante ramène la version complète. Le
bot retente donc immédiatement, une seule fois et seulement après une lecture
réussie, plutôt que de perdre le tour. `/status` compte ces pages incomplètes.

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

### Trouver une source plus légère que la page

```bash
python -m stockwatch probe
```

La page pèse ~350 Ko compressés ; l'appel JSON que fait le navigateur pour
afficher les mêmes tailles en pèse dix à vingt fois moins. `probe` récupère la
page, en extrait toutes les URL qui ressemblent à une API, les essaie une par
une, garde celles qui renvoient réellement des tailles et affiche la plus
légère avec la ligne à coller :

```
🏆 Le plus léger qui lit le stock : 14,2 Ko contre 352,8 Ko pour la page — 25× moins.
   https://www.hollisterco.com/api/…
À coller dans .env :
   STOCKWATCH_API_URL=https://www.hollisterco.com/api/…
```

Vérifie ensuite avec `diagnose` que les tailles correspondent à la fiche avant
de compter dessus. Si rien n'est trouvé, la page reste la seule source et il ne
reste que l'intervalle comme levier.

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

## 12. Architecture

```
stockwatch/
├── __main__.py   CLI : `run` (défaut) et `diagnose`
├── app.py        câblage : bot Telegram + boucle de surveillance
├── bot.py        commandes Telegram et menu à boutons
├── keyboards.py  claviers inline
├── browser.py    renouvellement du cookie via un navigateur headless
├── schedule.py   veille nocturne (fuseau de l'utilisateur)
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
python -m pytest -q        # 198 tests
python -m ruff check .
```

---

## 13. Honnêteté technique

- **Le HTML de hollisterco.com ne porte pas le stock** (constaté en conditions
  réelles) : les tailles y sont listées, leur disponibilité est chargée ensuite
  en JavaScript. Tant que `STOCKWATCH_API_URL` ne pointe pas vers l'appel qui
  porte le stock (voir §8), le bot dira « surveillance dégradée » — c'est
  volontaire : il ne devine pas. Une première version devinait, et a annoncé
  « XS, S disponibles » sur un produit intégralement épuisé ; c'est corrigé et
  couvert par un test de non-régression.
- **Le lecteur n'a pas été validé contre la vraie page depuis l'environnement de
  développement** : `hollisterco.com` y était bloqué (sortie réseau filtrée).
  Les 198 tests couvrent chaque format de réponse géré ; `diagnose` sert à
  confirmer le format réellement servi et fournit les « Pistes » nécessaires
  pour écrire le lecteur manquant.
- **Le stock affiché n'est pas une réservation.** Le bot te prévient, il
  n'achète rien : sur une pièce très demandée, la taille peut repartir entre
  l'alerte et ton passage en caisse.
- **Un seul produit surveillé à la fois** (`/produit` bascule de l'un à
  l'autre). Le suivi simultané de plusieurs fiches n'est pas implémenté.
- Respecte les conditions d'utilisation du site : garde une cadence raisonnable
  et n'utilise ce bot que pour ton usage personnel.
