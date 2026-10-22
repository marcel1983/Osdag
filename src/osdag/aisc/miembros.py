"""Verificación de miembros de acero según AISC 360-22 (Capítulos B, D, E, F, G y H).

Uso típico::

    sec = secciones.ISection.from_dims(d=450, bf=190, tf=14.6, tw=9.4, r=15)
    datos = MemberInput(section=sec, Fy=345, Fu=450, method='LRFD', P=-800e3, Mx=150e6,
                        Lx=6000, Ly=3000, Lz=3000, Lb=3000, Cb=1.0)
    res = check_member(datos)
    for c in res.checks: print(c.name, c.ratio, c.status)

Convención de signos: P > 0 tracción, P < 0 compresión. Unidades N, mm, MPa, N·mm.
Las solicitaciones deben incluir los efectos de segundo orden (Capítulo C), p. ej. B1/B2.
"""
import math
from dataclasses import dataclass, field

from .base import Check, Limit, LRFD, ASD, E_STEEL, G_STEEL, available_strength, factor_text
from .norma import AISC360_22 as A, COMPACT, NONCOMPACT, SLENDER, NONSLENDER
from . import secciones as S


@dataclass
class MemberInput:
    section: S.Section
    Fy: float
    Fu: float
    method: str = LRFD
    # Solicitaciones requeridas (Pu, Mu, Vu en LRFD; Pa, Ma, Va en ASD)
    P: float = 0.0           # N  (+ tracción, − compresión)
    Mx: float = 0.0          # N·mm, eje mayor
    My: float = 0.0          # N·mm, eje menor
    Vy: float = 0.0          # N, corte paralelo al alma (asociado a Mx)
    Vx: float = 0.0          # N, corte paralelo a las alas (asociado a My)
    # Longitudes no arriostradas (mm) y factores de longitud efectiva
    Lx: float = 3000.0
    Ly: float = 3000.0
    Lz: float = 3000.0
    Kx: float = 1.0
    Ky: float = 1.0
    Kz: float = 1.0
    Lb: float = 3000.0
    Cb: float = 1.0
    # Tracción
    An: float = 0.0          # área neta (0 => Ag)
    U: float = 1.0           # factor de retraso de corte (Tabla D3.1)
    # Ángulos simples conectados por un ala (E5)
    angle_E5: bool = False
    angle_truss_web: bool = False
    angle_short_leg_connected: bool = False
    # Corte: separación de rigidizadores transversales (0 = sin rigidizadores)
    stiffener_a: float = 0.0
    E: float = E_STEEL
    G: float = G_STEEL


@dataclass
class MemberResult:
    checks: list = field(default_factory=list)
    limits: list = field(default_factory=list)
    classification: list = field(default_factory=list)   # (elemento, λ, λp, λr, clase, solicitación)
    notes: list = field(default_factory=list)
    capacities: dict = field(default_factory=dict)

    @property
    def governing(self):
        allc = [c for c in self.checks]
        return max(allc, key=lambda c: c.ratio) if allc else None

    @property
    def ok(self):
        return all(c.ok for c in self.checks) and all(l.ok for l in self.limits if not l.name.startswith('(Rec.)'))


def _kN(x):
    return x / 1e3


def _kNm(x):
    return x / 1e6


