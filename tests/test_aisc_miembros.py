"""Pruebas de la verificación de miembros AISC 360-22 contra valores publicados en el
Manual AISC (16.ª ed.) y los Design Examples. Las propiedades se ingresan en pulgadas
(tal como figuran en las tablas) y se convierten a mm; las resistencias se comparan en kips."""
import math

import pytest

from osdag.aisc import secciones as S
from osdag.aisc.base import LRFD, ASD
from osdag.aisc.miembros import MemberInput, check_member, classify
from osdag.aisc.norma import AISC360_22 as A, COMPACT, NONCOMPACT
from osdag.aisc.materiales import KSI

IN = 25.4
KIP = 4448.222
E_US = 29000 * KSI
FY50 = 50 * KSI
FU65 = 65 * KSI


def kips(N):
    return N / KIP


def kipft(Nmm):
    return Nmm / (KIP * 12 * IN)


def w_shape(d, bf, tf, tw, kdes, A_, Ix, Sx, Zx, Iy, Sy, Zy, J, Cw, name):
    s = S.ISection.from_dims(d * IN, bf * IN, tf * IN, tw * IN, (kdes - tf) * IN, True, name)
    s.A, s.Ix, s.Sx, s.Zx = A_ * IN ** 2, Ix * IN ** 4, Sx * IN ** 3, Zx * IN ** 3
    s.Iy, s.Sy, s.Zy, s.J, s.Cw = Iy * IN ** 4, Sy * IN ** 3, Zy * IN ** 3, J * IN ** 4, Cw * IN ** 6
    s._finish()
    return s


W18x50 = w_shape(18.0, 7.50, 0.570, 0.355, 0.972, 14.7, 800, 88.9, 101, 40.1, 10.7, 16.6, 1.24, 3040, 'W18x50')
W14x90 = w_shape(14.0, 14.5, 0.710, 0.440, 1.31, 26.5, 999, 143, 157, 362, 49.9, 75.6, 4.06, 16000, 'W14x90')
W24x62 = w_shape(23.7, 7.04, 0.590, 0.430, 1.09, 18.2, 1550, 131, 153, 34.5, 9.80, 15.7, 1.71, 4620, 'W24x62')
W14x132 = w_shape(14.7, 14.7, 1.03, 0.645, 1.63, 38.8, 1530, 209, 234, 548, 74.5, 113, 12.3, 25500, 'W14x132')


def _inp(sec, **kw):
    base = dict(section=sec, Fy=FY50, Fu=FU65, E=E_US, method=LRFD)
    base.update(kw)
    return MemberInput(**base)


def _check(res, clause_prefix):
    return next(c for c in res.checks if c.clause.startswith(clause_prefix))


def test_W18x50_Lp_Lr_tabla_3_2():
    Lp, Lr = A.F2_limits(W18x50.ry, W18x50.rts, W18x50.J, W18x50.Sx, W18x50.ho, FY50, E_US)
    assert Lp / (12 * IN) == pytest.approx(5.83, rel=0.01)
    assert Lr / (12 * IN) == pytest.approx(16.9, rel=0.01)


def test_W18x50_momento_plastico():
    res = check_member(_inp(W18x50, Mx=100e6, Lb=1.0))
    c = _check(res, 'F2')
    assert c.capacity * 1e6 / (KIP * 12 * IN) == pytest.approx(379, rel=0.005)   # φMp = 379 kip-ft


def test_W18x50_LTB_lb_35ft_Cb_114():
    # Ejemplo F.1-3A: Lb = 35 ft, Cb = 1.14 -> Mn = Fcr·Sx con F2-4
    res = check_member(_inp(W18x50, Mx=100e6, Lb=35 * 12 * IN, Cb=1.14))
    c = _check(res, 'F2')
    rts = W18x50.rts / IN
    x = (35 * 12 / rts) ** 2
    Fcr = 1.14 * math.pi ** 2 * 29000 / x * math.sqrt(1 + 0.078 * 1.24 / (88.9 * W18x50.ho / IN) * x)
    assert c.capacity * 1e6 / (KIP * 12 * IN) == pytest.approx(0.9 * Fcr * 88.9 / 12, rel=0.005)


def test_W14x90_ala_no_compacta():
    cls = classify(W14x90, FY50, E_US)
    assert cls[0][4] == NONCOMPACT and cls[1][4] == COMPACT
    res = check_member(_inp(W14x90, Mx=100e6, Lb=1.0))
    assert _check(res, 'F3').capacity * 1e6 / (KIP * 12 * IN) == pytest.approx(574, rel=0.005)


def test_W24x62_corte():
    res = check_member(_inp(W24x62, Vy=100e3, Mx=1e6))
    c = _check(res, 'G2.1')
    assert c.capacity * 1e3 / KIP == pytest.approx(306, rel=0.005)   # φvVn = 306 kips


def test_W14x132_columna_30ft():
    # Ejemplo E.1A: W14x132, Lc = 30 ft -> φcPn ≈ 892 kips (Tabla 4-1a)
    L = 30 * 12 * IN
    res = check_member(_inp(W14x132, P=-500e3, Lx=L, Ly=L, Lz=L))
    c = _check(res, 'E3')
    assert c.capacity * 1e3 / KIP == pytest.approx(892, rel=0.01)


