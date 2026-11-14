# -*- coding: utf-8 -*-
"""
almacen.py
Dónde se guardan las vacaciones confirmadas.

* AlmacenArchivo: un archivo JSON local (datos/elecciones.json). Es el modo por defecto.
* AlmacenHoja:    una hoja de cálculo de Google (pestaña «elecciones»). Se activa cuando existen los
                  secretos «gcp_service_account» y «sheet_id» (ver GUIA_PUBLICAR.txt).

Cada fila guardada es una vacación confirmada:
    anio, trabajador (A-D), estacion ('v' verano | 'i' invierno), inicio (AAAA-MM-DD), origen, fecha
En verano solo se guarda la elección de 2027; los demás años rotan solos.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path

COLUMNAS = ["anio", "trabajador", "estacion", "inicio", "origen", "fecha"]
LETRAS = "ABCD"
_LOCK = threading.Lock()


def nueva_fila(anio, trabajador, estacion, inicio, origen):
    return {
        "anio": int(anio),
        "trabajador": trabajador,
        "estacion": estacion,
        "inicio": inicio.isoformat() if hasattr(inicio, "isoformat") else str(inicio),
        "origen": origen,
        "fecha": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def a_bloqueados(filas):
    """Filas guardadas -> {(año, índice_trabajador, estación): fecha_inicio}."""
    out = {}
    for f in filas:
        try:
            out[(int(f["anio"]), LETRAS.index(f["trabajador"]), f["estacion"])] = dt.date.fromisoformat(f["inicio"])
        except (KeyError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------- archivo local
class AlmacenArchivo:
    nombre = "archivo local (datos/elecciones.json)"
    compartido = False

    def __init__(self, ruta):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)

    def _leer(self):
        if not self.ruta.exists():
            return []
        try:
            datos = json.loads(self.ruta.read_text(encoding="utf-8"))
            return datos if isinstance(datos, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _escribir(self, filas):
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.ruta)

    def leer(self):
        with _LOCK:
            return self._leer()

    def agregar(self, fila):
        with _LOCK:
            filas = self._leer()
            filas.append(fila)
            self._escribir(filas)

    def borrar(self, anio, trabajador, estacion):
        with _LOCK:
            filas = [f for f in self._leer()
                     if not (int(f["anio"]) == int(anio) and f["trabajador"] == trabajador
                             and f["estacion"] == estacion)]
            self._escribir(filas)

    def borrar_todo(self):
        with _LOCK:
            self._escribir([])


# --------------------------------------------------------------------------- Google Sheets
class AlmacenHoja:
    nombre = "hoja de cálculo de Google"
    compartido = True

    def __init__(self, info_cuenta, hoja_id, nombre_pestana="elecciones"):
        import gspread          # se importa aquí para no exigirlo en el modo local

        cliente = gspread.service_account_from_dict(dict(info_cuenta))
        libro = cliente.open_by_key(hoja_id)
        try:
            self.ws = libro.worksheet(nombre_pestana)
        except gspread.WorksheetNotFound:
            self.ws = libro.add_worksheet(title=nombre_pestana, rows=200, cols=len(COLUMNAS))
        if not self.ws.get_all_values():
            self.ws.append_row(COLUMNAS, value_input_option="RAW")

    def leer(self):
        valores = self.ws.get_all_values()
        filas = []
        for v in valores[1:]:
            if len(v) < 4 or not str(v[0]).strip():
                continue
            v = list(v) + [""] * (len(COLUMNAS) - len(v))
            try:
                filas.append({"anio": int(v[0]), "trabajador": v[1], "estacion": v[2],
                              "inicio": v[3], "origen": v[4], "fecha": v[5]})
            except ValueError:
                continue
        return filas

    def agregar(self, fila):
        self.ws.append_row([fila[c] for c in COLUMNAS], value_input_option="RAW")

    def borrar(self, anio, trabajador, estacion):
        valores = self.ws.get_all_values()
        indices = [i + 1 for i, v in enumerate(valores)
                   if i > 0 and len(v) >= 3 and str(v[0]).strip() == str(int(anio))
                   and v[1] == trabajador and v[2] == estacion]
        for i in reversed(indices):
            self.ws.delete_rows(i)

    def borrar_todo(self):
        self.ws.clear()
        self.ws.append_row(COLUMNAS, value_input_option="RAW")
