from flask import Flask, request, render_template, send_from_directory, jsonify, Response, stream_with_context
from flask_httpauth import HTTPBasicAuth
import os
import shutil
import sqlite3
import xml.etree.ElementTree as ET
import json
import threading
import urllib.request
import queue
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from datetime import datetime, timedelta

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    print("FIGYELEM: paho-mqtt nincs telepítve, MQTT funkció nem elérhető!")

app = Flask(__name__)

APP_VERSION = "2.2602"

IMAGE_SAVE_PATH = './received_images'
DATABASE_FILE = "vehicles.db"
LOG_XML_DATA = True

auth = HTTPBasicAuth()

users = {
    "admin": os.environ.get("ANPR_ADMIN_PASSWORD", "password123"),
    "user": os.environ.get("ANPR_USER_PASSWORD", "userpassword"),
}

# MQTT globális állapot
mqtt_client = None
mqtt_connected = False
mqtt_lock = threading.Lock()

# SSE (Server-Sent Events) – valós idejű értesítések a böngészőbe
_sse_listeners = []
_sse_lock = threading.Lock()

def push_sse_event(data: dict):
    """Push egy eseményt az összes csatlakozott SSE kliensnek."""
    msg = json.dumps(data, ensure_ascii=False)
    with _sse_lock:
        dead = []
        for q in _sse_listeners:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_listeners.remove(q)


@auth.verify_password
def verify_password(username, password):
    if username in users and users[username] == password:
        return username
    return None


def log_with_timestamp(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"{timestamp} - {message}")


def initialize_database():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS anpr_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT,
                event_type TEXT,
                license_plate TEXT,
                confidence_level TEXT,
                vehicle_type TEXT,
                vehicle_color TEXT,
                vehicle_direction TEXT,
                image_path TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mqtt_config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS known_plates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                license_plate TEXT UNIQUE,
                friendly_name TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cameras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT UNIQUE,
                friendly_name TEXT,
                relay_enabled INTEGER DEFAULT 0,
                relay_url TEXT DEFAULT '',
                relay_method TEXT DEFAULT 'GET',
                relay_body TEXT DEFAULT '{"id":0,"on":true}',
                relay_trigger_on TEXT DEFAULT 'arrival'
            )
        ''')
        # Migrate existing cameras table (add relay columns if missing)
        for col_sql in [
            "ALTER TABLE cameras ADD COLUMN relay_enabled INTEGER DEFAULT 0",
            "ALTER TABLE cameras ADD COLUMN relay_url TEXT DEFAULT ''",
            "ALTER TABLE cameras ADD COLUMN relay_method TEXT DEFAULT 'GET'",
            "ALTER TABLE cameras ADD COLUMN relay_body TEXT DEFAULT '{\"id\":0,\"on\":true}'",
            "ALTER TABLE cameras ADD COLUMN relay_trigger_on TEXT DEFAULT 'arrival'",
        ]:
            try:
                cursor.execute(col_sql)
            except sqlite3.OperationalError:
                pass  # oszlop már létezik
        defaults = {
            'mqtt_enabled': '0',
            'mqtt_host': '',
            'mqtt_port': '1883',
            'mqtt_username': '',
            'mqtt_password': '',
            'mqtt_topic': 'anpr/event',
            'mqtt_retain': '0',
            'mqtt_per_plate': '0',
            'mqtt_discovery': '0',
            'mqtt_discovery_prefix': 'homeassistant',
            'mqtt_base_url': 'http://192.168.0.136:5555',
            'auto_refresh': '0',
            'auto_refresh_interval': '30',
            'events_per_page': '100',
            'image_max_days': '0',
            'image_max_count': '0',
            'webhook_enabled': '0',
            'webhook_known_arrival_url': '',
            'webhook_known_departure_url': '',
            'webhook_unknown_url': '',
            'relay_enabled': '0',
            'relay_url': '',
            'relay_method': 'GET',
            'relay_body': '{"id":0,"on":true}',
            'relay_trigger_on': 'arrival',
            'email_enabled': '0',
            'email_smtp_host': '',
            'email_smtp_port': '587',
            'email_smtp_user': '',
            'email_smtp_password': '',
            'email_from': '',
            'email_to': '',
            'email_trigger': 'unknown',
            'email_attach_image': '1',
        }
        for key, value in defaults.items():
            cursor.execute('INSERT OR IGNORE INTO mqtt_config (key, value) VALUES (?, ?)', (key, value))
        conn.commit()
        conn.close()
        log_with_timestamp("Az adatbázis inicializálva lett!")
    except sqlite3.Error as e:
        log_with_timestamp(f"Adatbázis inicializálási hiba: {e}")


def get_config(key, default=None):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM mqtt_config WHERE key = ?', (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default
    except:
        return default


def set_config(key, value):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO mqtt_config (key, value) VALUES (?, ?)', (key, str(value)))
        conn.commit()
        conn.close()
    except Exception as e:
        log_with_timestamp(f"Config mentési hiba: {e}")


def get_all_config():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT key, value FROM mqtt_config')
        rows = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except:
        return {}


def fetch_cameras():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, ip_address, friendly_name,
                   relay_enabled, relay_url, relay_method, relay_body, relay_trigger_on
            FROM cameras ORDER BY ip_address
        ''')
        rows = cursor.fetchall()
        conn.close()
        return [{
            'id': r[0], 'ip': r[1], 'name': r[2],
            'relay_enabled': bool(int(r[3] or 0)),
            'relay_url': r[4] or '',
            'relay_method': r[5] or 'GET',
            'relay_body': r[6] or '{"id":0,"on":true}',
            'relay_trigger_on': r[7] or 'arrival',
        } for r in rows]
    except:
        return []


