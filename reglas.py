# -*- coding: utf-8 -*-
"""
reglas.py
Reglas de las vacaciones, buscador de cuadrantes completos y comprobación de elecciones.

Modelo
------
* Verano (1 jul - 31 ago): bloques de 15 días. Cada trabajador elige SOLO su quincena de 2027;
  en los años siguientes pasa solo a la quincena siguiente (rotación cíclica en orden cronológico).
  Las 4 quincenas ocupan 60 de los 62 días: solo hay 2 días de holgura en total.
* Invierno (16 ene - 30 jun y 1 sep - 30 nov): un bloque de 15 días por trabajador y año,
  que elige cada trabajador para cada año. Diciembre y la 1.ª quincena de enero quedan fuera, y
  también la quincena natural (1-15 o 16-fin de mes) en la que caen el Jueves y el Viernes Santo.
* Solo uno de vacaciones a la vez y ninguna vacación sobre los 6 días señalados.
* Un cuadrante es válido cuando se cumplen además todas las condiciones de motor.auditar.

Una elección es aceptable si, junto con todo lo ya confirmado, existe al menos un cuadrante
completo válido. El buscador es aleatorio: si encuentra uno, es válido (se comprueba); si no lo
encuentra en el tiempo dado, se considera que no cuadra.
"""
from __future__ import annotations

import datetime as dt
import functools
import itertools
import random
import time

import motor

TD = dt.timedelta
ANIOS = motor.ANIOS
ANIO0 = ANIOS[0]
TRAB = motor.TRABAJADORES
DIAS = motor.DIAS_VAC                     # 15
EST = {"v": "Verano", "i": "Invierno"}

VENT_VERANO = (((7, 1), (8, 31)),)
VENT_INVIERNO = (((1, 16), (6, 30)), ((9, 1), (11, 30)))
TXT_VENT_INVIERNO = "del 16 de enero al 30 de junio, o del 1 de septiembre al 30 de noviembre"

FEST = {y: list(motor.festivos(y).values()) for y in ANIOS}


def quincena_natural(d):
    """Quincena natural de una fecha: del 1 al 15 o del 16 al fin de mes -> (inicio, fin)."""
    if d.day <= 15:
        return d.replace(day=1), d.replace(day=15)
    ultimo = (d.replace(day=28) + TD(4)).replace(day=1) - TD(1)
    return d.replace(day=16), ultimo


def quincenas_semana_santa(y):
    """Quincenas naturales en las que caen el Jueves y el Viernes Santo de un año (1 o 2)."""
    f = motor.festivos(y)
    return sorted({quincena_natural(f["Jueves Santo"]), quincena_natural(f["Viernes Santo"])})


QUINCENAS_SS = {y: quincenas_semana_santa(y) for y in ANIOS}


def _txt_quincenas_ss(y):
    return " y ".join(f"{a:%d/%m}–{b:%d/%m}" for a, b in QUINCENAS_SS[y])


def _toca_semana_santa(y, st):
    """True si el bloque de 15 días que empieza en st toca la quincena de Jueves/Viernes Santo."""
    fin = st + TD(DIAS - 1)
    return any(st <= b and a <= fin for a, b in QUINCENAS_SS[y])


def _starts(y, ventanas):
    """Fechas de inicio posibles de un bloque de 15 días que termina dentro de alguna ventana."""
    out = []
    for (m1, d1), (m2, d2) in ventanas:
        d = dt.date(y, m1, d1)
        fin = dt.date(y, m2, d2)
        while d + TD(DIAS - 1) <= fin:
            out.append(d)
            d += TD(1)
    return out


def _cubre_festivo(y, st):
    span = {st + TD(i) for i in range(DIAS)}
    return any(f in span for f in FEST[y])


INVIERNO_VENTANA = {y: _starts(y, VENT_INVIERNO) for y in ANIOS}
INVIERNO_OK = {y: [s for s in INVIERNO_VENTANA[y]
                   if not _cubre_festivo(y, s) and not _toca_semana_santa(y, s)] for y in ANIOS}


# --------------------------------------------------------------------------- verano
@functools.lru_cache(maxsize=None)
def layouts_verano():
    """Todas las distribuciones posibles de las 4 quincenas de 2027: lista de {trabajador: inicio}."""
    out = []
    for perm in itertools.permutations(range(4)):
        for sl in itertools.product(range(3), repeat=5):
            if sum(sl) != 2:
                continue
            st = dt.date(ANIO0, 7, 1)
            d = {}
            for k, w in enumerate(perm):
                st += TD(sl[k])
                d[w] = st
                st += TD(DIAS)
            out.append(d)
    return out