# ---------------------------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------------------------
def classify(sec, Fy, E=E_STEEL):
    """Devuelve lista de (elemento, λ, λp, λr_flexión, clase_flexión, λr_compresión, clase_compresión)."""
    out = []
    r = math.sqrt(E / Fy)
    if isinstance(sec, (S.ISection, S.ChannelSection)):
        is_I = isinstance(sec, S.ISection)
        lam_f = (sec.bf / 2 if is_I else sec.bf) / sec.tf
        rolled = sec.rolled if is_I else True
        kc = A.kc(sec.h, sec.tw)
        if rolled:
            lrc = A.B4_1a_limit('ala_laminada', Fy, E)
            lp, lr = A.B4_1b_limits('ala_laminada', Fy, E)
        else:
            lrc = A.B4_1a_limit('ala_armada', Fy, E, kc=kc)
            lp, lr = A.B4_1b_limits('ala_armada', Fy, E, kc=kc)
        out.append(('Ala (b/t)', lam_f, lp, lr, A.classify_flexure(lam_f, lp, lr), lrc,
                    NONSLENDER if lam_f <= lrc else SLENDER))
        lam_w = sec.h / sec.tw
        lp, lr = A.B4_1b_limits('alma_I', Fy, E)
        lrc = A.B4_1a_limit('alma_I', Fy, E)
        out.append(('Alma (h/tw)', lam_w, lp, lr, A.classify_flexure(lam_w, lp, lr), lrc,
                    NONSLENDER if lam_w <= lrc else SLENDER))
    elif isinstance(sec, S.AngleSection):
        lam = sec.b_long / sec.t
        lp, lr = A.B4_1b_limits('ala_angulo', Fy, E)
        lrc = A.B4_1a_limit('ala_angulo', Fy, E)
        out.append(('Ala del ángulo (b/t)', lam, lp, lr, A.classify_flexure(lam, lp, lr), lrc,
                    NONSLENDER if lam <= lrc else SLENDER))
    elif isinstance(sec, S.RectHSS):
        lrc = A.B4_1a_limit('pared_hss_rect', Fy, E)
        lam_f = sec.b_flat / sec.t
        lp, lr = A.B4_1b_limits('ala_hss_rect', Fy, E)
        out.append(('Ala (b/t), b = B − 3t', lam_f, lp, lr, A.classify_flexure(lam_f, lp, lr), lrc,
                    NONSLENDER if lam_f <= lrc else SLENDER))
        lam_w = sec.h_flat / sec.t
        lp, lr = A.B4_1b_limits('alma_hss_rect', Fy, E)
        out.append(('Alma (h/t), h = H − 3t', lam_w, lp, lr, A.classify_flexure(lam_w, lp, lr), lrc,
                    NONSLENDER if lam_w <= lrc else SLENDER))
    elif isinstance(sec, S.RoundHSS):
        lam = sec.D / sec.t
        lp, lr = A.B4_1b_limits('hss_circ', Fy, E)
        lrc = A.B4_1a_limit('pared_hss_circ', Fy, E)
        out.append(('Pared (D/t)', lam, lp, lr, A.classify_flexure(lam, lp, lr), lrc,
                    NONSLENDER if lam <= lrc else SLENDER))
    return out


# ---------------------------------------------------------------------------------------------
# Tracción (Capítulo D)
# ---------------------------------------------------------------------------------------------
def tension_strength(inp: MemberInput):
    sec, m = inp.section, inp.method
    y = A.D2_yielding(inp.Fy, sec.A)
    An = inp.An if inp.An > 0 else sec.A
    Ae = An * inp.U
    r = A.D2_rupture(inp.Fu, Ae)
    r.steps.insert(0, 'An = %.0f mm², U = %.3f → Ae = U·An = %.0f mm² (D3-1)' % (An, inp.U, Ae))
    cy, cr = y.available(m), r.available(m)
    checks = [
        Check('Tracción: fluencia en sección bruta', 'D2(a)', _kN(abs(inp.P)), _kN(cy), 'kN',
              y.steps + ['%s → resistencia disponible = %.1f kN' % (factor_text(y.phi, y.omega, m), _kN(cy))]),
        Check('Tracción: rotura en sección neta efectiva', 'D2(b)', _kN(abs(inp.P)), _kN(cr), 'kN',
              r.steps + ['%s → resistencia disponible = %.1f kN' % (factor_text(r.phi, r.omega, m), _kN(cr))]),
    ]
    return min(cy, cr), checks


