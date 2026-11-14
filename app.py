# -*- coding: utf-8 -*-
"""
Vacaciones y cuadrantes de guardias 2027-2030 · 4 trabajadores (A, B, C, D)

Cada trabajador elige sus vacaciones y la aplicación comprueba que, junto con lo ya confirmado,
sigue existiendo un cuadrante completo que cumpla todas las condiciones. Lo confirmado por un
trabajador nunca lo mueve la elección de otro. Arranque local: run.bat  (streamlit run app.py)
"""
import datetime as dt
import hmac
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
    return AL.AlmacenArchivo(Path(__file__).resolve().parent / "datos" / "elecciones.json")


try:
    ALM = crear_almacen()
except Exception as e:                                    # noqa: BLE001
    st.error(f"No se pudo conectar con la hoja de Google: {e}")
    st.stop()

PINES = secreto("pines")
PIN_ADMIN = secreto("admin")
if ALM.compartido and (not PINES or not PIN_ADMIN):
    st.error("Falta configurar «pines» y «admin» en los secretos de la aplicación (ver GUIA_PUBLICAR.txt).")
    st.stop()


@st.cache_data(ttl=8, show_spinner=False)
def _leer_cache(_alm, version):
    return _alm.leer()


def filas():
    return _leer_cache(ALM, st.session_state.get("v", 0)) if ALM.compartido else ALM.leer()


def bloqueados():
    return AL.a_bloqueados(filas())


@st.cache_data(show_spinner=False)
def calcular(items):
    bl = {k: dt.date.fromisoformat(v) for k, v in items}
    res, lista, vac = R.calendario(bl)
    return res, lista, vac, R.auditar(bl, res, lista, vac)


def a_items(bl):
    return tuple(sorted((k, v.isoformat()) for k, v in bl.items()))


@st.cache_data(show_spinner=False)
def libro(items, anios, provisional):
    res, lista, vac, aud = calcular(items)
    return exportar.crear_libro(res, lista, aud, list(anios), provisional)


st.session_state.setdefault("msgs", {})
st.session_state.setdefault("v", 0)

# ----------------------------------------------------------------------------- acceso
with st.sidebar:
    st.header("👤 Acceso")
    usuario = st.session_state.get("usuario")
    if usuario:
        st.success(f"Sesión: {'Administración' if usuario == ADMIN else 'Trabajador ' + usuario}")
        if st.button("Salir"):
            st.session_state.pop("usuario", None)
            st.rerun()
    else:
        quien = st.selectbox("¿Quién eres?", list(LETRAS) + [ADMIN])
        esperado = (str(PIN_ADMIN) if PIN_ADMIN else None) if quien == ADMIN else \
            (str(PINES[quien]) if PINES and quien in PINES else None)
        necesita = esperado is not None
        pin = st.text_input("PIN", type="password") if necesita else ""
        if st.button("Entrar"):
            if st.session_state.get("intentos", 0) >= 5:
                st.error("Demasiados intentos fallidos. Cierra y vuelve a abrir la página.")
            elif (not necesita) or hmac.compare_digest(pin, esperado):
                st.session_state["usuario"] = quien
                st.session_state["intentos"] = 0
                st.rerun()
            else:
                st.session_state["intentos"] = st.session_state.get("intentos", 0) + 1
                st.error("PIN incorrecto.")
        if not necesita:
            st.caption("Modo local: sin PIN.")
    st.divider()
    st.caption(f"Datos guardados en: {ALM.nombre}")
    if st.button("🔄 Actualizar"):
        st.session_state["v"] += 1
        st.rerun()
usuario = st.session_state.get("usuario")

