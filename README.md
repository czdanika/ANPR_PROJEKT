# ANPR Projekt v2

Automatikus rendszámfelismerő rendszer Hikvision IP kamerákhoz. Flask alapú REST szerver, amely fogadja a kamera eseményeit, tárolja azokat SQLite adatbázisban, és különböző integrációkat (MQTT, Webhook, Relé/Shelly, Home Assistant) biztosít.

---

## Tartalom

- [Hogyan működik](#hogyan-működik)
- [Funkciók](#funkciók)
- [Telepítés – Raspberry Pi](#telepítés--raspberry-pi)
- [Telepítés – NAS (Docker Hub)](#telepítés--nas-docker-hub)
- [Konfiguráció (.env)](#konfiguráció-env)
- [Beállítások oldal](#beállítások-oldal)
- [API végpontok](#api-végpontok)
- [Home Assistant integráció](#home-assistant-integráció)
- [Könyvtárszerkezet](#könyvtárszerkezet)

---

## Hogyan működik

```
Hikvision kamera
      │
      │  HTTP POST (XML esemény)
      ▼
ANPR szerver (Flask, :5555)
      │
      ├─► SQLite adatbázis (vehicles.db)
      ├─► Képek mentése (/received_images/)
      ├─► MQTT publish → Home Assistant
      ├─► Webhook POST → HA / egyéb
      ├─► Relé HTTP trigger → Shelly kapu
      ├─► E-mail értesítés (SMTP/Gmail)
      └─► SSE push → böngésző (valós idejű lista)
```

1. A **Hikvision kamera** rendszámfelismerés esetén HTTP POST kérést küld a `/api/event` végpontra (XML formátumban).
2. A szerver **kicsomagolja** az eseményt: rendszám, jármű típus, szín, irány, kamera IP, bizonyosság, kép.
3. Az esemény **eltárolódik** az SQLite adatbázisba, a kép a `received_images/` könyvtárba.
4. Ha a rendszám szerepel az **ismert rendszámok** listájában → `known: true`, `friendly_name` kitöltve.
5. Az integráció-modulok párhuzamosan futnak: MQTT, Webhook, Relé.

---

## Funkciók

| Funkció | Leírás |
|---|---|
| **Esemény lista** | Rendszámok, típus, szín, irány, kép, szűrők, lapozás |
| **Több kamera** | Kamerák IP alapján azonosítva, per-kamera megnevezés |
| **Ismert rendszámok** | Név hozzárendelés, `known` flag az MQTT/webhook payloadban |
| **MQTT** | JSON publish, retain flag, per-plate topik, MQTT Discovery |
| **Webhook** | POST érkezés/távozás/ismeretlen URL-ekre |
| **Relé / Shelly** | GET (Gen1) és POST (Gen2+) HTTP trigger kapu nyitáshoz |
| **Per-kamera relé** | Minden kamerához külön relé URL/módszer konfigurálható |
| **Home Assistant** | MQTT szenzor, REST szenzor, Webhook automáció, Dashboard YAML generátor |
| **Lapozás** | Beállítható hány esemény töltődjön be egyszerre (+ „Több betöltése" gomb) |
| **Automatikus frissítés** | Configolható időközönként újratölti az oldalt |
| **Képtakarítás** | Automatikus törlés kor és/vagy darabszám limit alapján |
| **Sötét/világos téma** | Rendszer-téma detektálás + manuális váltás |
| **Valós idejű frissítés (SSE)** | Új esemény azonnal megjelenik a listában (zöld/piros highlight), oldal-újratöltés nélkül |
| **E-mail értesítés** | SMTP/Gmail alapú értesítés rendszám észleléskor, képmelléklettel, trigger beállítással |
| **Statisztika dashboard** | Napi/heti trend, óránkénti eloszlás, ismert/ismeretlen arány, Top 10 rendszám, kameránkénti bontás |
| **Kamera snapshot URL** | `/api/snapshot` – mindig az utolsó kép, HA generic camera integrációhoz |

---

## Telepítés – Raspberry Pi

### Előfeltételek

- Raspberry Pi (tesztelve: Pi 4 / Pi 5, Debian/Raspberry Pi OS)
- Docker + Docker Compose telepítve

```bash
# Docker telepítése (ha még nincs)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Újrabejelentkezés szükséges!
```

### 1. Projekt letöltése

```bash
git clone https://github.com/czdanika/ANPR_PROJEKT.git ANPR_PROJEKT_v2
cd ANPR_PROJEKT_v2
```

### 2. Konfiguráció

```bash
nano .env
```

```env
ANPR_PORT=5555
ANPR_ADMIN_PASSWORD=titkosJelszo
ANPR_USER_PASSWORD=userJelszo
```

### 3. Indítás

```bash
docker-compose up -d
```

Az alkalmazás elérhető: `http://<PI_IP>:5555/vehicles`

### 4. Kamera beállítása (Hikvision)

A kamera kezelőfelületén (vagy iVMS-4200-ban):

**Konfiguráció → Hálózat → Speciális → HTTP értesítés:**
- URL: `http://<PI_IP>:5555/api/event`
- Módszer: `POST`
- Eseménytípus: `ANPR` (rendszámfelismerés)

### 5. Frissítés

```bash
cd ~/ANPR_PROJEKT_v2
git pull origin main
docker-compose restart
```

> ⚠️ **Port vagy jelszóváltás után** `restart` helyett `up --force-recreate` kell:
> ```bash
> docker-compose up -d --force-recreate
> ```

---

## Telepítés – NAS (Docker Hub)

Synology, QNAP, Unraid, TrueNAS, TerraMaster és minden Docker-képes NAS-on működik.

### 1. Könyvtár létrehozása a NAS-on

```
/share/docker/anpr/
├── docker-compose.yml
├── .env
└── anpr-data/
    └── received_images/
```

```bash
mkdir -p /share/docker/anpr/anpr-data/received_images
```

### 2. `.env` fájl

```env
ANPR_PORT=5555
ANPR_ADMIN_PASSWORD=titkosJelszo
ANPR_USER_PASSWORD=userJelszo
```

### 3. `docker-compose.yml` fájl

```yaml
services:
  anpr:
    image: czdanika/anpr-projekt:latest
    ports:
      - "${ANPR_PORT:-5555}:${ANPR_PORT:-5555}"
    volumes:
      - ./anpr-data/vehicles.db:/app/vehicles.db
      - ./anpr-data/received_images:/app/received_images
    restart: unless-stopped
    environment:
      - ANPR_PORT=${ANPR_PORT:-5555}
      - ANPR_ADMIN_PASSWORD=${ANPR_ADMIN_PASSWORD:-password123}
      - ANPR_USER_PASSWORD=${ANPR_USER_PASSWORD:-userpassword}
```

### 4. Indítás

```bash
cd /share/docker/anpr
docker-compose up -d
```

**Synology DSM:** Container Manager → Project → Create → illeszd be a compose tartalmát.

### 5. Image frissítése

```bash
docker-compose pull
docker-compose up -d --force-recreate
```

---

## Az image saját buildelése és push-olása

Ha módosítottad a kódot és frissíteni szeretnéd a Docker Hub image-et:

```bash
# Pi-n (ARM64 + AMD64 egyszerre):
docker login

# Első alkalommal – multi-arch builder létrehozása:
docker run --privileged --rm tonistiigi/binfmt --install all
docker buildx create --name multiarch --driver docker-container --use
docker buildx inspect --bootstrap

# Build + push:
cd ~/ANPR_PROJEKT_v2
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t <DOCKERHUB_FELHASZNALONEV>/anpr-projekt:latest \
  --push .
```

---

## Konfiguráció (.env)

| Változó | Alapértelmezett | Leírás |
|---|---|---|
| `ANPR_PORT` | `5555` | A szerver portja |
| `ANPR_ADMIN_PASSWORD` | `password123` | Admin felhasználó jelszava (teljes hozzáférés) |
| `ANPR_USER_PASSWORD` | `userpassword` | Olvasó felhasználó jelszava |

> ⚠️ Mindig változtasd meg az alapértelmezett jelszavakat éles telepítésnél!

---

## Beállítások oldal

Elérhető: `http://<PI_IP>:<PORT>/config`

| Szekció | Tartalom |
|---|---|
| ⚡ MQTT Kapcsolat | Broker IP, port, felhasználónév, jelszó |
| 📡 Topik beállítások | Fő topik, retain flag, per-plate topik |
| 🏠 Home Assistant | MQTT Discovery, discovery prefix, alap URL |
| 🔄 Automatikus frissítés | Auto-reload időköz, események száma oldalanként |
| 🖼️ Kép tárolás | Megőrzési idő (nap), maximum képszám, azonnali takarítás |
| 🔗 Webhook | Érkezés / Távozás / Ismeretlen rendszám URL-ek |
| 🚪 Relé / Kapu | Globális Shelly relé konfig (GET/POST, irány) |
| 🏠 HA konfiguráció | YAML generátor: MQTT szenzor, REST szenzor, Webhook, Dashboard |
| 📷 Kamerák | Kamera lista, per-kamera relé beállítás |
| 🚗 Ismert rendszámok | Rendszám ↔ név hozzárendelés |

---

## API végpontok

Minden végpont **HTTP Basic Auth** hitelesítést igényel.

### Események

| Módszer | Végpont | Leírás |
|---|---|---|
| `POST` | `/api/event` | Hikvision kamera esemény fogadása (XML) |
| `GET` | `/api/vehicles` | Összes esemény JSON-ban. Paraméterek: `?limit=N&offset=M` |
| `GET` | `/api/latest` | Utolsó N esemény. Paraméter: `?count=5` (max 100) |
| `DELETE` | `/api/events/<id>` | Egy esemény törlése |
| `POST` | `/api/events/cleanup` | Azonnali képtakarítás a konfig szerint |
| `GET` | `/api/events/stream` | SSE stream – valós idejű push (EventSource) |
| `GET` | `/api/snapshot` | Utolsó kamerakép JPEG-ben. `?ip=<camera_ip>` per-kamera szűrés |
| `GET` | `/api/stats` | Statisztika: napi/heti/top rendszámok/óránkénti eloszlás |

### Konfiguráció

| Módszer | Végpont | Leírás |
|---|---|---|
| `GET` | `/api/config` | Aktuális beállítások lekérése |
| `POST` | `/api/config` | Beállítások mentése (JSON body) |
| `POST` | `/api/mqtt/test` | MQTT kapcsolat tesztelése |
| `GET` | `/api/mqtt/status` | MQTT kapcsolat állapota |
| `POST` | `/api/email/test` | Teszt e-mail küldése |

### Ismert rendszámok

| Módszer | Végpont | Leírás |
|---|---|---|
| `GET` | `/api/known-plates` | Összes ismert rendszám listája |
| `POST` | `/api/known-plates` | Új rendszám: `{"plate": "ABC-123", "name": "Saját autó"}` |
| `DELETE` | `/api/known-plates/<id>` | Rendszám törlése |

### Kamerák

| Módszer | Végpont | Leírás |
|---|---|---|
| `GET` | `/api/cameras` | Kamerák listája relé konfiggal |
| `POST` | `/api/cameras` | Új kamera hozzáadása |
| `DELETE` | `/api/cameras/<id>` | Kamera törlése |

---

## Home Assistant integráció

A beállítások oldal (`/config`) **HA konfiguráció** szekciójában 4 féle YAML generálható:

### MQTT payload mezők

Minden felismeréskor a következő JSON kerül publish-olásra:

```json
{
  "license_plate": "ABC-123",
  "known": true,
  "friendly_name": "Saját autó",
  "vehicle_type": "car",
  "vehicle_color": "white",
  "direction": "approaching",
  "confidence": 98.5,
  "image_url": "http://192.168.0.136:5555/received_images/abc.jpg",
  "camera_ip": "192.168.0.101",
  "timestamp": "2026-02-27 10:30:00"
}
```

### 1. MQTT szenzor

Az ANPR szerver publish-ol, a HA szenzor feliratkozik a topikra.

```yaml
# configuration.yaml
mqtt:
  sensor:
    - name: "ANPR Utolsó rendszám"
      state_topic: "anpr/event"
      value_template: "{{ value_json.license_plate }}"
      json_attributes_topic: "anpr/event"
      icon: mdi:car
```

### 2. REST szenzor

A HA 30 másodpercenként lekérdezi az `/api/latest` végpontot – MQTT nélkül is működik.

```yaml
# configuration.yaml
rest:
  - resource: "http://192.168.0.136:5555/api/latest?count=1"
    authentication: basic
    username: admin
    password: !secret anpr_password
    scan_interval: 30
    sensor:
      - name: "ANPR Utolsó rendszám"
        value_template: "{{ value_json[0].license_plate }}"
```

### 3. Webhook automáció

Az ANPR szerver hívja meg a HA webhook végpontját. Beállítható:
- **Érkezés URL**: ismert rendszám + közeledő irány
- **Távozás URL**: ismert rendszám + távolodó irány
- **Ismeretlen URL**: nem ismert rendszám esetén

### 4. Lovelace Dashboard kártya

A generált YAML beilleszthető a Lovelace szerkesztőbe (Kártya hozzáadása → Manuális).

---

## Könyvtárszerkezet

```
ANPR_PROJEKT_v2/
├── app.py                  # Flask szerver, összes logika
├── Dockerfile              # Docker image build
├── docker-compose.yml      # Fejlesztői / Pi compose (forráskód mount)
├── docker-compose.nas.yml  # NAS / production compose (image pull)
├── requirements.txt        # Python függőségek
├── .env                    # Jelszavak és port (NEM kerül git-be!)
├── .dockerignore           # Build-ből kizárt fájlok
├── vehicles.db             # SQLite adatbázis (automatikusan jön létre)
├── received_images/        # Kamera képek tárolója
│   └── *.jpg
└── templates/
    ├── vehicles.html       # Esemény lista főoldal
    └── config.html         # Beállítások oldal
```

> **Megjegyzés:** A `vehicles.db` és a `received_images/` könyvtár nincs a git repóban —
> ezek az adatfájlok a Pi-n / NAS-on keletkeznek helyben.
