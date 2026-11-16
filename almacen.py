# -*- coding: utf-8 -*-
"""
almacen.py
Dónde se guardan las unidades (ambulancia + localidad), las vacaciones confirmadas y los PIN.

* AlmacenArchivo: un archivo JSON local (datos/vacaciones.json). Es el modo por defecto.
* AlmacenHoja:    una hoja de cálculo de Google con 3 pestañas: «unidades», «vacaciones» y «pines».
                  Se activa cuando existen los secretos «gcp_service_account» y «sheet_id».

Tablas
    unidades   : clave, localidad, ambulancia, creada
    vacaciones : unidad, anio, trabajador (A-D), estacion ('v' verano | 'i' invierno), inicio, origen, fecha
    pines      : unidad, trabajador, sal, hash, fecha        (el PIN nunca se guarda en claro)
En verano solo se guarda la elección de 2027; los demás años rotan solos.
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import unicodedata
from pathlib import Path

LETRAS = "ABCD"
T_UNI, T_VAC, T_PIN = "unidades", "vacaciones", "pines"
COLS = {
    T_UNI: ["clave", "localidad", "ambulancia", "creada"],
    T_VAC: ["unidad", "anio", "trabajador", "estacion", "inicio", "origen", "fecha"],
    T_PIN: ["unidad", "trabajador", "sal", "hash", "fecha"],
}
_LOCK = threading.RLock()
TTL = 8.0            # segundos que se reutiliza una lectura de la hoja de Google


# --------------------------------------------------------------------------- nombres de unidades
def normalizar(txt):
    """Forma comparable de un nombre: sin tildes, sin mayúsculas, sin signos. 'UVI-1' == 'uvi 1' == 'Uvi1'."""
    t = str(txt or "").replace("º", "").replace("ª", "")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)
    t = re.sub(r"\b0+(\d)", r"\1", t)
    return " ".join(t.split())


def limpiar_texto(txt, maximo=40):
    """Quita caracteres raros (también los que podrían interpretarse como fórmula) y recorta."""
    t = re.sub(r"[^\w\s.\-'ºª()/]", "", str(txt or ""), flags=re.UNICODE).replace("_", " ")
    t = " ".join(t.split())
    return t.lstrip("-./' ")[:maximo].strip()


_PEQ = {"de", "del", "la", "las", "los", "el", "y", "en"}


def formato_localidad(txt):
    t = limpiar_texto(txt)
    if t and (t.islower() or t.isupper()):
        pal = t.lower().split(" ")
        t = " ".join(p if (i and p in _PEQ) else p[:1].upper() + p[1:] for i, p in enumerate(pal))
    return t


def formato_ambulancia(txt):
    t = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", limpiar_texto(txt))
    out = []
    for p in t.split(" "):
        if p.isalpha():
            p = p.upper() if len(p) <= 4 else p.capitalize()
        out.append(p)
    return " ".join(out)


def clave_unidad(localidad, ambulancia):
    return f"{normalizar(localidad)}|{normalizar(ambulancia)}"


def nombre_unidad(u):
    """'UVI 1 de Martos'."""
    return f"{u['ambulancia']} de {u['localidad']}"


def slug(txt):
    t = unicodedata.normalize("NFKD", str(txt))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_") or "unidad"


def parecidas(texto, existentes, n=3, corte=0.8):
    """Nombres ya existentes (en forma normalizada) que se parecen al texto, sin ser iguales."""
    t = normalizar(texto)
    num = re.findall(r"\d+", t)
    # «UVI 1» y «UVI 2» son ambulancias distintas: si los números difieren, no se consideran parecidas
    return [e for e in difflib.get_close_matches(t, list(existentes), n=n, cutoff=corte)
            if e != t and re.findall(r"\d+", e) == num]


# --------------------------------------------------------------------------- PIN
def _hash_pin(pin, sal_hex):
    return hashlib.pbkdf2_hmac("sha256", str(pin).encode("utf-8"), bytes.fromhex(sal_hex), 120_000).hex()


def _ahora():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- filas
def nueva_fila(unidad, anio, trabajador, estacion, inicio, origen):
    return {
        "unidad": unidad,
        "anio": int(anio),
        "trabajador": trabajador,
        "estacion": estacion,
        "inicio": inicio.isoformat() if hasattr(inicio, "isoformat") else str(inicio),
        "origen": origen,
        "fecha": _ahora(),
    }


def a_bloqueados(filas):
    """Filas guardadas (de UNA unidad) -> {(año, índice_trabajador, estación): fecha_inicio}."""
    out = {}
    for f in filas:
        try:
            out[(int(f["anio"]), LETRAS.index(f["trabajador"]), f["estacion"])] = dt.date.fromisoformat(f["inicio"])
        except (KeyError, ValueError):
            continue
    return out


def _tipar_vac(f):
    try:
        return {**f, "anio": int(f["anio"])}
    except (KeyError, ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- lógica común
class AlmacenBase:
    compartido = False
    nombre = ""

    # --- primitivas que implementa cada almacén
    def _leer(self, tabla, fresco=False):
        raise NotImplementedError

    def _anadir(self, tabla, fila):
        raise NotImplementedError

    def _borrar_donde(self, tabla, pred):
        raise NotImplementedError

    def _reemplazar(self, tabla, filas):
        raise NotImplementedError

    def invalidar(self):
        pass

    # --- unidades
    def unidades(self, fresco=False):
        out = [f for f in self._leer(T_UNI, fresco) if f.get("clave")]
        return sorted(out, key=lambda u: (normalizar(u["localidad"]), normalizar(u["ambulancia"])))

    def unidad(self, clave, fresco=False):
        return next((u for u in self.unidades(fresco) if u["clave"] == clave), None)

    def crear_unidad(self, localidad, ambulancia):
        """Crea la unidad si no existe y la devuelve (si ya existía, devuelve la existente)."""
        loc, amb = formato_localidad(localidad), formato_ambulancia(ambulancia)
        clave = clave_unidad(loc, amb)
        with _LOCK:
            ya = self.unidad(clave, fresco=True)
            if ya:
                return ya
            fila = {"clave": clave, "localidad": loc, "ambulancia": amb, "creada": _ahora()}
            self._anadir(T_UNI, fila)
            return fila

    def renombrar_unidad(self, clave, localidad, ambulancia):
        loc, amb = formato_localidad(localidad), formato_ambulancia(ambulancia)
        nueva = clave_unidad(loc, amb)
        if not normalizar(loc) or not normalizar(amb):
            return False, "Escribe la localidad y la ambulancia.", clave
        with _LOCK:
            unis = self._leer(T_UNI, True)
            if nueva != clave and any(u["clave"] == nueva for u in unis):
                return False, "Ya existe una unidad con ese nombre. Usa «Fusionar» si es la misma.", clave
            for u in unis:
                if u["clave"] == clave:
                    u.update({"clave": nueva, "localidad": loc, "ambulancia": amb})
            self._reemplazar(T_UNI, unis)
            if nueva != clave:
                for t in (T_VAC, T_PIN):
                    filas = self._leer(t, True)
                    for f in filas:
                        if f["unidad"] == clave:
                            f["unidad"] = nueva
                    self._reemplazar(t, filas)
        return True, "Hecho.", nueva

    def fusionar_unidades(self, origen, destino):
        """Pasa todo lo de «origen» a «destino» y borra «origen». Si hay elecciones que chocan, no hace nada."""
        if origen == destino:
            return False, "Elige dos unidades distintas."
        with _LOCK:
            vac = self._leer(T_VAC, True)
            en_dest = {(str(f["anio"]), f["trabajador"], f["estacion"]) for f in vac if f["unidad"] == destino}
            choques = sorted({f"{f['trabajador']} {'verano' if f['estacion'] == 'v' else 'invierno'} {f['anio']}"
                              for f in vac if f["unidad"] == origen
                              and (str(f["anio"]), f["trabajador"], f["estacion"]) in en_dest})
            if choques:
                return False, ("Ambas unidades tienen elegido lo mismo (" + ", ".join(choques) +
                               "). Retira antes esas elecciones en una de las dos.")
            for f in vac:
                if f["unidad"] == origen:
                    f["unidad"] = destino
            self._reemplazar(T_VAC, vac)
            pin = self._leer(T_PIN, True)
            en_dest = {f["trabajador"] for f in pin if f["unidad"] == destino}
            pin = [({**f, "unidad": destino} if f["unidad"] == origen else f) for f in pin
                   if not (f["unidad"] == origen and f["trabajador"] in en_dest)]
            self._reemplazar(T_PIN, pin)
            self._borrar_donde(T_UNI, lambda f: f["clave"] == origen)
        return True, "Unidades fusionadas. Conviene pulsar «Comprobar ahora» en la unidad resultante."

    def borrar_unidad(self, clave):
        with _LOCK:
            for t in (T_VAC, T_PIN):
                self._borrar_donde(t, lambda f: f["unidad"] == clave)
            self._borrar_donde(T_UNI, lambda f: f["clave"] == clave)

    # --- vacaciones
    def leer(self, unidad, fresco=False):
        out = []
        for f in self._leer(T_VAC, fresco):
            if f.get("unidad") == unidad:
                g = _tipar_vac(f)
                if g:
                    out.append(g)
        return out

    def leer_todas(self, fresco=False):
        return [g for g in (_tipar_vac(f) for f in self._leer(T_VAC, fresco)) if g and g.get("unidad")]

    def agregar(self, fila):
        self._anadir(T_VAC, fila)

    def borrar(self, unidad, anio, trabajador, estacion):
        self._borrar_donde(T_VAC, lambda f: f["unidad"] == unidad and str(f["anio"]).strip() == str(int(anio))
                           and f["trabajador"] == trabajador and f["estacion"] == estacion)

    def borrar_elecciones(self, unidad):
        self._borrar_donde(T_VAC, lambda f: f["unidad"] == unidad)

    # --- PIN
    def letras_con_pin(self, unidad, fresco=False):
        return {f["trabajador"] for f in self._leer(T_PIN, fresco) if f.get("unidad") == unidad}

    def pin_registrado(self, unidad, trabajador, fresco=False):
        return trabajador in self.letras_con_pin(unidad, fresco)

    def fijar_pin(self, unidad, trabajador, pin):
        """Guarda el PIN la primera vez. Devuelve False si alguien lo había fijado antes."""
        with _LOCK:
            if self.pin_registrado(unidad, trabajador, fresco=True):
                return False
            sal = secrets.token_hex(16)
            self._anadir(T_PIN, {"unidad": unidad, "trabajador": trabajador, "sal": sal,
                                 "hash": _hash_pin(pin, sal), "fecha": _ahora()})
            mias = [f for f in self._leer(T_PIN, True) if f["unidad"] == unidad and f["trabajador"] == trabajador]
            if len(mias) > 1 and mias[0]["sal"] != sal:         # otra instancia llegó antes
                self._borrar_donde(T_PIN, lambda f: f["unidad"] == unidad and f["trabajador"] == trabajador
                                   and f["sal"] == sal)
                return False
        return True

    def comprobar_pin(self, unidad, trabajador, pin):
        for f in self._leer(T_PIN):
            if f.get("unidad") == unidad and f.get("trabajador") == trabajador:
                return hmac.compare_digest(_hash_pin(pin, f["sal"]), str(f["hash"]))
        return False

    def restablecer_pin(self, unidad, trabajador):
        self._borrar_donde(T_PIN, lambda f: f["unidad"] == unidad and f["trabajador"] == trabajador)

    def borrar_todo(self):
        with _LOCK:
            for t in (T_VAC, T_PIN, T_UNI):
                self._reemplazar(t, [])


# --------------------------------------------------------------------------- archivo local
class AlmacenArchivo(AlmacenBase):
    nombre = "archivo local (datos/vacaciones.json)"
    compartido = False

    def __init__(self, ruta):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)

    def _datos(self):
        if not self.ruta.exists():
            return {t: [] for t in COLS}
        try:
            d = json.loads(self.ruta.read_text(encoding="utf-8"))
            return {t: (d.get(t) if isinstance(d.get(t), list) else []) for t in COLS}
        except (json.JSONDecodeError, OSError, AttributeError):
            return {t: [] for t in COLS}

    def _escribir(self, d):
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.ruta)

    def _leer(self, tabla, fresco=False):
        with _LOCK:
            return [dict(f) for f in self._datos()[tabla]]

    def _anadir(self, tabla, fila):
        with _LOCK:
            d = self._datos()
            d[tabla].append({c: fila.get(c, "") for c in COLS[tabla]})
            self._escribir(d)

    def _borrar_donde(self, tabla, pred):
        with _LOCK:
            d = self._datos()
            d[tabla] = [f for f in d[tabla] if not pred(f)]
            self._escribir(d)

    def _reemplazar(self, tabla, filas):
        with _LOCK:
            d = self._datos()
            d[tabla] = [{c: f.get(c, "") for c in COLS[tabla]} for f in filas]
            self._escribir(d)


# --------------------------------------------------------------------------- Google Sheets
class AlmacenHoja(AlmacenBase):
    nombre = "hoja de cálculo de Google"
    compartido = True

    def __init__(self, info_cuenta, hoja_id):
        import gspread          # se importa aquí para no exigirlo en el modo local

        self._gs = gspread
        cliente = gspread.service_account_from_dict(dict(info_cuenta))
        libro = cliente.open_by_key(hoja_id)
        self.ws = {}
        for t, cols in COLS.items():
            try:
                w = libro.worksheet(t)
            except gspread.WorksheetNotFound:
                w = libro.add_worksheet(title=t, rows=500, cols=len(cols))
            if not self._reint(w.get_all_values):
                self._reint(w.append_row, cols, value_input_option="RAW")
            self.ws[t] = w
        self._cache = {}

    def _reint(self, fn, *a, **k):
        """Reintenta unas veces si Google responde con exceso de peticiones o un fallo pasajero."""
        for i in range(4):
            try:
                return fn(*a, **k)
            except Exception as e:                                        # noqa: BLE001
                estado = getattr(getattr(e, "response", None), "status_code", None)
                if i == 3 or not (estado in (429, 500, 502, 503) or e.__class__.__name__ == "APIError"
                                  and estado is None):
                    raise
                time.sleep(1.5 * 2 ** i)

    def invalidar(self):
        self._cache = {}

    def _leer(self, tabla, fresco=False):
        c = self._cache.get(tabla)
        if c and not fresco and time.time() - c[0] < TTL:
            return [dict(f) for f in c[1]]
        vals = self._reint(self.ws[tabla].get_all_values)
        cols = COLS[tabla]
        filas = []
        for v in vals[1:]:
            if not v or not str(v[0]).strip():
                continue
            v = list(v) + [""] * (len(cols) - len(v))
            filas.append(dict(zip(cols, v[:len(cols)])))
        self._cache[tabla] = (time.time(), filas)
        return [dict(f) for f in filas]

    def _anadir(self, tabla, fila):
        self._reint(self.ws[tabla].append_row, [fila.get(c, "") for c in COLS[tabla]], value_input_option="RAW")
        self._cache.pop(tabla, None)

    def _borrar_donde(self, tabla, pred):
        vals = self._reint(self.ws[tabla].get_all_values)
        cols = COLS[tabla]
        idx = []
        for i, v in enumerate(vals):
            if i == 0 or not v or not str(v[0]).strip():
                continue
            v = list(v) + [""] * (len(cols) - len(v))
            if pred(dict(zip(cols, v[:len(cols)]))):
                idx.append(i + 1)
        for i in reversed(idx):
            self._reint(self.ws[tabla].delete_rows, i)
        self._cache.pop(tabla, None)

    def _reemplazar(self, tabla, filas):
        cols = COLS[tabla]
        w = self.ws[tabla]
        self._reint(w.clear)
        datos = [cols] + [[f.get(c, "") for c in cols] for f in filas]
        self._reint(w.resize, rows=max(len(datos) + 50, 100))
        self._reint(w.update, range_name="A1", values=datos, value_input_option="RAW")
        self._cache.pop(tabla, None)
