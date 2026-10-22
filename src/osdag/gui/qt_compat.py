"""Ajustes de compatibilidad de Qt."""
from PyQt5 import QtGui

# Fuentes con cobertura completa de símbolos técnicos (≤, ≥, λ, φ, Ω, ², ⁴ ...)
_PREFERRED = ['DejaVu Sans', 'Segoe UI', 'Liberation Sans', 'Arial']


def apply_safe_font(app):
    """Usa una fuente con cobertura de símbolos matemáticos.

    Con algunas combinaciones de Qt (p. ej. el Qt de conda-forge) y fuentes del sistema, la búsqueda
    de una fuente de respaldo para caracteres como «≤» provoca un fallo de segmentación cuando la
    fuente por defecto es «Noto Sans». Fijar una fuente que ya contiene esos glifos evita el problema.
    """
    families = set(QtGui.QFontDatabase().families())
    for fam in _PREFERRED:
        if fam in families:
            font = QtGui.QFont(fam, app.font().pointSize())
            app.setFont(font)
            return fam
    return None


def safe_family():
    families = set(QtGui.QFontDatabase().families())
    for fam in _PREFERRED:
        if fam in families:
            return fam
    return None


def fix_stylesheet(qss):
    """Sustituye la familia «Helvetica» de las hojas de estilo de Osdag por una fuente con los glifos
    técnicos (p. ej. «⁴» en cm⁴); con Helvetica ausente, la búsqueda de respaldo de Qt puede fallar."""
    fam = safe_family()
    if fam:
        qss = qss.replace('font-family: Helvetica', "font-family: '%s'" % fam)
    return qss
