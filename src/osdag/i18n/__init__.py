"""Traducción de la interfaz de Osdag (inglés -> español).

La lógica de Osdag compara internamente los textos en inglés (p. ej. ``'Bearing Bolt'`` o los
títulos de pestañas), por eso la traducción se aplica solo en la capa de visualización:

* Un ``QTranslator`` traduce los textos que pasan por ``QCoreApplication.translate`` (``_translate``).
* Se intercepta ``setText``/``setTitle``/``setToolTip``/``setWindowTitle``/``addTab``/``QMessageBox``
  para mostrar el texto traducido.
* Los ``QComboBox`` y las pestañas muestran el texto en español pero guardan el original en inglés,
  de modo que ``currentText()``, ``findText()``, ``itemText()`` y ``tabText()`` siguen devolviendo
  el valor en inglés que espera el código.
* Los mensajes del registro (``logging``) se traducen con un filtro.

Idioma: variable de entorno ``OSDAG_LANG`` (``es`` por defecto, ``en`` para desactivar).
"""
import json
import logging
import os
import re
from importlib.resources import files

from PyQt5 import QtCore, QtGui, QtWidgets

_LANG = None
_DICT = {}          # inglés normalizado -> español
_REVERSE = {}       # español -> inglés (solo si es único)
_PATTERNS = []      # (regex, plantilla) para cadenas con marcadores {} / %s
_PHRASES = []       # (regex de frase, traducción), de mayor a menor longitud, para mensajes compuestos

_HTML_WRAP = re.compile(r'^(\s*<html><head/><body><p>)(.*?)(</p></body></html>\s*)$', re.S)
_SUFFIX = re.compile(r'^(.*?)(\s*\*+|\s*:)?(\s*)$', re.S)
_PREFIX = re.compile(r'^(\s*)(.*)$', re.S)


def _norm(s):
    return re.sub(r'[ \t]+', ' ', s.strip())


def load(lang='es'):
    """Carga el diccionario del idioma."""
    global _LANG, _DICT, _REVERSE, _PATTERNS, _PHRASES
    _LANG = lang
    _DICT, _REVERSE, _PATTERNS, _PHRASES = {}, {}, [], []
    if lang in (None, '', 'en'):
        return
    path = files('osdag.i18n').joinpath('%s.json' % lang)
    data = json.loads(path.read_text(encoding='utf-8'))
    rev_count = {}
    for en, es in data.items():
        if not es or en.startswith('_'):
            continue
        k = _norm(en)
        _DICT[k] = es
        rev_count[es] = rev_count.get(es, 0) + 1
        _REVERSE[es] = k
        if re.search(r'\{\d*\}|%[sdif]|%\.\d+f', k):
            rx = re.escape(k)
            rx = re.sub(r'\\\{\d*\\\}|%(?:\\\.\d+)?[sdif]', '(.+?)', rx)
            _PATTERNS.append((re.compile('^' + rx + '$', re.S), es))
        elif len(k) >= 12 and ' ' in k:
            _PHRASES.append((k, es))
    for es, n in rev_count.items():
        if n > 1:
            _REVERSE.pop(es, None)
    _PHRASES.sort(key=lambda kv: -len(kv[0]))


def active():
    return bool(_DICT)


def _lookup(core):
    if not core:
        return None
    t = _DICT.get(core)
    if t is not None:
        return t
    t = _DICT.get(core.rstrip('.'))
    if t is not None:
        return t + ('.' if core.endswith('.') and not t.endswith('.') else '')
    for rx, tpl in _PATTERNS:
        m = rx.match(core)
        if m:
            vals = list(m.groups())
            out = re.sub(r'\{\d*\}|%(?:\.\d+)?[sdif]', lambda _m: vals.pop(0) if vals else '', tpl)
            return out
    return None