# ---------------------------------------------------------------------------------------------
# Compresión (Capítulo E)
# ---------------------------------------------------------------------------------------------
def compression_strength(inp: MemberInput, cls):
    sec, Fy, E, G = inp.section, inp.Fy, inp.E, inp.G
    Lcx, Lcy, Lcz = inp.Kx * inp.Lx, inp.Ky * inp.Ly, inp.Kz * inp.Lz
    steps = ['Lcx = Kx·Lx = %.0f mm, Lcy = Ky·Ly = %.0f mm, Lcz = Kz·Lz = %.0f mm' % (Lcx, Lcy, Lcz)]
    limits = []
    Fe_list = []

    if isinstance(sec, S.AngleSection) and inp.angle_E5:
        # E5: ángulos simples cargados a través de un ala
        ra = math.sqrt((sec.Ix if not inp.angle_short_leg_connected else sec.Iy) / sec.A)
        L = max(inp.Lx, inp.Ly)
        Lc_r = A.E5_single_angle_slenderness(L, ra, inp.angle_truss_web, inp.angle_short_leg_connected,
                                             sec.b_long, sec.b_short, sec.r_min)
        Fe = math.pi ** 2 * E / Lc_r ** 2
        steps.append('Ángulo simple conectado por un ala (E5): L/ra = %.1f → Lc/r = %.1f' % (L / ra, Lc_r))
        steps.append('Fe = π²E/(Lc/r)² = %.1f MPa (E3-4)' % Fe)
        Fe_list.append(('E5', Fe))
        slend = Lc_r
    else:
        if isinstance(sec, S.AngleSection):
            r_min = sec.r_min
            Lc = max(Lcx, Lcy)
            Fe_z = A.E3_Fe(Lc, r_min, E)
            steps.append('Pandeo por flexión en eje principal menor: Lc/rz = %.1f → Fe = %.1f MPa (E3-4)'
                         % (Lc / r_min, Fe_z))
            Fe_list.append(('E3 (eje z)', Fe_z))
            slend = Lc / r_min
            if sec.b_long / sec.t > 0.71 * math.sqrt(E / Fy):
                # E4: pandeo flexotorsional usando ejes principales
                rw = math.sqrt(sec.Iw_major / sec.A)
                Few = A.E3_Fe(Lc, rw, E)
                ro2 = sec.xo ** 2 + sec.yo ** 2 + (sec.Iw_major + sec.Iz_minor) / sec.A
                Fez = A.E4_Fez(sec.Cw, sec.J, sec.A, ro2, Lcz, E, G)
                Fe_ft = A.E4_Fe_unsymmetric(Few, Fe_z, Fez, sec.xo, sec.yo, ro2) if not sec.equal_legs else \
                    A.E4_Fe_singly_symmetric(Few, Fez, 1 - (sec.xo ** 2 + sec.yo ** 2) / ro2)
                steps.append('b/t > 0.71√(E/Fy): pandeo flexotorsional (E4): Fe = %.1f MPa' % Fe_ft)
                Fe_list.append(('E4', Fe_ft))
        elif isinstance(sec, (S.RectHSS, S.RoundHSS)):
            Fex, Fey = A.E3_Fe(Lcx, sec.rx, E), A.E3_Fe(Lcy, sec.ry, E)
            steps.append('Lcx/rx = %.1f → Fex = %.1f MPa ; Lcy/ry = %.1f → Fey = %.1f MPa (E3-4)'
                         % (Lcx / sec.rx, Fex, Lcy / sec.ry, Fey))
            Fe_list += [('E3 (eje x)', Fex), ('E3 (eje y)', Fey)]
            slend = max(Lcx / sec.rx, Lcy / sec.ry)
        elif isinstance(sec, S.ISection):
            Fex, Fey = A.E3_Fe(Lcx, sec.rx, E), A.E3_Fe(Lcy, sec.ry, E)
            Fez = A.E4_Fe_doubly_symmetric(sec.Cw, sec.J, sec.Ix, sec.Iy, Lcz, E, G)
            steps.append('Lcx/rx = %.1f → Fex = %.1f MPa ; Lcy/ry = %.1f → Fey = %.1f MPa (E3-4)'
                         % (Lcx / sec.rx, Fex, Lcy / sec.ry, Fey))
            steps.append('Pandeo torsional: Fe = (π²ECw/Lcz² + GJ)/(Ix+Iy) = %.1f MPa (E4-2)' % Fez)
            Fe_list += [('E3 (eje x)', Fex), ('E3 (eje y)', Fey), ('E4 (torsional)', Fez)]
            slend = max(Lcx / sec.rx, Lcy / sec.ry)
        elif isinstance(sec, S.ChannelSection):
            # Eje de simetría = x (eje mayor)
            Fex, Fey = A.E3_Fe(Lcx, sec.rx, E), A.E3_Fe(Lcy, sec.ry, E)
            ro2 = sec.xo ** 2 + (sec.Ix + sec.Iy) / sec.A
            H = 1 - sec.xo ** 2 / ro2
            Fez = A.E4_Fez(sec.Cw, sec.J, sec.A, ro2, Lcz, E, G)
            Fe_ft = A.E4_Fe_singly_symmetric(Fex, Fez, H)
            steps.append('Lcy/ry = %.1f → Fey = %.1f MPa (E3-4)' % (Lcy / sec.ry, Fey))
            steps.append('Flexotorsional (eje de simetría x): xo = %.1f mm, r̄o² = %.0f mm², H = %.3f, Fez = %.1f MPa, '
                         'Fe = %.1f MPa (E4-3, E4-9)' % (sec.xo, ro2, H, Fez, Fe_ft))
            Fe_list += [('E3 (eje y)', Fey), ('E4 (flexotorsional)', Fe_ft)]
            slend = max(Lcx / sec.rx, Lcy / sec.ry)
        else:
            raise TypeError('Sección no soportada')
    limits.append(Limit('(Rec.) Esbeltez Lc/r ≤ 200', 'E2 (nota)', slend, 200.0, 'max', ''))

    gov, Fe = min(Fe_list, key=lambda x: x[1])
    Fcr, txt = A.E3_Fcr(Fy, Fe)
    steps.append('Gobierna %s: Fe = %.1f MPa' % (gov, Fe))
    steps.append(txt + ' = %.1f MPa' % Fcr)

    # Elementos esbeltos (E7)
    Ae = sec.A
    slender_elems = [c for c in cls if c[6] == SLENDER]
    if slender_elems:
        if isinstance(sec, S.RoundHSS):
            Ae = A.E7_round_hss_area(sec.A, sec.D / sec.t, Fy, E)
            steps.append('Pared esbelta: Ae = [0.038E/(Fy·D/t) + 2/3]·Ag = %.0f mm² (E7-7)' % Ae)
        else:
            for name, lam, *_rest in slender_elems:
                lam_r = _rest[3]
                if isinstance(sec, S.ISection):
                    if name.startswith('Ala'):
                        b, t, n, key = sec.bf / 2, sec.tf, 4, 'no_atiesado'
                    else:
                        b, t, n, key = sec.h, sec.tw, 1, 'atiesado'
                elif isinstance(sec, S.ChannelSection):
                    if name.startswith('Ala'):
                        b, t, n, key = sec.bf, sec.tf, 2, 'no_atiesado'
                    else:
                        b, t, n, key = sec.h, sec.tw, 1, 'atiesado'
                elif isinstance(sec, S.AngleSection):
                    b, t, n, key = sec.b_long, sec.t, 1, 'no_atiesado'
                    # ala corta
                    lam_s = sec.b_short / sec.t
                    if lam_s > lam_r * math.sqrt(Fy / Fcr):
                        c1, c2 = A.E7_C['no_atiesado']
                        be_s = A.E7_effective_width(sec.b_short, lam_s, lam_r, Fy, Fcr, c1, c2)
                        Ae -= (sec.b_short - be_s) * t
                else:  # RectHSS
                    if name.startswith('Ala'):
                        b, t, n, key = sec.b_flat, sec.t, 2, 'pared_hss'
                    else:
                        b, t, n, key = sec.h_flat, sec.t, 2, 'pared_hss'
                c1, c2 = A.E7_C[key]
                be = A.E7_effective_width(b, lam, lam_r, Fy, Fcr, c1, c2)
                Ae -= n * (b - be) * t
                steps.append('%s esbelta: λ = %.1f, be = %.1f mm de b = %.1f mm (c1 = %.2f, c2 = %.2f; E7-3)'
                             % (name, lam, be, b, c1, c2))
        steps.append('Área efectiva Ae = %.0f mm² (Ag = %.0f mm²)' % (Ae, sec.A))
    Pn = Fcr * Ae
    phi, omega = 0.90, 1.67
    steps.append('Pn = Fcr·Ae = %.1f × %.0f = %.1f kN (E3-1 / E7-1)' % (Fcr, Ae, _kN(Pn)))
    cap = available_strength(Pn, phi, omega, inp.method)
    steps.append('%s → resistencia disponible = %.1f kN' % (factor_text(phi, omega, inp.method), _kN(cap)))
    check = Check('Compresión axial (pandeo)', 'E3/E4/E7', _kN(abs(inp.P)), _kN(cap), 'kN', steps)
    return cap, [check], limits


