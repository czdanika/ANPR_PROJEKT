# ANPR Projekt – Claude konfiguráció

## Raspberry Pi deployment

- **IP:** 192.168.0.136
- **SSH user:** admin
- **SSH password:** admin
- **App URL:** http://192.168.0.136:5555/vehicles
- **Projekt mappa a Pi-n:** /home/admin/ANPR_PROJEKT_v2

### Deployment lépések

1. Fájlok másolása rsync-kel:
```bash
sshpass -p 'admin' rsync -avz \
  app.py \
  templates/vehicles.html \
  templates/config.html \
  templates/stats.html \
  admin@192.168.0.136:/home/admin/ANPR_PROJEKT_v2/templates/

sshpass -p 'admin' rsync -avz app.py admin@192.168.0.136:/home/admin/ANPR_PROJEKT_v2/
```

2. Docker újraindítás:
```bash
sshpass -p 'admin' ssh admin@192.168.0.136 \
  "cd /home/admin/ANPR_PROJEKT_v2 && docker-compose down && docker-compose up -d --build"
```

## NAS deployment

A NAS-on nincs git, ezért GitHub ZIP-ből frissítünk.

### Helyes update script

```bash
cd /volume1/docker/ANPR_PROJEKT
curl -L -o anpr.zip "https://github.com/czdanika/ANPR_PROJEKT/archive/refs/heads/main.zip"
python3 -c "import zipfile; zipfile.ZipFile('anpr.zip').extractall('.')"
cp -rf ANPR_PROJEKT-main/. .
rm -rf ANPR_PROJEKT-main anpr.zip
sudo docker compose up -d --build
```

### Fontos: `cp -rf` és nem `mv`

A `mv` parancs **nem írja felül a meglévő mappákat**, hanem belemozgatja azokat.
Például `mv ANPR_PROJEKT-main/templates .` létrehozza a `templates/templates/` struktúrát
a régi `templates/` felülírása helyett – ezért a régi HTML fájlok maradnak érvényben.

A `cp -rf ANPR_PROJEKT-main/. .` viszont minden fájlt és mappát felülír.

## Flask app

- **Helyi dev szerver:** http://localhost:5555
- **Auth:** admin / password123
- **Worktree branch:** claude/eager-hellman