# ----------------------------------------------------------------------------- tramitar una elección
def tramitar(clave, fecha, origen):
    y, w, s = clave
    with st.spinner("Comprobando que el cuadrante completo sigue cuadrando…"):
        bloq = AL.a_bloqueados(ALM.leer())
        hu = huella(bloq)
        r = R.evaluar(clave, fecha, bloq)
        if r["estado"] == "ok":
            ALM.agregar(AL.nueva_fila(y, LETRAS[w], s, fecha, origen))
            tras = AL.a_bloqueados(ALM.leer())
            otros = {k: v for k, v in tras.items() if k != clave}
            if R.validar_rapido(clave, fecha, otros):
                ALM.borrar(y, LETRAS[w], s)
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
    st.session_state["msgs"][f"{y}_{w}_{s}"] = msg
    st.session_state["v"] += 1
    st.rerun()


def retirar(clave):
    y, w, s = clave
    ALM.borrar(y, LETRAS[w], s)
    st.session_state["msgs"].pop(f"{y}_{w}_{s}", None)
    st.session_state["v"] += 1
    st.rerun()


def mostrar_mensaje(clave, bloq, origen):
    y, w, s = clave
    k = f"{y}_{w}_{s}"
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
    if not usuario:
        st.info("Entra desde la barra lateral con tu letra (y tu PIN, si se ha configurado) para elegir tus vacaciones.")
        return
    if usuario == ADMIN:
        w = LETRAS.index(st.selectbox("Elegir en nombre de", list(LETRAS)))
        origen = "admin"
    else:
        w = LETRAS.index(usuario)
        origen = usuario
    bloq = bloqueados()
    st.markdown(f"## Vacaciones del trabajador {LETRAS[w]}")
    st.caption("Lo que confirmes no lo cambia la elección de otro. Antes de confirmar, la aplicación comprueba que, "
               "con lo ya confirmado por todos, sigue existiendo un cuadrante completo que cumpla las condiciones.")

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
    st.caption("Ventanas: del 16 de enero al 31 de mayo y del 1 de octubre al 30 de noviembre. "
               "No pueden cubrir el Jueves ni el Viernes Santo.")
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


def pestana_estado():
    bloq = bloqueados()
    v, i = R.progreso(bloq)
    total = 4 + 16
    st.progress((v + i) / total, text=f"{v + i} de {total} elecciones confirmadas "
                                      f"(verano {v}/4 · invierno {i}/16)")
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
    if prov and prov[0] == huella(bloq):
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
            st.session_state["prov"] = (huella(bloq), comp) if comp else None
            if not comp:
                st.error("No he encontrado ahora un cuadrante completo con lo confirmado. Avisa a la administración.")
    bl, definitivo = cuadrante_actual()
    if bl is None:
        return
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
    st.markdown("Una hoja por año (días × meses con colores), más vacaciones, festivos, resumen y auditoría.")
    st.download_button("⬇️ Excel completo 2027-2030", data=libro(items, tuple(motor.ANIOS), not definitivo),
                       file_name=f"cuadrante_2027-2030{sufijo}.xlsx", mime=XLSX)
    cols = st.columns(4)
    for j, y in enumerate(motor.ANIOS):
        cols[j].download_button(f"⬇️ Año {y}", data=libro(items, (y,), not definitivo),
                                file_name=f"cuadrante_{y}{sufijo}.xlsx", mime=XLSX, key=f"dl_{y}")


