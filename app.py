"""Sistema de préstamo de dispositivos (computadores, cámaras y tablets)."""
import os
import re
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db as database
from db import get_db

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")
app.config["DATABASE"] = os.environ.get(
    "DATABASE_PATH", os.path.join(app.root_path, "database", "prestamos.db")
)

# ---------------------------------------------------------------------------
# Reglas del negocio
# ---------------------------------------------------------------------------
DOMINIO_INSTITUCIONAL = "poligran.edu.co"
DIAS_MAX_PRESTAMO = {"estudiante": 7, "profesor": 15, "administrador": 15}
DIAS_ANTICIPACION_MAX = 60          # con cuánta anticipación se puede reservar
MAX_PRESTAMOS_ACTIVOS = 3           # préstamos pendientes/aprobados simultáneos por usuario
HORA_LIMITE_ENTREGA = "6:00 p. m."  # hora límite del día de entrega
ESTADOS_QUE_OCUPAN = ("pendiente", "aprobado")

TIPOS_DOCUMENTO = {
    "CC": "Cédula de ciudadanía",
    "TI": "Tarjeta de identidad",
    "CE": "Cédula de extranjería",
    "PA": "Pasaporte",
    "PPT": "Permiso por Protección Temporal",
}
FACULTADES = [
    "Facultad de Ingeniería, Diseño e Innovación",
    "Facultad de Negocios, Gestión y Sostenibilidad",
    "Facultad de Sociedad, Cultura y Creatividad",
]
PROGRAMAS = [
    "Administración de Empresas", "Comunicación Social", "Contaduría Pública",
    "Diseño Gráfico", "Gastronomía", "Ingeniería de Software", "Ingeniería de Sistemas",
    "Ingeniería Industrial", "Mercadeo y Publicidad", "Negocios Internacionales",
    "Psicología", "Seguridad y Salud en el Trabajo",
]
MODALIDADES = ["Presencial", "Virtual"]
VINCULACIONES = ["Tiempo completo", "Medio tiempo", "Cátedra"]

database.init_app(app)


# ---------------------------------------------------------------------------
# Utilidades: sesión, CSRF, permisos
# ---------------------------------------------------------------------------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token  # disponible también dentro de las macros


@app.before_request
def antes_de_cada_peticion():
    if request.method == "POST":
        if not session.get("_csrf") or request.form.get("_csrf") != session.get("_csrf"):
            abort(400, "Token de seguridad inválido. Recarga la página e inténtalo de nuevo.")
    g.user = None
    if "user_id" in session:
        g.user = get_db().execute(
            "SELECT * FROM usuarios WHERE id = ?", (session["user_id"],)
        ).fetchone()
        if g.user is None:
            session.clear()


@app.context_processor
def variables_globales():
    return {
        "hoy": date.today().isoformat(),
        "HORA_LIMITE_ENTREGA": HORA_LIMITE_ENTREGA,
        "DIAS_MAX_PRESTAMO": DIAS_MAX_PRESTAMO,
    }


@app.template_filter("fecha")
def formato_fecha(valor):
    if not valor:
        return ""
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    d = datetime.fromisoformat(str(valor))
    return f"{d.day:02d} {meses[d.month - 1]} {d.year}"


