"""Materiales según AISC 360-22 y AISC Steel Construction Manual (16.ª ed.), en unidades SI.

Valores mínimos especificados de Fy y Fu (MPa). Referencias: Manual AISC Tablas 2-4 y 2-5,
AISC 360-22 Tabla J3.2 (pernos) y Tabla J2.5 (electrodos).
"""
from dataclasses import dataclass

KSI = 6.894757  # MPa


@dataclass(frozen=True)
class Steel:
    name: str
    Fy: float      # MPa
    Fu: float      # MPa
    note: str = ''


# Clave = nombre mostrado en la interfaz
ACEROS = {s.name: s for s in [
    Steel('ASTM A36', 250, 400, 'Perfiles y placas'),
    Steel('ASTM A572 Gr. 42', 290, 415),
    Steel('ASTM A572 Gr. 50', 345, 450),
    Steel('ASTM A572 Gr. 55', 380, 485),
    Steel('ASTM A572 Gr. 60', 415, 520),
    Steel('ASTM A572 Gr. 65', 450, 550),
    Steel('ASTM A992', 345, 450, 'Perfiles W'),
    Steel('ASTM A588 Gr. 50', 345, 485),
    Steel('ASTM A913 Gr. 50', 345, 450),
    Steel('ASTM A913 Gr. 65', 450, 550),
    Steel('ASTM A529 Gr. 50', 345, 450),
    Steel('ASTM A500 Gr. B (rect.)', 315, 400, 'HSS rectangular'),
    Steel('ASTM A500 Gr. B (circ.)', 290, 400, 'HSS circular'),
    Steel('ASTM A500 Gr. C (rect.)', 345, 425, 'HSS rectangular'),
    Steel('ASTM A500 Gr. C (circ.)', 315, 425, 'HSS circular'),
    Steel('ASTM A1085 Gr. A', 345, 450, 'HSS'),
    Steel('ASTM A53 Gr. B', 240, 415, 'Tubo'),
]}

# Placas y conectores (subconjunto habitual)
ACEROS_PLACA = ['ASTM A36', 'ASTM A572 Gr. 50', 'ASTM A588 Gr. 50']


@dataclass(frozen=True)
class BoltGrade:
    name: str
    Fu: float          # MPa, resistencia mínima a tracción del perno
    Fnt: float         # MPa, tensión nominal a tracción (Tabla J3.2)
    Fnv_N: float       # MPa, corte con rosca incluida en el plano de corte
    Fnv_X: float       # MPa, corte con rosca excluida del plano de corte
    pretensionable: bool = True
    group: str = ''    # 'A' (Grupo 120), 'B' (Grupo 150), '144', '' (no pretensionable)
    note: str = ''


def _iso(name, Fu, group='', note=''):
    # Criterio AISC (Comentario J3.7): Fnt = 0.75 Fu ; Fnv = 0.450 Fu (N) ; 0.563 Fu (X)
    return BoltGrade(name, Fu, round(0.75 * Fu), round(0.450 * Fu), round(0.563 * Fu),
                     bool(group), group, note or 'Derivado con Fnt=0.75Fu, Fnv=0.450Fu/0.563Fu')


PERNOS = {b.name: b for b in [
    BoltGrade('ASTM A307 Gr. A', 414, 310, 188, 188, False, '', 'Perno común, no pretensionable'),
    BoltGrade('Grupo 120 (A325 / F3125)', 827, 620, 372, 469, True, 'A'),
    _iso('Grupo 144 (F3148)', 993, '144'),
    BoltGrade('Grupo 150 (A490 / F3125)', 1034, 780, 469, 579, True, 'B'),
    _iso('ISO 898-1 clase 4.6', 400),
    _iso('ISO 898-1 clase 5.6', 500),
    _iso('ISO 898-1 clase 8.8', 800, 'A'),
    _iso('ISO 898-1 clase 10.9', 1000, 'B'),
    _iso('ASTM F1554 Gr. 36 (anclaje)', 400),
    _iso('ASTM F1554 Gr. 55 (anclaje)', 517),
    _iso('ASTM F1554 Gr. 105 (anclaje)', 862),
]}

# Electrodos: resistencia de clasificación F_EXX (MPa)
ELECTRODOS = {
    'E60XX': 415.0,
    'E70XX': 480.0,
    'E80XX': 550.0,
    'E90XX': 620.0,
    'E100XX': 690.0,
    'E110XX': 760.0,
}

# ---------------------------------------------------------------------------------------------
# Diámetros de pernos. Clave = texto mostrado; valor = diámetro nominal en mm
# ---------------------------------------------------------------------------------------------
BOLT_DIAMETERS = {
    'M12': 12.0, 'M16': 16.0, 'M20': 20.0, 'M22': 22.0, 'M24': 24.0, 'M27': 27.0,
    'M30': 30.0, 'M36': 36.0,
    '1/2 in': 12.7, '5/8 in': 15.875, '3/4 in': 19.05, '7/8 in': 22.225, '1 in': 25.4,
    '1-1/8 in': 28.575, '1-1/4 in': 31.75, '1-1/2 in': 38.1,
}

# Área de esfuerzo a tracción (ISO 898-1) en mm², para la pretensión de pernos métricos
_AS_METRIC = {12: 84.3, 16: 157.0, 20: 245.0, 22: 303.0, 24: 353.0, 27: 459.0, 30: 561.0, 36: 817.0}

