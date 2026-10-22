"""ANSI/AISC 360-22 — Especificación para edificaciones de acero estructural.

Funciones por cláusula, en unidades N, mm, MPa. Cada función devuelve un objeto ``Nominal``
con la resistencia nominal Rn, los factores phi (LRFD) y Omega (ASD) y el desarrollo del cálculo
en español (``steps``), listo para la memoria de cálculo.

Capítulos cubiertos:
    B4  Clasificación de secciones por pandeo local (Tablas B4.1a y B4.1b)
    D   Miembros a tracción (D2, D3)
    E   Miembros a compresión (E3, E4, E5, E7)
    F   Miembros a flexión (F2, F3, F4, F5, F6, F7, F8, F10, F13)
    G   Corte (G2.1, G2.2, G3, G4, G5, G6)
    H   Solicitaciones combinadas (H1.1, H1.2)
    J   Conexiones (J2 soldaduras, J3 pernos, J4 elementos, J7 aplastamiento, J8 bases,
        J10 alas y almas con cargas concentradas) y acción de palanca (Manual AISC, Parte 9)
"""
import math
from dataclasses import dataclass, field

from .base import E_STEEL, G_STEEL, LRFD, ASD, available_strength

COMPACT = 'Compacta'
NONCOMPACT = 'No compacta'
SLENDER = 'Esbelta'
NONSLENDER = 'No esbelta'


@dataclass
class Nominal:
    Rn: float
    phi: float
    omega: float
    steps: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def available(self, method=LRFD):
        return available_strength(self.Rn, self.phi, self.omega, method)


def _f(x, nd=1):
    return ('%.' + str(nd) + 'f') % x