def login_requerido(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if g.user is None:
            flash("Inicia sesión para continuar.", "info")
            return redirect(url_for("login", next=request.path))
        return vista(*args, **kwargs)
    return envoltura


def admin_requerido(vista):
    @wraps(vista)
    @login_requerido
    def envoltura(*args, **kwargs):
        if g.user["rol"] != "administrador":
            abort(403)
        return vista(*args, **kwargs)
    return envoltura


def es_admin():
    return g.user is not None and g.user["rol"] == "administrador"


# ---------------------------------------------------------------------------
# Lógica de préstamos
# ---------------------------------------------------------------------------
def parse_fecha(texto):
    try:
        return date.fromisoformat(texto)
    except (TypeError, ValueError):
        return None


def buscar_cruces(conn, dispositivo_id, inicio, fin, excluir_id=None):
    """Préstamos activos del dispositivo cuyas fechas se cruzan con [inicio, fin] (inclusive)."""
    sql = """SELECT id, fecha_inicio, fecha_fin, estado FROM reservas
             WHERE dispositivo_id = ? AND estado IN ('pendiente', 'aprobado')
               AND fecha_inicio <= ? AND fecha_fin >= ?"""
    params = [dispositivo_id, fin.isoformat(), inicio.isoformat()]
    if excluir_id is not None:
        sql += " AND id != ?"
        params.append(excluir_id)
    return conn.execute(sql, params).fetchall()


def crear_prestamo(solicitante, dispositivo_id, inicio_txt, fin_txt, motivo, creado_por, por_admin):
    """Valida y registra un préstamo. Devuelve (ok, mensaje)."""
    conn = get_db()
    hoy = date.today()
    inicio, fin = parse_fecha(inicio_txt), parse_fecha(fin_txt)
    motivo = (motivo or "").strip()

    if inicio is None or fin is None:
        return False, "Selecciona una fecha de recogida y una fecha de entrega válidas."
    if inicio < hoy:
        return False, "La fecha de recogida no puede ser anterior a hoy."
    if inicio > hoy + timedelta(days=DIAS_ANTICIPACION_MAX):
        return False, f"Solo se puede reservar con máximo {DIAS_ANTICIPACION_MAX} días de anticipación."
    if fin < inicio:
        return False, "La fecha de entrega no puede ser anterior a la fecha de recogida."
    dias_max = DIAS_MAX_PRESTAMO[solicitante["rol"]]
    if fin > inicio + timedelta(days=dias_max):
        limite = inicio + timedelta(days=dias_max)
        return False, (f"El préstamo puede durar máximo {dias_max} días para un {solicitante['rol']}: "
                       f"la fecha límite de entrega sería el {formato_fecha(limite.isoformat())}.")
    if len(motivo) < 10:
        return False, "Describe el motivo del préstamo (mínimo 10 caracteres)."

    try:
        conn.execute("BEGIN IMMEDIATE")  # bloquea escrituras concurrentes mientras se valida
        disp = conn.execute(
            "SELECT * FROM unidades_equipo WHERE id = ? AND activo = 1", (dispositivo_id,)
        ).fetchone()
        if disp is None:
            conn.execute("ROLLBACK")
            return False, "El dispositivo seleccionado no existe o no está disponible."

        activos = conn.execute(
            """SELECT COUNT(*) FROM reservas WHERE usuario_id = ?
               AND estado IN ('pendiente', 'aprobado') AND fecha_fin >= ?""",
            (solicitante["id"], hoy.isoformat()),
        ).fetchone()[0]
        if activos >= MAX_PRESTAMOS_ACTIVOS:
            conn.execute("ROLLBACK")
            return False, (f"El usuario ya tiene {MAX_PRESTAMOS_ACTIVOS} préstamos activos o pendientes; "
                           "debe entregar o cancelar alguno antes de solicitar otro.")

        cruces = buscar_cruces(conn, disp["id"], inicio, fin)
        if cruces:
            conn.execute("ROLLBACK")
            ocupado = ", ".join(
                f"{formato_fecha(c['fecha_inicio'])} – {formato_fecha(c['fecha_fin'])}" for c in cruces
            )
            return False, (f"El dispositivo {disp['codigo']} ya está reservado en esas fechas "
                           f"({ocupado}). Elige otras fechas u otro dispositivo.")

        if por_admin:
            estado, revisado_por, observacion = "aprobado", creado_por, "Creado y aprobado por el administrador."
        elif disp["requiere_autorizacion"]:
            estado, revisado_por, observacion = "pendiente", None, None
        else:
            estado, revisado_por, observacion = "aprobado", None, "Aprobación automática (no requiere autorización)."

        conn.execute(
            """INSERT INTO reservas (usuario_id, dispositivo_id, fecha_inicio, fecha_fin, motivo,
                                      estado, creado_por, revisado_por, revisado_en, observacion)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (solicitante["id"], disp["id"], inicio.isoformat(), fin.isoformat(), motivo, estado,
             creado_por, revisado_por,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S") if revisado_por else None, observacion),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    if estado == "pendiente":
        return True, (f"Solicitud registrada. El dispositivo {disp['codigo']} requiere autorización: "
                      "queda pendiente hasta que el administrador la apruebe. Las fechas quedan apartadas.")
    if por_admin:
        return True, (f"Préstamo #{conn.execute('SELECT last_insert_rowid()').fetchone()[0]} creado y aprobado para "
                      f"{solicitante['nombres']} {solicitante['apellidos']}: {disp['codigo']} del "
                      f"{formato_fecha(inicio.isoformat())} al {formato_fecha(fin.isoformat())}.")
    return True, (f"Préstamo aprobado: recoge el {disp['codigo']} el {formato_fecha(inicio.isoformat())} "
                  f"y entrégalo a más tardar el {formato_fecha(fin.isoformat())} antes de las {HORA_LIMITE_ENTREGA}")


CONSULTA_PRESTAMOS = """
    SELECT p.*, d.codigo, d.tipo, d.marca, d.modelo, d.requiere_autorizacion,
           u.nombres || ' ' || u.apellidos AS solicitante, u.correo, u.rol AS rol_solicitante,
           u.numero_documento, u.codigo_institucional,
           c.nombres || ' ' || c.apellidos AS creador, c.rol AS rol_creador,
           r.nombres || ' ' || r.apellidos AS revisor
    FROM reservas p
    JOIN unidades_equipo d ON d.id = p.dispositivo_id
    JOIN usuarios u ON u.id = p.usuario_id
    JOIN usuarios c ON c.id = p.creado_por
    LEFT JOIN usuarios r ON r.id = p.revisado_por
"""


def con_estado_visible(filas):
    """Agrega 'estado_visible' (p. ej. 'vencido') sin modificar la base de datos."""
    hoy = date.today().isoformat()
    resultado = []
    for f in filas:
        p = dict(f)
        p["estado_visible"] = p["estado"]
        if p["estado"] == "aprobado" and p["fecha_fin"] < hoy:
            p["estado_visible"] = "vencido"
        elif p["estado"] == "aprobado" and p["fecha_inicio"] <= hoy:
            p["estado_visible"] = "en curso"
        elif p["estado"] == "pendiente" and p["fecha_fin"] < hoy:
            p["estado_visible"] = "expirado"
        resultado.append(p)
    return resultado


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
@app.route("/")
def inicio():
    return redirect(url_for("panel" if g.user else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("panel"))
    identificador = ""
    if request.method == "POST":
        identificador = request.form.get("identificador", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM usuarios WHERE lower(usuario) = ? OR lower(correo) = ?",
            (identificador, identificador),
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            destino = request.args.get("next", "")
            if not destino.startswith("/") or destino.startswith("//"):
                destino = url_for("panel")
            return redirect(destino)
        flash("Usuario/correo o contraseña incorrectos.", "error")
    return render_template("login.html", identificador=identificador)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Sesión cerrada.", "info")
    return redirect(url_for("login"))


def validar_registro(f):
    """Valida el formulario de registro. Devuelve lista de errores."""
    errores = []
    rol = f.get("rol")
    if rol not in ("estudiante", "profesor"):
        errores.append("Selecciona si eres estudiante o profesor.")

    if f.get("tipo_documento") not in TIPOS_DOCUMENTO:
        errores.append("Selecciona un tipo de documento válido.")
    doc = f.get("numero_documento", "")
    patron_doc = r"[A-Za-z0-9]{5,15}" if f.get("tipo_documento") in ("PA", "PPT") else r"\d{6,12}"
    if not re.fullmatch(patron_doc, doc):
        errores.append("El número de documento no es válido (solo números, entre 6 y 12 dígitos).")

    letras = r"[A-Za-zÁÉÍÓÚáéíóúÑñÜü' ]{2,60}"
    if not re.fullmatch(letras, f.get("nombres", "")):
        errores.append("Los nombres solo pueden contener letras.")
    if not re.fullmatch(letras, f.get("apellidos", "")):
        errores.append("Los apellidos solo pueden contener letras.")
    if not re.fullmatch(r"3\d{9}", f.get("telefono", "")):
        errores.append("El celular debe tener 10 dígitos y empezar por 3.")

    correo = f.get("correo", "")
    if not re.fullmatch(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", correo):
        errores.append("El correo no tiene un formato válido.")
    elif not correo.endswith("@" + DOMINIO_INSTITUCIONAL):
        errores.append(f"Debes usar tu correo institucional (@{DOMINIO_INSTITUCIONAL}).")
    if correo != f.get("correo_confirmacion", ""):
        errores.append("Los correos no coinciden.")

    pwd = f.get("password", "")
    if len(pwd) < 8 or not re.search(r"[A-Za-z]", pwd) or not re.search(r"\d", pwd):
        errores.append("La contraseña debe tener mínimo 8 caracteres e incluir letras y números.")
    if pwd != f.get("password_confirmacion", ""):
        errores.append("Las contraseñas no coinciden.")

    if not re.fullmatch(r"\d{4,12}", f.get("codigo_institucional", "")):
        errores.append("El código institucional debe tener entre 4 y 12 dígitos.")
    if f.get("facultad") not in FACULTADES:
        errores.append("Selecciona una facultad.")
    if rol == "estudiante":
        if f.get("programa") not in PROGRAMAS:
            errores.append("Selecciona tu programa académico.")
        if f.get("semestre") not in [str(i) for i in range(1, 11)]:
            errores.append("Selecciona el semestre que cursas.")
        if f.get("modalidad") not in MODALIDADES:
            errores.append("Selecciona tu modalidad de estudio.")
    elif rol == "profesor":
        if f.get("vinculacion") not in VINCULACIONES:
            errores.append("Selecciona tu tipo de vinculación.")

    if not f.get("acepta_datos"):
        errores.append("Debes aceptar la política de tratamiento de datos personales.")
    return errores


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if g.user:
        return redirect(url_for("panel"))
    form = {}
    if request.method == "POST":
        form = {k: v.strip() for k, v in request.form.items() if k != "_csrf"}
        form["correo"] = form.get("correo", "").lower()
        form["correo_confirmacion"] = form.get("correo_confirmacion", "").lower()
        errores = validar_registro(form)

        conn = get_db()
        if not errores:
            if conn.execute("SELECT 1 FROM usuarios WHERE lower(correo) = ?", (form["correo"],)).fetchone():
                errores.append("Ya existe una cuenta con ese correo.")
            if conn.execute("SELECT 1 FROM usuarios WHERE numero_documento = ?",
                            (form["numero_documento"],)).fetchone():
                errores.append("Ya existe una cuenta con ese número de documento.")

        if errores:
            for e in errores:
                flash(e, "error")
        else:
            es_est = form["rol"] == "estudiante"
            conn.execute(
                """INSERT INTO usuarios (correo, password_hash, rol, tipo_documento, numero_documento,
                       nombres, apellidos, telefono, codigo_institucional, programa, semestre,
                       modalidad, facultad, vinculacion)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (form["correo"], generate_password_hash(form["password"], method="pbkdf2:sha256"),
                 form["rol"], form["tipo_documento"], form["numero_documento"],
                 form["nombres"].title(), form["apellidos"].title(), form["telefono"],
                 form["codigo_institucional"],
                 form.get("programa") if es_est else None,
                 int(form["semestre"]) if es_est else None,
                 form.get("modalidad") if es_est else None,
                 form["facultad"],
                 None if es_est else form.get("vinculacion")),
            )
            flash("Cuenta creada. Ya puedes iniciar sesión con tu correo institucional y contraseña.", "success")
            return redirect(url_for("login"))
        # nunca devolvemos las contraseñas al formulario
        form.pop("password", None)
        form.pop("password_confirmacion", None)

    return render_template(
        "registro.html", form=form, tipos_documento=TIPOS_DOCUMENTO, facultades=FACULTADES,
        programas=PROGRAMAS, modalidades=MODALIDADES, vinculaciones=VINCULACIONES,
        dominio=DOMINIO_INSTITUCIONAL,
    )


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------
@app.route("/panel")
@login_requerido
def panel():
    conn = get_db()
    hoy = date.today().isoformat()
    if es_admin():
        stats = conn.execute(
            """SELECT
                 SUM(estado = 'pendiente' AND fecha_fin >= :hoy) AS pendientes,
                 SUM(estado = 'aprobado' AND fecha_inicio <= :hoy AND fecha_fin >= :hoy) AS en_curso,
                 SUM(estado = 'aprobado' AND fecha_fin < :hoy) AS vencidos,
                 SUM(estado = 'aprobado' AND fecha_inicio > :hoy) AS proximos
               FROM reservas""", {"hoy": hoy},
        ).fetchone()
        pendientes = con_estado_visible(conn.execute(
            CONSULTA_PRESTAMOS + " WHERE p.estado = 'pendiente' ORDER BY p.fecha_inicio"
        ).fetchall())
        return render_template("panel_admin.html", stats=stats, pendientes=pendientes)

    prestamos = con_estado_visible(conn.execute(
        CONSULTA_PRESTAMOS + """ WHERE p.usuario_id = ? AND p.estado IN ('pendiente', 'aprobado')
                                ORDER BY p.fecha_inicio""", (g.user["id"],)
    ).fetchall())
    return render_template("panel_usuario.html", prestamos=prestamos,
                           max_activos=MAX_PRESTAMOS_ACTIVOS)


# ---------------------------------------------------------------------------
# Préstamos
# ---------------------------------------------------------------------------
@app.route("/prestamos/nuevo", methods=["GET", "POST"])
@login_requerido
def nuevo_prestamo():
    conn = get_db()
    form = {}
    if request.method == "POST":
        form = request.form.to_dict()
        if es_admin():
            solicitante = conn.execute(
                "SELECT * FROM usuarios WHERE id = ? AND rol != 'administrador'",
                (form.get("usuario_id"),),
            ).fetchone()
            if solicitante is None:
                flash("Selecciona el estudiante o profesor que recibirá el dispositivo.", "error")
                solicitante = None
        else:
            solicitante = g.user

        if solicitante is not None:
            ok, mensaje = crear_prestamo(
                solicitante, form.get("dispositivo_id"), form.get("fecha_inicio"),
                form.get("fecha_fin"), form.get("motivo"), g.user["id"], es_admin(),
            )
            flash(mensaje, "success" if ok else "error")
            if ok:
                return redirect(url_for("admin_prestamos" if es_admin() else "mis_prestamos"))

    dispositivos = conn.execute(
        "SELECT * FROM unidades_equipo WHERE activo = 1 ORDER BY tipo, codigo"
    ).fetchall()
    usuarios = []
    if es_admin():
        usuarios = conn.execute(
            """SELECT id, nombres, apellidos, correo, rol, numero_documento FROM usuarios
               WHERE rol != 'administrador' ORDER BY apellidos, nombres"""
        ).fetchall()
    return render_template(
        "nuevo_prestamo.html", dispositivos=dispositivos, usuarios=usuarios, form=form,
        preseleccion=request.args.get("dispositivo", type=int),
        max_fecha_inicio=(date.today() + timedelta(days=DIAS_ANTICIPACION_MAX)).isoformat(),
    )


@app.route("/mis-prestamos")
@login_requerido
def mis_prestamos():
    if es_admin():
        return redirect(url_for("admin_prestamos"))
    prestamos = con_estado_visible(get_db().execute(
        CONSULTA_PRESTAMOS + " WHERE p.usuario_id = ? ORDER BY p.creado_en DESC", (g.user["id"],)
    ).fetchall())
    return render_template("mis_prestamos.html", prestamos=prestamos)


@app.route("/prestamos/<int:prestamo_id>/cancelar", methods=["POST"])
@login_requerido
def cancelar_prestamo(prestamo_id):
    conn = get_db()
    p = conn.execute("SELECT * FROM reservas WHERE id = ?", (prestamo_id,)).fetchone()
    if p is None or (p["usuario_id"] != g.user["id"] and not es_admin()):
        abort(404)
    if p["estado"] not in ESTADOS_QUE_OCUPAN or p["fecha_inicio"] < date.today().isoformat():
        flash("Solo se pueden cancelar préstamos pendientes o aprobados que aún no han iniciado.", "error")
    else:
        conn.execute(
            "UPDATE reservas SET estado = 'cancelado', observacion = ? WHERE id = ?",
            (f"Cancelado por {g.user['nombres']} {g.user['apellidos']}.", prestamo_id),
        )
        flash("Préstamo cancelado. Las fechas quedaron liberadas.", "success")
    return redirect(request.referrer or url_for("mis_prestamos"))


@app.route("/dispositivos")
@login_requerido
def dispositivos():
    conn = get_db()
    hoy = date.today().isoformat()
    lista = []
    for d in conn.execute("SELECT * FROM unidades_equipo WHERE activo = 1 ORDER BY tipo, codigo"):
        reservas = conn.execute(
            """SELECT fecha_inicio, fecha_fin, estado FROM reservas
               WHERE dispositivo_id = ? AND estado IN ('pendiente', 'aprobado') AND fecha_fin >= ?
               ORDER BY fecha_inicio""", (d["id"], hoy),
        ).fetchall()
        ocupado_hoy = any(r["fecha_inicio"] <= hoy <= r["fecha_fin"] for r in reservas)
        lista.append({**dict(d), "reservas": reservas, "ocupado_hoy": ocupado_hoy})
    return render_template("dispositivos.html", dispositivos=lista)


@app.route("/api/dispositivos/<int:dispositivo_id>/ocupacion")
@login_requerido
def api_ocupacion(dispositivo_id):
    filas = get_db().execute(
        """SELECT fecha_inicio, fecha_fin, estado FROM reservas
           WHERE dispositivo_id = ? AND estado IN ('pendiente', 'aprobado') AND fecha_fin >= ?
           ORDER BY fecha_inicio""", (dispositivo_id, date.today().isoformat()),
    ).fetchall()
    return jsonify([
        {"inicio": f["fecha_inicio"], "fin": f["fecha_fin"], "estado": f["estado"],
         "texto": f"{formato_fecha(f['fecha_inicio'])} – {formato_fecha(f['fecha_fin'])}"}
        for f in filas
    ])


# ---------------------------------------------------------------------------
# Administración
# ---------------------------------------------------------------------------
@app.route("/admin/prestamos")
@admin_requerido
def admin_prestamos():
    estado = request.args.get("estado", "")
    q = request.args.get("q", "").strip()
    sql, params = CONSULTA_PRESTAMOS + " WHERE 1 = 1", []
    if estado in ("pendiente", "aprobado", "rechazado", "devuelto", "cancelado"):
        sql += " AND p.estado = ?"
        params.append(estado)
    elif estado == "vencido":
        sql += " AND p.estado = 'aprobado' AND p.fecha_fin < ?"
        params.append(date.today().isoformat())
    if q:
        sql += """ AND (u.nombres || ' ' || u.apellidos LIKE ? OR u.correo LIKE ?
                       OR u.numero_documento LIKE ? OR d.codigo LIKE ?)"""
        params += [f"%{q}%"] * 4
    sql += " ORDER BY p.creado_en DESC"
    prestamos = con_estado_visible(get_db().execute(sql, params).fetchall())
    return render_template("admin_prestamos.html", prestamos=prestamos, estado_filtro=estado, q=q)


def _prestamo_o_404(prestamo_id):
    p = get_db().execute("SELECT * FROM reservas WHERE id = ?", (prestamo_id,)).fetchone()
    if p is None:
        abort(404)
    return p


@app.route("/admin/prestamos/<int:prestamo_id>/aprobar", methods=["POST"])
@admin_requerido
def aprobar_prestamo(prestamo_id):
    conn = get_db()
    p = _prestamo_o_404(prestamo_id)
    if p["estado"] != "pendiente":
        flash("Solo se pueden aprobar solicitudes pendientes.", "error")
    elif p["fecha_fin"] < date.today().isoformat():
        flash("La solicitud ya expiró (su fecha de entrega pasó). Recházala.", "error")
    elif buscar_cruces(conn, p["dispositivo_id"], date.fromisoformat(p["fecha_inicio"]),
                       date.fromisoformat(p["fecha_fin"]), excluir_id=p["id"]):
        flash("No se puede aprobar: las fechas se cruzan con otro préstamo del mismo dispositivo.", "error")
    else:
        conn.execute(
            """UPDATE reservas SET estado = 'aprobado', revisado_por = ?, revisado_en = ?, observacion = ?
               WHERE id = ?""",
            (g.user["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             request.form.get("observacion", "").strip() or "Aprobado por el administrador.", prestamo_id),
        )
        flash(f"Préstamo #{prestamo_id} aprobado.", "success")
    return redirect(request.referrer or url_for("admin_prestamos"))


@app.route("/admin/prestamos/<int:prestamo_id>/rechazar", methods=["POST"])
@admin_requerido
def rechazar_prestamo(prestamo_id):
    p = _prestamo_o_404(prestamo_id)
    if p["estado"] != "pendiente":
        flash("Solo se pueden rechazar solicitudes pendientes.", "error")
    else:
        get_db().execute(
            """UPDATE reservas SET estado = 'rechazado', revisado_por = ?, revisado_en = ?, observacion = ?
               WHERE id = ?""",
            (g.user["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             request.form.get("observacion", "").strip() or "Rechazado por el administrador.", prestamo_id),
        )
        flash(f"Solicitud #{prestamo_id} rechazada. Las fechas quedaron liberadas.", "success")
    return redirect(request.referrer or url_for("admin_prestamos"))


@app.route("/admin/prestamos/<int:prestamo_id>/devolver", methods=["POST"])
@admin_requerido
def devolver_prestamo(prestamo_id):
    p = _prestamo_o_404(prestamo_id)
    if p["estado"] != "aprobado" or p["fecha_inicio"] > date.today().isoformat():
        flash("Solo se puede registrar la devolución de préstamos aprobados que ya iniciaron.", "error")
    else:
        ahora = datetime.now()
        tarde = ahora.date().isoformat() > p["fecha_fin"]
        get_db().execute(
            "UPDATE reservas SET estado = 'devuelto', devuelto_en = ?, observacion = ? WHERE id = ?",
            (ahora.strftime("%Y-%m-%d %H:%M:%S"),
             "Entregado con retraso." if tarde else "Entregado a tiempo.", prestamo_id),
        )
        flash(f"Devolución del préstamo #{prestamo_id} registrada"
              + (" (con retraso)." if tarde else "."), "success")
    return redirect(request.referrer or url_for("admin_prestamos"))


@app.route("/admin/usuarios")
@admin_requerido
def admin_usuarios():
    usuarios = get_db().execute(
        """SELECT u.*, (SELECT COUNT(*) FROM reservas p WHERE p.usuario_id = u.id) AS total_prestamos
           FROM usuarios u WHERE rol != 'administrador' ORDER BY u.creado_en DESC"""
    ).fetchall()
    return render_template("admin_usuarios.html", usuarios=usuarios, tipos_documento=TIPOS_DOCUMENTO)


@app.errorhandler(403)
def prohibido(_e):
    return render_template("error.html", codigo=403, mensaje="No tienes permiso para ver esta página."), 403


@app.errorhandler(404)
def no_encontrado(_e):
    return render_template("error.html", codigo=404, mensaje="La página que buscas no existe."), 404


@app.errorhandler(400)
def peticion_invalida(e):
    return render_template("error.html", codigo=400, mensaje=e.description), 400


if __name__ == "__main__":
    app.run(debug=True)