# ---------------------------------------------------------------------------------------------
# Flexión (Capítulo F)
# ---------------------------------------------------------------------------------------------
def flexure_major(inp: MemberInput, cls):
    sec, Fy, E = inp.section, inp.Fy, inp.E
    Lb, Cb = inp.Lb, inp.Cb
    steps = []
    clause = ''
    if isinstance(sec, (S.ISection, S.ChannelSection)):
        web_cls = cls[1][4]
        flange_cls = cls[0][4]
        if isinstance(sec, S.ISection) and web_cls != COMPACT:
            Mn, st = A.F4_F5_I_noncompact_web(sec, Fy, Lb, Cb, E)
            steps += st
            clause = 'F4' if web_cls == NONCOMPACT else 'F5'
        else:
            Mp = Fy * sec.Zx
            steps.append('Mp = Fy·Zx = %.1f × %.0f = %.2f kN·m (F2-1)' % (Fy, sec.Zx, _kNm(Mp)))
            c = 1.0 if isinstance(sec, S.ISection) else sec.c_ltb
            Lp, Lr = A.F2_limits(sec.ry, sec.rts, sec.J, sec.Sx, sec.ho, Fy, E, c)
            steps.append('rts = %.1f mm, ho = %.1f mm, c = %.3f → Lp = %.0f mm, Lr = %.0f mm (F2-5, F2-6)'
                         % (sec.rts, sec.ho, c, Lp, Lr))
            Mn, st = A.F2_LTB(Mp, sec.Sx, Fy, Lb, Lp, Lr, Cb, sec.rts, sec.J, sec.ho, c, E)
            steps += st
            clause = 'F2'
            if flange_cls != COMPACT:
                lam = cls[0][1]
                kc = A.kc(sec.h, sec.tw)
                Mf, txt = A.F3_FLB(Mp, sec.Sx, Fy, lam, cls[0][2], cls[0][3], kc, E)
                steps.append(txt + ' → Mn(FLB) = %.2f kN·m' % _kNm(Mf))
                Mn = min(Mn, Mf)
                clause = 'F3' if isinstance(sec, S.ISection) else 'F2/F3'
    elif isinstance(sec, S.RectHSS):
        Mn, st, _ = A.F7_rect_hss(sec, Fy, Lb, Cb, 'x', E)
        steps += st
        clause = 'F7'
    elif isinstance(sec, S.RoundHSS):
        Mn, txt, _ = A.F8_round_hss(Fy, sec.Zx, sec.Sx, sec.D / sec.t, E)
        steps.append(txt)
        clause = 'F8'
    elif isinstance(sec, S.AngleSection):
        Mn, st = A.F10_single_angle(sec, Fy, Lb, Cb, E)
        steps += st
        clause = 'F10'
    else:
        raise TypeError
    steps.append('Mn = %.2f kN·m' % _kNm(Mn))
    cap = available_strength(Mn, 0.90, 1.67, inp.method)
    steps.append('%s → resistencia disponible = %.2f kN·m' % (factor_text(0.90, 1.67, inp.method), _kNm(cap)))
    return cap, Check('Flexión en eje mayor (x)', clause, _kNm(abs(inp.Mx)), _kNm(cap), 'kN·m', steps)