class AISC360_22:
    """Cálculos según AISC 360-22. Todos los métodos son estáticos."""

    E = E_STEEL
    G = G_STEEL

    # =========================================================================================
    # B4  CLASIFICACIÓN DE SECCIONES
    # =========================================================================================
    @staticmethod
    def B4_1a_limit(case, Fy, E=E_STEEL, kc=None):
        """Límite λr para elementos en compresión axial (Tabla B4.1a).

        case: 'ala_laminada' (caso 1), 'ala_armada' (2), 'ala_angulo' (3), 'alma_te' (4),
              'alma_I' (5), 'pared_hss_rect' (6), 'pared_hss_circ' (9; devuelve límite D/t),
              'otros_atiesados' (8)
        """
        r = math.sqrt(E / Fy)
        if case == 'ala_laminada':
            return 0.56 * r
        if case == 'ala_armada':
            return 0.64 * math.sqrt(kc * E / Fy)
        if case == 'ala_angulo':
            return 0.45 * r
        if case == 'alma_te':
            return 0.75 * r
        if case == 'alma_I':
            return 1.49 * r
        if case == 'pared_hss_rect':
            return 1.40 * r
        if case == 'otros_atiesados':
            return 1.49 * r
        if case == 'pared_hss_circ':
            return 0.11 * E / Fy
        raise ValueError(case)

    @staticmethod
    def kc(h, tw):
        """kc = 4/sqrt(h/tw), 0.35 <= kc <= 0.76 (Tabla B4.1, nota [a])."""
        return min(max(4 / math.sqrt(h / tw), 0.35), 0.76)

    @staticmethod
    def B4_1b_limits(case, Fy, E=E_STEEL, kc=None, FL=None):
        """(λp, λr) para elementos en flexión (Tabla B4.1b).

        case: 'ala_laminada' (10), 'ala_armada' (11), 'ala_angulo' (12), 'ala_I_eje_menor' (13),
              'alma_te' (14), 'alma_I' (15), 'ala_hss_rect' (17), 'alma_hss_rect' (19), 'hss_circ' (20)
        """
        r = math.sqrt(E / Fy)
        if case in ('ala_laminada', 'ala_I_eje_menor'):
            return 0.38 * r, 1.0 * r
        if case == 'ala_armada':
            FL = FL if FL else 0.7 * Fy
            return 0.38 * r, 0.95 * math.sqrt(kc * E / FL)
        if case == 'ala_angulo':
            return 0.54 * r, 0.91 * r
        if case == 'alma_te':
            return 0.84 * r, 1.52 * r
        if case == 'alma_I':
            return 3.76 * r, 5.70 * r
        if case == 'ala_hss_rect':
            return 1.12 * r, 1.40 * r
        if case == 'alma_hss_rect':
            return 2.42 * r, 5.70 * r
        if case == 'hss_circ':
            return 0.07 * E / Fy, 0.31 * E / Fy
        raise ValueError(case)

    @staticmethod
    def classify_flexure(lam, lam_p, lam_r):
        if lam <= lam_p:
            return COMPACT
        if lam <= lam_r:
            return NONCOMPACT
        return SLENDER

    # =========================================================================================
    # D  TRACCIÓN
    # =========================================================================================
    @staticmethod
    def D2_yielding(Fy, Ag):
        """Fluencia en la sección bruta, ec. D2-1: Pn = Fy Ag ; phi = 0.90, Omega = 1.67."""
        Pn = Fy * Ag
        return Nominal(Pn, 0.90, 1.67, ['Pn = Fy·Ag = %s × %s = %s kN' % (_f(Fy), _f(Ag), _f(Pn / 1e3))])

    @staticmethod
    def D2_rupture(Fu, Ae):
        """Rotura en la sección neta efectiva, ec. D2-2: Pn = Fu Ae ; phi = 0.75, Omega = 2.00."""
        Pn = Fu * Ae
        return Nominal(Pn, 0.75, 2.00, ['Pn = Fu·Ae = %s × %s = %s kN' % (_f(Fu), _f(Ae), _f(Pn / 1e3))])

    @staticmethod
    def D3_shear_lag(case, **kw):
        """Factor de retraso de corte U (Tabla D3.1).

        case 1: todos los elementos conectados                        -> U = 1.0
        case 2: U = 1 - x̄/l
        case 7: perfiles W, M, S, HP o T: conexión por alas con >= 3 pernos por línea:
                bf >= 2/3 d -> 0.90 ; bf < 2/3 d -> 0.85 ; conexión por alma con >= 4 pernos -> 0.70
        case 8: ángulos simples/dobles: >= 4 pernos por línea -> 0.80 ; 3 pernos -> 0.60
        case 4: placas o ángulos soldados longitudinalmente: U = 3l²/(3l²+w²)·(1 - x̄/l)
        """
        if case == 1:
            return 1.0
        if case == 2:
            return max(0.0, 1 - kw['x_bar'] / kw['l']) if kw['l'] > 0 else 1.0
        if case == 4:
            l, w = kw['l'], kw['w']
            return (3 * l ** 2 / (3 * l ** 2 + w ** 2)) * (1 - kw.get('x_bar', 0.0) / l)
        if case == 7:
            if kw.get('web_connected'):
                return 0.70
            return 0.90 if kw['bf'] >= 2 / 3 * kw['d'] else 0.85
        if case == 8:
            return 0.80 if kw['n_bolts'] >= 4 else 0.60
        raise ValueError(case)

    # =========================================================================================
    # E  COMPRESIÓN
    # =========================================================================================
    @staticmethod
    def E3_Fcr(Fy, Fe):
        """Tensión crítica, ec. E3-2 / E3-3."""
        if Fy / Fe <= 2.25:
            return (0.658 ** (Fy / Fe)) * Fy, 'Fy/Fe = %.3f ≤ 2.25 → Fcr = 0.658^(Fy/Fe)·Fy (E3-2)' % (Fy / Fe)
        return 0.877 * Fe, 'Fy/Fe = %.3f > 2.25 → Fcr = 0.877·Fe (E3-3)' % (Fy / Fe)

    @staticmethod
    def E3_Fe(Lc, r, E=E_STEEL):
        """Tensión de pandeo elástico por flexión, ec. E3-4."""
        return math.pi ** 2 * E / (Lc / r) ** 2

    @staticmethod
    def E4_Fe_doubly_symmetric(Cw, J, Ix, Iy, Lcz, E=E_STEEL, G=G_STEEL):
        """Pandeo torsional, miembros doblemente simétricos, ec. E4-2."""
        return (math.pi ** 2 * E * Cw / Lcz ** 2 + G * J) / (Ix + Iy)

    @staticmethod
    def E4_Fe_singly_symmetric(Fey, Fez, H):
        """Pandeo flexotorsional, miembros con simetría simple (eje y de simetría), ec. E4-3."""
        s = Fey + Fez
        return s / (2 * H) * (1 - math.sqrt(max(0.0, 1 - 4 * Fey * Fez * H / s ** 2)))

    @staticmethod
    def E4_Fez(Cw, J, Ag, ro2, Lcz, E=E_STEEL, G=G_STEEL):
        """Ec. E4-9."""
        return (math.pi ** 2 * E * Cw / Lcz ** 2 + G * J) / (Ag * ro2)

    @staticmethod
    def E4_Fe_unsymmetric(Fex, Fey, Fez, xo, yo, ro2):
        """Menor raíz de la ecuación cúbica E4-6 (miembros asimétricos)."""
        def f(Fe):
            return ((Fe - Fex) * (Fe - Fey) * (Fe - Fez) - Fe ** 2 * (Fe - Fey) * (xo ** 2 / ro2)
                    - Fe ** 2 * (Fe - Fex) * (yo ** 2 / ro2))
        hi = min(Fex, Fey, Fez)
        lo = 0.0
        # f(0) = -Fex·Fey·Fez < 0 ; f(min) >= 0 -> raíz en (0, min]
        for _ in range(200):
            m = (lo + hi) / 2
            if f(m) < 0:
                lo = m
            else:
                hi = m
        return (lo + hi) / 2

    @staticmethod
    def E5_single_angle_slenderness(L, ra, truss_web=False, short_leg_connected=False, bl=None, bs=None,
                                    rz=None):
        """Esbeltez efectiva Lc/r de ángulos simples conectados por un ala (E5).

        ra: radio de giro respecto al eje geométrico paralelo al ala conectada.
        truss_web=False -> E5(a) (miembros individuales o almas de armaduras planas)
        truss_web=True  -> E5(b) (almas de armaduras espaciales/cajón)
        short_leg_connected: ángulo de alas desiguales conectado por el ala corta (bl/bs < 1.7):
            Lc/r se incrementa en 4[(bl/bs)² − 1] y Lc/r ≥ 0.95 L/rz.
        """
        lr = L / ra
        if not truss_web:
            Lc_r = 72 + 0.75 * lr if lr <= 80 else 32 + 1.25 * lr
        else:
            Lc_r = 60 + 0.8 * lr if lr <= 75 else 45 + lr
        if short_leg_connected and bl and bs:
            inc = 3.0 if truss_web else 4.0
            Lc_r += inc * ((bl / bs) ** 2 - 1)
            if rz:
                Lc_r = max(Lc_r, (0.82 if truss_web else 0.95) * L / rz)
        return min(Lc_r, 200.0)

    @staticmethod
    def E7_effective_width(b, lam, lam_r, Fy, Fcr, c1, c2):
        """Ancho efectivo de elementos esbeltos (E7.1, ecs. E7-2 a E7-5)."""
        if lam <= lam_r * math.sqrt(Fy / Fcr):
            return b
        Fel = (c2 * lam_r / lam) ** 2 * Fy
        be = b * (1 - c1 * math.sqrt(Fel / Fcr)) * math.sqrt(Fel / Fcr)
        return min(b, be)

    E7_C = {'atiesado': (0.18, 1.31), 'pared_hss': (0.20, 1.38), 'no_atiesado': (0.22, 1.49)}

    @staticmethod
    def E7_round_hss_area(Ag, D_t, Fy, E=E_STEEL):
        """Área efectiva de HSS circulares esbeltos, ec. E7-7."""
        if D_t <= 0.11 * E / Fy:
            return Ag
        return (0.038 * E / (Fy * D_t) + 2 / 3) * Ag

    # =========================================================================================
    # F  FLEXIÓN
    # =========================================================================================
    @staticmethod
    def F2_limits(ry, rts, J, Sx, ho, Fy, E=E_STEEL, c=1.0):
        """Lp (F2-5) y Lr (F2-6) para perfiles I compactos y canales."""
        Lp = 1.76 * ry * math.sqrt(E / Fy)
        jc = J * c / (Sx * ho)
        Lr = 1.95 * rts * E / (0.7 * Fy) * math.sqrt(jc + math.sqrt(jc ** 2 + 6.76 * (0.7 * Fy / E) ** 2))
        return Lp, Lr

    @staticmethod
    def F2_LTB(Mp, Sx, Fy, Lb, Lp, Lr, Cb, rts, J, ho, c=1.0, E=E_STEEL):
        """Pandeo lateral-torsional F2.2 (ecs. F2-2, F2-3, F2-4)."""
        steps = []
        if Lb <= Lp:
            steps.append('Lb = %s ≤ Lp = %s mm → no aplica pandeo lateral-torsional' % (_f(Lb), _f(Lp)))
            return Mp, steps
        if Lb <= Lr:
            Mn = Cb * (Mp - (Mp - 0.7 * Fy * Sx) * (Lb - Lp) / (Lr - Lp))
            steps.append('Lp < Lb = %s ≤ Lr = %s mm → Mn = Cb[Mp − (Mp − 0.7FySx)(Lb−Lp)/(Lr−Lp)] (F2-2)'
                         % (_f(Lb), _f(Lr)))
        else:
            x = (Lb / rts) ** 2
            Fcr = Cb * math.pi ** 2 * E / x * math.sqrt(1 + 0.078 * J * c / (Sx * ho) * x)
            Mn = Fcr * Sx
            steps.append('Lb = %s > Lr = %s mm → Fcr = Cbπ²E/(Lb/rts)²·√(1+0.078Jc/(Sxho)(Lb/rts)²) = %s MPa (F2-4)'
                         % (_f(Lb), _f(Lr), _f(Fcr)))
        Mn = min(Mn, Mp)
        steps.append('Mn(LTB) = %s kN·m' % _f(Mn / 1e6, 2))
        return Mn, steps

    @staticmethod
    def F3_FLB(Mp, Sx, Fy, lam, lam_pf, lam_rf, kc, E=E_STEEL):
        """Pandeo local del ala en compresión (F3.2)."""
        if lam <= lam_pf:
            return Mp, 'Ala compacta: no aplica F3.2'
        if lam <= lam_rf:
            Mn = Mp - (Mp - 0.7 * Fy * Sx) * (lam - lam_pf) / (lam_rf - lam_pf)
            return Mn, 'Ala no compacta: Mn = Mp − (Mp − 0.7FySx)(λ−λpf)/(λrf−λpf) (F3-1)'
        Mn = 0.9 * E * kc * Sx / lam ** 2
        return Mn, 'Ala esbelta: Mn = 0.9·E·kc·Sx/λ² (F3-2)'

    @staticmethod
    def F4_F5_I_noncompact_web(sec, Fy, Lb, Cb, E=E_STEEL):
        """Perfiles I doblemente simétricos con alma no compacta (F4) o esbelta (F5).

        sec: ISection. Devuelve (Mn, steps).
        """
        steps = []
        h, tw, bf, tf = sec.h, sec.tw, sec.bf, sec.tf
        hc = h
        lam_w = hc / tw
        lam_pw, lam_rw = AISC360_22.B4_1b_limits('alma_I', Fy, E)
        Sxc = Sxt = sec.Sx
        Myc = Fy * Sxc
        Mp = min(Fy * sec.Zx, 1.6 * Fy * sec.Sx)
        aw = hc * tw / (bf * tf)
        Iyc = sec.Iy / 2
        rt = bf / math.sqrt(12 * (1 + aw / 6))     # ec. F4-11
        if lam_w <= lam_rw:   # ---------------- F4 ----------------
            if Iyc / sec.Iy <= 0.23:
                Rpc = 1.0
            elif lam_w <= lam_pw:
                Rpc = Mp / Myc
            else:
                Rpc = min(Mp / Myc - (Mp / Myc - 1) * (lam_w - lam_pw) / (lam_rw - lam_pw), Mp / Myc)
            steps.append('Alma no compacta (F4): hc/tw = %.1f, Rpc = %.3f' % (lam_w, Rpc))
            M1 = Rpc * Myc   # fluencia del ala comprimida (F4-1)
            FL = 0.7 * Fy if Sxt / Sxc >= 0.7 else max(Fy * Sxt / Sxc, 0.5 * Fy)
            Lp = 1.1 * rt * math.sqrt(E / Fy)
            jj = sec.J / (Sxc * sec.ho)
            Lr = 1.95 * rt * E / FL * math.sqrt(jj + math.sqrt(jj ** 2 + 6.76 * (FL / E) ** 2))
            if Lb <= Lp:
                M2 = M1
            elif Lb <= Lr:
                M2 = min(Cb * (Rpc * Myc - (Rpc * Myc - FL * Sxc) * (Lb - Lp) / (Lr - Lp)), Rpc * Myc)
            else:
                x = (Lb / rt) ** 2
                Fcr = Cb * math.pi ** 2 * E / x * math.sqrt(1 + 0.078 * jj * x)
                M2 = min(Fcr * Sxc, Rpc * Myc)
            steps.append('Lp = %s mm, Lr = %s mm, Mn(LTB) = %s kN·m (F4-2)' % (_f(Lp), _f(Lr), _f(M2 / 1e6, 2)))
            lam_f = bf / (2 * tf)
            lam_pf, lam_rf = AISC360_22.B4_1b_limits('ala_laminada' if sec.rolled else 'ala_armada', Fy, E,
                                                     kc=AISC360_22.kc(h, tw), FL=FL)
            if lam_f <= lam_pf:
                M3 = M1
            elif lam_f <= lam_rf:
                M3 = Rpc * Myc - (Rpc * Myc - FL * Sxc) * (lam_f - lam_pf) / (lam_rf - lam_pf)
            else:
                M3 = 0.9 * E * AISC360_22.kc(h, tw) * Sxc / lam_f ** 2
            steps.append('Mn(FLB) = %s kN·m (F4-13/14)' % _f(M3 / 1e6, 2))
            return min(M1, M2, M3), steps
        # ---------------- F5 (alma esbelta) ----------------
        Rpg = min(1.0, 1 - aw / (1200 + 300 * aw) * (hc / tw - 5.7 * math.sqrt(E / Fy)))
        steps.append('Alma esbelta (F5): hc/tw = %.1f, aw = %.3f, Rpg = %.3f (F5-6)' % (lam_w, aw, Rpg))
        M1 = Rpg * Fy * Sxc
        Lp = 1.1 * rt * math.sqrt(E / Fy)
        Lr = math.pi * rt * math.sqrt(E / (0.7 * Fy))
        if Lb <= Lp:
            Fcr = Fy
        elif Lb <= Lr:
            Fcr = min(Cb * (Fy - 0.3 * Fy * (Lb - Lp) / (Lr - Lp)), Fy)
        else:
            Fcr = min(Cb * math.pi ** 2 * E / (Lb / rt) ** 2, Fy)
        M2 = Rpg * Fcr * Sxc
        lam_f = bf / (2 * tf)
        kc = AISC360_22.kc(h, tw)
        lam_pf, lam_rf = AISC360_22.B4_1b_limits('ala_armada', Fy, E, kc=kc)
        if lam_f <= lam_pf:
            Fcr_f = Fy
        elif lam_f <= lam_rf:
            Fcr_f = Fy - 0.3 * Fy * (lam_f - lam_pf) / (lam_rf - lam_pf)
        else:
            Fcr_f = 0.9 * E * kc / lam_f ** 2
        M3 = Rpg * Fcr_f * Sxc
        steps.append('Mn(LTB) = %s kN·m, Mn(FLB) = %s kN·m (F5-2, F5-7)' % (_f(M2 / 1e6, 2), _f(M3 / 1e6, 2)))
        return min(M1, M2, M3), steps

    @staticmethod
    def F6_minor_axis(Fy, Zy, Sy, lam, lam_p, lam_r, E=E_STEEL):
        """Perfiles I y canales, flexión en el eje menor (F6)."""
        Mp = min(Fy * Zy, 1.6 * Fy * Sy)
        if lam <= lam_p:
            return Mp, 'Ala compacta: Mn = Mp = min(FyZy, 1.6FySy) (F6-1)'
        if lam <= lam_r:
            Mn = Mp - (Mp - 0.7 * Fy * Sy) * (lam - lam_p) / (lam_r - lam_p)
            return Mn, 'Ala no compacta: Mn = Mp − (Mp − 0.7FySy)(λ−λpf)/(λrf−λpf) (F6-2)'
        Fcr = 0.70 * E / lam ** 2
        return Fcr * Sy, 'Ala esbelta: Mn = Fcr·Sy, Fcr = 0.70E/(b/t)² (F6-3, F6-4)'

    @staticmethod
    def F7_rect_hss(sec, Fy, Lb, Cb, axis='x', E=E_STEEL):
        """HSS rectangulares y cuadrados (F7). Devuelve (Mn, steps, clases)."""
        steps = []
        if axis == 'x':
            Z, S, b, h, I, r_other = sec.Zx, sec.Sx, sec.b_flat, sec.h_flat, sec.Ix, sec.ry
            Bf, Hd = sec.B, sec.H
        else:
            Z, S, b, h, I, r_other = sec.Zy, sec.Sy, sec.h_flat, sec.b_flat, sec.Iy, sec.rx
            Bf, Hd = sec.H, sec.B
        t = sec.t
        Mp = Fy * Z
        steps.append('Mp = Fy·Z = %s kN·m (F7-1)' % _f(Mp / 1e6, 2))
        lam_f, lam_w = b / t, h / t
        lp_f, lr_f = AISC360_22.B4_1b_limits('ala_hss_rect', Fy, E)
        lp_w, lr_w = AISC360_22.B4_1b_limits('alma_hss_rect', Fy, E)
        cls_f = AISC360_22.classify_flexure(lam_f, lp_f, lr_f)
        cls_w = AISC360_22.classify_flexure(lam_w, lp_w, lr_w)
        Mn = Mp
        # Pandeo local del ala (F7.2)
        if cls_f == NONCOMPACT:
            M = min(Mp, Mp - (Mp - Fy * S) * (3.57 * lam_f * math.sqrt(Fy / E) - 4.0))
            steps.append('Ala no compacta b/t = %.1f: Mn = %s kN·m (F7-2)' % (lam_f, _f(M / 1e6, 2)))
            Mn = min(Mn, M)
        elif cls_f == SLENDER:
            be = min(b, 1.92 * t * math.sqrt(E / Fy) * (1 - 0.38 / lam_f * math.sqrt(E / Fy)))
            # Se = I_efectivo / c, reduciendo el ala comprimida (b - be)
            dA = (b - be) * t
            yc = (Hd - t) / 2
            A_eff = sec.A - dA
            shift = dA * yc / A_eff
            Ie = I - dA * yc ** 2 + 0 - A_eff * shift ** 2
            Se = Ie / (Hd / 2 + shift)
            M = Fy * Se
            steps.append('Ala esbelta b/t = %.1f: be = %s mm, Se = %s cm³, Mn = FySe = %s kN·m (F7-3)'
                         % (lam_f, _f(be), _f(Se / 1e3), _f(M / 1e6, 2)))
            Mn = min(Mn, M)
        # Pandeo local del alma (F7.3)
        if cls_w == NONCOMPACT:
            M = min(Mp, Mp - (Mp - Fy * S) * (0.305 * lam_w * math.sqrt(Fy / E) - 0.738))
            steps.append('Alma no compacta h/t = %.1f: Mn = %s kN·m (F7-6)' % (lam_w, _f(M / 1e6, 2)))
            Mn = min(Mn, M)
        elif cls_w == SLENDER:
            # F7-7 a F7-9 (alma esbelta)
            aw = 2 * h * t / (Bf * t)
            Rpg = min(1.0, 1 - aw / (1200 + 300 * aw) * (lam_w - 5.70 * math.sqrt(E / Fy)))
            kc = 4.0
            Fcr = min(Fy, 0.9 * E * kc / lam_f ** 2)
            M = min(Rpg * Fy * S, Rpg * Fcr * S)
            steps.append('Alma esbelta h/t = %.1f: Rpg = %.3f, Mn = %s kN·m (F7-7)' % (lam_w, Rpg, _f(M / 1e6, 2)))
            Mn = min(Mn, M)
        # Pandeo lateral-torsional (F7.4), solo eje mayor y si H > B
        if axis == 'x' and sec.H > sec.B:
            Ag, J = sec.A, sec.J
            Lp = 0.13 * E * sec.ry * math.sqrt(J * Ag) / Mp
            Lr = 2 * E * sec.ry * math.sqrt(J * Ag) / (0.7 * Fy * S)
            if Lb > Lp:
                if Lb <= Lr:
                    M = min(Mp, Cb * (Mp - (Mp - 0.7 * Fy * S) * (Lb - Lp) / (Lr - Lp)))
                else:
                    M = min(Mp, 2 * E * Cb * math.sqrt(J * Ag) / (Lb / sec.ry))
                steps.append('LTB: Lp = %s mm, Lr = %s mm, Mn = %s kN·m (F7-10/11)'
                             % (_f(Lp), _f(Lr), _f(M / 1e6, 2)))
                Mn = min(Mn, M)
        return Mn, steps, (cls_f, cls_w)

    @staticmethod
    def F8_round_hss(Fy, Z, S, D_t, E=E_STEEL):
        """HSS circulares (F8). D/t debe ser < 0.45E/Fy."""
        lp, lr = AISC360_22.B4_1b_limits('hss_circ', Fy, E)
        Mp = Fy * Z
        if D_t <= lp:
            return Mp, 'Sección compacta: Mn = Mp = FyZ (F8-1)', COMPACT
        if D_t <= lr:
            return min(Mp, (0.021 * E / D_t + Fy) * S), 'No compacta: Mn = (0.021E/(D/t) + Fy)S (F8-2)', NONCOMPACT
        return min(Mp, 0.33 * E / D_t * S), 'Esbelta: Mn = Fcr·S, Fcr = 0.33E/(D/t) (F8-3, F8-4)', SLENDER

    @staticmethod
    def F10_single_angle(sec, Fy, Lb, Cb, E=E_STEEL):
        """Ángulos simples, flexión alrededor del eje geométrico paralelo al ala larga, con punta del ala
        corta en compresión y sin restricción lateral-torsional (F10, enfoque conservador: eje geométrico
        para alas iguales; eje principal mayor para alas desiguales)."""
        steps = []
        b, t = sec.b_short, sec.t
        My = 0.80 * Fy * min(sec.Sx, sec.Sy)   # F10.2(b): 0.80 My para eje geométrico sin restricción
        Mn1 = 1.5 * My
        steps.append('Fluencia: Mn = 1.5My = %s kN·m (F10-1)' % _f(Mn1 / 1e6, 2))
        # Pandeo lateral-torsional F10.2 (alas iguales, eje geométrico, ec. F10-5a, máximo compresión en la punta)
        if sec.equal_legs:
            bb = sec.b_long
            Mcr = 0.58 * E * bb ** 4 * t * Cb / Lb ** 2 * (math.sqrt(1 + 0.88 * (Lb * t / bb ** 2) ** 2) - 1)
            steps.append('Mcr = 0.58Eb⁴tCb/Lb²[√(1+0.88(Lbt/b²)²) − 1] = %s kN·m (F10-5a)' % _f(Mcr / 1e6, 2))
        else:
            Iz = sec.Iz_minor
            rz = math.sqrt(Iz / sec.A)
            # F10-6 con βw (aprox. conservador βw = 0)
            Mcr = 9 * E * sec.A * rz * t * Cb / (8 * Lb) * math.sqrt(1 + (4.4 * 0 * rz / (Lb * t)) ** 2)
            steps.append('Mcr (eje principal mayor, βw≈0) = %s kN·m (F10-6)' % _f(Mcr / 1e6, 2))
        if My / Mcr <= 1.0:
            Mn2 = (1.92 - 1.17 * math.sqrt(My / Mcr)) * My
        else:
            Mn2 = (0.92 - 0.17 * Mcr / My) * Mcr
        Mn2 = min(Mn2, 1.5 * My)
        steps.append('Mn(LTB) = %s kN·m (F10-2/F10-3)' % _f(Mn2 / 1e6, 2))
        # Pandeo local del ala (F10.3)
        lam = sec.b_long / t
        lp, lr = AISC360_22.B4_1b_limits('ala_angulo', Fy, E)
        Sc = 0.80 * min(sec.Sx, sec.Sy)   # 0.80 × módulo geométrico (sin restricción lateral-torsional)
        if lam <= lp:
            Mn3 = Mn1
        elif lam <= lr:
            Mn3 = Fy * Sc * (2.43 - 1.72 * lam * math.sqrt(Fy / E))
        else:
            Mn3 = 0.71 * E / lam ** 2 * Sc
        steps.append('Pandeo local del ala b/t = %.1f: Mn = %s kN·m (F10-7/8)' % (lam, _f(Mn3 / 1e6, 2)))
        return min(Mn1, Mn2, Mn3), steps

    @staticmethod
    def F13_holes_tension_flange(Fy, Fu, Afg, Afn, Sx):
        """Reducción por agujeros en el ala traccionada (F13.1). Devuelve Mn límite o None si no aplica."""
        Yt = 1.0 if Fy / Fu <= 0.8 else 1.1
        if Fu * Afn >= Yt * Fy * Afg:
            return None
        return Fu * Afn / Afg * Sx

    # =========================================================================================
    # G  CORTE
    # =========================================================================================
    @staticmethod
    def G2_1_Cv1(h_tw, Fy, kv=5.34, E=E_STEEL):
        lim = 1.10 * math.sqrt(kv * E / Fy)
        if h_tw <= lim:
            return 1.0
        return lim / h_tw

    @staticmethod
    def G2_kv(a=None, h=None, stiffened=False):
        """kv (G2-5): 5.34 sin rigidizadores o a/h > 3; 5 + 5/(a/h)² con rigidizadores."""
        if not stiffened or a is None or h is None or a / h > 3.0:
            return 5.34
        return 5 + 5 / (a / h) ** 2

    @staticmethod
    def G_Cv2(h_tw, Fy, kv, E=E_STEEL):
        """Coeficiente Cv2 (G2-9 a G2-11)."""
        l1 = 1.10 * math.sqrt(kv * E / Fy)
        l2 = 1.37 * math.sqrt(kv * E / Fy)
        if h_tw <= l1:
            return 1.0
        if h_tw <= l2:
            return l1 / h_tw
        return 1.51 * kv * E / (h_tw ** 2 * Fy)

    @staticmethod
    def G2_1_I_web(sec, Fy, kv=5.34, rolled=True, E=E_STEEL):
        """Corte en almas de perfiles I y canales (G2.1). Devuelve Nominal."""
        h_tw = sec.h / sec.tw
        Aw = sec.d * sec.tw
        steps = ['Aw = d·tw = %s × %s = %s mm²' % (_f(sec.d), _f(sec.tw), _f(Aw)),
                 'h/tw = %.2f' % h_tw]
        if rolled and h_tw <= 2.24 * math.sqrt(E / Fy):
            Cv1, phi, omega = 1.0, 1.00, 1.50
            steps.append('Perfil laminado con h/tw ≤ 2.24√(E/Fy) = %.2f → Cv1 = 1.0, φv = 1.00, Ωv = 1.50 (G2.1(a))'
                         % (2.24 * math.sqrt(E / Fy)))
        else:
            Cv1 = AISC360_22.G2_1_Cv1(h_tw, Fy, kv, E)
            phi, omega = 0.90, 1.67
            steps.append('kv = %.2f, Cv1 = %.3f (G2-3/G2-4), φv = 0.90, Ωv = 1.67' % (kv, Cv1))
        Vn = 0.6 * Fy * Aw * Cv1
        steps.append('Vn = 0.6·Fy·Aw·Cv1 = %s kN (G2-1)' % _f(Vn / 1e3))
        return Nominal(Vn, phi, omega, steps, {'Cv1': Cv1, 'h_tw': h_tw})

    @staticmethod
    def G2_2_tension_field(Fy, Aw, h, tw, a, bfc, tfc, bft, tft, E=E_STEEL):
        """Corte con acción de campo de tracción (G2.2). Requiere rigidizadores transversales."""
        kv = AISC360_22.G2_kv(a, h, True)
        h_tw = h / tw
        Cv2 = AISC360_22.G_Cv2(h_tw, Fy, kv, E)
        steps = ['kv = %.3f, Cv2 = %.3f' % (kv, Cv2)]
        cond = (2 * Aw / (bfc * tfc + bft * tft) <= 2.5 and h / bfc <= 6.0 and h / bft <= 6.0 and a / h <= 3.0)
        if h_tw <= 1.10 * math.sqrt(kv * E / Fy):
            Vn = 0.6 * Fy * Aw
            steps.append('h/tw ≤ 1.10√(kvE/Fy) → Vn = 0.6FyAw (G2-7)')
        elif cond:
            Vn = 0.6 * Fy * Aw * (Cv2 + (1 - Cv2) / (1.15 * math.sqrt(1 + (a / h) ** 2)))
            steps.append('Vn = 0.6FyAw[Cv2 + (1−Cv2)/(1.15√(1+(a/h)²))] (G2-8)')
        else:
            Vn = 0.6 * Fy * Aw * (Cv2 + (1 - Cv2) / (1.15 * (a / h + math.sqrt(1 + (a / h) ** 2))))
            steps.append('Panel extremo o proporciones fuera de límites → Vn según G2-9')
        steps.append('Vn = %s kN' % _f(Vn / 1e3))
        return Nominal(Vn, 0.90, 1.67, steps)

    @staticmethod
    def G3_angle_tee(Fy, b, t, E=E_STEEL):
        """Corte en ángulos simples y tes (G3): Vn = 0.6 Fy b t Cv2, kv = 1.2."""
        Cv2 = AISC360_22.G_Cv2(b / t, Fy, 1.2, E)
        Vn = 0.6 * Fy * b * t * Cv2
        return Nominal(Vn, 0.90, 1.67, ['b/t = %.2f, kv = 1.2, Cv2 = %.3f' % (b / t, Cv2),
                                        'Vn = 0.6·Fy·b·t·Cv2 = %s kN (G3-1)' % _f(Vn / 1e3)])

    @staticmethod
    def G4_rect_hss(Fy, h, t, E=E_STEEL):
        """Corte en HSS rectangulares (G4): Aw = 2ht, kv = 5, h = H - 3t."""
        Aw = 2 * h * t
        Cv2 = AISC360_22.G_Cv2(h / t, Fy, 5.0, E)
        Vn = 0.6 * Fy * Aw * Cv2
        return Nominal(Vn, 0.90, 1.67, ['Aw = 2ht = %s mm², h/t = %.2f, kv = 5, Cv2 = %.3f' % (_f(Aw), h / t, Cv2),
                                        'Vn = 0.6·Fy·Aw·Cv2 = %s kN (G4-1)' % _f(Vn / 1e3)])

    @staticmethod
    def G5_round_hss(Fy, Ag, D, t, Lv, E=E_STEEL):
        """Corte en HSS circulares (G5). Lv = distancia de corte máximo a corte nulo."""
        D_t = D / t
        Fcr1 = 1.60 * E / (math.sqrt(Lv / D) * D_t ** 1.25) if Lv > 0 else float('inf')
        Fcr2 = 0.78 * E / D_t ** 1.5
        Fcr = min(max(Fcr1, Fcr2), 0.6 * Fy)
        Vn = Fcr * Ag / 2
        return Nominal(Vn, 0.90, 1.67, ['Fcr = max(1.60E/(√(Lv/D)(D/t)^1.25), 0.78E/(D/t)^1.5) ≤ 0.6Fy = %s MPa'
                                        % _f(Fcr), 'Vn = Fcr·Ag/2 = %s kN (G5-1)' % _f(Vn / 1e3)])

    @staticmethod
    def G6_weak_axis(Fy, bf, tf, n_flanges=2, E=E_STEEL):
        """Corte en el eje menor de perfiles I y canales (G6): Vn = 0.6 Fy bf tf Cv2 por ala, kv = 1.2."""
        b_t = (bf / 2) / tf
        Cv2 = AISC360_22.G_Cv2(b_t, Fy, 1.2, E)
        Vn = n_flanges * 0.6 * Fy * bf * tf * Cv2
        return Nominal(Vn, 0.90, 1.67, ['b/tf = %.2f, kv = 1.2, Cv2 = %.3f' % (b_t, Cv2),
                                        'Vn = %d × 0.6·Fy·bf·tf·Cv2 = %s kN (G6-1)' % (n_flanges, _f(Vn / 1e3))])

    # =========================================================================================
    # H  SOLICITACIONES COMBINADAS
    # =========================================================================================
    @staticmethod
    def H1_1_interaction(Pr, Pc, Mrx, Mcx, Mry, Mcy):
        """Interacción flexión + carga axial (H1-1a / H1-1b). Devuelve (valor, ecuación)."""
        p = abs(Pr) / Pc if Pc > 0 else 0.0
        mx = abs(Mrx) / Mcx if Mcx > 0 else 0.0
        my = abs(Mry) / Mcy if Mcy > 0 else 0.0
        if p >= 0.2:
            return p + 8.0 / 9.0 * (mx + my), 'Pr/Pc ≥ 0.2 → Pr/Pc + 8/9(Mrx/Mcx + Mry/Mcy) ≤ 1.0 (H1-1a)'
        return p / 2 + (mx + my), 'Pr/Pc < 0.2 → Pr/(2Pc) + (Mrx/Mcx + Mry/Mcy) ≤ 1.0 (H1-1b)'

    @staticmethod
    def H1_2_Cb_tension(Cb, Pr, Pey, method=LRFD):
        """Factor de amplificación de Cb por tracción axial (H1.2): Cb·√(1 + αPr/Pey)."""
        alpha = 1.0 if method == LRFD else 1.6
        return Cb * math.sqrt(1 + alpha * Pr / Pey)

    # =========================================================================================
    # J2  SOLDADURAS
    # =========================================================================================
    @staticmethod
    def J2_min_fillet(t_thinner):
        """Tamaño mínimo de soldadura de filete (Tabla J2.4, mm), según el espesor de la parte más delgada."""
        if t_thinner <= 6:
            return 3.0
        if t_thinner <= 13:
            return 5.0
        if t_thinner <= 19:
            return 6.0
        return 8.0

    @staticmethod
    def J2_max_fillet(t_edge):
        """Tamaño máximo a lo largo de bordes (J2.2b): t si t < 6 mm; t − 2 mm si t ≥ 6 mm."""
        return t_edge if t_edge < 6 else t_edge - 2.0

    @staticmethod
    def J2_beta_long(l, w):
        """Reducción por longitud en soldaduras cargadas en el extremo (J2-1)."""
        if l <= 100 * w:
            return 1.0, l
        if l <= 300 * w:
            beta = min(1.0, 1.2 - 0.002 * (l / w))
            return beta, beta * l
        return 0.6, 180 * w

    @staticmethod
    def J2_fillet(w, L, FEXX, theta_deg=0.0, n=1, end_loaded=False, te=None):
        """Resistencia de soldadura de filete (J2.4, Tabla J2.5), ec. J2-3 y J2-5.

        Rn = Fnw·Awe ; Fnw = 0.60 FEXX (1 + 0.50 sin^1.5 θ) ; phi = 0.75, Omega = 2.00
        """
        te = te if te is not None else 0.707 * w
        steps = ['te = %s mm (garganta efectiva)' % _f(te, 2)]
        Le = L
        if end_loaded:
            beta, Le = AISC360_22.J2_beta_long(L, w)
            if beta < 1.0:
                steps.append('Soldadura larga cargada en el extremo: β = %.3f, Le = %s mm (J2-1)' % (beta, _f(Le)))
        kds = 1.0 + 0.50 * math.sin(math.radians(theta_deg)) ** 1.5
        Fnw = 0.60 * FEXX * kds
        Awe = te * Le * n
        Rn = Fnw * Awe
        steps.append('Fnw = 0.60·FEXX·(1 + 0.50 sin^1.5 θ) = 0.60 × %s × %.3f = %s MPa (J2-5)'
                     % (_f(FEXX), kds, _f(Fnw)))
        steps.append('Rn = Fnw·Awe = %s × %s = %s kN' % (_f(Fnw), _f(Awe), _f(Rn / 1e3)))
        return Nominal(Rn, 0.75, 2.00, steps, {'Fnw': Fnw, 'Awe': Awe})

    @staticmethod
    def J2_groove_pjp_shear(FEXX, Awe):
        """Soldadura de penetración parcial a corte: Fnw = 0.60 FEXX (Tabla J2.5)."""
        Rn = 0.60 * FEXX * Awe
        return Nominal(Rn, 0.75, 2.00, ['Rn = 0.60·FEXX·Awe = %s kN' % _f(Rn / 1e3)])

    @staticmethod
    def J2_4b_weld_group(Rnwl, Rnwt):
        """Grupo de filetes longitudinales y transversales concéntricos (J2-6a/b)."""
        return max(Rnwl + Rnwt, 0.85 * Rnwl + 1.5 * Rnwt)

    @staticmethod
    def J2_base_metal_shear(Fy, Fu, Agv, Anv):
        """Metal base adyacente a la soldadura (J2.4 → J4.2)."""
        y = AISC360_22.J4_2_shear_yielding(Fy, Agv)
        r = AISC360_22.J4_2_shear_rupture(Fu, Anv)
        return y, r

    # =========================================================================================
    # J3  PERNOS
    # =========================================================================================
    @staticmethod
    def J3_min_spacing(d):
        """Separación mínima entre centros (J3.3): 2⅔d (se recomienda 3d)."""
        return 2 * d + 2 * d / 3

    @staticmethod
    def J3_max_spacing(t_thinner, painted=True):
        """Separación máxima (J3.5): 24t ≤ 305 mm (pintado o no sujeto a corrosión)."""
        return min(24 * t_thinner, 305.0) if painted else min(14 * t_thinner, 180.0)

    @staticmethod
    def J3_max_edge(t_thinner):
        """Distancia máxima al borde (J3.5): 12t ≤ 150 mm."""
        return min(12 * t_thinner, 150.0)

    @staticmethod
    def J3_tension(Fnt, Ab):
        Rn = Fnt * Ab
        return Nominal(Rn, 0.75, 2.00, ['Rn = Fnt·Ab = %s × %s = %s kN (J3-1)' % (_f(Fnt), _f(Ab), _f(Rn / 1e3))])

    @staticmethod
    def J3_shear(Fnv, Ab, n_planes=1):
        Rn = Fnv * Ab * n_planes
        return Nominal(Rn, 0.75, 2.00, ['Rn = Fnv·Ab·ns = %s × %s × %d = %s kN (J3-1)'
                                        % (_f(Fnv), _f(Ab), n_planes, _f(Rn / 1e3))])

    @staticmethod
    def J3_combined_Fnt_prime(Fnt, Fnv, frv, method=LRFD):
        """Tensión de tracción modificada por corte (J3-3a LRFD / J3-3b ASD)."""
        if method == LRFD:
            F = 1.3 * Fnt - Fnt / (0.75 * Fnv) * frv
        else:
            F = 1.3 * Fnt - 2.00 * Fnt / Fnv * frv
        return max(0.0, min(F, Fnt))

    @staticmethod
    def J3_slip(mu, Tb, ns=1, hf=1.0, hole='Estándar (STD)', Du=1.13, ksc=1.0):
        """Resistencia al deslizamiento (J3.9): Rn = μ·Du·hf·Tb·ns·ksc."""
        if hole.startswith('Estándar') or (hole.startswith('Ranura corta') and 'perp' in hole):
            phi, omega = 1.00, 1.50
        elif hole.startswith('Agrandado') or hole.startswith('Ranura corta'):
            phi, omega = 0.85, 1.76
        else:
            phi, omega = 0.70, 2.14
        Rn = mu * Du * hf * Tb * ns * ksc
        return Nominal(Rn, phi, omega, ['Rn = μ·Du·hf·Tb·ns·ksc = %.2f × %.2f × %.2f × %s × %d × %.3f = %s kN (J3-4)'
                                        % (mu, Du, hf, _f(Tb / 1e3), ns, ksc, _f(Rn / 1e3))])

    @staticmethod
    def J3_ksc(Tr, Tb, nb, method=LRFD, Du=1.13):
        """Factor por tracción en uniones de deslizamiento crítico (J3-5a / J3-5b)."""
        if Tb <= 0 or nb <= 0:
            return 1.0
        if method == LRFD:
            return max(0.0, 1 - Tr / (Du * Tb * nb))
        return max(0.0, 1 - 1.5 * Tr / (Du * Tb * nb))

    @staticmethod
    def J3_bearing_tearout(d, t, Fu, lc, deformation_considered=True, long_slot_perp=False):
        """Aplastamiento y desgarramiento en agujeros de pernos (AISC 360-22 J3.11).

        Devuelve Nominal con Rn = min(aplastamiento, desgarramiento) por perno y por plano de apoyo.
        """
        if long_slot_perp:
            rb, rt = 2.0 * d * t * Fu, 1.0 * lc * t * Fu
            txt = 'agujero de ranura larga perpendicular a la carga: Rn = 2.0dtFu (aplast.), 1.0lc·t·Fu (desgarr.)'
        elif deformation_considered:
            rb, rt = 2.4 * d * t * Fu, 1.2 * lc * t * Fu
            txt = 'deformación en servicio es consideración de diseño: Rn = 2.4dtFu, 1.2lc·t·Fu (J3-6a, J3-6c)'
        else:
            rb, rt = 3.0 * d * t * Fu, 1.5 * lc * t * Fu
            txt = 'deformación no es consideración de diseño: Rn = 3.0dtFu, 1.5lc·t·Fu (J3-6b, J3-6d)'
        Rn = min(rb, rt)
        return Nominal(Rn, 0.75, 2.00, [txt, 'Aplastamiento = %s kN, desgarramiento (lc = %s mm) = %s kN → Rn = %s kN'
                                        % (_f(rb / 1e3, 2), _f(lc), _f(rt / 1e3, 2), _f(Rn / 1e3, 2))],
                       {'bearing': rb, 'tearout': rt})

    # =========================================================================================
    # J4  ELEMENTOS AFECTADOS DE MIEMBROS Y ELEMENTOS DE CONEXIÓN
    # =========================================================================================
    @staticmethod
    def J4_1_tension_yielding(Fy, Ag):
        Rn = Fy * Ag
        return Nominal(Rn, 0.90, 1.67, ['Rn = Fy·Ag = %s kN (J4-1)' % _f(Rn / 1e3)])

    @staticmethod
    def J4_1_tension_rupture(Fu, An, Ag=None, splice=False):
        Ae = min(An, 0.85 * Ag) if (splice and Ag) else An
        Rn = Fu * Ae
        return Nominal(Rn, 0.75, 2.00, ['Ae = %s mm²%s' % (_f(Ae), ' (≤ 0.85Ag, placas de empalme)' if splice else ''),
                                        'Rn = Fu·Ae = %s kN (J4-2)' % _f(Rn / 1e3)])

    @staticmethod
    def J4_2_shear_yielding(Fy, Agv):
        Rn = 0.60 * Fy * Agv
        return Nominal(Rn, 1.00, 1.50, ['Rn = 0.60·Fy·Agv = 0.60 × %s × %s = %s kN (J4-3)'
                                        % (_f(Fy), _f(Agv), _f(Rn / 1e3))])

    @staticmethod
    def J4_2_shear_rupture(Fu, Anv):
        Rn = 0.60 * Fu * Anv
        return Nominal(Rn, 0.75, 2.00, ['Rn = 0.60·Fu·Anv = 0.60 × %s × %s = %s kN (J4-4)'
                                        % (_f(Fu), _f(Anv), _f(Rn / 1e3))])

    @staticmethod
    def J4_3_block_shear(Fy, Fu, Agv, Anv, Ant, Ubs=1.0):
        """Bloque de corte (J4-5): Rn = 0.60FuAnv + UbsFuAnt ≤ 0.60FyAgv + UbsFuAnt."""
        r1 = 0.60 * Fu * Anv + Ubs * Fu * Ant
        r2 = 0.60 * Fy * Agv + Ubs * Fu * Ant
        Rn = min(r1, r2)
        steps = ['Agv = %s, Anv = %s, Ant = %s mm², Ubs = %.1f' % (_f(Agv), _f(Anv), _f(Ant), Ubs),
                 '0.60FuAnv + UbsFuAnt = %s kN ; 0.60FyAgv + UbsFuAnt = %s kN' % (_f(r1 / 1e3), _f(r2 / 1e3)),
                 'Rn = %s kN (J4-5)' % _f(Rn / 1e3)]
        return Nominal(Rn, 0.75, 2.00, steps)

    @staticmethod
    def J4_4_compression(Fy, Ag, Lc_r, E=E_STEEL):
        """Elementos de conexión en compresión (J4.4)."""
        if Lc_r <= 25:
            Rn = Fy * Ag
            return Nominal(Rn, 0.90, 1.67, ['Lc/r = %.1f ≤ 25 → Pn = Fy·Ag = %s kN (J4-6)' % (Lc_r, _f(Rn / 1e3))])
        Fe = math.pi ** 2 * E / Lc_r ** 2
        Fcr, txt = AISC360_22.E3_Fcr(Fy, Fe)
        Rn = Fcr * Ag
        return Nominal(Rn, 0.90, 1.67, ['Lc/r = %.1f > 25 → Capítulo E: Fe = %s MPa' % (Lc_r, _f(Fe)), txt,
                                        'Pn = Fcr·Ag = %s kN' % _f(Rn / 1e3)])

    @staticmethod
    def J4_5_plate_flexure_yield(Fy, t, h):
        """Flexión de placas de conexión: fluencia Mn = Fy·Z (Z = t h²/4)."""
        Z = t * h ** 2 / 4
        Mn = Fy * Z
        return Nominal(Mn, 0.90, 1.67, ['Z = t·h²/4 = %s mm³ ; Mn = Fy·Z = %s kN·m' % (_f(Z), _f(Mn / 1e6, 2))])

    @staticmethod
    def J4_5_plate_flexure_rupture(Fu, Znet):
        Mn = Fu * Znet
        return Nominal(Mn, 0.75, 2.00, ['Znet = %s mm³ ; Mn = Fu·Znet = %s kN·m (Manual AISC 9-4)'
                                        % (_f(Znet), _f(Mn / 1e6, 2))])

    # =========================================================================================
    # J7 / J8  APLASTAMIENTO Y BASES DE COLUMNA
    # =========================================================================================
    @staticmethod
    def J7_bearing(Fy, Apb):
        Rn = 1.8 * Fy * Apb
        return Nominal(Rn, 0.75, 2.00, ['Rn = 1.8·Fy·Apb = %s kN (J7-1)' % _f(Rn / 1e3)])

    @staticmethod
    def J8_concrete_bearing(fc, A1, A2):
        """Aplastamiento del concreto (J8-2): Pp = 0.85f'cA1√(A2/A1) ≤ 1.7f'cA1 ; phi = 0.65, Omega = 2.31."""
        ratio = min(math.sqrt(max(A2, A1) / A1), 2.0)
        Pp = 0.85 * fc * A1 * ratio
        return Nominal(Pp, 0.65, 2.31, ['√(A2/A1) = %.3f ≤ 2' % ratio,
                                        "Pp = 0.85·f'c·A1·√(A2/A1) = %s kN (J8-2)" % _f(Pp / 1e3)])

    # =========================================================================================
    # J10  ALAS Y ALMAS CON FUERZAS CONCENTRADAS
    # =========================================================================================
    @staticmethod
    def J10_1_flange_local_bending(Fyf, tf, at_end=False):
        Rn = 6.25 * Fyf * tf ** 2 * (0.5 if at_end else 1.0)
        return Nominal(Rn, 0.90, 1.67, ['Rn = 6.25·Fyf·tf²%s = %s kN (J10-1)'
                                        % (' × 0.5 (cerca del extremo)' if at_end else '', _f(Rn / 1e3))])

    @staticmethod
    def J10_2_web_local_yielding(Fyw, tw, k, lb, at_end=False):
        Rn = Fyw * tw * ((2.5 * k + lb) if at_end else (5 * k + lb))
        return Nominal(Rn, 1.00, 1.50, ['Rn = Fyw·tw·(%s) = %s kN (%s)'
                                        % ('2.5k + lb' if at_end else '5k + lb', _f(Rn / 1e3),
                                           'J10-3' if at_end else 'J10-2')])

    @staticmethod
    def J10_3_web_crippling(tw, tf, d, lb, Fyw, at_end=False, Qf=1.0, E=E_STEEL):
        base = math.sqrt(E * Fyw * tf / tw) * Qf
        if not at_end:
            Rn = 0.80 * tw ** 2 * (1 + 3 * (lb / d) * (tw / tf) ** 1.5) * base
            eq = 'J10-4'
        elif lb / d <= 0.2:
            Rn = 0.40 * tw ** 2 * (1 + 3 * (lb / d) * (tw / tf) ** 1.5) * base
            eq = 'J10-5a'
        else:
            Rn = 0.40 * tw ** 2 * (1 + (4 * lb / d - 0.2) * (tw / tf) ** 1.5) * base
            eq = 'J10-5b'
        return Nominal(Rn, 0.75, 2.00, ['Rn = %s kN (%s)' % (_f(Rn / 1e3), eq)])

    @staticmethod
    def J10_5_web_compression_buckling(tw, h, Fyw, at_end=False, Qf=1.0, E=E_STEEL):
        Rn = 24 * tw ** 3 * math.sqrt(E * Fyw) / h * Qf * (0.5 if at_end else 1.0)
        return Nominal(Rn, 0.90, 1.67, ['Rn = 24·tw³·√(E·Fyw)/h·Qf = %s kN (J10-8)' % _f(Rn / 1e3)])

    @staticmethod
    def J10_6_panel_zone(Fy, dc, tw, Pr=0.0, Py=None):
        """Corte en la zona de panel (J10-9/J10-10), sin considerar inestabilidad del pórtico."""
        if Py is None or Pr <= 0.4 * Py:
            Rn = 0.60 * Fy * dc * tw
            eq = 'J10-9'
        else:
            Rn = 0.60 * Fy * dc * tw * (1.4 - Pr / Py)
            eq = 'J10-10'
        return Nominal(Rn, 0.90, 1.67, ['Rn = %s kN (%s)' % (_f(Rn / 1e3), eq)])

    # =========================================================================================
    # MANUAL AISC PARTE 9: ACCIÓN DE PALANCA (PRYING)
    # =========================================================================================
    @staticmethod
    def prying_action(T_per_bolt, B_avail, p, b, a, d_b, d_hole, t, Fu, method=LRFD):
        """Acción de palanca en elementos en T / placas de extremo (Manual AISC 16.ª ed., Parte 9).

        T_per_bolt: tracción requerida por perno (N) ; B_avail: resistencia disponible a tracción del perno (N)
        p: ancho tributario por perno ; b, a: distancias del eje del perno a la cara del alma y al borde
        Devuelve dict con t_min, q, Tdisponible y pasos.
        """
        phi = 0.90
        omega = 1.67
        b_ = b - d_b / 2
        a_ = min(a, 1.25 * b) + d_b / 2
        rho = b_ / a_
        delta = 1 - d_hole / p
        fac = (phi if method == LRFD else 1 / omega)
        tc = math.sqrt(4 * B_avail * b_ / (fac * p * Fu))
        steps = ["b' = %s mm, a' = %s mm, ρ = %.3f, δ = %.3f" % (_f(b_, 2), _f(a_, 2), rho, delta),
                 'tc = √(4·B·b\'/(φ·p·Fu)) = %s mm (9-30a)' % _f(tc, 2)]
        alpha_p = 1 / (delta * (1 + rho)) * ((tc / t) ** 2 - 1)
        if alpha_p < 0:
            Q = 1.0
        elif alpha_p <= 1:
            Q = (t / tc) ** 2 * (1 + delta * alpha_p)
        else:
            Q = (t / tc) ** 2 * (1 + delta)
        T_avail = B_avail * Q
        q = max(0.0, B_avail * (delta * alpha_p * rho * (t / tc) ** 2)) if 0 <= alpha_p <= 1 else 0.0
        t_min = math.sqrt(4 * T_per_bolt * b_ / (fac * p * Fu * (1 + delta)))
        steps.append("α' = %.3f, Q = %.3f, T disponible = B·Q = %s kN por perno" % (alpha_p, Q, _f(T_avail / 1e3, 2)))
        steps.append('t mín. (sin palanca adicional) = √(4·T·b\'/(φ·p·Fu·(1+δ))) = %s mm (9-26a)' % _f(t_min, 2))
        return {'tc': tc, 'Q': Q, 'T_avail': T_avail, 'q': q, 't_min': t_min, 'steps': steps}