VERANO_OK = sorted({s for d in layouts_verano() for s in d.values()})     # 12 fechas posibles


def verano_todos(d2027):
    """{w: inicio 2027} completo -> {(año, w, 'v'): inicio} con la rotación cíclica año a año."""
    S = sorted(d2027.values())
    out = {}
    for w, st in d2027.items():
        k = S.index(st)
        for i, y in enumerate(ANIOS):
            s = S[(k + i) % 4]
            out[(y, w, "v")] = dt.date(y, s.month, s.day)
    return out


def nombre_quincena(st):
    """Nombre orientativo de la quincena de verano según su fecha de inicio (2027)."""
    S = sorted(VERANO_OK)
    cerca = min(range(4), key=lambda k: abs((st - [dt.date(ANIO0, 7, 1), dt.date(ANIO0, 7, 16),
                                                  dt.date(ANIO0, 8, 1), dt.date(ANIO0, 8, 16)][k]).days))
    return ["1.ª quincena de julio", "2.ª quincena de julio",
            "1.ª quincena de agosto", "2.ª quincena de agosto"][cerca]


# --------------------------------------------------------------------------- validación rápida
def _fmt(d):
    return f"{d:%d/%m/%Y}"


def validar_rapido(clave, inicio, bloqueados):
    """
    Comprobaciones inmediatas de una elección. clave = (año, trabajador, 'v'|'i').
    bloqueados: dict de lo ya confirmado, SIN incluir la propia clave.
    Devuelve un texto con el motivo si no es válida, o None.
    """
    y, w, s = clave
    if s == "v":
        if y != ANIO0:
            return f"El verano solo se elige para {ANIO0}; en los años siguientes rota solo."
        if inicio not in VERANO_OK:
            return ("En verano las 4 quincenas ocupan 60 de los 62 días de julio y agosto, así que solo "
                    "valen estas fechas de inicio: " + ", ".join(f"{d:%d/%m}" for d in VERANO_OK) + ".")
        for (y2, w2, s2), st in bloqueados.items():
            if s2 == "v" and abs((st - inicio).days) < DIAS:
                return (f"Se solapa con la quincena de verano de {TRAB[w2]} "
                        f"({st:%d/%m}–{st + TD(DIAS - 1):%d/%m}).")
        return None
    if inicio.year != y:
        return f"La fecha debe estar en {y}."
    if inicio not in INVIERNO_VENTANA[y]:
        return ("El bloque de 15 días debe quedar dentro de las ventanas de invierno: "
                + TXT_VENT_INVIERNO + ".")
    if inicio not in INVIERNO_OK[y]:
        return (f"Esas fechas tocan la quincena del Jueves y el Viernes Santo de {y} "
                f"({_txt_quincenas_ss(y)}), que no puede entrar en ninguna vacación.")
    for (y2, w2, s2), st in bloqueados.items():
        if s2 == "i" and y2 == y and abs((st - inicio).days) < DIAS:
            return (f"Se solapa con las vacaciones de invierno de {TRAB[w2]} "
                    f"({st:%d/%m}–{st + TD(DIAS - 1):%d/%m}).")
    return None


# --------------------------------------------------------------------------- buscador
def _sample_invierno(y, locked_i, rnd):
    res = dict(locked_i)
    free = [w for w in range(4) if w not in res]
    if not free:
        return res
    ok = [s for s in INVIERNO_OK[y] if all(abs((s - t).days) >= DIAS for t in locked_i.values())]
    if len(ok) < len(free):
        return None
    for _ in range(40):
        pick = rnd.sample(ok, len(free))
        if all(abs((pick[i] - pick[j]).days) >= DIAS for i in range(len(pick)) for j in range(i)):
            rnd.shuffle(free)
            for w, s in zip(free, pick):
                res[w] = s
            return res
    return None


