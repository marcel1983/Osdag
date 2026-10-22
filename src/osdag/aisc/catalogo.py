"""Catálogo de perfiles AISC (AISC Shapes Database) en unidades SI.

Archivo: data/ResourceFiles/Database/aisc_shapes_si.csv (ver AISC_SHAPES_NOTICE.txt).
"""
import csv
import math
from importlib.resources import files

from . import secciones as S

FAMILY_KIND = {'W': S.I_SHAPE, 'M': S.I_SHAPE, 'S': S.I_SHAPE, 'HP': S.I_SHAPE,
               'C': S.CHANNEL, 'MC': S.CHANNEL, 'L': S.ANGLE, 'HSS': S.RECT_HSS,
               'HSSR': S.ROUND_HSS, 'PIPE': S.ROUND_HSS}

_ROWS = None


def _rows():
    global _ROWS
    if _ROWS is None:
        path = files('osdag.data.ResourceFiles.Database').joinpath('aisc_shapes_si.csv')
        with open(str(path), encoding='utf-8') as fh:
            _ROWS = [r for r in csv.DictReader(fh)]
    return _ROWS


def _f(r, key, default=0.0):
    v = r.get(key, '')
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def list_designations(kind, families=None):
    """Designaciones AISC disponibles para un tipo de sección (en el orden del Manual)."""
    out = []
    for r in _rows():
        if FAMILY_KIND.get(r['family']) == kind and (families is None or r['family'] in families):
            out.append(r['name'])
    return out


def families(kind):
    seen = []
    for r in _rows():
        if FAMILY_KIND.get(r['family']) == kind and r['family'] not in seen:
            seen.append(r['family'])
    return seen


def find(name):
    for r in _rows():
        if r['name'] == name:
            return r
    return None


def from_catalog(name):
    """Crea la sección (con las propiedades tabuladas por AISC) a partir de su designación."""
    r = find(name)
    if r is None:
        return None
    fam = r['family']
    kind = FAMILY_KIND.get(fam)
    src = 'Catálogo AISC (AISC Shapes Database, %s)' % fam
    if kind == S.I_SHAPE:
        d, bf, tw, tf, k = _f(r, 'd'), _f(r, 'bf'), _f(r, 'tw'), _f(r, 'tf'), _f(r, 'k')
        s = S.ISection.from_dims(d, bf, tf, tw, max(k - tf, 0.0), True, name)
        _set(s, r, ['A:area', 'Ix', 'Iy', 'Sx', 'Sy', 'Zx', 'Zy', 'J', 'Cw'])
    elif kind == S.CHANNEL:
        d, bf, tw, tf, k = _f(r, 'd'), _f(r, 'bf'), _f(r, 'tw'), _f(r, 'tf'), _f(r, 'k')
        s = S.ChannelSection.from_dims(d, bf, tf, tw, max(k - tf, 0.0), name)
        _set(s, r, ['A:area', 'Ix', 'Iy', 'Sx', 'Sy', 'Zx', 'Zy', 'J', 'Cw'])
        s.x_bar = _f(r, 'x', s.x_bar)
        eo = _f(r, 'eo')
        if eo:
            s.xo = s.x_bar - s.tw / 2 + eo
    elif kind == S.ANGLE:
        d, b, t = _f(r, 'd'), _f(r, 'b'), _f(r, 't')
        s = S.AngleSection.from_dims(max(d, b), min(d, b), t, name)
        _set(s, r, ['A:area', 'Ix', 'Iy', 'Sx', 'Sy', 'Zx', 'Zy', 'J', 'Cw'])
        s.x_bar, s.y_bar = _f(r, 'x', s.x_bar), _f(r, 'y', s.y_bar)
        Iz = _f(r, 'Iz')
        if Iz:
            s.Iz_minor = Iz
            s.Iw_major = s.Ix + s.Iy - Iz
            s.r_min = math.sqrt(Iz / s.A)
    elif kind == S.RECT_HSS:
        H, B, td, tn = _f(r, 'Ht'), _f(r, 'B'), _f(r, 'tdes'), _f(r, 'tnom')
        s = S.RectHSS.from_dims(H, B, td, False, name)
        s.t_nom = tn
        _set(s, r, ['A:area', 'Ix', 'Iy', 'Sx', 'Sy', 'Zx', 'Zy', 'J'])
    elif kind == S.ROUND_HSS:
        D, td, tn = _f(r, 'OD'), _f(r, 'tdes'), _f(r, 'tnom')
        s = S.RoundHSS.from_dims(D, td, False, name)
        s.t_nom = tn
        _set(s, r, ['A:area', 'Ix', 'Iy', 'Sx', 'Sy', 'Zx', 'Zy', 'J'])
    else:
        return None
    s.source = src
    s.extra['masa_kg_m'] = _f(r, 'weight')
    s._finish()
    return s


def _set(s, r, keys):
    for k in keys:
        attr, col = (k.split(':') + [k])[:2] if ':' in k else (k, k)
        v = _f(r, col)
        if v > 0:
            setattr(s, attr, v)
