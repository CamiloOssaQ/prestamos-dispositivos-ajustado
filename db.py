"""Conexión a la base de datos SQLite e inicialización (tablas + datos iniciales)."""
import os
import sqlite3

import click
from flask import current_app, g
from werkzeug.security import generate_password_hash

# Único usuario administrador del sistema
ADMIN_USUARIO = "administrador023"
ADMIN_PASSWORD = "poli123"
ADMIN_CORREO = "administrador023@poligran.edu.co"

# Inventario inicial de dispositivos
DISPOSITIVOS_INICIALES = [
    # (codigo, tipo, marca, modelo, descripcion, requiere_autorizacion)
    ("PC-001", "Computador", "Lenovo", "ThinkPad E14", "Portátil 14\", Intel Core i5, 16 GB RAM", 0),
    ("PC-002", "Computador", "HP", "ProBook 440 G9", "Portátil 14\", Intel Core i5, 8 GB RAM", 0),
    ("PC-003", "Computador", "Dell", "XPS 15", "Portátil para diseño y edición, 32 GB RAM, GPU dedicada", 1),
    ("PC-004", "Computador", "Apple", "MacBook Pro 14\"", "Chip M3, 16 GB RAM", 1),
    ("CAM-001", "Cámara", "Canon", "EOS Rebel T7", "Réflex 24 MP con lente 18-55 mm", 1),
    ("CAM-002", "Cámara", "Sony", "Alpha a6400", "Mirrorless 4K con lente 16-50 mm", 1),
    ("CAM-003", "Cámara", "GoPro", "HERO12 Black", "Cámara de acción 5.3K con accesorios", 0),
    ("TAB-001", "Tablet", "Samsung", "Galaxy Tab A9+", "Pantalla 11\", 64 GB", 0),
    ("TAB-002", "Tablet", "Apple", "iPad 10.ª generación", "Pantalla 10.9\", 64 GB", 0),
    ("TAB-003", "Tablet", "Apple", "iPad Pro 11\" + Apple Pencil", "Para ilustración y diseño", 1),
]


def get_db():
    """Devuelve la conexión de la petición actual (una por request)."""
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            isolation_level=None,  # autocommit; las transacciones se abren de forma explícita
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(reset=False):
    """Crea las tablas (si no existen) y carga el administrador y el inventario."""
    path = current_app.config["DATABASE"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if reset and os.path.exists(path):
        os.remove(path)

    conn = sqlite3.connect(path)
    with current_app.open_resource("schema.sql") as f:
        conn.executescript(f.read().decode("utf-8"))

    conn.execute(
        """INSERT OR IGNORE INTO usuarios
           (usuario, correo, password_hash, rol, nombres, apellidos)
           VALUES (?, ?, ?, 'administrador', 'Administrador', 'del Sistema')""",
        (ADMIN_USUARIO, ADMIN_CORREO, generate_password_hash(ADMIN_PASSWORD, method="pbkdf2:sha256")),
    )

    if conn.execute("SELECT COUNT(*) FROM unidades_equipo").fetchone()[0] == 0:
        conn.executemany(
            """INSERT INTO unidades_equipo (codigo, tipo, marca, modelo, descripcion, requiere_autorizacion)
               VALUES (?, ?, ?, ?, ?, ?)""",
            DISPOSITIVOS_INICIALES,
        )
    conn.commit()
    conn.close()


@click.command("init-db")
@click.option("--reset", is_flag=True, help="Borra la base de datos actual y la crea de nuevo.")
def init_db_command(reset):
    """Inicializa la base de datos."""
    init_db(reset=reset)
    click.echo("Base de datos lista." + (" (reiniciada)" if reset else ""))


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    with app.app_context():
        init_db()
