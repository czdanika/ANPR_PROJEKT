from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class VehicleData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(50))
    event_type = db.Column(db.String(50))
    license_plate = db.Column(db.String(50))
    confidence_level = db.Column(db.Float)
    vehicle_type = db.Column(db.String(50))
    vehicle_color = db.Column(db.String(50))
    vehicle_direction = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)