def tr(text):
    """Traduce un texto de la interfaz; si no hay traducción devuelve el original."""
    if not _DICT or not isinstance(text, str) or not text.strip():
        return text
    m = _HTML_WRAP.match(text)
    if m:
        inner = tr(m.group(2))
        return m.group(1) + inner + m.group(3)
    lead = _PREFIX.match(text).group(1)
    body = text[len(lead):]
    t = _lookup(_norm(body))
    if t is not None:
        return lead + t + body[len(body.rstrip()):]
    sm = _SUFFIX.match(body)
    core, suf, trail = sm.group(1), sm.group(2) or '', sm.group(3) or ''
    t = _lookup(_norm(core))
    if t is not None:
        return lead + t + suf + trail
    # Texto con saltos de línea / <br>: traducir por partes
    for sep in ('\n', '<br>', '<br/>', '<br />'):
        if sep in body:
            parts = body.split(sep)
            out = [tr(p) for p in parts]
            if out != parts:
                return lead + sep.join(out)
    if _MISSING is not None and re.search('[A-Za-z]{3}', text) and text not in _REVERSE:
        _MISSING.add(text)
    return text


# Diagnóstico: OSDAG_I18N_MISSING=archivo.txt guarda los textos mostrados sin traducción al salir
_MISSING = set() if os.environ.get('OSDAG_I18N_MISSING') else None
if _MISSING is not None:
    import atexit

    @atexit.register
    def _dump_missing():
        with open(os.environ['OSDAG_I18N_MISSING'], 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(sorted(_MISSING)))


def tr_phrases(text):
    """Traducción aproximada de mensajes compuestos (registro): reemplaza frases conocidas."""
    if not _DICT or not isinstance(text, str):
        return text
    full = tr(text)
    if full != text:
        return full
    out = text
    for en, es in _PHRASES:
        if en in out:
            out = out.replace(en, es)
    return out


def original(text):
    """Texto original en inglés a partir del texto mostrado (si se conoce de forma única)."""
    if not isinstance(text, str):
        return text
    m = _HTML_WRAP.match(text)
    if m:
        return m.group(1) + original(m.group(2)) + m.group(3)
    k = _REVERSE.get(text.strip())
    return text if k is None else text.replace(text.strip(), k)


# ---------------------------------------------------------------------------------------------
# Instalación en Qt
# ---------------------------------------------------------------------------------------------
class _DictTranslator(QtCore.QTranslator):
    def translate(self, context, source, disambiguation=None, n=-1):
        # None = sin traducción (QString nulo); una cadena vacía se mostraría como texto vacío
        if not source:
            return None
        t = tr(source)
        return t if t != source else None


_ROLE = QtCore.Qt.UserRole + 731    # rol propio para guardar el texto original en los combos


def _patch_combobox():
    C = QtWidgets.QComboBox
    o_addItem, o_insertItem, o_setItemText = C.addItem, C.insertItem, C.setItemText
    o_currentText, o_itemText, o_findText, o_setCurrentText = C.currentText, C.itemText, C.findText, C.setCurrentText

    def _store(self, idx, en):
        if idx >= 0:
            self.setItemData(idx, en, _ROLE)

    def addItem(self, *args, **kw):
        if args and isinstance(args[0], str):
            en = args[0]
            o_addItem(self, tr(en), *args[1:], **kw)
            _store(self, self.count() - 1, en)
            return
        if len(args) >= 2 and isinstance(args[1], str):
            en = args[1]
            o_addItem(self, args[0], tr(en), *args[2:], **kw)
            _store(self, self.count() - 1, en)
            return
        return o_addItem(self, *args, **kw)

    def addItems(self, texts):
        for t in texts:
            addItem(self, t)

    def insertItem(self, index, *args, **kw):
        if args and isinstance(args[0], str):
            en = args[0]
            o_insertItem(self, index, tr(en), *args[1:], **kw)
            _store(self, min(index, self.count() - 1) if index >= 0 else self.count() - 1, en)
            return
        return o_insertItem(self, index, *args, **kw)

    def insertItems(self, index, texts):
        for i, t in enumerate(texts):
            insertItem(self, index + i, t)

    def setItemText(self, index, text):
        en = original(text)
        o_setItemText(self, index, tr(text))
        _store(self, index, en if en != text else text)

    def _orig_at(self, index):
        if index < 0:
            return None
        d = self.itemData(index, _ROLE)
        return d if isinstance(d, str) else None

    def itemText(self, index):
        d = _orig_at(self, index)
        return d if d is not None else o_itemText(self, index)

    def currentText(self):
        idx = self.currentIndex()
        shown = o_currentText(self)
        d = _orig_at(self, idx)
        if d is not None and (not self.isEditable() or shown == o_itemText(self, idx)):
            return d
        return shown

    def findText(self, text, flags=QtCore.Qt.MatchExactly | QtCore.Qt.MatchCaseSensitive):
        for i in range(self.count()):
            if _orig_at(self, i) == text:
                return i
        return o_findText(self, text, flags)

    def setCurrentText(self, text):
        i = findText(self, text)
        if i >= 0:
            self.setCurrentIndex(i)
        else:
            o_setCurrentText(self, tr(text))

    C.addItem, C.addItems, C.insertItem, C.insertItems = addItem, addItems, insertItem, insertItems
    C.setItemText, C.itemText, C.currentText = setItemText, itemText, currentText
    C.findText, C.setCurrentText = findText, setCurrentText