def flexure_minor(inp: MemberInput, cls):
    sec, Fy, E = inp.section, inp.Fy, inp.E
    steps = []
    if isinstance(sec, (S.ISection, S.ChannelSection)):
        lam, lp, lr = cls[0][1], *A.B4_1b_limits('ala_I_eje_menor', Fy, E)
        Mn, txt = A.F6_minor_axis(Fy, sec.Zy, sec.Sy, lam, lp, lr, E)
        steps.append('λ = b/t = %.2f, λp = %.2f, λr = %.2f' % (lam, lp, lr))
        steps.append(txt)
        clause = 'F6'
    elif isinstance(sec, S.RectHSS):
        Mn, st, _ = A.F7_rect_hss(sec, Fy, inp.Lb, inp.Cb, 'y', E)
        steps += st
        clause = 'F7'
    elif isinstance(sec, S.RoundHSS):
        Mn, txt, _ = A.F8_round_hss(Fy, sec.Zy, sec.Sy, sec.D / sec.t, E)
        steps.append(txt)
        clause = 'F8'
    elif isinstance(sec, S.AngleSection):
        Mn, st = A.F10_single_angle(sec, Fy, inp.Lb, inp.Cb, E)
        steps += st
        clause = 'F10'
    else:
        raise TypeError
    steps.append('Mn = %.2f kN·m' % _kNm(Mn))
    cap = available_strength(Mn, 0.90, 1.67, inp.method)
    steps.append('%s → resistencia disponible = %.2f kN·m' % (factor_text(0.90, 1.67, inp.method), _kNm(cap)))
    return cap, Check('Flexión en eje menor (y)', clause, _kNm(abs(inp.My)), _kNm(cap), 'kN·m', steps)


