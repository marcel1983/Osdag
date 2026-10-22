"""Pruebas de conexiones AISC 360-22 (Capítulo J) contra valores del Manual AISC 16.ª ed."""
import pytest

from osdag.aisc import secciones as S
from osdag.aisc.base import LRFD, ASD
from osdag.aisc.conexiones import (Bolt, ShearTabInput, check_shear_tab, BoltedTensionInput, check_bolted_tension,
                                   WeldedTensionInput, check_welded_tension, BasePlateInput, check_base_plate,
                                   ic_coefficient, elastic_coefficient, MEMBER_PLATE, MEMBER_ANGLE, THREADS_X)
from osdag.aisc.materiales import KSI, bolt_pretension, PERNOS, hole_size, min_edge_distance
from osdag.aisc.norma import AISC360_22 as A

IN = 25.4
KIP = 4448.222


def test_perno_A325N_3_4_corte_tabla_7_1():
    b = Bolt('Grupo 120 (A325 / F3125)', '3/4 in')
    assert b.shear().available(LRFD) / KIP == pytest.approx(17.9, rel=0.01)
    assert b.shear().available(ASD) / KIP == pytest.approx(11.9, rel=0.01)
    bx = Bolt('Grupo 120 (A325 / F3125)', '3/4 in', THREADS_X)
    assert bx.shear().available(LRFD) / KIP == pytest.approx(22.5, rel=0.01)
    assert b.tension().available(LRFD) / KIP == pytest.approx(29.8, rel=0.01)


def test_deslizamiento_tabla_7_3():
    b = Bolt('Grupo 120 (A325 / F3125)', '3/4 in', slip_critical=True)
    assert bolt_pretension(b.g, b.d) / KIP == pytest.approx(28, rel=0.001)
    assert b.slip().available(LRFD) / KIP == pytest.approx(9.49, rel=0.01)


def test_pretension_metrica_tabla_J3_1M():
    assert bolt_pretension(PERNOS['Grupo 120 (A325 / F3125)'], 20.0) == pytest.approx(142e3)
    assert bolt_pretension(PERNOS['Grupo 150 (A490 / F3125)'], 24.0) == pytest.approx(257e3)


def test_agujeros_y_bordes():
    assert hole_size(20.0) == (22, 22)
    assert hole_size(19.05)[0] == pytest.approx(19.05 + 1.5875)
    assert min_edge_distance(20.0) == 26
    assert min_edge_distance(19.05) == pytest.approx(25.4)


def test_filete_E70_por_dieciseisavo():
    # φRn = 1.392 kips/in por cada 1/16 in de tamaño (Manual Parte 8)
    nom = A.J2_fillet(IN / 16, IN, 70 * KSI)
    assert nom.available(LRFD) / KIP == pytest.approx(1.392, rel=0.005)
    trans = A.J2_fillet(IN / 16, IN, 70 * KSI, 90.0)
    assert trans.Rn / nom.Rn == pytest.approx(1.5)


def test_aplastamiento_desgarramiento():
    nom = A.J3_bearing_tearout(19.05, 10, 400, 100)
    assert nom.Rn == pytest.approx(2.4 * 19.05 * 10 * 400)
    nom = A.J3_bearing_tearout(19.05, 10, 400, 15)
    assert nom.Rn == pytest.approx(1.2 * 15 * 10 * 400)


def test_bloque_de_corte():
    nom = A.J4_3_block_shear(250, 400, 2000, 1500, 500, 1.0)
    assert nom.Rn == pytest.approx(min(0.6 * 400 * 1500 + 400 * 500, 0.6 * 250 * 2000 + 400 * 500))


def test_centro_instantaneo():
    pts = [(0, 75 * i) for i in range(4)]
    assert ic_coefficient(pts, 0) == pytest.approx(4)
    c1, c2 = ic_coefficient(pts, 50), ic_coefficient(pts, 150)
    assert 4 > c1 > c2 > 0
    assert c1 >= elastic_coefficient(pts, 50) - 1e-6
    # Manual Tabla 7-6 (una columna, s = 3 in, n = 4, ex = 3 in): C = 2.81
    pts_in = [(0, 3 * IN * i) for i in range(4)]
    assert ic_coefficient(pts_in, 3 * IN) == pytest.approx(2.81, abs=0.06)


