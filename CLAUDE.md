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

## Flask app

- **Helyi dev szerver:** http://localhost:5555
- **Auth:** admin / password123
- **Worktree branch:** claude/eager-hellman
