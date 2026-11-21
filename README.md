# Sistema de préstamo de dispositivos

Aplicación web para solicitar computadores, cámaras y tablets. Los usuarios crean una solicitud, el sistema valida que el equipo esté libre en esas fechas y, según el dispositivo, el préstamo se aprueba de inmediato o queda pendiente de autorización del administrador.

**Tecnologías:** Python 3.9+ · Flask · SQLite (no requiere instalar un servidor de base de datos) · HTML/CSS/JS sin frameworks.

## Instalación y ejecución

```bash
git clone <url-del-repositorio>
cd prestamos-dispositivos

python -m venv venv
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

pip install -r requirements.txt
python app.py
```

Abre **http://127.0.0.1:5000** en el navegador.

La base de datos `database/prestamos.db` se crea automáticamente la primera vez que se ejecuta la aplicación, con las tablas, el usuario administrador y el inventario de dispositivos.

Para reiniciar la base de datos desde cero:

```bash
flask --app app init-db --reset
```

## Credenciales del administrador

| Usuario | Contraseña |
|---|---|
| `administrador023` | `poli123` |

Es el único administrador. Todas las personas que se registran desde la página quedan como **estudiante** o **profesor** según lo que elijan en el formulario.

## Funcionalidades

### Registro (estudiantes y profesores)
- Tipo y número de documento, nombres, apellidos, celular.
- Código estudiantil o docente y facultad.
- Estudiantes: programa académico, semestre y modalidad. Profesores: tipo de vinculación.
- Correo institucional (`@poligran.edu.co`) y contraseña, cada uno **escrito dos veces** para confirmar que coinciden (validado en el navegador y en el servidor).
- Autorización de tratamiento de datos (Ley 1581 de 2012).
- Contraseñas guardadas con hash (PBKDF2-SHA256), nunca en texto plano.

El inicio de sesión se hace con el **correo institucional** (o con el nombre de usuario, en el caso del administrador) y la contraseña.

### Estudiantes y profesores
- Crear solicitudes de préstamo.
- Ver la ocupación de cada equipo antes de elegir fechas.
- Consultar sus préstamos y su estado, y cancelar los que aún no han iniciado.

### Administrador
- Crear préstamos a nombre de cualquier estudiante o profesor (quedan aprobados).
- Aprobar o rechazar solicitudes pendientes, con observación opcional.
- Consultar **todos** los préstamos con quién los solicitó, quién los creó y quién los revisó; filtrar por estado y buscar por nombre, correo, documento o código del equipo.
- Registrar devoluciones (marca si fue a tiempo o con retraso).
- Ver la lista de usuarios registrados.

## Reglas del préstamo

| Regla | Valor |
|---|---|
| Choque de fechas | Un dispositivo **no puede** tener dos préstamos (pendientes o aprobados) con fechas que se crucen. Se valida dentro de una transacción para evitar reservas simultáneas. |
| Fecha límite de entrega | Estudiantes: máximo **7 días** después de la recogida. Profesores: máximo **15 días**. Entrega antes de las **6:00 p. m.** |
| Anticipación | Se puede reservar desde hoy y hasta 60 días adelante. |
| Préstamos simultáneos | Máximo 3 préstamos activos o pendientes por usuario. |
| Autorización | Los equipos marcados con *requiere autorización* (portátiles de alta gama, cámaras réflex/mirrorless, iPad Pro) quedan **pendientes**; los demás se aprueban automáticamente. Una solicitud pendiente también bloquea sus fechas hasta que se apruebe o rechace. |

Estos valores se pueden cambiar al inicio de `app.py`.

**Estados:** pendiente → aprobado / rechazado; aprobado → devuelto / cancelado. La interfaz además muestra *en curso* (aprobado y ya iniciado), *vencido* (aprobado y pasó la fecha de entrega sin devolución) y *expirado* (pendiente cuya fecha ya pasó).

## Estructura del proyecto

```
prestamos-dispositivos/
├── app.py              # Rutas, validaciones y reglas de negocio
├── db.py               # Conexión a SQLite, creación de tablas y datos iniciales
├── schema.sql          # Modelo de datos
├── requirements.txt
├── database/           # Aquí se genera prestamos.db (ignorado por git)
├── static/
│   ├── css/estilos.css # Estilos con la paleta institucional
│   └── js/app.js       # Confirmación de correo/contraseña y ocupación de equipos
└── templates/          # Vistas (Jinja2)
```

## Modelo de datos

- **usuarios** (Usuario): datos personales y académicos, rol (`administrador`, `estudiante`, `profesor`) y hash de la contraseña.
- **unidades_equipo** (UnidadEquipo): código de inventario, tipo (Computador, Cámara, Tablet), marca, modelo y si requiere autorización.
- **reservas** (Reserva): solicitante, dispositivo, fecha de recogida, fecha límite de entrega, motivo, estado, quién lo creó, quién lo revisó y fecha de devolución.

## Variables de entorno (opcionales)

| Variable | Uso |
|---|---|
| `SECRET_KEY` | Clave para firmar las sesiones. Cámbiala si despliegas la aplicación. |
| `DATABASE_PATH` | Ruta alternativa para el archivo SQLite. |