def test_placa_de_cortante():
    beam = S.ISection.from_dims(406, 178, 12.8, 7.7, 10)
    inp = ShearTabInput(beam=beam, beam_Fy=345, beam_Fu=450, plate_t=10, plate_Fy=250, plate_Fu=400,
                        bolt=Bolt('Grupo 120 (A325 / F3125)', 'M20'), n_rows=3, pitch=75, Lev=40, Leh=40,
                        Leh_beam=40, a=75, weld_size=6, FEXX=480, V=150e3)
    res = check_shear_tab(inp)
    names = [c.name for c in res.checks]
    assert any('bloque' in n for n in names) and any('Soldadura' in n for n in names)
    assert all(c.capacity > 0 for c in res.checks)
    res2 = check_shear_tab(ShearTabInput(**{**inp.__dict__, 'V': 2000e3}))
    assert not res2.ok


def test_traccion_atornillada_angulo_y_placa():
    L = S.AngleSection.from_dims(100, 100, 10)
    inp = BoltedTensionInput(member_kind=MEMBER_ANGLE, member_t=10, member_Fy=250, member_Fu=400, gusset_t=12,
                             gusset_Fy=250, gusset_Fu=400, bolt=Bolt('Grupo 120 (A325 / F3125)', 'M20'),
                             angle=L, n_per_line=4, n_lines=1, pitch=70, Le_member=40, Le_gusset=40, gauge=55,
                             T=250e3)
    res = check_bolted_tension(inp)
    rup = next(c for c in res.checks if 'rotura en sección neta' in c.name)
    assert any('caso 8' in s for s in rup.steps)
    inp2 = BoltedTensionInput(member_kind=MEMBER_PLATE, member_t=12, member_Fy=250, member_Fu=400, gusset_t=12,
                              gusset_Fy=250, gusset_Fu=400, bolt=Bolt('Grupo 120 (A325 / F3125)', 'M20'),
                              plate_width=200, n_per_line=3, n_lines=2, pitch=70, Le_member=40, Le_gusset=40,
                              gauge=100, T=-300e3, gusset_free_length=150)
    res2 = check_bolted_tension(inp2)
    assert any('compresión' in c.name for c in res2.checks)


def test_traccion_soldada():
    inp = WeldedTensionInput(member_kind=MEMBER_PLATE, member_t=12, member_Fy=250, member_Fu=400, gusset_t=12,
                             gusset_Fy=250, gusset_Fu=400, plate_width=150, weld_size=8, L_long=200, n_long=2,
                             L_trans=150, FEXX=480, T=400e3)
    res = check_welded_tension(inp)
    w = next(c for c in res.checks if c.name == 'Soldaduras de filete')
    assert any('J2-6' in s for s in w.steps)


def test_placa_base_DG1():
    # DG1 ejemplo 4.7-1 (adaptado): W12x96 (d = 12.7 in, bf = 12.2 in), Pu = 700 kips, f'c = 3 ksi, A2 = 4·A1
    col = S.ISection.from_dims(12.7 * IN, 12.2 * IN, 0.9 * IN, 0.55 * IN)
    B = N = 18 * IN
    res = check_base_plate(BasePlateInput(column=col, B=B, N=N, tp=1.5 * IN, plate_Fy=36 * KSI, fc=3 * KSI,
                                          A2=4 * B * N, P=700 * KIP))
    t = next(c for c in res.checks if 'Espesor' in c.name)
    # m = (18 − 0.95·12.7)/2 = 2.97 in ; n = (18 − 0.8·12.2)/2 = 4.12 in -> l ≈ 4.12 in
    # tmín = 4.12·√(2·700/(0.9·36·324)) = 1.51 in
    assert t.demand / IN == pytest.approx(1.51, rel=0.02)


# ---------------------------------------------------------------------------------------------
# Conexiones agregadas para los submódulos de Osdag en modo AISC
# ---------------------------------------------------------------------------------------------
from osdag.aisc import catalogo as K
from osdag.aisc.conexiones import (DoubleAngleInput, check_double_angle, ShearEndPlateInput, check_shear_end_plate,
                                   SeatedAngleInput, check_seated_angle, BoltedSpliceInput, check_bolted_splice,
                                   MomentEndPlateInput, check_moment_end_plate, ENDPLATE_4E, ENDPLATE_FLUSH)

