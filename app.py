from flask import Flask, request, render_template, send_from_directory, jsonify
from flask_httpauth import HTTPBasicAuth
import os
import sqlite3
import xml.etree.ElementTree as ET

app = Flask(__name__)

# Alapértelmezett könyvtár az érkezett képek mentéséhez
IMAGE_SAVE_PATH = './received_images'
DATABASE_FILE = "vehicles.db"  # Adatbázis fájl neve
LOG_XML_DATA = True  # Ha True, akkor az XML adatokat naplózza

# HTTP Basic Authentication inicializálása
auth = HTTPBasicAuth()

# Felhasználónév és jelszó definiálása
users = {
    "admin": "password123",  # Fő admin fiók
    "user": "userpassword"  # Alternatív felhasználó
}


# Hitelesítéshez szükséges felhasználó-jelszó ellenőrzés
@auth.verify_password
def verify_password(username, password):
    if username in users and users[username] == password:
        return username
    return None


# Naplózás időbélyeggel
def log_with_timestamp(message):
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"{timestamp} - {message}")


# Adatbázis inicializálása
def initialize_database():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        # Tábla létrehozása, ha még nem létezik
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
        conn.commit()
        conn.close()
        log_with_timestamp("Az adatbázis inicializálva lett!")
    except sqlite3.Error as e:
        log_with_timestamp(f"Adatbázis inicializálási hiba: {e}")


# Adatok beszúrása az adatbázisba
def insert_event_to_db(event_data):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO anpr_events (
                ip_address,
                event_type,
                license_plate,
                confidence_level,
                vehicle_type,
                vehicle_color,
                vehicle_direction,
                image_path
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
        log_with_timestamp("Esemény sikeresen mentve az adatbázisba!")
    except sqlite3.Error as e:
        log_with_timestamp(f"Hiba az adatbázis mentés során: {e}")


# Jármű adatok lekérdezése az adatbázisból
def fetch_vehicles():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT license_plate, vehicle_type, vehicle_color, vehicle_direction, timestamp, confidence_level, image_path
            FROM anpr_events
            ORDER BY timestamp DESC
        ''')
        rows = cursor.fetchall()
        conn.close()
        # Adatok átalakítása listává
        vehicles = [
            {
                "license_plate": row[0],
                "type": row[1],
                "color": row[2],
                "direction": row[3],
                "timestamp": row[4],
                "confidence": row[5],
                "image": row[6]
            }
            for row in rows
        ]
        return vehicles
    except sqlite3.Error as e:
        log_with_timestamp(f"Hiba az adatbázis lekérdezés során: {e}")
        return []


@app.route('/received_images/<path:filename>')
def serve_image(filename):
    """Statikus képek kiszolgálása a '/received_images' útvonalról."""
    return send_from_directory(IMAGE_SAVE_PATH, filename)


@app.route('/api/event', methods=['POST'])
def receive_event():
    # A kérés fejlécének naplózása
    log_with_timestamp("Headers:")
    log_with_timestamp(str(request.headers))
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
                    log_with_timestamp(f"Kép mentve: {file.filename} -> {file_path}")
                    event_data["image_path"] = f"/received_images/{file.filename}"
                elif file.filename.endswith('.xml'):
                    try:
                        xml_data = file.read()
                        if LOG_XML_DATA:
                            log_with_timestamp(f"Kapott XML adatok:\n{xml_data.decode('utf-8')}")
                        root = ET.fromstring(xml_data)
                        event_data["ip_address"] = root.find('ns:ipAddress', namespace).text if root.find(
                            'ns:ipAddress', namespace) is not None else "Unknown"
                        event_data["event_type"] = root.find('ns:eventType', namespace).text if root.find(
                            'ns:eventType', namespace) is not None else "Unknown"
                        anpr_node = root.find('ns:ANPR', namespace)
                        if anpr_node:
                            event_data["license_plate"] = anpr_node.find('ns:licensePlate',
                                                                         namespace).text if anpr_node.find(
                                'ns:licensePlate', namespace) is not None else "Unknown"
                            event_data["confidence_level"] = anpr_node.find('ns:confidenceLevel',
                                                                            namespace).text if anpr_node.find(
                                'ns:confidenceLevel', namespace) is not None else "0"
                            event_data["vehicle_type"] = anpr_node.find('ns:vehicleType',
                                                                        namespace).text if anpr_node.find(
                                'ns:vehicleType', namespace) is not None else "Unknown"
                            event_data["vehicle_direction"] = anpr_node.find('ns:direction',
                                                                             namespace).text if anpr_node.find(
                                'ns:direction', namespace) is not None else "Unknown"
                            vehicle_info_node = anpr_node.find('ns:vehicleInfo', namespace)
                            if vehicle_info_node:
                                event_data["vehicle_color"] = vehicle_info_node.find('ns:color',
                                                                                     namespace).text if vehicle_info_node.find(
                                    'ns:color', namespace) is not None else "Unknown"
                    except ET.ParseError as e:
                        log_with_timestamp(f"XML feldolgozási hiba: {e}")
                        return "Hiba az XML feldolgozásában", 400
            insert_event_to_db(event_data)
            log_with_timestamp(f"Esemény sikeresen feldolgozva: {event_data}")
            return "ANPR esemény sikeresen feldolgozva!", 200
        except Exception as e:
            log_with_timestamp(f"Váratlan hiba: {e}")
            return "Hiba a kérés feldolgozása során", 500
    else:
        log_with_timestamp(f"Nem támogatott Content-Type: {content_type}")
        return "Nem támogatott Content-Type", 415


@app.route('/vehicles')
@auth.login_required
def vehicle_list():
    vehicles = fetch_vehicles()
    return render_template('vehicles.html', vehicles=vehicles)


@app.route('/protected')
@auth.login_required
def protected():
    return jsonify({"message": f"Hello, {auth.current_user()}! Ez egy védett oldal."})


if __name__ == "__main__":
    initialize_database()  # Adatbázis inicializálása
    app.run(debug=True, host='0.0.0.0', port=5555)