def pestana_admin():
    if usuario != ADMIN:
        st.info("Esta sección es solo para la administración.")
        return
    bloq = bloqueados()
    fl = filas()
    st.markdown(f"**Almacenamiento:** {ALM.nombre}")
    st.markdown("**Elecciones confirmadas**")
    if fl:
        df = pd.DataFrame(fl).rename(columns={"anio": "Año", "trabajador": "Trabajador", "estacion": "Estación",
                                              "inicio": "Inicio", "origen": "Confirmó", "fecha": "Cuándo"})
        df["Estación"] = df["Estación"].map({"v": "Verano", "i": "Invierno"})
        st.dataframe(df.sort_values(["Año", "Trabajador", "Estación"]), hide_index=True)
        st.download_button("⬇️ Copia de seguridad (CSV)", data=df.to_csv(index=False).encode("utf-8-sig"),
                           file_name="elecciones_vacaciones.csv", mime="text/csv")
    else:
        st.caption("Todavía no hay nada confirmado.")

    st.markdown("**Retirar una elección**")
    if bloq:
        claves = sorted(bloq, key=lambda k: (k[0], k[1], k[2]))
        k = st.selectbox("Elección", claves, format_func=lambda k: (
            f"{LETRAS[k[1]]} · {'Verano' if k[2] == 'v' else 'Invierno'} {k[0]} · {rango(bloq[k])}"))
        if st.button("Retirar esta elección"):
            retirar(k)
    st.markdown("**Completar automáticamente lo que falta**")
    falta = R.faltan(bloq)
    st.caption(f"Faltan {len(falta)} elecciones. Se rellenan con un cuadrante válido que respeta todo lo confirmado.")
    if falta and st.button("Completar lo que falta"):
        with st.spinner("Buscando un cuadrante completo…"):
            comp = R.completar(bloq, presupuesto=20)
        if comp:
            for (y, w, s) in falta:
                ALM.agregar(AL.nueva_fila(y, LETRAS[w], s, comp[(y, w, s)], "auto"))
            st.session_state["v"] += 1
            st.success("Hecho: se han añadido las elecciones que faltaban.")
            st.rerun()
        else:
            st.error("No se ha encontrado ningún cuadrante completo con lo confirmado.")
    st.markdown("**Comprobar que todo lo confirmado sigue cuadrando**")
    if st.button("Comprobar ahora"):
        with st.spinner("Comprobando…"):
            ok = R.completar(bloq, presupuesto=10) is not None
        (st.success if ok else st.error)("Todo cuadra." if ok else "No se encuentra un cuadrante completo.")
    st.markdown("**Borrar todo**")
    if st.checkbox("Entiendo que se borrarán todas las elecciones confirmadas") and st.button("Borrar todo"):
        ALM.borrar_todo()
        st.session_state["msgs"].clear()
        st.session_state.pop("prov", None)
        st.session_state["v"] += 1
        st.rerun()


# ----------------------------------------------------------------------------- página
st.title("📅 Vacaciones y cuadrantes de guardias 2027-2030")
st.caption("4 trabajadores (A, B, C, D) · guardias de 24 h · del 01/01/2027 al 31/12/2030")
with st.expander("ℹ️ Cómo funciona"):
    st.markdown(
        "- **Verano** (julio y agosto): cada trabajador elige su quincena de 2027 y rota solo a la siguiente cada año.\n"
        "- **Invierno** (16 ene-31 may y 1 oct-30 nov): cada trabajador elige 15 días para cada año.\n"
        "- Solo uno de vacaciones a la vez. Fuera de vacaciones: 24 h de trabajo y 3 días libres; con alguien de "
        "vacaciones, 24 h y 2 días libres.\n"
        "- Navidad (24, 25 y 31 dic y 1 ene) y Semana Santa (Jueves y Viernes Santo) rotan entre los 4 trabajadores.\n"
        "- Si tu fecha no deja un cuadrante válido, la aplicación te propone las dos más parecidas que sí lo dejan. "
        "Lo que ya ha confirmado otro trabajador no se mueve; por eso, quien confirma antes tiene más margen."
    )

pestanas = ["✍️ Mis vacaciones", "📋 Estado", "📅 Cuadrante", "📥 Excel"] + (["🛠️ Administración"] if usuario == ADMIN else [])
tabs = st.tabs(pestanas)
with tabs[0]:
    pestana_mis_vacaciones()
with tabs[1]:
    pestana_estado()
with tabs[2]:
    pestana_cuadrante()
with tabs[3]:
    pestana_excel()
if usuario == ADMIN:
    with tabs[4]:
        pestana_admin()