# Tabla J3.1 (kips) -> pretensión mínima para pernos en pulgadas
_TB_IMPERIAL_KIPS = {
    'A': {12.7: 12, 15.875: 19, 19.05: 28, 22.225: 39, 25.4: 51, 28.575: 64, 31.75: 81, 38.1: 118},
    'B': {12.7: 15, 15.875: 24, 19.05: 35, 22.225: 49, 25.4: 64, 28.575: 80, 31.75: 102, 38.1: 148},
}
# Tabla J3.1M (kN)
_TB_METRIC_KN = {
    'A': {16: 91, 20: 142, 22: 176, 24: 205, 27: 267, 30: 326, 36: 475},
    'B': {16: 114, 20: 179, 22: 221, 24: 257, 27: 334, 30: 408, 36: 595},
}


def is_metric(d):
    return abs(d - round(d)) < 1e-6


def bolt_pretension(grade: BoltGrade, d):
    """Pretensión mínima Tb (N), AISC 360-22 Tabla J3.1 / J3.1M.

    Para diámetros/grados no tabulados se usa Tb = 0.70 Fu As (base de la tabla).
    """
    if not grade.pretensionable:
        return 0.0
    if grade.group in ('A', 'B'):
        if is_metric(d) and int(round(d)) in _TB_METRIC_KN[grade.group] and grade.name.startswith('Grupo'):
            return _TB_METRIC_KN[grade.group][int(round(d))] * 1000.0
        for dd, kips in _TB_IMPERIAL_KIPS[grade.group].items():
            if abs(dd - d) < 0.01 and grade.name.startswith('Grupo'):
                return kips * 4448.222
    As = _AS_METRIC.get(int(round(d))) if is_metric(d) else 0.7854 * (d - 0.9743 * 25.4 / _tpi(d)) ** 2
    if As is None:
        As = 0.7854 * (d - 0.9382 * _pitch_metric(d)) ** 2
    return 0.70 * grade.Fu * As


def _tpi(d_mm):
    # Hilos por pulgada, serie UNC
    table = {12.7: 13, 15.875: 11, 19.05: 10, 22.225: 9, 25.4: 8, 28.575: 7, 31.75: 7, 38.1: 6}
    for k, v in table.items():
        if abs(k - d_mm) < 0.01:
            return v
    return 8


def _pitch_metric(d):
    table = {12: 1.75, 16: 2.0, 20: 2.5, 22: 2.5, 24: 3.0, 27: 3.0, 30: 3.5, 36: 4.0}
    return table.get(int(round(d)), 0.125 * d)


# ---------------------------------------------------------------------------------------------
# Tabla J3.3 / J3.3M: dimensiones nominales de agujeros (mm)
# ---------------------------------------------------------------------------------------------
HOLE_TYPES = ['Estándar (STD)', 'Agrandado (OVS)', 'Ranura corta (SSL)', 'Ranura larga (LSL)']

_HOLES_METRIC = {  # d: (STD, OVS, SSL ancho x largo, LSL largo)
    16: (18, 20, 22, 40), 20: (22, 24, 26, 50), 22: (24, 28, 30, 55), 24: (27, 30, 32, 60),
    27: (30, 35, 37, 67), 30: (33, 38, 40, 75),
}
IN = 25.4


def hole_size(d, hole_type='Estándar (STD)'):
    """Devuelve (ancho, largo) nominal del agujero en mm (AISC 360-22 Tabla J3.3 / J3.3M)."""
    if is_metric(d):
        di = int(round(d))
        if di in _HOLES_METRIC:
            std, ovs, ssl, lsl = _HOLES_METRIC[di]
        elif di < 16:
            std, ovs, ssl, lsl = d + 2, d + 4, d + 6, 2.5 * d
        else:
            std, ovs, ssl, lsl = d + 3, d + 8, d + 10, 2.5 * d
    else:
        if d < 1.0 * IN - 0.01:
            std, ovs = d + IN / 16, d + 3 * IN / 16
            ssl, lsl = d + IN / 4, 2.5 * d
        elif d < 1.125 * IN - 0.01:   # 1 in
            std, ovs, ssl, lsl = d + IN / 8, d + IN / 4, d + 5 * IN / 16, 2.5 * d
        else:
            std, ovs, ssl, lsl = d + IN / 8, d + 5 * IN / 16, d + 3 * IN / 8, 2.5 * d
        # 5/8 a 7/8 in: ranura corta = d+1/16 x d+1/4 ; std = d+1/16 ; ovs = d+3/16
    if hole_type.startswith('Estándar'):
        return std, std
    if hole_type.startswith('Agrandado'):
        return ovs, ovs
    if hole_type.startswith('Ranura corta'):
        return std, ssl
    return std, lsl


# Tabla J3.4 / J3.4M: distancia mínima al borde, medida desde el centro del agujero (mm)
_EDGE_METRIC = {16: 22, 20: 26, 22: 28, 24: 30, 27: 34, 30: 38, 36: 46}
_EDGE_IMPERIAL = {12.7: 0.75, 15.875: 0.875, 19.05: 1.0, 22.225: 1.125, 25.4: 1.25,
                  28.575: 1.5, 31.75: 1.625}


def min_edge_distance(d):
    if is_metric(d):
        return float(_EDGE_METRIC.get(int(round(d)), 1.25 * d))
    for k, v in _EDGE_IMPERIAL.items():
        if abs(k - d) < 0.01:
            return v * IN
    return 1.25 * d