def _patch_tabs():
    T = QtWidgets.QTabWidget
    o_addTab, o_insertTab, o_setTabText, o_tabText = T.addTab, T.insertTab, T.setTabText, T.tabText

    def _store(self, idx, en):
        if idx >= 0:
            self.tabBar().setTabData(idx, en)

    def addTab(self, widget, *args):
        if args and isinstance(args[-1], str):
            en = args[-1]
            idx = o_addTab(self, widget, *args[:-1], tr(en))
            _store(self, idx, en)
            return idx
        return o_addTab(self, widget, *args)

    def insertTab(self, index, widget, *args):
        if args and isinstance(args[-1], str):
            en = args[-1]
            idx = o_insertTab(self, index, widget, *args[:-1], tr(en))
            _store(self, idx, en)
            return idx
        return o_insertTab(self, index, widget, *args)

    def setTabText(self, index, text):
        o_setTabText(self, index, tr(text))
        _store(self, index, original(text))

    def tabText(self, index):
        d = self.tabBar().tabData(index)
        return d if isinstance(d, str) else o_tabText(self, index)

    T.addTab, T.insertTab, T.setTabText, T.tabText = addTab, insertTab, setTabText, tabText


def _wrap_method(cls, name, arg_index=0):
    orig = getattr(cls, name)

    def wrapper(self, *args, **kw):
        if len(args) > arg_index and isinstance(args[arg_index], str):
            args = list(args)
            args[arg_index] = tr(args[arg_index])
        return orig(self, *args, **kw)
    wrapper.__name__ = name
    setattr(cls, name, wrapper)