def _check_year(y, blocks, ptr, last, usedF):
    """Simula un año. blocks = {(w, 'v'|'i'): inicio}. Devuelve (puntero_final, last, usedF) o None."""
    j1 = dt.date(y, 1, 1)
    n = 366 if y % 4 == 0 else 365
    off = (j1 - motor.FECHA_INICIO).days
    mask = [0] * n
    for (w, s), st in blocks.items():
        k0 = (st - j1).days
        for k in range(k0, k0 + DIAS):
            if mask[k]:
                return None
            mask[k] = w + 1
    fi = [(f - j1).days for f in FEST[y]]
    if any(mask[k] for k in fi):
        return None
    res = [0] * n
    prev = ptr
    last = list(last)
    for i in range(n):
        c = (prev + 1) & 3
        if c == mask[i] - 1:
            c = (c + 1) & 3
        res[i] = prev = c
        a = off + i
        if last[c] is not None:
            g = a - last[c]
            if g != 3 and g != 4:
                lo = max(last[c] + 1 - off, 0)
                if not any(mask[k] == c + 1 for k in range(lo, i)):
                    return None
        last[c] = a
    if y == ANIO0 and res[0] != motor.PRIMER_TRABAJADOR:
        return None
    for k in range(6):
        if res[fi[k]] in usedF[k]:
            return None
    newF = [set(s) for s in usedF]
    for k in range(6):
        newF[k].add(res[fi[k]])
    return res[-1], last, newF


_VACIO = tuple(frozenset() for _ in range(6))


def _last_desde_ptr(y, ptr):
    """Último día de guardia de cada trabajador al empezar el año y (diciembre sin vacaciones)."""
    off = (dt.date(y, 1, 1) - motor.FECHA_INICIO).days
    last = [None] * 4
    for j in range(4):
        last[(ptr - j) % 4] = off - 1 - j
    return last


def _resultados_anio(y, verano_y, locked_i, ptr, rnd, N):
    """
    Resultados posibles de un año si empieza con el puntero ptr:
    {(trabajador en cada uno de los 6 días señalados, puntero final): bloques del año}.
    Con todo fijado hay uno solo; con un bloque libre se prueban todas sus fechas; con más, N muestras.
    """
    last = [None] * 4 if y == ANIO0 else _last_desde_ptr(y, ptr)
    free = [w for w in range(4) if w not in locked_i]
    out = {}

    def probar(inv):
        blocks = {(w, "v"): verano_y[w] for w in range(4)}
        blocks.update({(w, "i"): st for w, st in inv.items()})
        r = _check_year(y, blocks, ptr, last, [set() for _ in range(6)])
        if r:
            out.setdefault((tuple(next(iter(s)) for s in r[2]), r[0]), blocks)

    if not free:
        probar(dict(locked_i))
    elif len(free) == 1:
        for s in INVIERNO_OK[y]:
            if all(abs((s - t).days) >= DIAS for t in locked_i.values()):
                probar({**locked_i, free[0]: s})
    else:
        for _ in range(N):
            inv = _sample_invierno(y, locked_i, rnd)
            if inv is not None:
                probar(inv)
    return out


def _dp(verano, inv_lock, rnd, t_fin, N):
    """Combina los resultados de los 4 años exigiendo que cada día señalado rote entre los 4."""
    cache = {}
    fallos = set()

    def res_anio(y, ptr):
        k = (y, ptr)
        if k not in cache:
            cache[k] = _resultados_anio(y, {w: verano[(y, w, "v")] for w in range(4)},
                                        inv_lock[y], ptr, rnd, N)
        return cache[k]

    def rec(yi, ptr, usedF):
        if yi == len(ANIOS):
            return {}
        if time.time() > t_fin:
            return None
        estado = (yi, ptr, usedF)
        if estado in fallos:
            return None
        y = ANIOS[yi]
        items = list(res_anio(y, ptr).items())
        rnd.shuffle(items)
        for (fest, pout), blocks in items:
            if any(fest[k] in usedF[k] for k in range(6)):
                continue
            nuevo = tuple(usedF[k] | {fest[k]} for k in range(6))
            sub = rec(yi + 1, pout, nuevo)
            if sub is not None:
                sub.update({(y, w, s): st for (w, s), st in blocks.items()})
                return sub
            if time.time() > t_fin:
                return None
        fallos.add(estado)
        return None

    return rec(0, motor.PRIMER_TRABAJADOR - 1, _VACIO)