def test_ASD_divide_por_omega():
    L = 30 * 12 * IN
    r_l = check_member(_inp(W14x132, P=-500e3, Lx=L, Ly=L, Lz=L))
    r_a = check_member(_inp(W14x132, P=-500e3, Lx=L, Ly=L, Lz=L, method=ASD))
    assert _check(r_a, 'E3').capacity == pytest.approx(_check(r_l, 'E3').capacity / (0.9 * 1.67), rel=1e-6)


def test_traccion_D2():
    # Placa 150x12, A36: φPn fluencia = 0.9·250·1800
    sec = S.ISection.from_dims(200, 100, 10, 6)
    res = check_member(MemberInput(section=sec, Fy=250, Fu=400, P=100e3, An=sec.A * 0.85, U=0.9))
    y = res.checks[0]
    r = res.checks[1]
    assert y.capacity == pytest.approx(0.9 * 250 * sec.A / 1e3)
    assert r.capacity == pytest.approx(0.75 * 400 * sec.A * 0.85 * 0.9 / 1e3)


def test_interaccion_H1():
    v, eq = A.H1_1_interaction(500, 1000, 100, 200, 0, 1)
    assert v == pytest.approx(0.5 + 8 / 9 * 0.5)
    v, eq = A.H1_1_interaction(100, 1000, 100, 200, 0, 1)
    assert v == pytest.approx(0.05 + 0.5)


def test_hss_circular_compacto_flexion():
    sec = S.RoundHSS.from_dims(6.625 * IN, 0.280 * IN, erw_a500=True)
    res = check_member(MemberInput(section=sec, Fy=42 * KSI, Fu=58 * KSI, Mx=1e6, E=E_US))
    c = _check(res, 'F8')
    # HSS6.625x0.280 (A500 Gr. B): Z = 10.6 in³ -> φMn = 0.9·42·10.6/12 = 33.4 kip-ft
    assert c.capacity * 1e6 / (KIP * 12 * IN) == pytest.approx(33.4, rel=0.01)


def test_hss_rectangular_compresion_y_flexion():
    sec = S.RectHSS.from_dims(8 * IN, 8 * IN, 0.5 * IN, erw_a500=True)
    L = 10 * 12 * IN
    res = check_member(MemberInput(section=sec, Fy=50 * KSI, Fu=62 * KSI, P=-200e3, Mx=50e6,
                                   Lx=L, Ly=L, Lz=L, Lb=L, E=E_US))
    assert res.governing is not None
    c = _check(res, 'E3')
    # Tabla 4-4: HSS8x8x1/2, Fy = 50 ksi, Lc = 10 ft -> φPn ≈ 548 kips
    assert c.capacity * 1e3 / KIP == pytest.approx(548, rel=0.015)
    f = _check(res, 'F7')
    assert f.capacity * 1e6 / (KIP * 12 * IN) == pytest.approx(0.9 * 50 * 37.5 / 12, rel=0.01)


def test_angulo_E5_y_canal_E4():
    L = S.AngleSection.from_dims(4 * IN, 4 * IN, 0.5 * IN)
    res = check_member(MemberInput(section=L, Fy=36 * KSI, Fu=58 * KSI, P=-50e3, Lx=2000, Ly=2000, Lz=2000,
                                   angle_E5=True))
    assert any('E5' in s for s in res.checks[0].steps)
    C = S.ChannelSection.from_dims(300, 90, 13, 9)
    res = check_member(MemberInput(section=C, Fy=345, Fu=450, P=-300e3, Lx=3000, Ly=3000, Lz=3000))
    assert any('Flexotorsional' in s for s in res.checks[0].steps)


def test_base_de_datos_osdag():
    names = S.list_designations(S.I_SHAPE)
    assert 'MB 450' in names
    sec = S.from_database(S.I_SHAPE, 'MB 450')
    assert sec.A == pytest.approx(9220, rel=0.02)
    res = check_member(MemberInput(section=sec, Fy=250, Fu=410, Mx=150e6, Vy=100e3, Lb=3000))
    assert res.checks and all(c.capacity > 0 for c in res.checks)


def test_catalogo_aisc():
    from osdag.aisc import catalogo as K
    assert len(K.list_designations(S.I_SHAPE)) > 300
    w = K.from_catalog('W14X132')
    assert w.A / IN ** 2 == pytest.approx(38.8, rel=0.005)
    L = 30 * 12 * IN
    res = check_member(_inp(w, P=-500e3, Lx=L, Ly=L, Lz=L))
    assert _check(res, 'E3').capacity * 1e3 / KIP == pytest.approx(892, rel=0.01)
    hss = K.from_catalog('HSS8X8X1/2')
    assert hss.t / IN == pytest.approx(0.465, rel=0.01)
    for name in ('L4X4X1/2', 'C12X30', 'Pipe4STD', 'HSS6.625X0.280'):
        assert K.from_catalog(name) is not None