def _patch_texts():
    W = QtWidgets
    for cls in (W.QLabel, W.QAbstractButton, W.QAction):
        _wrap_method(cls, 'setText')
    for cls in (W.QWidget, W.QAction):
        _wrap_method(cls, 'setToolTip')
        _wrap_method(cls, 'setStatusTip')
    _wrap_method(W.QWidget, 'setWindowTitle')
    _wrap_method(W.QGroupBox, 'setTitle')
    _wrap_method(W.QMenu, 'setTitle')
    _wrap_method(W.QDockWidget, 'setWindowTitle')
    _wrap_method(W.QLineEdit, 'setPlaceholderText')

    # QMenu.addMenu(str) / addAction(str, ...)
    o_addMenu = W.QMenu.addMenu

    def addMenu(self, *args):
        if args and isinstance(args[0], str):
            return o_addMenu(self, tr(args[0]), *args[1:])
        if len(args) >= 2 and isinstance(args[1], str):
            return o_addMenu(self, args[0], tr(args[1]), *args[2:])
        return o_addMenu(self, *args)
    W.QMenu.addMenu = addMenu
    o_mb_addMenu = W.QMenuBar.addMenu

    def mb_addMenu(self, *args):
        if args and isinstance(args[0], str):
            return o_mb_addMenu(self, tr(args[0]), *args[1:])
        return o_mb_addMenu(self, *args)
    W.QMenuBar.addMenu = mb_addMenu
    o_addAction = W.QMenu.addAction

    def addAction(self, *args, **kw):
        if args and isinstance(args[0], str):
            return o_addAction(self, tr(args[0]), *args[1:], **kw)
        if len(args) >= 2 and isinstance(args[0], QtGui.QIcon) and isinstance(args[1], str):
            return o_addAction(self, args[0], tr(args[1]), *args[2:], **kw)
        return o_addAction(self, *args, **kw)
    W.QMenu.addAction = addAction

    # Encabezados de tablas
    for name in ('setHorizontalHeaderLabels', 'setVerticalHeaderLabels'):
        orig = getattr(W.QTableWidget, name)

        def make(orig):
            def f(self, labels):
                return orig(self, [tr(x) for x in labels])
            return f
        setattr(W.QTableWidget, name, make(orig))
    o_hdr = W.QTreeWidget.setHeaderLabels
    W.QTreeWidget.setHeaderLabels = lambda self, labels: o_hdr(self, [tr(x) for x in labels])

    # Los anchos de los paneles se calculan con QFontMetrics sobre el texto original en inglés
    for FM in (QtGui.QFontMetrics, QtGui.QFontMetricsF):
        o_br = FM.boundingRect

        def make(o_br):
            def boundingRect(self, *args):
                if len(args) == 1 and isinstance(args[0], str):
                    return o_br(self, tr(args[0]))
                return o_br(self, *args)
            return boundingRect
        FM.boundingRect = make(o_br)

    # Nota: QListWidget no se traduce (customized_popup compara item.text() con valores del código).

    # QMessageBox: métodos estáticos y de instancia
    MB = W.QMessageBox
    for name in ('about', 'information', 'warning', 'critical', 'question'):
        orig = getattr(MB, name)

        def make(orig):
            def f(parent, title, text, *args, **kw):
                return orig(parent, tr(title), tr(text), *args, **kw)
            return staticmethod(f)
        setattr(MB, name, make(orig))
    _wrap_method(MB, 'setText')
    _wrap_method(MB, 'setInformativeText')
    _wrap_method(MB, 'setDetailedText')

    # Diálogos de archivo: título
    FD = W.QFileDialog
    for name in ('getSaveFileName', 'getOpenFileName', 'getExistingDirectory', 'getOpenFileNames'):
        orig = getattr(FD, name)

        def make(orig):
            def f(parent=None, caption='', *args, **kw):
                return orig(parent, tr(caption), *args, **kw)
            return staticmethod(f)
        setattr(FD, name, make(orig))


class _LogFilter(logging.Filter):
    def filter(self, record):
        try:
            msg = record.getMessage()
            t = tr_phrases(msg)
            if t != msg:
                record.msg, record.args = t, ()
        except Exception:
            pass
        return True


def _patch_logging():
    # Se traduce en Logger.handle (antes de los manejadores, p. ej. el panel de mensajes de Osdag)
    flt = _LogFilter()
    o_handle = logging.Logger.handle

    def handle(self, record):
        if self.name.lower().startswith('osdag'):
            flt.filter(record)
        return o_handle(self, record)
    logging.Logger.handle = handle


_installed = False
_translator = None


def install(app=None, lang=None):
    """Activa la traducción. Debe llamarse después de crear QApplication y antes de crear ventanas."""
    global _installed, _translator
    lang = lang or os.environ.get('OSDAG_LANG', 'es')
    if _installed or lang in ('', 'en'):
        return False
    load(lang)
    if not _DICT:
        return False
    app = app or QtWidgets.QApplication.instance()
    _translator = _DictTranslator()
    if app is not None:
        app.installTranslator(_translator)
        # Traducciones estándar de Qt (botones Sí/No, etc.) si están disponibles
        qt_tr = QtCore.QTranslator(app)
        path = QtCore.QLibraryInfo.location(QtCore.QLibraryInfo.TranslationsPath)
        if qt_tr.load('qtbase_' + lang, path):
            app.installTranslator(qt_tr)
            app._osdag_qt_tr = qt_tr
    _patch_combobox()
    _patch_tabs()
    _patch_texts()
    _patch_logging()
    _installed = True
    return True