# ---------------------------------------------------------------------------------------------
# Corte (Capítulo G)
# ---------------------------------------------------------------------------------------------
def shear_major(inp: MemberInput):
    sec, Fy, E = inp.section, inp.Fy, inp.E
    if isinstance(sec, (S.ISection, S.ChannelSection)):
        rolled = sec.rolled if isinstance(sec, S.ISection) else True
        kv = A.G2_kv(inp.stiffener_a or None, sec.h, inp.stiffener_a > 0)
        nom = A.G2_1_I_web(sec, Fy, kv, rolled and inp.stiffener_a <= 0, E)
        clause = 'G2.1'
    elif isinstance(sec, S.RectHSS):
        nom = A.G4_rect_hss(Fy, sec.h_flat, sec.t, E)
        clause = 'G4'
    elif isinstance(sec, S.RoundHSS):
        nom = A.G5_round_hss(Fy, sec.A, sec.D, sec.t, inp.Lx / 2, E)
        nom.steps.insert(0, 'Lv = L/2 = %.0f mm (corte máximo a nulo, viga simplemente apoyada)' % (inp.Lx / 2))
        clause = 'G5'
    elif isinstance(sec, S.AngleSection):
        nom = A.G3_angle_tee(Fy, sec.b_long, sec.t, E)
        clause = 'G3'
    else:
        raise TypeError
    cap = nom.available(inp.method)
    nom.steps.append('%s → resistencia disponible = %.1f kN' % (factor_text(nom.phi, nom.omega, inp.method), _kN(cap)))
    return cap, Check('Corte paralelo al alma (eje y)', clause, _kN(abs(inp.Vy)), _kN(cap), 'kN', nom.steps)


def shear_minor(inp: MemberInput):
    sec, Fy, E = inp.section, inp.Fy, inp.E
    if isinstance(sec, S.ISection):
        nom = A.G6_weak_axis(Fy, sec.bf, sec.tf, 2, E)
        clause = 'G6'
    elif isinstance(sec, S.ChannelSection):
        # Para canales b = bf completo
        Cv2 = A.G_Cv2(sec.bf / sec.tf, Fy, 1.2, E)
        Vn = 2 * 0.6 * Fy * sec.bf * sec.tf * Cv2
        from .norma import Nominal
        nom = Nominal(Vn, 0.90, 1.67, ['b/tf = %.2f, kv = 1.2, Cv2 = %.3f' % (sec.bf / sec.tf, Cv2),
                                       'Vn = 2 × 0.6·Fy·bf·tf·Cv2 = %.1f kN (G6-1)' % _kN(Vn)])
        clause = 'G6'
    elif isinstance(sec, S.RectHSS):
        nom = A.G4_rect_hss(Fy, sec.b_flat, sec.t, E)
        clause = 'G4'
    elif isinstance(sec, S.RoundHSS):
        nom = A.G5_round_hss(Fy, sec.A, sec.D, sec.t, inp.Lx / 2, E)
        clause = 'G5'
    elif isinstance(sec, S.AngleSection):
        nom = A.G3_angle_tee(Fy, sec.b_short, sec.t, E)
        clause = 'G3'
    else:
        raise TypeError
    cap = nom.available(inp.method)
    nom.steps.append('%s → resistencia disponible = %.1f kN' % (factor_text(nom.phi, nom.omega, inp.method), _kN(cap)))
    return cap, Check('Corte paralelo a las alas (eje x)', clause, _kN(abs(inp.Vx)), _kN(cap), 'kN', nom.steps)


