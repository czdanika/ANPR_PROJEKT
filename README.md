# ANPR_PROJEKT_v2

Modern ANPR megjelenito felulet, a meglvo Flask API-val.

## Futtatas Dockerrel (TerraMaster)

1. Masold fel a projektet a NAS-ra, peldaul:

   /share/Projects/ANPR_PROJEKT_v2

2. Inditsd a kontenert:

   cd /share/Projects/ANPR_PROJEKT_v2
   docker compose up -d --build

3. Megnyitas a bongeszoben:

   http://<NAS_IP>:5555/vehicles

## Alap belepes

Felhasznalo/jelszo az app.py-ben van:
- admin / password123

## Megjegyzes

A kepek a received_images mappaba kerulnek, az adatok pedig a vehicles.db SQLite fajlba.

