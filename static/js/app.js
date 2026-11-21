// Comportamiento del lado del cliente (las validaciones definitivas se hacen en el servidor).

// ---------- Registro: campos según rol y confirmación de correo/contraseña ----------
(function () {
  const form = document.getElementById("form-registro");
  if (!form) return;

  function actualizarRol() {
    const rol = form.querySelector("input[name=rol]:checked").value;
    form.querySelectorAll("[data-rol]").forEach((el) => {
      const visible = el.dataset.rol === rol;
      el.hidden = !visible;
      el.querySelectorAll("select, input").forEach((c) => (c.required = visible));
    });
    form.querySelectorAll("[data-rol-texto]").forEach((el) => (el.hidden = el.dataset.rolTexto !== rol));
  }
  form.querySelectorAll("input[name=rol]").forEach((r) => r.addEventListener("change", actualizarRol));
  actualizarRol();

  form.querySelectorAll("[data-igual-a]").forEach((confirmacion) => {
    const original = form.querySelector(`[name=${confirmacion.dataset.igualA}]`);
    const ayuda = confirmacion.parentElement.querySelector(".ayuda-error");
    function comparar() {
      const esCorreo = confirmacion.type === "email";
      const a = esCorreo ? original.value.trim().toLowerCase() : original.value;
      const b = esCorreo ? confirmacion.value.trim().toLowerCase() : confirmacion.value;
      const malo = b !== "" && a !== b;
      confirmacion.classList.toggle("no-coincide", malo);
      ayuda.hidden = !malo;
    }
    confirmacion.addEventListener("input", comparar);
    original.addEventListener("input", comparar);
    // Evita pegar en el campo de confirmación para que realmente se escriba dos veces
    confirmacion.addEventListener("paste", (e) => e.preventDefault());
  });

  form.addEventListener("submit", (e) => {
    if (form.querySelector(".no-coincide")) {
      e.preventDefault();
      form.querySelector(".no-coincide").focus();
    }
  });
})();

// ---------- Nuevo préstamo: ocupación del dispositivo y límites de fecha ----------
(function () {
  const form = document.getElementById("form-prestamo");
  if (!form) return;

  const selDisp = document.getElementById("dispositivo_id");
  const selUsuario = document.getElementById("usuario_id");
  const inicio = document.getElementById("fecha_inicio");
  const fin = document.getElementById("fecha_fin");
  const ayudaFin = document.getElementById("ayuda-fin");
  const lista = document.getElementById("ocupacion");
  const ayudaOcupacion = document.getElementById("ocupacion-ayuda");
  const avisoAut = document.getElementById("aviso-autorizacion");
  const avisoCruce = document.getElementById("aviso-cruce");
  let reservas = [];

  const sumarDias = (iso, n) => {
    const d = new Date(iso + "T00:00:00");
    d.setDate(d.getDate() + n);
    return d.toISOString().slice(0, 10);
  };

  function diasMax() {
    const opt = selUsuario && selUsuario.selectedOptions[0];
    return parseInt((opt && opt.dataset.diasMax) || form.dataset.diasMax, 10);
  }

  function actualizarLimites() {
    const max = diasMax();
    ayudaFin.textContent = `Máximo ${max} días después de la recogida.`;
    if (inicio.value) {
      fin.min = inicio.value;
      fin.max = sumarDias(inicio.value, max);
      if (!fin.value || fin.value < fin.min || fin.value > fin.max) fin.value = fin.max;
    }
    revisarCruce();
  }

  function revisarCruce() {
    if (!inicio.value || !fin.value) { avisoCruce.hidden = true; return; }
    const choque = reservas.find((r) => r.inicio <= fin.value && r.fin >= inicio.value);
    avisoCruce.hidden = !choque;
    if (choque) avisoCruce.textContent = `Esas fechas se cruzan con una reserva existente (${choque.texto}).`;
  }

  async function cargarOcupacion() {
    const opt = selDisp.selectedOptions[0];
    avisoAut.hidden = !(opt && opt.dataset.autorizacion === "1") || !!selUsuario;
    lista.innerHTML = "";
    reservas = [];
    if (!selDisp.value) {
      ayudaOcupacion.textContent = "Selecciona un dispositivo para ver sus reservas.";
      revisarCruce();
      return;
    }
    const url = form.dataset.api.replace("/0/", `/${selDisp.value}/`);
    const resp = await fetch(url);
    reservas = await resp.json();
    ayudaOcupacion.textContent = reservas.length
      ? "Estas fechas ya están reservadas para el equipo seleccionado:"
      : "Este equipo no tiene reservas próximas. ¡Todas las fechas están libres!";
    reservas.forEach((r) => {
      const li = document.createElement("li");
      li.className = r.estado;
      li.textContent = r.texto + (r.estado === "pendiente" ? " · pendiente de aprobación" : "");
      lista.appendChild(li);
    });
    revisarCruce();
  }

  selDisp.addEventListener("change", cargarOcupacion);
  if (selUsuario) selUsuario.addEventListener("change", actualizarLimites);
  inicio.addEventListener("change", actualizarLimites);
  fin.addEventListener("change", revisarCruce);
  if (selDisp.value) cargarOcupacion();
  if (inicio.value) { fin.min = inicio.value; fin.max = sumarDias(inicio.value, diasMax()); }
})();