A325 = 'Grupo 120 (A325 / F3125)'


def test_angulos_dobles():
    beam = K.from_catalog('W18X50')
    res = check_double_angle(DoubleAngleInput(beam=beam, beam_Fy=345, beam_Fu=450, angle=K.from_catalog('L4X3-1/2X3/8'),
                                              angle_Fy=250, angle_Fu=400, bolt=Bolt(A325, '3/4 in'), n_rows=3,
                                              pitch=76.2, Lev=31.75, Leh_beam_leg=31.75, Leh_support_leg=31.75,
                                              support_t=12, support_Fu=450, V=150e3))
    bolts = res.checks[0]
    # 3 pernos A325-N 3/4" en corte doble: 3 × 35.8 kips = 107 kips (si gobierna el corte)
    assert bolts.capacity * 1e3 / KIP <= 3 * 35.8 + 0.5
    assert all(c.capacity > 0 for c in res.checks)


def test_placa_extremo_cortante():
    beam = K.from_catalog('W18X50')
    res = check_shear_end_plate(ShearEndPlateInput(beam=beam, beam_Fy=345, beam_Fu=450, plate_t=6.35, plate_width=203,
                                                   plate_Fy=250, plate_Fu=400, bolt=Bolt(A325, '3/4 in'), n_rows=3,
                                                   pitch=76.2, Lev=31.75, gauge=139.7, weld_size=5, V=150e3))
    assert res.governing.ratio > 0 and len(res.checks) >= 7


def test_angulo_asiento():
    beam = K.from_catalog('W16X26')
    res = check_seated_angle(SeatedAngleInput(beam=beam, beam_Fy=345, angle=K.from_catalog('L6X4X5/8'), angle_Fy=250,
                                              angle_Fu=400, bolt=Bolt(A325, '3/4 in'), angle_length=203, setback=19,
                                              n_bolts=2, V=100e3))
    names = [c.name for c in res.checks]
    assert any('fluencia local' in n for n in names) and any('flexión del ala saliente' in n for n in names)


def test_empalme_atornillado():
    beam = K.from_catalog('W18X50')
    res = check_bolted_splice(BoltedSpliceInput(member=beam, Fy=345, Fu=450, fp_width=190, fp_t=16, fp_Fy=345,
                                                fp_Fu=450, wp_height=300, wp_t=8, flange_bolt=Bolt(A325, 'M20'),
                                                web_bolt=Bolt(A325, 'M20'), M=150e6, V=100e3))
    ff = res.geometry[0][1]
    assert ff == pytest.approx(150e6 / (beam.d + 16) / 1e3, rel=1e-6)
    assert any('Placa de ala' in c.name for c in res.checks)


def test_placa_extremo_momento_4E_DG16():
    # Comparación con la fórmula de Yp del DG16 (4E) evaluada a mano en pulgadas
    IN = 25.4
    beam = K.from_catalog('W18X50')
    inp = MomentEndPlateInput(beam=beam, beam_Fy=345, plate_Fy=250, plate_Fu=400, bp=7.75 * IN, tp=0.75 * IN,
                              bolt=Bolt('Grupo 150 (A490 / F3125)', '1 in'), config=ENDPLATE_4E, g=5.5 * IN,
                              pfi=1.75 * IN, pfo=1.75 * IN, de=1.5 * IN, M=300e6, V=100e3)
    res = check_moment_end_plate(inp)
    d, tf = 18.0, 0.570
    h1, h0 = d - 1.5 * tf - 1.75, d - tf / 2 + 1.75
    s = 0.5 * (7.75 * 5.5) ** 0.5
    Yp = 7.75 / 2 * (h1 * (1 / 1.75 + 1 / s) + h0 / 1.75 - 0.5) + 2 / 5.5 * h1 * (1.75 + s)
    yp_mm = [v for n, v, u in res.geometry if n.startswith('Yp')][0]
    assert yp_mm / IN == pytest.approx(Yp, rel=0.01)
    res2 = check_moment_end_plate(MomentEndPlateInput(**{**inp.__dict__, 'config': ENDPLATE_FLUSH,
                                                         'column': K.from_catalog('W14X90'), 'column_Fy': 345}))
    assert any('Columna' in c.name for c in res2.checks)