def _buscar(bloq, rnd, presupuesto, a_fondo=True):
    t_fin = time.time() + presupuesto
    v_lock = {w: st for (y, w, s), st in bloq.items() if s == "v"}
    inv_lock = {y: {} for y in ANIOS}
    for (y, w, s), st in bloq.items():
        if s == "i":
            inv_lock[y][w] = st
    cands = [d for d in layouts_verano() if all(d[w] == st for w, st in v_lock.items())]
    rnd.shuffle(cands)
    pasadas = [400] + ([1500] if (a_fondo and len(cands) <= 24) else [])
    for N in pasadas:
        for d in cands:
            if time.time() > t_fin:
                return None
            r = _dp(verano_todos(d), inv_lock, rnd, t_fin, N)
            if r is not None:
                return r
    return None


def invalidas(bloqueados):
    """
    Elecciones ya confirmadas que NO cumplen las reglas actuales (por ejemplo, guardadas antes de
    cambiar las ventanas). Devuelve una lista de (clave, motivo).
    """
    out = []
    for clave, st in sorted(bloqueados.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        otros = {k: v for k, v in bloqueados.items() if k != clave}
        m = validar_rapido(clave, st, otros)
        if m:
            out.append((clave, m))
    return out


def completar(bloqueados, presupuesto=3.0, semilla=None, a_fondo=True):
    """
    Busca un cuadrante completo y válido que respete TODO lo confirmado.
    Devuelve {(año, w, 'v'|'i'): inicio} con los 4 veranos de cada año y los 16 inviernos,
    o None si no lo encuentra. Con a_fondo=False solo hace la pasada rápida.
    Si algo ya confirmado incumple las reglas actuales, devuelve None.
    """
    if invalidas(bloqueados):
        return None
    return _buscar(bloqueados, random.Random(semilla), presupuesto, a_fondo)


# --------------------------------------------------------------------------- evaluación y alternativas
def evaluar(clave, inicio, bloqueados, presupuesto=3.5):
    """
    Evalúa una elección frente a lo ya confirmado.
    Devuelve {'estado': 'ok'|'error'|'no_cuadra', 'motivo': str, 'completo': dict}
    """
    if clave in bloqueados:
        return {"estado": "error",
                "motivo": "Este periodo ya está confirmado. Retíralo primero si quieres cambiarlo."}
    otros = dict(bloqueados)
    m = validar_rapido(clave, inicio, otros)
    if m:
        return {"estado": "error", "motivo": m}
    malas = invalidas(otros)
    if malas:
        k, mm = malas[0]
        return {"estado": "error",
                "motivo": f"Hay una elección ya confirmada que no cumple las reglas actuales "
                          f"({TRAB[k[1]]}, {EST[k[2]].lower()} {k[0]}): {mm} "
                          "Hay que retirarla desde Administración antes de seguir."}
    comp = completar({**otros, clave: inicio}, presupuesto=presupuesto)
    if comp:
        return {"estado": "ok", "completo": comp}
    return {"estado": "no_cuadra",
            "motivo": "Con las vacaciones ya confirmadas no se encuentra ningún cuadrante completo que "
                      "cumpla todas las condiciones (rotación de Navidad y Semana Santa, descansos y "
                      "un solo trabajador de vacaciones a la vez)."}


def alternativas(clave, deseada, bloqueados, n=2, presupuesto_total=25.0, por_candidato=1.2):
    """Las n fechas válidas más cercanas a la deseada, de más a menos parecidas."""
    y, w, s = clave
    otros = {k: v for k, v in bloqueados.items() if k != clave}
    base = VERANO_OK if s == "v" else INVIERNO_OK[y]
    cands = sorted((d for d in base if d != deseada), key=lambda d: (abs((d - deseada).days), d))
    out = []
    t0 = time.time()
    for d in cands:
        if len(out) >= n or time.time() - t0 > presupuesto_total:
            break
        if validar_rapido(clave, d, otros):
            continue
        if completar({**otros, clave: d}, presupuesto=por_candidato, a_fondo=False) is not None:
            out.append(d)
    return out


# --------------------------------------------------------------------------- calendario y auditoría
def completos(bloques):
    """Si están los 4 veranos de 2027, añade la rotación de los años siguientes."""
    out = dict(bloques)
    d2027 = {w: st for (y, w, s), st in bloques.items() if s == "v" and y == ANIO0}
    if len(d2027) == 4:
        out.update(verano_todos(d2027))
    return out


def faltan(bloqueados):
    """Elecciones que aún no se han confirmado: lista de claves (año, w, est)."""
    out = [(ANIO0, w, "v") for w in range(4) if (ANIO0, w, "v") not in bloqueados]
    out += [(y, w, "i") for y in ANIOS for w in range(4) if (y, w, "i") not in bloqueados]
    return out


def lista_bloques(bl):
    out = []
    for (y, w, s), st in sorted(bl.items(), key=lambda kv: (kv[1], kv[0][1])):
        out.append({"trabajador": w, "tipo": EST[s], "periodo": str(y),
                    "inicio": st, "fin": st + TD(DIAS - 1)})
    return out


def calendario(bl):
    """bl = diccionario completo (32 bloques). Devuelve (res, lista_bloques, vac)."""
    lista = lista_bloques(bl)
    vac = [[] for _ in range(motor.N_DIAS)]
    for b in lista:
        d = b["inicio"]
        while d <= b["fin"]:
            i = (d - motor.FECHA_INICIO).days
            if 0 <= i < motor.N_DIAS:
                vac[i].append(b["trabajador"])
            d += TD(1)
    res = [motor.PRIMER_TRABAJADOR]
    for i in range(1, motor.N_DIAS):
        prev = res[-1]
        elegido = None
        for k in range(1, 5):
            c = (prev + k) % 4
            if c not in vac[i]:
                elegido = c
                break
        res.append(elegido if elegido is not None else (prev + 1) % 4)
    return res, lista, vac


def auditar(bl, res, lista, vac):
    base = motor.auditar(lista, res, vac)
    for a in base:
        if a["nombre"].startswith("Reparto"):
            a["nombre"] = "(Informativo) " + a["nombre"]
            a["ok"] = True
    extra = []

    def add(nombre, ok, detalle):
        extra.append({"nombre": nombre, "ok": bool(ok), "detalle": detalle})

    fuera = [f"{TRAB[w]} {y}" for (y, w, s), st in bl.items() if s == "v"
             and not (dt.date(y, 7, 1) <= st and st + TD(DIAS - 1) <= dt.date(y, 8, 31))]
    add("Vacaciones de verano dentro de julio y agosto", not fuera,
        "Correcto." if not fuera else "Fuera de ventana: " + ", ".join(fuera))
    fuera = [f"{TRAB[w]} {y}" for (y, w, s), st in bl.items() if s == "i" and st not in INVIERNO_VENTANA[y]]
    add("Vacaciones de invierno dentro del 16 ene-30 jun o 1 sep-30 nov", not fuera,
        "Correcto." if not fuera else "Fuera de ventana: " + ", ".join(fuera))
    dic = []
    for (y, w, s), st in bl.items():
        ds = [st + TD(i) for i in range(DIAS)]
        if any(d.month == 12 or (d.month == 1 and d.day <= 15) for d in ds):
            dic.append(f"{TRAB[w]} {y}")
    add("Ninguna vacación en diciembre ni en la 1.ª quincena de enero", not dic,
        "Correcto." if not dic else "Incumplen: " + ", ".join(dic))
    ss = [f"{TRAB[w]} {y}" for (y, w, s), st in bl.items() if _toca_semana_santa(st.year, st)]
    add("Ninguna vacación en la quincena de Jueves y Viernes Santo",
        not ss, "Correcto." if not ss else "Incumplen: " + ", ".join(ss))
    malos = [f"{TRAB[w]} {y}" for y in ANIOS for w in range(4) if (y, w, "i") not in bl]
    add("Cada trabajador tiene 15 días de invierno cada año", not malos,
        "4 trabajadores x 4 años." if not malos else "Faltan: " + ", ".join(malos))
    slots = {}
    for (y, w, s), st in bl.items():
        if s == "v":
            slots.setdefault(w, []).append((y, st))
    rota = all(len({(st.month, st.day) for _, st in v}) == 4 for v in slots.values()) and len(slots) == 4
    add("Rotación del verano: cada trabajador pasa por las 4 quincenas, una por año", rota,
        "Correcto." if rota else "Algún trabajador repite quincena.")
    return base + extra


def progreso(bloqueados):
    v = sum(1 for k in bloqueados if k[2] == "v")
    i = sum(1 for k in bloqueados if k[2] == "i")
    return v, i


if __name__ == "__main__":
    t = time.time()
    r = completar({}, presupuesto=20, semilla=1)
    print("completado sin bloqueos:", r is not None, f"{time.time() - t:.2f} s")
    if r:
        res, lista, vac = calendario(r)
        for a in auditar(r, res, lista, vac):
            print("OK " if a["ok"] else "MAL", a["nombre"], "-", a["detalle"])
