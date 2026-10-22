"""Diálogo de selección de la norma de diseño (IS 800:2007 o AISC 360-22)."""
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

CODE_IS = 'IS 800:2007'
CODE_AISC = 'AISC 360-22'
CODES = [CODE_IS, CODE_AISC]

_SETTINGS = ('FOSSEE', 'Osdag')


def saved_code():
    """Norma guardada si el usuario marcó «Recordar mi elección»; '' si no hay."""
    s = QtCore.QSettings(*_SETTINGS)
    if s.value('norma/recordar', False, type=bool):
        code = s.value('norma/codigo', '', type=str)
        return code if code in CODES else ''
    return ''


def last_code():
    code = QtCore.QSettings(*_SETTINGS).value('norma/codigo', CODE_AISC, type=str)
    return code if code in CODES else CODE_AISC


def save_code(code, remember):
    s = QtCore.QSettings(*_SETTINGS)
    s.setValue('norma/codigo', code)
    s.setValue('norma/recordar', bool(remember))


_DESC = {
    CODE_IS: ('IS 800:2007', 'Norma india (estados límite). Módulos originales de Osdag con modelo 3D y '
                             'memoria LaTeX. Catálogo de perfiles IS 808.'),
    CODE_AISC: ('AISC 360-22', 'Especificación ANSI/AISC 360-22 (LRFD y ASD). Verificación de miembros y '
                               'conexiones con memoria de cálculo en español. Catálogo AISC Shapes Database.'),
}


class SelectorNorma(QtWidgets.QDialog):
    def __init__(self, parent=None, current=None):
        super().__init__(parent)
        self.code = None
        self.setWindowTitle('Osdag — Norma de diseño')
        self.setMinimumWidth(680)
        lay = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel('<h2>Seleccione la norma de diseño</h2>'
                                 '<p>Los módulos de Osdag se mostrarán según la norma elegida. '
                                 'Puede cambiarla en cualquier momento desde la página principal.</p>')
        title.setWordWrap(True)
        lay.addWidget(title)
        row = QtWidgets.QHBoxLayout()
        current = current or last_code()
        for code in CODES:
            name, desc = _DESC[code]
            b = QtWidgets.QPushButton()
            b.setObjectName('btn_' + code.split()[0])
            b.setMinimumSize(300, 210)
            b.setCheckable(True)
            b.setChecked(code == current)
            lbl = QtWidgets.QLabel('<div style="font-size:16pt"><b>%s</b></div><p>%s</p>' % (name, desc), b)
            lbl.setWordWrap(True)
            lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
            bl = QtWidgets.QVBoxLayout(b)
            bl.addWidget(lbl)
            b.clicked.connect(lambda _=False, c=code: self._choose(c))
            row.addWidget(b)
        lay.addLayout(row)
        self.remember = QtWidgets.QCheckBox('Recordar mi elección y no volver a preguntar al iniciar')
        lay.addWidget(self.remember)
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _choose(self, code):
        self.code = code
        save_code(code, self.remember.isChecked())
        self.accept()


def ask_code(parent=None, current=None):
    """Muestra el diálogo y devuelve la norma elegida o None si se canceló."""
    dlg = SelectorNorma(parent, current)
    if dlg.exec_() == QtWidgets.QDialog.Accepted:
        return dlg.code
    return None
