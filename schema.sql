-- Esquema de la base de datos (SQLite)
-- Se ejecuta automáticamente la primera vez que se inicia la aplicación.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario              TEXT UNIQUE,                 -- nombre de usuario (solo lo usa el administrador)
    correo               TEXT NOT NULL UNIQUE,        -- correo institucional (credencial de inicio de sesión)
    password_hash        TEXT NOT NULL,
    rol                  TEXT NOT NULL CHECK (rol IN ('administrador', 'estudiante', 'profesor')),
    tipo_documento       TEXT,
    numero_documento     TEXT UNIQUE,
    nombres              TEXT NOT NULL,
    apellidos            TEXT NOT NULL,
    telefono             TEXT,
    codigo_institucional TEXT,                        -- código estudiantil o código docente
    programa             TEXT,                        -- solo estudiantes
    semestre             INTEGER,                     -- solo estudiantes
    modalidad            TEXT,                        -- solo estudiantes (Presencial / Virtual)
    facultad             TEXT,                        -- estudiantes y profesores
    vinculacion          TEXT,                        -- solo profesores (Tiempo completo, Medio tiempo, Cátedra)
    creado_en            TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS unidades_equipo (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo                 TEXT NOT NULL UNIQUE,      -- código de inventario, ej. PC-001
    tipo                   TEXT NOT NULL CHECK (tipo IN ('Computador', 'Cámara', 'Tablet')),
    marca                  TEXT NOT NULL,
    modelo                 TEXT NOT NULL,
    descripcion            TEXT,
    requiere_autorizacion  INTEGER NOT NULL DEFAULT 0, -- 1 = el préstamo queda pendiente hasta que el admin lo apruebe
    activo                 INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS reservas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id      INTEGER NOT NULL REFERENCES usuarios(id),
    dispositivo_id  INTEGER NOT NULL REFERENCES unidades_equipo(id),
    fecha_inicio    TEXT NOT NULL,                    -- YYYY-MM-DD (día de recogida)
    fecha_fin       TEXT NOT NULL,                    -- YYYY-MM-DD (fecha límite de entrega)
    motivo          TEXT NOT NULL,
    estado          TEXT NOT NULL CHECK (estado IN ('pendiente', 'aprobado', 'rechazado', 'devuelto', 'cancelado')),
    creado_por      INTEGER NOT NULL REFERENCES usuarios(id),
    revisado_por    INTEGER REFERENCES usuarios(id),
    revisado_en     TEXT,
    observacion     TEXT,
    devuelto_en     TEXT,
    creado_en       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (fecha_fin >= fecha_inicio)
);

CREATE INDEX IF NOT EXISTS idx_prestamos_dispositivo_fechas
    ON reservas (dispositivo_id, fecha_inicio, fecha_fin);
CREATE INDEX IF NOT EXISTS idx_reservas_usuario ON reservas (usuario_id);
