"""Verificación de elementos y conexiones de acero según ANSI/AISC 360-22.

Paquete independiente del módulo IS 800:2007 de Osdag. Todas las funciones trabajan en
unidades SI consistentes: N, mm, MPa (N/mm²) y N·mm. La interfaz gráfica convierte a kN y kN·m.

Métodos de diseño soportados (AISC 360-22, B3):
    - LRFD: resistencia de diseño   = phi * Rn
    - ASD:  resistencia admisible   = Rn / Omega
"""

from .base import LRFD, ASD, Check, available_strength
from .materiales import ACEROS, PERNOS, ELECTRODOS, Steel, BoltGrade
from . import secciones
from .norma import AISC360_22

__all__ = ['LRFD', 'ASD', 'Check', 'available_strength', 'ACEROS', 'PERNOS', 'ELECTRODOS',
           'Steel', 'BoltGrade', 'secciones', 'AISC360_22']