def get_cameras_dict():
    """Returns {ip: friendly_name} dict for template rendering."""
    return {c['ip']: c['name'] for c in fetch_cameras() if c['name']}


def fetch_known_plates():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT id, license_plate, friendly_name FROM known_plates ORDER BY license_plate')
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'plate': r[1], 'name': r[2]} for r in rows]
    except:
        return []


ARRIVAL_DIRECTIONS = {'approaching', 'forward', 'enter', 'in'}
DEPARTURE_DIRECTIONS = {'away', 'reverse', 'backward', 'exit', 'out', 'leaving'}


def send_webhook(url, payload):
    if not url:
        return
    try:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=data,
                                     headers={'Content-Type': 'application/json'},
                                     method='POST')
        with urllib.request.urlopen(req, timeout=5) as resp:
            log_with_timestamp(f"Webhook OK ({resp.status}): {url}")
    except Exception as e:
        log_with_timestamp(f"Webhook hiba ({url}): {e}")


def trigger_webhooks(event_data, friendly_name):
    if get_config('webhook_enabled', '0') != '1':
        return

    base_url = (get_config('mqtt_base_url', '') or '').rstrip('/')
    image_path = event_data.get('image_path', '')
    plate = (event_data.get('license_plate') or '').upper()

    payload = {
        'license_plate': plate,
        'friendly_name': friendly_name or plate,
        'known': friendly_name is not None,
        'vehicle_type': event_data.get('vehicle_type', ''),
        'color': event_data.get('vehicle_color', ''),
        'direction': event_data.get('vehicle_direction', ''),
        'confidence': event_data.get('confidence_level', ''),
        'camera_ip': event_data.get('ip_address', ''),
        'image_url': f"{base_url}{image_path}" if image_path and base_url else '',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    direction = (event_data.get('vehicle_direction') or '').lower()

    if friendly_name is not None:
        # Ismert rendszám
        if direction in ARRIVAL_DIRECTIONS:
            threading.Thread(target=send_webhook,
                             args=(get_config('webhook_known_arrival_url', ''), payload),
                             daemon=True).start()
        elif direction in DEPARTURE_DIRECTIONS:
            threading.Thread(target=send_webhook,
                             args=(get_config('webhook_known_departure_url', ''), payload),
                             daemon=True).start()
        else:
            # Ismeretlen irány: mindkét URL-t megpróbálja, ha csak egy van beállítva
            arrival_url = get_config('webhook_known_arrival_url', '')
            departure_url = get_config('webhook_known_departure_url', '')
            url = arrival_url or departure_url
            if url:
                threading.Thread(target=send_webhook, args=(url, payload), daemon=True).start()
    else:
        # Ismeretlen rendszám
        threading.Thread(target=send_webhook,
                         args=(get_config('webhook_unknown_url', ''), payload),
                         daemon=True).start()


def _fire_relay(url, method, body_str, camera_label=''):
    """Sends GET or POST to relay URL in a background thread."""
    def _send():
        try:
            if method == 'GET':
                req = urllib.request.Request(url, method='GET')
            else:
                req = urllib.request.Request(
                    url, data=(body_str or '{}').encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
            with urllib.request.urlopen(req, timeout=5) as resp:
                log_with_timestamp(f"Relé trigger OK ({resp.status}){' – ' + camera_label if camera_label else ''}: {url}")
        except Exception as e:
            log_with_timestamp(f"Relé trigger hiba ({url}): {e}")
    threading.Thread(target=_send, daemon=True).start()


def _should_trigger(direction, trigger_on):
    if trigger_on == 'both':
        return True
    if trigger_on == 'departure':
        return direction in DEPARTURE_DIRECTIONS
    # 'arrival' – ismeretlen irány esetén is triggerel
    return direction in ARRIVAL_DIRECTIONS or direction not in DEPARTURE_DIRECTIONS


def trigger_relay_if_needed(event_data, friendly_name):
    if friendly_name is None:
        return  # csak ismert rendszámnál nyit kaput

    camera_ip = (event_data.get('ip_address') or '').strip()
    direction = (event_data.get('vehicle_direction') or '').lower()

    # --- Per-kamera relé ---
    if camera_ip:
        try:
            conn = sqlite3.connect(DATABASE_FILE)
            cursor = conn.cursor()
            cursor.execute(
                'SELECT relay_enabled, relay_url, relay_method, relay_body, relay_trigger_on '
                'FROM cameras WHERE ip_address = ?', (camera_ip,)
            )
            row = cursor.fetchone()
            conn.close()
            if row and int(row[0] or 0):
                url = (row[1] or '').strip()
                if url and _should_trigger(direction, row[4] or 'arrival'):
                    _fire_relay(url, (row[2] or 'GET').upper(), row[3] or '{}', camera_label=camera_ip)
                    return  # per-kamera config érvényesül, globálist kihagyjuk
        except Exception as e:
            log_with_timestamp(f"Per-kamera relé lekérdezési hiba: {e}")

    # --- Globális relé fallback ---
    if get_config('relay_enabled', '0') != '1':
        return
    url = (get_config('relay_url', '') or '').strip()
    if not url:
        return
    if _should_trigger(direction, get_config('relay_trigger_on', 'arrival')):
        _fire_relay(url, (get_config('relay_method', 'GET') or 'GET').upper(),
                    (get_config('relay_body', '') or '').strip() or '{}')


def send_email_notification(event_data, friendly_name):
    """E-mail értesítés küldése rendszám észleléskor."""
    if get_config('email_enabled', '0') != '1':
        return
    smtp_host = (get_config('email_smtp_host', '') or '').strip()
    if not smtp_host:
        return
    smtp_port = int(get_config('email_smtp_port', '587') or 587)
    smtp_user = (get_config('email_smtp_user', '') or '').strip()
    smtp_pass = (get_config('email_smtp_password', '') or '').strip()
    from_addr = (get_config('email_from', '') or smtp_user).strip()
    to_raw = (get_config('email_to', '') or '').strip()
    to_addrs = [a.strip() for a in to_raw.split(',') if a.strip()]
    if not to_addrs:
        return
    trigger = get_config('email_trigger', 'unknown')
    attach_image = get_config('email_attach_image', '1') == '1'

    plate = (event_data.get('license_plate') or 'Unknown').upper()
    is_known = friendly_name is not None

    if trigger == 'unknown' and is_known:
        return
    if trigger == 'known' and not is_known:
        return

    def _send():
        try:
            status_text = f'✅ Ismert – {friendly_name}' if is_known else '⚠️ Ismeretlen rendszám'
            base_url = (get_config('mqtt_base_url', '') or '').rstrip('/')
            image_path = event_data.get('image_path', '')
            image_url = f"{base_url}{image_path}" if image_path and base_url else ''
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            subject = f"ANPR: {plate} – {status_text}"

            html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;color:#222;">
<h2 style="color:#2d4df4;">🚗 ANPR Értesítés</h2>
<table style="width:100%;border-collapse:collapse;">
  <tr><td style="padding:6px 10px;font-weight:bold;">Rendszám</td><td style="padding:6px 10px;font-family:monospace;font-size:1.1em;">{plate}</td></tr>
  <tr style="background:#f4f4f4;"><td style="padding:6px 10px;font-weight:bold;">Státusz</td><td style="padding:6px 10px;">{status_text}</td></tr>
  <tr><td style="padding:6px 10px;font-weight:bold;">Irány</td><td style="padding:6px 10px;">{event_data.get('vehicle_direction','–')}</td></tr>
  <tr style="background:#f4f4f4;"><td style="padding:6px 10px;font-weight:bold;">Jármű típusa</td><td style="padding:6px 10px;">{event_data.get('vehicle_type','–')}</td></tr>
  <tr><td style="padding:6px 10px;font-weight:bold;">Szín</td><td style="padding:6px 10px;">{event_data.get('vehicle_color','–')}</td></tr>
  <tr style="background:#f4f4f4;"><td style="padding:6px 10px;font-weight:bold;">Kamera IP</td><td style="padding:6px 10px;">{event_data.get('ip_address','–')}</td></tr>
  <tr><td style="padding:6px 10px;font-weight:bold;">Időpont</td><td style="padding:6px 10px;">{now_str}</td></tr>
</table>
{f'<p><img src="{image_url}" style="max-width:400px;border-radius:8px;margin-top:12px;"></p>' if image_url else ''}
<p style="margin-top:20px;font-size:12px;color:#888;">Küldve: ANPR Projekt – <a href="{base_url}">{base_url}</a></p>
</body></html>"""

            msg = MIMEMultipart('related')
            msg['Subject'] = subject
            msg['From'] = from_addr
            msg['To'] = ', '.join(to_addrs)
            msg_alt = MIMEMultipart('alternative')
            msg_alt.attach(MIMEText(html, 'html', 'utf-8'))
            msg.attach(msg_alt)

            if attach_image and image_path:
                full_path = '.' + image_path
                if os.path.exists(full_path):
                    with open(full_path, 'rb') as f:
                        img_data = f.read()
                    img = MIMEImage(img_data)
                    img.add_header('Content-Disposition', 'attachment',
                                   filename=os.path.basename(image_path))
                    msg.attach(img)

            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as srv:
                srv.ehlo()
                srv.starttls()
                srv.ehlo()
                if smtp_user and smtp_pass:
                    srv.login(smtp_user, smtp_pass)
                srv.sendmail(from_addr, to_addrs, msg.as_string())
            log_with_timestamp(f"Email elküldve: {plate} → {', '.join(to_addrs)}")
        except Exception as e:
            log_with_timestamp(f"Email hiba: {e}")

    threading.Thread(target=_send, daemon=True).start()


def cleanup_images():
    try:
        max_days = int(get_config('image_max_days', '0') or 0)
        max_count = int(get_config('image_max_count', '0') or 0)
        if max_days <= 0 and max_count <= 0:
            return
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        deleted = 0
        if max_days > 0:
            cutoff = (datetime.now() - timedelta(days=max_days)).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("SELECT id, image_path FROM anpr_events WHERE timestamp < ? AND image_path != ''", (cutoff,))
            for rec_id, image_path in cursor.fetchall():
                full = '.' + image_path
                if os.path.exists(full):
                    os.remove(full)
                    deleted += 1
                cursor.execute("UPDATE anpr_events SET image_path = '' WHERE id = ?", (rec_id,))
        if max_count > 0:
            cursor.execute("SELECT COUNT(*) FROM anpr_events WHERE image_path != ''")
            count = cursor.fetchone()[0]
            if count > max_count:
                cursor.execute("SELECT id, image_path FROM anpr_events WHERE image_path != '' ORDER BY timestamp ASC LIMIT ?", (count - max_count,))
                for rec_id, image_path in cursor.fetchall():
                    full = '.' + image_path
                    if os.path.exists(full):
                        os.remove(full)
                        deleted += 1
                    cursor.execute("UPDATE anpr_events SET image_path = '' WHERE id = ?", (rec_id,))
        conn.commit()
        conn.close()
        if deleted > 0:
            log_with_timestamp(f"Képek takarítása: {deleted} törölve")
    except Exception as e:
        log_with_timestamp(f"Kép takarítási hiba: {e}")


def get_friendly_name(plate):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT friendly_name FROM known_plates WHERE license_plate = ?', (plate.upper(),))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except:
        return None


def connect_mqtt():
    global mqtt_client, mqtt_connected
    if not MQTT_AVAILABLE:
        log_with_timestamp("paho-mqtt nincs telepítve!")
        return False
    with mqtt_lock:
        try:
            if mqtt_client:
                try:
                    mqtt_client.loop_stop()
                    mqtt_client.disconnect()
                except:
                    pass
                mqtt_client = None
            mqtt_connected = False

            host = (get_config('mqtt_host', '') or '').strip()
            port = int(get_config('mqtt_port', '1883') or 1883)
            username = (get_config('mqtt_username', '') or '').strip()
            password = (get_config('mqtt_password', '') or '').strip()

            if not host:
                log_with_timestamp("MQTT host nincs beállítva!")
                return False

            client = mqtt.Client(client_id="anpr_projekt_v2")
            if username:
                client.username_pw_set(username, password or None)

            def on_connect(c, userdata, flags, rc):
                global mqtt_connected
                mqtt_connected = (rc == 0)
                log_with_timestamp(f"MQTT {'kapcsolódva' if rc == 0 else f'hiba: rc={rc}'}")

            def on_disconnect(c, userdata, rc):
                global mqtt_connected
                mqtt_connected = False
                log_with_timestamp("MQTT kapcsolat megszakadt")

            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            client.connect(host, port, keepalive=60)
            client.loop_start()
            mqtt_client = client

            import time
            time.sleep(1.5)
            return mqtt_connected
        except Exception as e:
            log_with_timestamp(f"MQTT kapcsolódási hiba: {e}")
            mqtt_connected = False
            return False


def publish_event(event_data):
    global mqtt_client, mqtt_connected
    if not MQTT_AVAILABLE:
        return
    if get_config('mqtt_enabled', '0') != '1':
        return
    if not mqtt_client or not mqtt_connected:
        log_with_timestamp("MQTT nincs csatlakozva, próbálkozom...")
        connect_mqtt()
    if not mqtt_client or not mqtt_connected:
        log_with_timestamp("MQTT publish sikertelen: nincs kapcsolat")
        return
    try:
        base_url = (get_config('mqtt_base_url', '') or '').rstrip('/')
        image_path = event_data.get('image_path', '')
        image_url = f"{base_url}{image_path}" if image_path and base_url else ''

        plate = (event_data.get('license_plate') or 'Unknown').upper()
        friendly_name = get_friendly_name(plate)

        payload = {
            'license_plate': plate,
            'friendly_name': friendly_name or plate,
            'known': friendly_name is not None,
            'vehicle_type': event_data.get('vehicle_type', ''),
            'color': event_data.get('vehicle_color', ''),
            'direction': event_data.get('vehicle_direction', ''),
            'confidence': event_data.get('confidence_level', ''),
            'camera_ip': event_data.get('ip_address', ''),
            'event_type': event_data.get('event_type', ''),
            'image_url': image_url,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        topic = get_config('mqtt_topic', 'anpr/event')
        retain = get_config('mqtt_retain', '0') == '1'

        mqtt_client.publish(topic, json.dumps(payload, ensure_ascii=False), retain=retain)
        log_with_timestamp(f"MQTT: {topic} → {plate}")

        if get_config('mqtt_per_plate', '0') == '1' and plate and plate.upper() != 'UNKNOWN':
            base_topic = topic.rsplit('/', 1)[0] if '/' in topic else topic
            plate_topic = f"{base_topic}/plates/{plate.replace(' ', '_')}"
            mqtt_client.publish(plate_topic, json.dumps(payload, ensure_ascii=False), retain=retain)
            log_with_timestamp(f"MQTT per-plate: {plate_topic}")

        if get_config('mqtt_discovery', '0') == '1':
            _publish_ha_discovery(topic)
    except Exception as e:
        log_with_timestamp(f"MQTT publish hiba: {e}")


def _publish_ha_discovery(main_topic):
    try:
        prefix = get_config('mqtt_discovery_prefix', 'homeassistant')
        discovery_topic = f"{prefix}/sensor/anpr_projekt/last_plate/config"
        discovery_payload = {
            "name": "ANPR Utolsó rendszám",
            "state_topic": main_topic,
            "value_template": "{{ value_json.license_plate }}",
            "json_attributes_topic": main_topic,
            "unique_id": "anpr_projekt_last_plate",
            "icon": "mdi:car",
            "device": {
                "identifiers": ["anpr_projekt_v2"],
                "name": "ANPR Rendszer",
                "model": "ANPR_PROJEKT_v2",
                "manufacturer": "Custom"
            }
        }
        mqtt_client.publish(discovery_topic, json.dumps(discovery_payload), retain=True)
        log_with_timestamp("HA MQTT Discovery config elküldve")
    except Exception as e:
        log_with_timestamp(f"HA Discovery hiba: {e}")


def insert_event_to_db(event_data):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO anpr_events (
                ip_address, event_type, license_plate, confidence_level,
                vehicle_type, vehicle_color, vehicle_direction, image_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            event_data['ip_address'],
            event_data['event_type'],
            event_data['license_plate'],
            event_data['confidence_level'],
            event_data['vehicle_type'],
            event_data['vehicle_color'],
            event_data['vehicle_direction'],
            event_data['image_path']
        ))
        conn.commit()
        conn.close()
        log_with_timestamp("Esemény mentve!")
    except sqlite3.Error as e:
        log_with_timestamp(f"Adatbázis mentési hiba: {e}")


def fetch_vehicles(limit=None, offset=0):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        sql = '''
            SELECT id, license_plate, vehicle_type, vehicle_color, vehicle_direction,
                   timestamp, confidence_level, image_path, ip_address
            FROM anpr_events
            ORDER BY timestamp DESC
        '''
        params = []
        if limit is not None:
            sql += ' LIMIT ? OFFSET ?'
            params = [limit, offset]
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "id": row[0],
                "license_plate": row[1],
                "type": row[2],
                "color": row[3],
                "direction": row[4],
                "timestamp": row[5],
                "confidence": row[6],
                "image": row[7],
                "ip_address": row[8] or "",
            }
            for row in rows
        ]
    except sqlite3.Error as e:
        log_with_timestamp(f"Adatbázis lekérdezési hiba: {e}")
        return []


def fetch_vehicles_count():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM anpr_events')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except:
        return 0


@app.route('/received_images/<path:filename>')
def serve_image(filename):
    return send_from_directory(IMAGE_SAVE_PATH, filename)


@app.route('/api/snapshot')
def latest_snapshot():
    """Returns the latest camera image as JPEG. Used by HA generic camera.
    Optional ?ip=<camera_ip> to filter by specific camera."""
    camera_ip = request.args.get('ip', '').strip()
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        if camera_ip:
            cursor.execute(
                "SELECT image_path FROM anpr_events WHERE image_path != '' AND ip_address = ? ORDER BY timestamp DESC LIMIT 1",
                (camera_ip,)
            )
        else:
            cursor.execute(
                "SELECT image_path FROM anpr_events WHERE image_path != '' ORDER BY timestamp DESC LIMIT 1"
            )
        row = cursor.fetchone()
        conn.close()
        if not row or not row[0]:
            return "Nincs elérhető kép", 404
        filename = os.path.basename(row[0])
        return send_from_directory(IMAGE_SAVE_PATH, filename)
    except Exception as e:
        log_with_timestamp(f"Snapshot hiba: {e}")
        return "Szerver hiba", 500


@app.route('/api/event', methods=['POST'])
def receive_event():
    log_with_timestamp("Bejövő esemény...")
    content_type = request.content_type
    if content_type and "multipart/form-data" in content_type:
        try:
            os.makedirs(IMAGE_SAVE_PATH, exist_ok=True)
            event_data = {
                "ip_address": "Unknown",
                "event_type": "Unknown",
                "license_plate": "Unknown",
                "confidence_level": "0",
                "vehicle_type": "Unknown",
                "vehicle_color": "Unknown",
                "vehicle_direction": "Unknown",
                "image_path": ""
            }
            namespace = {'ns': 'http://www.hikvision.com/ver20/XMLSchema'}
            for file_key in request.files:
                file = request.files[file_key]
                if file.filename.endswith('.jpg'):
                    file_path = os.path.join(IMAGE_SAVE_PATH, file.filename)
                    with open(file_path, 'wb') as f:
                        f.write(file.read())
                    event_data["image_path"] = f"/received_images/{file.filename}"
                    shutil.copy2(file_path, os.path.join(IMAGE_SAVE_PATH, 'latest.jpg'))
                elif file.filename.endswith('.xml'):
                    try:
                        xml_data = file.read()
                        if LOG_XML_DATA:
                            log_with_timestamp(f"XML:\n{xml_data.decode('utf-8')}")
                        root = ET.fromstring(xml_data)
                        event_data["ip_address"] = root.findtext('ns:ipAddress', 'Unknown', namespace)
                        event_data["event_type"] = root.findtext('ns:eventType', 'Unknown', namespace)
                        anpr_node = root.find('ns:ANPR', namespace)
                        if anpr_node:
                            event_data["license_plate"] = anpr_node.findtext('ns:licensePlate', 'Unknown', namespace)
                            event_data["confidence_level"] = anpr_node.findtext('ns:confidenceLevel', '0', namespace)
                            event_data["vehicle_type"] = anpr_node.findtext('ns:vehicleType', 'Unknown', namespace)
                            event_data["vehicle_direction"] = anpr_node.findtext('ns:direction', 'Unknown', namespace)
                            vehicle_info = anpr_node.find('ns:vehicleInfo', namespace)
                            if vehicle_info:
                                event_data["vehicle_color"] = vehicle_info.findtext('ns:color', 'Unknown', namespace)
                    except ET.ParseError as e:
                        log_with_timestamp(f"XML hiba: {e}")
                        return "Hiba az XML feldolgozásában", 400
            insert_event_to_db(event_data)
            publish_event(event_data)
            friendly = get_friendly_name(event_data.get('license_plate', ''))
            trigger_webhooks(event_data, friendly)
            trigger_relay_if_needed(event_data, friendly)
            send_email_notification(event_data, friendly)
            cleanup_images()
            # SSE – push valós időben a böngészőkbe
            base_url = (get_config('mqtt_base_url', '') or '').rstrip('/')
            image_path = event_data.get('image_path', '')
            push_sse_event({
                'license_plate': (event_data.get('license_plate') or '').upper(),
                'type': event_data.get('vehicle_type', ''),
                'color': event_data.get('vehicle_color', ''),
                'direction': event_data.get('vehicle_direction', ''),
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'confidence': event_data.get('confidence_level', ''),
                'image': image_path,
                'ip_address': event_data.get('ip_address', ''),
                'known': friendly is not None,
                'friendly_name': friendly or '',
            })
            log_with_timestamp(f"Esemény feldolgozva: {event_data['license_plate']}")
            return "ANPR esemény sikeresen feldolgozva!", 200
        except Exception as e:
            log_with_timestamp(f"Váratlan hiba: {e}")
            return "Hiba a kérés feldolgozása során", 500
    else:
        return "Nem támogatott Content-Type", 415


@app.route('/vehicles')
@auth.login_required
def vehicle_list():
    page_size = int(get_config('events_per_page', '100') or 100)
    total_count = fetch_vehicles_count()
    vehicles = fetch_vehicles(limit=page_size, offset=0)
    auto_refresh = get_config('auto_refresh', '0') == '1'
    refresh_interval = int(get_config('auto_refresh_interval', '30') or 30)
    cameras = get_cameras_dict()
    return render_template('vehicles.html', vehicles=vehicles,
                           auto_refresh=auto_refresh, refresh_interval=refresh_interval,
                           cameras=cameras, page_size=page_size, total_count=total_count,
                           version=APP_VERSION)


@app.route('/api/vehicles')
@auth.login_required
def vehicles_api():
    try:
        limit = int(request.args.get('limit', 0)) or None
        offset = int(request.args.get('offset', 0))
    except (ValueError, TypeError):
        limit = None
        offset = 0
    return jsonify(fetch_vehicles(limit=limit, offset=offset))


@app.route('/api/latest')
@auth.login_required
def latest_events_api():
    """Returns the latest N events. Used by HA REST sensor. ?count=N (default 5)"""
    try:
        count = min(int(request.args.get('count', 5)), 100)
    except (ValueError, TypeError):
        count = 5
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, license_plate, vehicle_type, vehicle_color, vehicle_direction,
                   timestamp, confidence_level, image_path, ip_address
            FROM anpr_events ORDER BY timestamp DESC LIMIT ?
        ''', (count,))
        rows = cursor.fetchall()
        conn.close()
        base_url = (get_config('mqtt_base_url', '') or '').rstrip('/')
        result = []
        for row in rows:
            image_path = row[7] or ''
            plate = (row[1] or '').upper()
            result.append({
                'id': row[0],
                'license_plate': plate,
                'vehicle_type': row[2] or '',
                'vehicle_color': row[3] or '',
                'direction': row[4] or '',
                'timestamp': row[5] or '',
                'confidence': row[6] or '',
                'image_url': f"{base_url}{image_path}" if image_path and base_url else '',
                'camera_ip': row[8] or '',
                'known': get_friendly_name(plate) is not None,
                'friendly_name': get_friendly_name(plate) or plate,
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/events/<int:event_id>', methods=['DELETE'])
@auth.login_required
def delete_event(event_id):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT image_path FROM anpr_events WHERE id = ?', (event_id,))
        row = cursor.fetchone()
        if row and row[0]:
            full = '.' + row[0]
            if os.path.exists(full):
                os.remove(full)
        cursor.execute('DELETE FROM anpr_events WHERE id = ?', (event_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/events/cleanup', methods=['POST'])
@auth.login_required
def manual_cleanup():
    try:
        cleanup_images()
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM anpr_events")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM anpr_events WHERE image_path != ''")
        with_image = cursor.fetchone()[0]
        conn.close()
        return jsonify({'status': 'ok', 'total_events': total, 'events_with_image': with_image})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/config')
@auth.login_required
def config_page():
    config = get_all_config()
    known_plates = fetch_known_plates()
    cameras = fetch_cameras()
    return render_template('config.html', config=config, known_plates=known_plates, cameras=cameras, version=APP_VERSION)


@app.route('/api/config', methods=['POST'])
@auth.login_required
def save_config_api():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Nincs adat'}), 400
    for key, value in data.items():
        set_config(key, str(value))
    if get_config('mqtt_enabled', '0') == '1':
        threading.Thread(target=connect_mqtt, daemon=True).start()
    return jsonify({'status': 'ok'})


@app.route('/api/mqtt/test', methods=['POST'])
@auth.login_required
def test_mqtt_connection():
    data = request.get_json() or {}
    for key, value in data.items():
        if key.startswith('mqtt_'):
            set_config(key, str(value))
    success = connect_mqtt()
    return jsonify({
        'connected': success,
        'message': 'Kapcsolódva!' if success else 'Kapcsolódás sikertelen. Ellenőrizd a beállításokat!'
    })


@app.route('/api/mqtt/status')
@auth.login_required
def mqtt_status_api():
    return jsonify({
        'connected': mqtt_connected,
        'enabled': get_config('mqtt_enabled', '0') == '1'
    })


@app.route('/api/known-plates', methods=['GET'])
@auth.login_required
def get_known_plates_api():
    return jsonify(fetch_known_plates())


@app.route('/api/known-plates', methods=['POST'])
@auth.login_required
def add_known_plate_api():
    data = request.get_json()
    plate = (data.get('plate') or '').upper().strip()
    name = (data.get('name') or '').strip()
    if not plate:
        return jsonify({'error': 'Rendszám kötelező'}), 400
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO known_plates (license_plate, friendly_name) VALUES (?, ?)', (plate, name))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/known-plates/<int:plate_id>', methods=['DELETE'])
@auth.login_required
def delete_known_plate_api(plate_id):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM known_plates WHERE id = ?', (plate_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cameras', methods=['GET'])
@auth.login_required
def get_cameras_api():
    return jsonify(fetch_cameras())


@app.route('/api/cameras', methods=['POST'])
@auth.login_required
def add_camera_api():
    data = request.get_json()
    ip = (data.get('ip') or '').strip()
    name = (data.get('name') or '').strip()
    relay_enabled = 1 if data.get('relay_enabled') else 0
    relay_url = (data.get('relay_url') or '').strip()
    relay_method = (data.get('relay_method') or 'GET').upper()
    relay_body = (data.get('relay_body') or '{"id":0,"on":true}').strip()
    relay_trigger_on = data.get('relay_trigger_on') or 'arrival'
    if not ip:
        return jsonify({'error': 'IP cím kötelező'}), 400
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO cameras
                (ip_address, friendly_name, relay_enabled, relay_url, relay_method, relay_body, relay_trigger_on)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (ip, name, relay_enabled, relay_url, relay_method, relay_body, relay_trigger_on))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cameras/<int:camera_id>', methods=['DELETE'])
@auth.login_required
def delete_camera_api(camera_id):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM cameras WHERE id = ?', (camera_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/protected')
@auth.login_required
def protected():
    return jsonify({"message": f"Hello, {auth.current_user()}! Ez egy védett oldal."})


# ─── SSE ──────────────────────────────────────────────────────────────────────

@app.route('/api/events/stream')
@auth.login_required
def event_stream():
    """Server-Sent Events stream – valós idejű esemény push a böngészőbe."""
    q = queue.Queue(maxsize=20)
    with _sse_lock:
        _sse_listeners.append(q)

    def generate():
        try:
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"  # kapcsolat életben tartása
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                try:
                    _sse_listeners.remove(q)
                except ValueError:
                    pass

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


# ─── Email teszt ──────────────────────────────────────────────────────────────

@app.route('/api/email/test', methods=['POST'])
@auth.login_required
def test_email():
    data = request.get_json() or {}
    for key, value in data.items():
        if key.startswith('email_'):
            set_config(key, str(value))
    dummy = {
        'license_plate': 'TEST-001',
        'vehicle_type': 'car',
        'vehicle_color': 'white',
        'vehicle_direction': 'forward',
        'confidence_level': '99',
        'ip_address': '192.168.0.test',
        'image_path': '',
    }
    try:
        # Teszt esetén minden trigger-t felülbírálunk
        orig = get_config('email_trigger', 'unknown')
        set_config('email_trigger', 'all')
        send_email_notification(dummy, None)
        set_config('email_trigger', orig)
        return jsonify({'status': 'ok', 'message': 'Teszt email elküldve (ha a beállítások helyesek)!'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ─── Statisztika ──────────────────────────────────────────────────────────────

@app.route('/stats')
@auth.login_required
def stats_page():
    return render_template('stats.html', version=APP_VERSION)


@app.route('/api/stats')
@auth.login_required
def stats_api():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        now = datetime.now()
        today = now.strftime('%Y-%m-%d')
        week_start = (now - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        month_start = (now - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')

        cursor.execute("SELECT COUNT(*) FROM anpr_events WHERE timestamp >= ?", (today + ' 00:00:00',))
        today_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM anpr_events WHERE timestamp >= ?", (week_start,))
        week_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM anpr_events")
        total_count = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM anpr_events e
            WHERE EXISTS (SELECT 1 FROM known_plates kp WHERE kp.license_plate = UPPER(e.license_plate))
        """)
        known_count = cursor.fetchone()[0]

        # Óránkénti eloszlás (utolsó 7 nap)
        cursor.execute("""
            SELECT strftime('%H', timestamp) as h, COUNT(*) as cnt
            FROM anpr_events WHERE timestamp >= ?
            GROUP BY h ORDER BY h
        """, (week_start,))
        hourly = {str(i).zfill(2): 0 for i in range(24)}
        for row in cursor.fetchall():
            hourly[row[0]] = row[1]

        # Napi események (utolsó 30 nap)
        cursor.execute("""
            SELECT strftime('%Y-%m-%d', timestamp) as day, COUNT(*) as cnt
            FROM anpr_events WHERE timestamp >= ?
            GROUP BY day ORDER BY day
        """, (month_start,))
        daily_rows = cursor.fetchall()

        # Top 10 rendszám
        cursor.execute("""
            SELECT license_plate, COUNT(*) as cnt
            FROM anpr_events GROUP BY license_plate
            ORDER BY cnt DESC LIMIT 10
        """)
        top_plates = [{'plate': r[0], 'count': r[1]} for r in cursor.fetchall()]

        # Kameránkénti bontás
        cursor.execute("""
            SELECT ip_address, COUNT(*) as cnt
            FROM anpr_events GROUP BY ip_address
            ORDER BY cnt DESC
        """)
        per_camera = [{'ip': r[0] or '–', 'count': r[1]} for r in cursor.fetchall()]

        conn.close()
        return jsonify({
            'today': today_count,
            'week': week_count,
            'total': total_count,
            'known': known_count,
            'unknown': total_count - known_count,
            'hourly': [hourly[str(i).zfill(2)] for i in range(24)],
            'daily': {r[0]: r[1] for r in daily_rows},
            'top_plates': top_plates,
            'per_camera': per_camera,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def init_latest_jpg():
    """Induláskor létrehozza a latest.jpg-t a legutóbbi képből, ha még nem létezik."""
    latest_path = os.path.join(IMAGE_SAVE_PATH, 'latest.jpg')
    if os.path.exists(latest_path):
        return
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT image_path FROM anpr_events WHERE image_path != '' ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            src = '.' + row[0]
            if os.path.exists(src):
                shutil.copy2(src, latest_path)
    except Exception as e:
        log_with_timestamp(f"latest.jpg init hiba: {e}")


if __name__ == "__main__":
    initialize_database()
    init_latest_jpg()
    if get_config('mqtt_enabled', '0') == '1':
        threading.Thread(target=connect_mqtt, daemon=True).start()
    port = int(os.environ.get('ANPR_PORT', 5555))
    app.run(debug=True, host='0.0.0.0', port=port)
