# -*- coding: utf-8 -*-
"""
Vacaciones y cuadrantes de guardias 2027-2030 · una unidad = una ambulancia de una localidad (4 trabajadores A-D)

Cada trabajador entra indicando su localidad, su ambulancia y su letra. La aplicación comprueba que, con lo
ya confirmado por los 4 de esa unidad, sigue existiendo un cuadrante completo que cumpla todas las
condiciones. Cada unidad es independiente. Arranque local: run.bat  (streamlit run app.py)
"""
import datetime as dt
import hmac
import time
from pathlib import Path

import pandas as pd
import streamlit as st

import almacen as AL
import exportar
import motor
import reglas as R

st.set_page_config(page_title="Vacaciones y cuadrantes 2027-2030", page_icon="📅", layout="wide")

LETRAS = motor.TRABAJADORES
ADMIN = "Administración"
COLORES = {"A": "#BDD7EE", "B": "#C6E0B4", "C": "#FFE699", "D": "#F8CBAD"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TD = dt.timedelta
NUEVA_LOC = "➕ Mi localidad no está (escribirla)"
NUEVA_AMB = "➕ Mi ambulancia no está (escribirla)"
MAX_FALLOS, BLOQUEO_S = 5, 300


# ----------------------------------------------------------------------------- utilidades
def secreto(clave, defecto=None):
    try:
        return st.secrets[clave]
    except Exception:
        return defecto


def rango(d):
    return f"{d:%d/%m/%Y} → {d + TD(R.DIAS - 1):%d/%m/%Y}"


def mapear(styler, func, subset=None):
    metodo = styler.map if hasattr(styler, "map") else styler.applymap
    return metodo(func, subset=subset)


def colorear(v):
    if not isinstance(v, str) or not v:
        return "background-color: #d9d9d9"
    estilo = f"background-color: {COLORES.get(v[0], '#ffffff')}; color: #000000; text-align: center;"
    if "★" in v:
        estilo += " font-weight: 700; border: 2px solid #c00000;"
    return estilo


def huella(bloq):
    return hash(frozenset(bloq.items()))


@st.cache_resource(show_spinner="Conectando con la hoja de Google…")
def crear_almacen():
    info = secreto("gcp_service_account")
    hoja = secreto("sheet_id")
    if info and hoja:
        return AL.AlmacenHoja(dict(info), str(hoja))
    return AL.AlmacenArchivo(Path(__file__).resolve().parent / "datos" / "vacaciones.json")


@st.cache_resource
def _fallos():
    """Intentos fallidos de PIN por (unidad, letra) mientras la aplicación está en marcha."""
    return {}


try:
    ALM = crear_almacen()
except Exception as e:                                    # noqa: BLE001
    st.error(f"No se pudo conectar con la hoja de Google: {e}")
    st.stop()

PIN_ADMIN = secreto("admin")
if ALM.compartido and not PIN_ADMIN:
    st.error("Falta configurar «admin» en los secretos de la aplicación (ver GUIA_PUBLICAR.txt).")
    st.stop()
USA_PIN = bool(ALM.compartido or PIN_ADMIN)


def pin_bloqueado(clave):
    n, hasta = _fallos().get(clave, (0, 0.0))
    return time.time() < hasta


def pin_fallo(clave):
    n, hasta = _fallos().get(clave, (0, 0.0))
    n += 1
    if n >= MAX_FALLOS:
        _fallos()[clave] = (0, time.time() + BLOQUEO_S)
    else:
        _fallos()[clave] = (n, 0.0)


def pin_ok(clave):
    _fallos().pop(clave, None)


@st.cache_data(show_spinner=False)
def calcular(items):
    bl = {k: dt.date.fromisoformat(v) for k, v in items}
    res, lista, vac = R.calendario(bl)
    return res, lista, vac, R.auditar(bl, res, lista, vac)


def a_items(bl):
    return tuple(sorted((k, v.isoformat()) for k, v in bl.items()))


@st.cache_data(show_spinner=False)
def libro(items, anios, provisional, unidad, localidad, ambulancia):
    res, lista, vac, aud = calcular(items)
    return exportar.crear_libro(res, lista, aud, list(anios), provisional, unidad, localidad, ambulancia)


st.session_state.setdefault("msgs", {})

# un cambio de unidad hecho por la administración (renombrar, fusionar, borrar) se aplica antes de pintar el desplegable
if "adm_next" in st.session_state:
    st.session_state["adm_unidad"] = st.session_state.pop("adm_next")


# ----------------------------------------------------------------------------- acceso
def _entrar(letra, clave):
    st.session_state["usuario"] = letra
    st.session_state["unidad"] = clave
    st.rerun()


def _texto_nuevo(etiqueta, clave_w, existentes, formato, ayuda_existente):
    """Pide un nombre nuevo y lo compara con los que ya existen. Devuelve el nombre o None si falta algo."""
    txt = st.text_input(etiqueta, key=clave_w, max_chars=40)
    f = formato(txt)
    n = AL.normalizar(f)
    if not n:
        st.caption("Escríbelo y pulsa Intro.")
        return None
    if n in existentes:
        st.info(f"Ya existe «{existentes[n]}»: se usará ese nombre.")
        return existentes[n]
    cerca = [existentes[k] for k in AL.parecidas(f, existentes)]
    if cerca:
        st.warning("¿Es alguno de estos?  " + " · ".join(cerca) +
                   "  Si lo es, elígelo en la lista de arriba para no duplicarlo.")
        if not st.checkbox("No, el mío es distinto", key=clave_w + "_ok"):
            return None
    return f


def acceso_trabajador():
    unidades = ALM.unidades()
    locs = {}
    for u in unidades:
        locs.setdefault(AL.normalizar(u["localidad"]), u["localidad"])
    sel = st.selectbox("Localidad", sorted(locs.values(), key=AL.normalizar) + [NUEVA_LOC], index=None,
                       placeholder="Elige tu localidad", key="acc_loc")
    if sel is None:
        return
    if sel == NUEVA_LOC:
        loc = _texto_nuevo("Escribe tu localidad", "acc_loc_txt", locs, AL.formato_localidad, "localidad")
        if loc is None:
            return
    else:
        loc = sel
    nl = AL.normalizar(loc)
    ambs = {AL.normalizar(u["ambulancia"]): u["ambulancia"] for u in unidades if AL.normalizar(u["localidad"]) == nl}
    if ambs:
        sel_a = st.selectbox("Ambulancia", sorted(ambs.values(), key=AL.normalizar) + [NUEVA_AMB], index=None,
                             placeholder="Elige tu ambulancia", key=f"acc_amb_{nl}")
        if sel_a is None:
            return
    else:
        st.caption("Todavía no hay ambulancias en esta localidad: escribe la tuya (por ejemplo, UVI 1).")
        sel_a = NUEVA_AMB
    if sel_a == NUEVA_AMB:
        amb = _texto_nuevo("Escribe tu ambulancia (por ejemplo, UVI 1)", f"acc_amb_txt_{nl}", ambs,
                           AL.formato_ambulancia, "ambulancia")
        if amb is None:
            return
    else:
        amb = sel_a
    letra = st.selectbox("Tu letra", list(LETRAS), index=None, placeholder="A, B, C o D", key="acc_letra")
    if letra is None:
        return
    clave = AL.clave_unidad(loc, amb)
    existe = any(u["clave"] == clave for u in unidades)
    registrado = existe and ALM.pin_registrado(clave, letra)
    nombre = f"{amb} de {loc}"
    if not USA_PIN:
        if st.button("Entrar", key="entrar_local"):
            ALM.crear_unidad(loc, amb)
            _entrar(letra, clave)
        return
    if registrado:
        with st.form("acceso_pin"):
            pin = st.text_input("PIN", type="password", key=f"pin_{clave}_{letra}")
            ok = st.form_submit_button("Entrar")
        if ok:
            if pin_bloqueado((clave, letra)):
                st.error("Demasiados intentos fallidos. Espera 5 minutos o pide a la administración que restablezca tu PIN.")
            elif ALM.comprobar_pin(clave, letra, (pin or "").strip()):
                pin_ok((clave, letra))
                _entrar(letra, clave)
            else:
                pin_fallo((clave, letra))
                st.error("PIN incorrecto. Comprueba la localidad, la ambulancia y la letra.")
        return
    st.info(f"Primera vez del trabajador {letra} en {nombre}: crea tu PIN. "
            + ("" if existe else "Se creará esta unidad. ") +
            "Recuérdalo: si lo olvidas, la administración puede restablecerlo.")
    with st.form("crear_pin"):
        p1 = st.text_input("Crea un PIN (4 a 12 caracteres)", type="password", key=f"p1_{clave}_{letra}")
        p2 = st.text_input("Repite el PIN", type="password", key=f"p2_{clave}_{letra}")
        ok = st.form_submit_button("Crear PIN y entrar")
    if ok:
        p1, p2 = (p1 or "").strip(), (p2 or "").strip()
        if not 4 <= len(p1) <= 12:
            st.error("El PIN debe tener entre 4 y 12 caracteres.")
        elif p1 != p2:
            st.error("Los dos PIN no coinciden.")
        else:
            ALM.crear_unidad(loc, amb)
            if ALM.fijar_pin(clave, letra, p1):
                _entrar(letra, clave)
            else:
                st.error(f"Otra persona acaba de crear el PIN del trabajador {letra} en esta unidad. "
                         "Si no has sido tú, avisa a la administración.")


def acceso_admin():
    if not PIN_ADMIN:
        if st.button("Entrar como administración", key="adm_local"):
            _entrar(ADMIN, None)
        return
    with st.form("acceso_admin"):
        pin = st.text_input("PIN de administración", type="password")
        ok = st.form_submit_button("Entrar")
    if ok:
        if pin_bloqueado("admin"):
            st.error("Demasiados intentos fallidos. Espera 5 minutos.")
        elif hmac.compare_digest((pin or "").strip().encode(), str(PIN_ADMIN).strip().encode()):
            pin_ok("admin")
            _entrar(ADMIN, None)
        else:
            pin_fallo("admin")
            st.error("PIN incorrecto.")


with st.sidebar:
    st.header("👤 Acceso")
    usuario = st.session_state.get("usuario")
    if usuario:
        if usuario == ADMIN:
            st.success("Sesión: Administración")
        if st.button("Salir"):
            for k in ("usuario", "unidad", "adm_unidad", "prov"):
                st.session_state.pop(k, None)
            st.rerun()
        if usuario == ADMIN:
            unis = ALM.unidades()
            if unis:
                st.selectbox("Unidad con la que trabajar", [u["clave"] for u in unis], index=None, key="adm_unidad",
                             placeholder="Elige una unidad",
                             format_func=lambda c: AL.nombre_unidad(next(u for u in unis if u["clave"] == c)))
            else:
                st.caption("Todavía no hay ninguna unidad creada.")
    else:
        modo = st.radio("Entrar como", ["Trabajador", ADMIN], key="modo")
        if modo == "Trabajador":
            acceso_trabajador()
        else:
            acceso_admin()
        if not USA_PIN:
            st.caption("Modo local: sin PIN.")
    st.divider()
    st.caption(f"Datos guardados en: {ALM.nombre}")
    if st.button("🔄 Actualizar"):
        ALM.invalidar()
        st.rerun()

usuario = st.session_state.get("usuario")
UNI = st.session_state.get("adm_unidad") if usuario == ADMIN else st.session_state.get("unidad")
UNIDAD = ALM.unidad(UNI) if UNI else None
if usuario and usuario != ADMIN and UNIDAD is None:          # la administración la ha borrado o renombrado
    for k in ("usuario", "unidad"):
        st.session_state.pop(k, None)
    st.warning("Tu unidad ya no existe o ha cambiado de nombre. Vuelve a entrar.")
    st.stop()
NOMBRE_UNI = AL.nombre_unidad(UNIDAD) if UNIDAD else ""


# ----------------------------------------------------------------------------- datos de la unidad
def bloqueados():
    return AL.a_bloqueados(ALM.leer(UNI)) if UNI else {}


# ----------------------------------------------------------------------------- tramitar una elección
def tramitar(clave, fecha, origen):
    y, w, s = clave
    with st.spinner("Comprobando que el cuadrante completo sigue cuadrando…"):
        bloq = AL.a_bloqueados(ALM.leer(UNI, fresco=True))
        hu = huella(bloq)
        r = R.evaluar(clave, fecha, bloq)
        if r["estado"] == "ok":
            ALM.agregar(AL.nueva_fila(UNI, y, LETRAS[w], s, fecha, origen))
            tras = AL.a_bloqueados(ALM.leer(UNI, fresco=True))
            otros = {k: v for k, v in tras.items() if k != clave}
            if R.validar_rapido(clave, fecha, otros):
                ALM.borrar(UNI, y, LETRAS[w], s)
                msg = ("error", "Otra persona ha confirmado a la vez unas fechas que se solapan con las tuyas. "
                                "Elige de nuevo.", [], hu)
            else:
                msg = ("success", f"✅ Confirmado: {rango(fecha)}", [], hu)
        elif r["estado"] == "error":
            msg = ("error", r["motivo"], [], hu)
        else:
            alts = R.alternativas(clave, fecha, bloq)
            texto = r["motivo"] + (" Las fechas más parecidas que sí cuadran son:" if alts else
                                   " Ahora mismo no encuentro ninguna fecha alternativa para este periodo; "
                                   "prueba con otro o consulta con la administración.")
            msg = ("warning", texto, alts, hu)
    st.session_state["msgs"][f"{UNI}_{y}_{w}_{s}"] = msg
    st.rerun()


def retirar(clave):
    y, w, s = clave
    ALM.borrar(UNI, y, LETRAS[w], s)
    st.session_state["msgs"].pop(f"{UNI}_{y}_{w}_{s}", None)
    st.rerun()


def mostrar_mensaje(clave, bloq, origen):
    y, w, s = clave
    k = f"{UNI}_{y}_{w}_{s}"
    m = st.session_state["msgs"].get(k)
    if not m:
        return
    tipo, texto, alts, hu = m
    if tipo == "warning" and hu != huella(bloq):
        st.session_state["msgs"].pop(k, None)           # lo confirmado ha cambiado: la sugerencia caducó
        return
    getattr(st, tipo)(texto)
    for d in alts:
        if st.button(f"Elegir {rango(d)}", key=f"alt_{k}_{d.isoformat()}"):
            tramitar(clave, d, origen)


# ----------------------------------------------------------------------------- pestañas
def pestana_mis_vacaciones():
    if usuario == ADMIN:
        w = LETRAS.index(st.selectbox("Elegir en nombre de", list(LETRAS)))
        origen = "admin"
    else:
        w = LETRAS.index(usuario)
        origen = usuario
    bloq = bloqueados()
    st.markdown(f"## Vacaciones del trabajador {LETRAS[w]} · {NOMBRE_UNI}")
    st.caption("Lo que confirmes no lo cambia la elección de otro. Antes de confirmar, la aplicación comprueba que, "
               "con lo ya confirmado por los 4 de tu ambulancia, sigue existiendo un cuadrante completo que cumpla "
               "las condiciones.")

    # ---- verano
    st.markdown("### ☀️ Verano: 15 días en julio y agosto")
    st.caption("Eliges solo tu quincena de 2027; cada año pasas automáticamente a la quincena siguiente.")
    ck = (R.ANIO0, w, "v")
    if ck in bloq:
        st.success(f"✅ 2027: {rango(bloq[ck])} ({R.nombre_quincena(bloq[ck])})")
        if len([1 for k in bloq if k[2] == "v"]) == 4:
            comp = R.completos(bloq)
            tabla = {str(y): rango(comp[(y, w, "v")]) for y in R.ANIOS}
            st.dataframe(pd.DataFrame({"Año": list(tabla), "Tus 15 días de verano": list(tabla.values())}),
                         hide_index=True)
        else:
            st.caption("Tus quincenas de 2028-2030 se conocerán cuando los 4 trabajadores hayan elegido la de 2027.")
        if st.button("Retirar mi verano", key="ret_verano"):
            retirar(ck)
    else:
        otros = [f"{LETRAS[k[1]]}: {bloq[k]:%d/%m}–{bloq[k] + TD(R.DIAS - 1):%d/%m}"
                 for k in sorted(bloq) if k[2] == "v"]
        if otros:
            st.caption("Ya elegidas por otros: " + " · ".join(otros))
        d = st.selectbox("Inicio de tu quincena de 2027", R.VERANO_OK, key="in_verano",
                         format_func=lambda d: f"{d:%d/%m} → {d + TD(R.DIAS - 1):%d/%m}  ({R.nombre_quincena(d)})")
        if st.button("Comprobar y confirmar verano", key="ok_verano"):
            tramitar(ck, d, origen)
    mostrar_mensaje(ck, bloq, origen)

    # ---- invierno
    st.markdown("### ❄️ Invierno: 15 días cada año")
    st.caption("Ventanas: " + R.TXT_VENT_INVIERNO + ". Diciembre y la 1.ª quincena de enero no valen, "
               "y tampoco la quincena en la que caen el Jueves y el Viernes Santo.")
    for y in R.ANIOS:
        ck = (y, w, "i")
        with st.expander(f"Invierno {y}", expanded=(y == R.ANIO0 or ck not in bloq)):
            if ck in bloq:
                st.success(f"✅ {rango(bloq[ck])}")
                if st.button(f"Retirar invierno {y}", key=f"ret_{y}"):
                    retirar(ck)
            else:
                otros = [f"{LETRAS[k[1]]}: {bloq[k]:%d/%m}–{bloq[k] + TD(R.DIAS - 1):%d/%m}"
                         for k in sorted(bloq) if k[2] == "i" and k[0] == y]
                if otros:
                    st.caption("Ya elegidas por otros este año: " + " · ".join(otros))
                d = st.date_input("Primer día de tus vacaciones", value=dt.date(y, 2, 1),
                                  min_value=dt.date(y, 1, 16), max_value=dt.date(y, 11, 16),
                                  format="DD/MM/YYYY", key=f"in_{y}")
                if d is None:
                    st.info("Elige la fecha de inicio de tus vacaciones.")
                else:
                    st.caption(f"Terminarían el {d + TD(R.DIAS - 1):%d/%m/%Y}.")
                    if st.button("Comprobar y confirmar", key=f"ok_{y}"):
                        tramitar(ck, d, origen)
            mostrar_mensaje(ck, bloq, origen)


def avisar_invalidas(bloq):
    """Avisa de elecciones ya guardadas que no cumplen las reglas actuales."""
    for k, m in R.invalidas(bloq):
        st.warning(f"⚠️ {LETRAS[k[1]]} · {'verano' if k[2] == 'v' else 'invierno'} {k[0]} "
                   f"({rango(bloq[k])}) ya no cumple las reglas: {m} Hay que retirarla y volver a elegir.")


def pestana_estado():
    bloq = bloqueados()
    v, i = R.progreso(bloq)
    total = 4 + 16
    st.progress((v + i) / total, text=f"{NOMBRE_UNI}: {v + i} de {total} elecciones confirmadas "
                                      f"(verano {v}/4 · invierno {i}/16)")
    reg = ALM.letras_con_pin(UNI) if USA_PIN else set(LETRAS)
    st.caption("Trabajadores que ya han entrado: " + " · ".join(f"{L} {'✅' if L in reg else '⏳'}" for L in LETRAS))
    comp = R.completos(bloq)
    st.markdown("**☀️ Verano**")
    filas_v = []
    for y in R.ANIOS:
        fila = {"Año": y}
        for w, L in enumerate(LETRAS):
            k = (y, w, "v")
            fila[L] = (f"{comp[k]:%d/%m}–{comp[k] + TD(R.DIAS - 1):%d/%m}" if k in comp
                       else ("⏳ pendiente" if y == R.ANIO0 else "—"))
        filas_v.append(fila)
    st.dataframe(pd.DataFrame(filas_v), hide_index=True)
    if v < 4:
        st.caption("Las quincenas de 2028-2030 se calculan solas cuando los 4 han elegido la de 2027.")
    st.markdown("**❄️ Invierno**")
    filas_i = []
    for y in R.ANIOS:
        fila = {"Año": y}
        for w, L in enumerate(LETRAS):
            k = (y, w, "i")
            fila[L] = f"{bloq[k]:%d/%m}–{bloq[k] + TD(R.DIAS - 1):%d/%m}" if k in bloq else "⏳ pendiente"
        filas_i.append(fila)
    st.dataframe(pd.DataFrame(filas_i), hide_index=True)
    avisar_invalidas(bloq)
    falta = R.faltan(bloq)
    if falta:
        por = {L: sum(1 for k in falta if k[1] == w) for w, L in enumerate(LETRAS)}
        st.caption("Pendientes por trabajador: " + " · ".join(f"{L}: {n}" for L, n in por.items()))
    else:
        st.success("Todas las vacaciones están confirmadas.")


def cuadrante_actual(permitir_calcular=True):
    """Devuelve (bloques completos, definitivo?) o (None, False)."""
    bloq = bloqueados()
    if not R.faltan(bloq):
        return R.completos(bloq), True
    prov = st.session_state.get("prov")
    if prov and prov[0] == (UNI, huella(bloq)):
        return prov[1], False
    return None, False


def mostrar_cuadrante(bl, definitivo):
    items = a_items(bl)
    res, lista, vac, aud = calcular(items)
    if definitivo:
        if all(a["ok"] for a in aud):
            st.success("✅ Cuadrante definitivo: cumple todas las condiciones.")
        else:
            st.error("❌ Hay condiciones que no se cumplen: " + "; ".join(a["nombre"] for a in aud if not a["ok"]))
    else:
        st.warning("⚠️ PROVISIONAL: completado automáticamente con lo que falta por elegir. "
                   "Cambiará cuando los trabajadores confirmen sus fechas.")
    t1, t2, t3, t4 = st.tabs(["Calendario", "Vacaciones", "Festivos", "Auditoría"])
    with t1:
        c1, c2 = st.columns([1, 2])
        anio = c1.selectbox("Año", motor.ANIOS)
        vista = c2.radio("Vista", ["Anual (días × meses)", "Mensual (detalle)"], horizontal=True)
        cuenta = motor.guardias_por_anio(res)[anio]
        cols = st.columns(4)
        for w, L in enumerate(LETRAS):
            cols[w].metric(f"Guardias de {L} en {anio}", cuenta[w])
        if vista.startswith("Anual"):
            letras, fest = motor.matriz_anual(res, anio)
            datos = [[(letras[d][m] + ("★" if fest[d][m] else "")) if letras[d][m] else ""
                      for m in range(12)] for d in range(31)]
            df = pd.DataFrame(datos, index=range(1, 32), columns=[m.capitalize() for m in motor.MESES])
            df.index.name = "Día"
            st.dataframe(mapear(df.style, colorear), height=1130)
            st.caption("★ = día señalado que rota (24, 25 y 31 dic, 1 ene, Jueves y Viernes Santo).")
        else:
            mes = st.selectbox("Mes", list(range(1, 13)),
                               format_func=lambda m: motor.MESES_LARGOS[m - 1].capitalize())
            nombres = {f: n for y in motor.ANIOS for n, f in motor.festivos(y).items()}
            filas_m = []
            d = dt.date(anio, mes, 1)
            while d.month == mes:
                i = (d - motor.FECHA_INICIO).days
                if 0 <= i < motor.N_DIAS:
                    filas_m.append({"Fecha": d.strftime("%d/%m/%Y"), "Día": motor.DIAS_SEMANA[d.weekday()],
                                    "Trabaja": LETRAS[res[i]],
                                    "De vacaciones": ", ".join(LETRAS[w] for w in vac[i]) or "—",
                                    "Día señalado": nombres.get(d, "")})
                d += TD(1)
            st.dataframe(mapear(pd.DataFrame(filas_m).style, colorear, subset=["Trabaja"]),
                         hide_index=True, height=1130)
    with t2:
        for tipo, icono in (("Verano", "☀️"), ("Invierno", "❄️")):
            filas_t = []
            for y in motor.ANIOS:
                fila = {"Año": y}
                for w, L in enumerate(LETRAS):
                    tr = [f"{b['inicio']:%d/%m}–{b['fin']:%d/%m}" for b in lista
                          if b["trabajador"] == w and b["tipo"] == tipo and b["inicio"].year == y]
                    fila[L] = " y ".join(tr) if tr else "—"
                filas_t.append(fila)
            st.markdown(f"{icono} **{tipo}**")
            st.dataframe(pd.DataFrame(filas_t), hide_index=True)
    with t3:
        filas_f = []
        for nombre in motor.NOMBRES_FESTIVOS:
            fila = {"Día señalado": nombre}
            usados = set()
            for y in motor.ANIOS:
                f = motor.festivos(y)[nombre]
                w = res[(f - motor.FECHA_INICIO).days]
                usados.add(w)
                fila[str(y)] = f"{LETRAS[w]} · {f:%d/%m}"
            fila["Rotación"] = "✅ los 4 distintos" if len(usados) == 4 else "❌ se repite"
            filas_f.append(fila)
        st.dataframe(mapear(pd.DataFrame(filas_f).style, colorear, subset=[str(y) for y in motor.ANIOS]),
                     hide_index=True)
    with t4:
        for a in aud:
            st.markdown(f"{'✅' if a['ok'] else '❌'} **{a['nombre']}** — {a['detalle']}")


def pestana_cuadrante():
    bloq = bloqueados()
    falta = R.faltan(bloq)
    if falta:
        st.info(f"Faltan {len(falta)} elecciones por confirmar. El cuadrante definitivo aparecerá cuando estén todas.")
        if st.button("Ver una propuesta provisional (se completa sola lo que falta)"):
            with st.spinner("Buscando un cuadrante completo que respete lo ya confirmado…"):
                comp = R.completar(bloq, presupuesto=10)
            st.session_state["prov"] = ((UNI, huella(bloq)), comp) if comp else None
            if not comp:
                st.error("No he encontrado ahora un cuadrante completo con lo confirmado. Avisa a la administración.")
    bl, definitivo = cuadrante_actual()
    if bl is None:
        return
    st.markdown(f"### {NOMBRE_UNI}")
    mostrar_cuadrante(bl, definitivo)


def pestana_excel():
    bl, definitivo = cuadrante_actual()
    if bl is None:
        st.info("Cuando estén confirmadas todas las vacaciones podrás descargar aquí el Excel definitivo. "
                "Mientras tanto, en la pestaña «Cuadrante» puedes generar una propuesta provisional "
                "y descargarla desde aquí.")
        return
    if not definitivo:
        st.warning("El Excel será PROVISIONAL: lleva marcada esa palabra en cada hoja.")
    items = a_items(bl)
    sufijo = "" if definitivo else "_PROVISIONAL"
    base = AL.slug(NOMBRE_UNI)
    extra = (NOMBRE_UNI, UNIDAD["localidad"], UNIDAD["ambulancia"])
    st.markdown(f"Cuadrante de **{NOMBRE_UNI}**: una hoja por año (días × meses con colores), más portada, "
                "vacaciones, festivos, resumen y auditoría.")
    st.download_button("⬇️ Excel completo 2027-2030", data=libro(items, tuple(motor.ANIOS), not definitivo, *extra),
                       file_name=f"cuadrante_{base}_2027-2030{sufijo}.xlsx", mime=XLSX)
    cols = st.columns(4)
    for j, y in enumerate(motor.ANIOS):
        cols[j].download_button(f"⬇️ Año {y}", data=libro(items, (y,), not definitivo, *extra),
                                file_name=f"cuadrante_{base}_{y}{sufijo}.xlsx", mime=XLSX, key=f"dl_{y}")


def pestana_admin():
    bloq = bloqueados()
    fl = ALM.leer(UNI)
    st.markdown(f"**Unidad:** {NOMBRE_UNI}  ·  **Almacenamiento:** {ALM.nombre}")
    st.markdown("**Elecciones confirmadas**")
    if fl:
        df = pd.DataFrame(fl).rename(columns={"anio": "Año", "trabajador": "Trabajador", "estacion": "Estación",
                                              "inicio": "Inicio", "origen": "Confirmó", "fecha": "Cuándo"})
        df = df.drop(columns=["unidad"], errors="ignore")
        df["Estación"] = df["Estación"].map({"v": "Verano", "i": "Invierno"})
        st.dataframe(df.sort_values(["Año", "Trabajador", "Estación"]), hide_index=True)
    else:
        st.caption("Todavía no hay nada confirmado en esta unidad.")

    st.markdown("**Retirar una elección**")
    if bloq:
        claves = sorted(bloq, key=lambda k: (k[0], k[1], k[2]))
        k = st.selectbox("Elección", claves, format_func=lambda k: (
            f"{LETRAS[k[1]]} · {'Verano' if k[2] == 'v' else 'Invierno'} {k[0]} · {rango(bloq[k])}"))
        if st.button("Retirar esta elección"):
            retirar(k)
    avisar_invalidas(bloq)
    st.markdown("**Completar automáticamente lo que falta**")
    falta = R.faltan(bloq)
    st.caption(f"Faltan {len(falta)} elecciones. Se rellenan con un cuadrante válido que respeta todo lo confirmado.")
    if falta and st.button("Completar lo que falta"):
        with st.spinner("Buscando un cuadrante completo…"):
            comp = R.completar(bloq, presupuesto=20)
        if comp:
            for (y, w, s) in falta:
                ALM.agregar(AL.nueva_fila(UNI, y, LETRAS[w], s, comp[(y, w, s)], "auto"))
            st.success("Hecho: se han añadido las elecciones que faltaban.")
            st.rerun()
        else:
            st.error("No se ha encontrado ningún cuadrante completo con lo confirmado.")
    st.markdown("**Comprobar que todo lo confirmado sigue cuadrando**")
    if st.button("Comprobar ahora"):
        with st.spinner("Comprobando…"):
            malas = R.invalidas(bloq)
            ok = (not malas) and R.completar(bloq, presupuesto=10) is not None
        if malas:
            st.error("Hay elecciones que incumplen las reglas (arriba): retíralas y vuelve a elegir.")
        else:
            (st.success if ok else st.error)("Todo cuadra." if ok else "No se encuentra un cuadrante completo.")
    st.markdown("**Vaciar las elecciones de esta unidad**")
    st.caption("Borra solo las vacaciones confirmadas de esta unidad. No borra la unidad ni los PIN.")
    if st.checkbox("Entiendo que se borrarán todas las elecciones de esta unidad", key="vaciar_ok") \
            and st.button("Vaciar esta unidad"):
        ALM.borrar_elecciones(UNI)
        st.session_state["msgs"].clear()
        st.session_state.pop("prov", None)
        st.rerun()


def pestana_unidades():
    unis = ALM.unidades()
    st.markdown(f"**Unidades creadas: {len(unis)}**  ·  Almacenamiento: {ALM.nombre}")
    if not unis:
        st.info("Todavía no hay ninguna unidad. Se crean solas cuando un trabajador entra por primera vez "
                "indicando su localidad y su ambulancia.")
        return
    vac = ALM.leer_todas()
    n_vac, n_pin = {}, {}
    for f in vac:
        n_vac[f["unidad"]] = n_vac.get(f["unidad"], 0) + 1
    for u in unis:
        n_pin[u["clave"]] = len(ALM.letras_con_pin(u["clave"]))
    df = pd.DataFrame([{"Localidad": u["localidad"], "Ambulancia": u["ambulancia"],
                        "Trabajadores registrados": f"{n_pin[u['clave']]}/4",
                        "Elecciones confirmadas": f"{n_vac.get(u['clave'], 0)}/20",
                        "Creada": u["creada"]} for u in unis])
    filtro = st.text_input("Buscar", key="uni_filtro", placeholder="Localidad o ambulancia")
    if filtro:
        n = AL.normalizar(filtro)
        df = df[[n in AL.normalizar(f"{r['Localidad']} {r['Ambulancia']}") for _, r in df.iterrows()]]
    st.dataframe(df, hide_index=True)
    por_clave = {u["clave"]: u for u in unis}
    nom = lambda c: AL.nombre_unidad(por_clave[c])           # noqa: E731
    claves = [u["clave"] for u in unis]

    resumen = pd.DataFrame([{"Localidad": por_clave[f["unidad"]]["localidad"] if f["unidad"] in por_clave else f["unidad"],
                             "Ambulancia": por_clave[f["unidad"]]["ambulancia"] if f["unidad"] in por_clave else "",
                             "Año": f["anio"], "Trabajador": f["trabajador"],
                             "Estación": {"v": "Verano", "i": "Invierno"}.get(f["estacion"], f["estacion"]),
                             "Inicio": f["inicio"], "Confirmó": f["origen"], "Cuándo": f["fecha"]} for f in vac])
    if len(resumen):
        st.download_button("⬇️ Copia de seguridad de todas las unidades (CSV)",
                           data=resumen.sort_values(["Localidad", "Ambulancia", "Año", "Trabajador"])
                           .to_csv(index=False).encode("utf-8-sig"),
                           file_name="vacaciones_todas_las_unidades.csv", mime="text/csv")

    with st.expander("🔑 Restablecer el PIN de un trabajador"):
        c = st.selectbox("Unidad", claves, format_func=nom, key="pin_uni", index=None, placeholder="Elige una unidad")
        L = st.selectbox("Letra", list(LETRAS), key="pin_let")
        if c and st.button("Restablecer PIN"):
            ALM.restablecer_pin(c, L)
            st.success(f"Hecho. {L} de {nom(c)} elegirá un PIN nuevo la próxima vez que entre.")
    with st.expander("✏️ Corregir el nombre de una unidad"):
        c = st.selectbox("Unidad", claves, format_func=nom, key="ren_uni", index=None, placeholder="Elige una unidad")
        if c:
            nl = st.text_input("Localidad", value=por_clave[c]["localidad"], key=f"ren_l_{c}", max_chars=40)
            na = st.text_input("Ambulancia", value=por_clave[c]["ambulancia"], key=f"ren_a_{c}", max_chars=40)
            if st.button("Guardar nombre"):
                ok, msg, nueva = ALM.renombrar_unidad(c, nl, na)
                if ok:
                    if st.session_state.get("adm_unidad") == c:
                        st.session_state["adm_next"] = nueva
                    st.session_state.pop("ren_uni", None)
                    st.rerun()
                else:
                    st.error(msg)
    with st.expander("🔀 Fusionar dos unidades duplicadas"):
        st.caption("Pasa todas las elecciones y los PIN de la primera a la segunda y borra la primera.")
        o = st.selectbox("Unidad a eliminar (el duplicado)", claves, format_func=nom, key="fus_o", index=None,
                         placeholder="Elige una unidad")
        d = st.selectbox("Unidad que se queda", claves, format_func=nom, key="fus_d", index=None,
                         placeholder="Elige una unidad")
        if o and d and st.checkbox("Confirmo la fusión", key="fus_ok") and st.button("Fusionar"):
            ok, msg = ALM.fusionar_unidades(o, d)
            if ok:
                if st.session_state.get("adm_unidad") == o:
                    st.session_state["adm_next"] = d
                for k in ("fus_o", "fus_d", "fus_ok"):
                    st.session_state.pop(k, None)
                st.rerun()
            else:
                st.error(msg)
    with st.expander("🗑️ Borrar una unidad entera"):
        c = st.selectbox("Unidad", claves, format_func=nom, key="del_uni", index=None, placeholder="Elige una unidad")
        if c and st.checkbox("Entiendo que se borran sus elecciones y sus PIN", key="del_ok") and st.button("Borrar unidad"):
            ALM.borrar_unidad(c)
            if st.session_state.get("adm_unidad") == c:
                st.session_state["adm_next"] = None
            for k in ("del_uni", "del_ok"):
                st.session_state.pop(k, None)
            st.session_state["msgs"].clear()
            st.rerun()


# ----------------------------------------------------------------------------- página
st.title("📅 Vacaciones y cuadrantes de guardias 2027-2030")
st.caption("4 trabajadores (A, B, C, D) por ambulancia · guardias de 24 h · del 01/01/2027 al 31/12/2030"
           + (f"  ·  **{NOMBRE_UNI}**" if NOMBRE_UNI else ""))
with st.expander("ℹ️ Cómo funciona", expanded=not usuario):
    st.markdown(
        "- **Entrada:** elige tu localidad, tu ambulancia (por ejemplo, UVI 1) y tu letra. Si tu ambulancia no está en "
        "la lista, escríbela y se crea. La primera vez creas tu PIN; después entras con él.\n"
        "- **Cada ambulancia tiene su propio cuadrante**, con sus 4 trabajadores y sus elecciones.\n"
        "- **Verano** (julio y agosto): cada trabajador elige su quincena de 2027 y rota solo a la siguiente cada año.\n"
        "- **Invierno** (16 ene-30 jun y 1 sep-30 nov, sin la quincena del Jueves y Viernes Santo): cada trabajador "
        "elige 15 días para cada año. Diciembre no entra en ninguna vacación.\n"
        "- Solo uno de vacaciones a la vez. Fuera de vacaciones: 24 h de trabajo y 3 días libres; con alguien de "
        "vacaciones, 24 h y 2 días libres.\n"
        "- Navidad (24, 25 y 31 dic y 1 ene) y Semana Santa (Jueves y Viernes Santo) rotan entre los 4 trabajadores.\n"
        "- Si tu fecha no deja un cuadrante válido, la aplicación te propone las dos más parecidas que sí lo dejan. "
        "Lo que ya ha confirmado otro trabajador no se mueve; por eso, quien confirma antes tiene más margen."
    )

if not usuario:
    st.info("👈 Entra desde la barra lateral (en el móvil, el icono « > » de arriba a la izquierda).")
    st.stop()

if UNI:
    pestanas = ["✍️ Mis vacaciones", "📋 Estado", "📅 Cuadrante", "📥 Excel"]
    if usuario == ADMIN:
        pestanas += ["🛠️ Administración", "🏢 Unidades"]
else:
    pestanas = ["🏢 Unidades"]
    st.info("Elige una unidad en la barra lateral para ver o gestionar su cuadrante, o usa la pestaña «Unidades».")
tabs = st.tabs(pestanas)
if UNI:
    with tabs[0]:
        pestana_mis_vacaciones()
    with tabs[1]:
        pestana_estado()
    with tabs[2]:
        pestana_cuadrante()
    with tabs[3]:
        pestana_excel()
if usuario == ADMIN:
    if UNI:
        with tabs[4]:
            pestana_admin()
    with tabs[-1]:
        pestana_unidades()