# ---------------------------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------------------------
def check_member(inp: MemberInput) -> MemberResult:
    res = MemberResult()
    sec = inp.section
    cls = classify(sec, inp.Fy, inp.E)
    res.classification = cls
    m = inp.method
    res.notes.append('Método de diseño: %s (AISC 360-22, %s)' % (m, 'B3.1' if m == LRFD else 'B3.2'))
    res.notes.append('Las solicitaciones deben incluir efectos de segundo orden (Capítulo C, método de análisis directo).')

    Pc = 0.0
    if inp.P > 0:      # tracción
        Pc, ch = tension_strength(inp)
        res.checks += ch
        L = max(inp.Lx, inp.Ly)
        r_min = sec.r_min if isinstance(sec, S.AngleSection) else min(sec.rx, sec.ry)
        res.limits.append(Limit('(Rec.) Esbeltez L/r ≤ 300', 'D1 (nota)', L / r_min, 300.0, 'max'))
    elif inp.P < 0:    # compresión
        Pc, ch, lim = compression_strength(inp, cls)
        res.checks += ch
        res.limits += lim
    res.capacities['Pc'] = Pc

    Mcx = Mcy = 0.0
    Cb_orig = inp.Cb
    if inp.Mx or inp.P == 0:
        if inp.P > 0 and isinstance(sec, S.ISection):
            # H1.2: Cb amplificado por tracción axial
            Pey = math.pi ** 2 * inp.E * sec.Iy / max(inp.Lb, 1.0) ** 2
            inp.Cb = A.H1_2_Cb_tension(Cb_orig, inp.P, Pey, m)
            res.notes.append('H1.2: Cb amplificado por tracción = %.3f' % inp.Cb)
        try:
            Mcx, ch = flexure_major(inp, cls)
        finally:
            inp.Cb = Cb_orig
        res.checks.append(ch)
    if inp.My:
        Mcy, ch = flexure_minor(inp, cls)
        res.checks.append(ch)
    res.capacities['Mcx'], res.capacities['Mcy'] = Mcx, Mcy

    if inp.Vy or (inp.P == 0 and inp.Mx):
        Vc, ch = shear_major(inp)
        res.checks.append(ch)
        res.capacities['Vcy'] = Vc
    if inp.Vx:
        Vc, ch = shear_minor(inp)
        res.checks.append(ch)
        res.capacities['Vcx'] = Vc

    # Interacción (H1)
    if inp.P != 0 and (inp.Mx or inp.My):
        val, eq = A.H1_1_interaction(inp.P, Pc, inp.Mx, Mcx, inp.My, Mcy if Mcy else 1.0)
        steps = ['Pr/Pc = %.1f/%.1f = %.3f' % (_kN(abs(inp.P)), _kN(Pc), abs(inp.P) / Pc),
                 'Mrx/Mcx = %.2f/%.2f = %.3f' % (_kNm(abs(inp.Mx)), _kNm(Mcx), abs(inp.Mx) / Mcx if Mcx else 0)]
        if inp.My:
            steps.append('Mry/Mcy = %.2f/%.2f = %.3f' % (_kNm(abs(inp.My)), _kNm(Mcy), abs(inp.My) / Mcy))
        steps += [eq, 'Valor de interacción = %.3f' % val]
        res.checks.append(Check('Interacción carga axial + flexión', 'H1.1' if inp.P < 0 else 'H1.2',
                                val, 1.0, '-', steps))
    return res
