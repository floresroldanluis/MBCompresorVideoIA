"""MB Compresor de Video IA: native Qt video queue with a fully local AI adviser."""
import dataclasses
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPainter, QPalette, QFontDatabase, QPixmap, QBitmap, QRegion
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
 QHBoxLayout, QLabel, QPushButton, QFrame, QLineEdit, QDoubleSpinBox, QSpinBox,
 QComboBox, QProgressBar, QPlainTextEdit, QFileDialog, QMessageBox, QSplitter,
 QScrollArea, QDialog, QFormLayout, QDialogButtonBox, QCheckBox, QGroupBox,
 QStackedWidget, QInputDialog, QGridLayout, QStyle, QStyleOptionProgressBar)

import engine
from acceleration import MODES
from local_ai import OllamaClient

VERSION = '1.0.45'
DATA = (Path(os.environ.get('USERPROFILE',str(Path.home())))/'AppData'/'Local' if os.name=='nt' else Path(os.environ.get('LOCALAPPDATA',str(Path.home()))))/'MBCompresorPro'
CONFIG = DATA / 'settings.json'

def load_settings():
    defaults = dict(theme='Automático', output_folder='', compression_only=True,
        ollama_url='http://127.0.0.1:11434', ollama_model='qwen3:0.6b', max_gb=1.95,unlimited=False,
        codec='h264', speed='calidad', height=1080, audio_kbps=512,mode='auto',
        ffmpeg='', ffprobe='', cookies_browser='', cookies_file='', update_url='')
    try:
        value = json.loads(CONFIG.read_text(encoding='utf-8'))
        if isinstance(value, dict): defaults.update(value)
    except (OSError, ValueError): pass
    if defaults.get('default_profile_version',0)<2:
        defaults.update(height=1080,audio_kbps=512,speed='calidad',output_folder='',default_profile_version=2)
    if defaults.get('output_profile_version',0)<5:
        defaults.update(output_folder='',output_profile_version=5)
    # Unlimited compression is an explicit choice for the current session.
    if defaults.get('full_hd_profile_version',0)<1:
        defaults.update(height=1080,speed='calidad',audio_kbps=512,full_hd_profile_version=1)
    defaults['unlimited']=False
    defaults['compression_only']=True
    if not defaults.get('releases_url'):defaults['releases_url']='https://github.com/floresroldanluis/MBCompresorVideoIA'
    for obsolete in ('download_folder','cookies_browser','cookies_file'):
        defaults.pop(obsolete,None)
    return defaults

def save_settings(settings):
    DATA.mkdir(parents=True, exist_ok=True)
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps(dict(settings,unlimited=False), ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, CONFIG)

def button(text, callback, primary=False):
    b = QPushButton(text)
    if primary: b.setObjectName('primary')
    b.clicked.connect(callback)
    return b

def label(text, name=None):
    w=QLabel(text)
    if name: w.setObjectName(name)
    return w

def combo(items, current=None):
    w=QComboBox()
    for text, data in items: w.addItem(text, data)
    index=w.findData(current)
    if index>=0: w.setCurrentIndex(index)
    return w

def card():
    f=QFrame(); f.setObjectName('card'); return f

def fmt_size(n): return f'{n/1e9:.2f} GB' if n>=1e9 else f'{n/1e6:.1f} MB'
def fmt_time(n):
    n=int(n or 0); return f'{n//3600:02d}:{n%3600//60:02d}:{n%60:02d}'

class Bridge(QObject):
    metadata=Signal(str,object)
    event=Signal(str)
    progress=Signal(str,object)
    result=Signal(str,str,str)
    save_result=Signal(str,str,str)
    done=Signal()
    advice=Signal(str,object)
    advice_error=Signal(str)
    download_done=Signal(str)

class ContrastProgressBar(QProgressBar):
    """Keep the percentage white on the blue fill and readable on the track."""
    def paintEvent(self,event):
        option=QStyleOptionProgressBar();self.initStyleOption(option)
        option.textVisible=False
        painter=QPainter(self)
        self.style().drawControl(QStyle.CE_ProgressBar,option,painter,self)
        if not self.isTextVisible() or self.maximum()<=self.minimum():return
        text_rect=self.style().subElementRect(QStyle.SE_ProgressBarLabel,option,self)
        font=painter.font();font.setBold(True);painter.setFont(font)
        painter.setPen(self.palette().color(QPalette.Text))
        painter.drawText(text_rect,Qt.AlignCenter,self.text())
        contents=self.style().subElementRect(QStyle.SE_ProgressBarContents,option,self)
        fraction=max(0.,min(1.,(self.value()-self.minimum())/(self.maximum()-self.minimum())))
        fill=contents.adjusted(0,0,-contents.width()+round(contents.width()*fraction),0)
        painter.setClipRect(fill)
        painter.setPen(Qt.white);painter.drawText(text_rect,Qt.AlignCenter,self.text())

class Item:
    def __init__(self,path,settings):
        self.path=path; self.info=None; self.state='Analizando'; self.output=None; self.recovery_target=None; self.track_defaults_applied=False
        self.output_dir=settings.get('output_folder','')
        self.options=engine.CompressionOptions(unlimited=bool(settings.get('unlimited',False)),max_gb=float(settings.get('max_gb',1.95)),
            codec=settings.get('codec','h264'),speed=settings.get('speed','calidad'),
            height=int(settings.get('height',1080)),audio_kbps=int(settings.get('audio_kbps',512)),mode=settings.get('mode','auto'))

class ItemOptions(QWidget):
    saved=Signal()
    closed=Signal()
    def __init__(self,item,parent):
        super().__init__(parent); self.setObjectName('inlineOptions')
        self.item=item; l=QVBoxLayout(self)
        f=QGridLayout();l.addLayout(f);f.setColumnStretch(1,1);f.setColumnStretch(3,1)
        self.height=combo([('Original',0),('4K · 2160p',2160),('1440p',1440),('Full HD · 1080p',1080),('720p',720),('480p',480)],item.options.height)
        self.audio=combo([('Todas las pistas',None),('Sin audio',[])] + [(f"{s['index']}: {s.get('tags',{}).get('language','und')} · {s.get('codec_name','')} · {s.get('channels',0)} canales",[s['index']]) for s in item.info['audio']],item.options.audio_indices)
        self.kbps=combo([(f'{n} kbps'+(' · máxima calidad' if n==512 else ''),n) for n in (512,384,320,256,192,160,128,96,64,32)],item.options.audio_kbps)
        self.sub=combo([('Sin subtítulos',[])]+[(f"{s['index']}: {s.get('tags',{}).get('language','und')} · {s.get('codec_name','')}",[s['index']]) for s in item.info['subtitles']],item.options.subtitle_indices)
        self.submode=combo([('Pista seleccionable en el MP4','selectable'),('Quemar en la imagen','burn')],getattr(item.options,'subtitle_mode','selectable'))
        self.start=QDoubleSpinBox(); self.start.setRange(0,item.info['duration']); self.start.setValue(item.options.trim_start); self.start.setSuffix(' s')
        self.end=QDoubleSpinBox(); self.end.setRange(0,item.info['duration']); self.end.setValue(item.options.trim_end or 0); self.end.setSuffix(' s'); self.end.setSpecialValueText('Hasta el final')
        self.out=QLineEdit(item.output_dir); row=QHBoxLayout(); row.addWidget(self.out); row.addWidget(button('Elegir…',self.choose))
        self.filename=QLineEdit(getattr(item.options,'output_name',None) or (Path(item.path).stem+'.mp4'));self.filename.setToolTip('Se usa este nombre. Si ya existe, se añade _1, _2, etc. El video original no se reemplaza.')
        self.adaptive=QCheckBox('Ajustar resolución al bitrate disponible'); self.adaptive.setChecked(item.options.local_ai)
        self.stereo=QCheckBox('Convertir audio a estéreo');self.stereo.setChecked(getattr(item.options,'downmix_audio',False))
        self.fps=combo([('Original',None),('24',24),('25',25),('30',30),('50',50),('60',60)],getattr(item.options,'fps',None))
        for title,w,r,c in [('Resolución',self.height,0,0),('Pista de audio',self.audio,0,2),('Calidad de audio',self.kbps,1,0),('Subtítulos',self.sub,1,2)]:
            f.addWidget(label(title),r,c);f.addWidget(w,r,c+1)
        f.addWidget(label('Carpeta de salida'),2,0);f.addLayout(row,2,1,1,3)
        f.addWidget(label('Nombre MP4'),3,0);f.addWidget(self.filename,3,1,1,3)
        self.unlimited=QCheckBox('Máximo por video sin límite');self.unlimited.setChecked(item.options.unlimited);f.addWidget(self.unlimited,5,0,1,4)
        self.mode=combo(MODES,item.options.mode);f.addWidget(label('Modo de compresión'),4,0);f.addWidget(self.mode,4,1,1,3)
        advanced=QWidget();af=QFormLayout(advanced);advanced.hide()
        for title,w in [('Modo de subtítulos',self.submode),('FPS',self.fps),('Inicio',self.start),('Final',self.end),('',self.stereo),('',self.adaptive)]:af.addRow(title,w)
        l.addWidget(button('Más opciones: recorte, FPS y procesamiento',lambda:advanced.setVisible(not advanced.isVisible())));l.addWidget(advanced)
        note=label('Si el nombre ya existe, se añade numeración: _1, _2, etc. Los cambios se guardan automáticamente; el original se conserva.','muted');note.setWordWrap(True);l.addWidget(note)
        l.addWidget(button('Ocultar opciones',self.closed.emit),alignment=Qt.AlignRight)
        for field in (self.height,self.audio,self.kbps,self.sub,self.submode,self.fps,self.mode):field.currentIndexChanged.connect(self.apply)
        for field in (self.start,self.end,self.out,self.filename):field.editingFinished.connect(self.apply)
        for field in (self.adaptive,self.stereo,self.unlimited):field.toggled.connect(self.apply)
    def choose(self):
        p=QFileDialog.getExistingDirectory(self,'Carpeta de salida',self.out.text())
        if p:self.out.setText(p);self.apply()
    def apply(self,*_):
        if self.end.value() and self.end.value()<=self.start.value():
            QMessageBox.warning(self,'Recorte','El final debe ser mayor que el inicio.'); return False
        o=self.item.options; o.unlimited=self.unlimited.isChecked(); o.height=self.height.currentData(); o.audio_indices=self.audio.currentData(); o.audio_kbps=self.kbps.currentData(); o.subtitle_indices=self.sub.currentData();o.mode=self.mode.currentData()
        if hasattr(o,'subtitle_mode'):o.subtitle_mode=self.submode.currentData()
        if hasattr(o,'output_name'):
            name=self.filename.text().strip()
            if not name or Path(name).name!=name or not name.lower().endswith('.mp4'):
                QMessageBox.warning(self,'Nombre de salida','Escribe solo un nombre de archivo terminado en .mp4.');return False
            o.output_name=name
        o.fps=self.fps.currentData(); o.local_ai=self.adaptive.isChecked();o.downmix_audio=self.stereo.isChecked()
        o.trim_start=self.start.value(); o.trim_end=self.end.value() or None; self.item.output_dir=self.out.text().strip(); self.saved.emit();return True

class MainWindow(QMainWindow):
    def __init__(self):
        if os.name=='nt':
            for font in ('segoeui.ttf','segoeuib.ttf'):
                QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/font))
        super().__init__(); self.version=VERSION; self.restored={}; self.settings=load_settings(); self.items=[]; self.rows={}; self.active=False; self.saving=False; self.pending_saves={}; self.control=None; self.paused=False; self.stop_event=threading.Event(); self.ai_busy=False; self.downloading=False; self.download_cancel=None; self.total_count=0; self.completed_count=0
        self._has_appeared=False;self._closing=False;self._allow_close=False;self._fade_animation=None
        self._animate_windows=QApplication.platformName() not in ('offscreen','minimal')
        if self._animate_windows:self.setWindowOpacity(0.0)
        self.bridge=Bridge(); self.bridge.metadata.connect(self.on_metadata); self.bridge.event.connect(self.log); self.bridge.progress.connect(self.on_progress); self.bridge.result.connect(self.on_result);self.bridge.save_result.connect(self.on_save_result); self.bridge.done.connect(self.on_done); self.bridge.advice.connect(self.on_advice); self.bridge.advice_error.connect(self.ai_error); self.bridge.download_done.connect(lambda _:None)
        self.setWindowTitle('MB Compresor de Video IA'); self.setMinimumSize(940,720); self.setAcceptDrops(True)
        screen=QApplication.primaryScreen();available=screen.availableGeometry() if screen else None
        self.resize(1200,min(1000,max(720,available.height()-40)) if available else 1000)
        icon=Path(__file__).resolve().parent/'assets'/'app-logo-02.ico'
        if icon.is_file():self.setWindowIcon(QIcon(str(icon)))
        root=QWidget();root.setObjectName('appRoot'); self.setCentralWidget(root); layout=QVBoxLayout(root); layout.setContentsMargins(26,20,26,18); layout.setSpacing(14)
        from popups import icon_button
        header=QGridLayout();header.setColumnMinimumWidth(0,150);header.setColumnMinimumWidth(2,150);header.setColumnStretch(1,1)
        titles=QVBoxLayout();titles.setSpacing(2);titles.setContentsMargins(0,0,0,0)
        self.brand_logo=QLabel();self.brand_logo.setAccessibleName('Logo MB Compresor de Video IA');self.brand_logo.setAlignment(Qt.AlignCenter)
        mark=QPixmap(str(Path(__file__).resolve().parent/'assets'/'mb-logo-02.png'))
        bounds=QRegion(QBitmap.fromImage(mark.toImage().createAlphaMask())).boundingRect()
        self.brand_logo.setPixmap(mark.copy(bounds).scaled(92,56,Qt.KeepAspectRatio,Qt.SmoothTransformation));self.brand_logo.setFixedHeight(56);titles.addWidget(self.brand_logo)
        self.heading=label('MB Compresor de Video IA','title');self.heading.setAlignment(Qt.AlignCenter);titles.addWidget(self.heading)
        self.subtitle=label('Compresión de alta calidad - MP4 en tu equipo','muted');self.subtitle.setAlignment(Qt.AlignCenter);titles.addWidget(self.subtitle);header.addLayout(titles,0,1)
        actions=QHBoxLayout();self.theme_icon=icon_button('theme','Cambiar tema',self.toggle_theme);self.settings_icon=icon_button('settings','Configuración',self.open_settings);self.about_icon=icon_button('about','Acerca de',self.open_about)
        for control in (self.about_icon,self.theme_icon,self.settings_icon):actions.addWidget(control)
        header.addLayout(actions,0,2,Qt.AlignRight|Qt.AlignTop);layout.addLayout(header)
        tabs=QHBoxLayout();tabs.addWidget(label('VIDEO A MP4','eyebrow'));tabs.addStretch();tabs.addWidget(label('1080p  ·  AUDIO DE ALTA CALIDAD  ·  IA LOCAL','badge'));layout.addLayout(tabs)
        self.pages=QStackedWidget(); layout.addWidget(self.pages,1)
        compression=QWidget(); cl=QVBoxLayout(compression); cl.setContentsMargins(0,0,0,0); cl.setSpacing(12); self.pages.addWidget(compression)
        controls=card(); c=QVBoxLayout(controls); c.setContentsMargins(18,16,18,16)
        outrow=QHBoxLayout(); outrow.addWidget(label('Guardar en')); self.output=QLineEdit(self.settings.get('output_folder','')); self.output.setReadOnly(True); self.output.setPlaceholderText('Misma carpeta que cada video original'); outrow.addWidget(self.output,1); outrow.addWidget(button('Cambiar…',self.choose_output));outrow.addWidget(button('Usar carpeta original',self.original_output));  c.addLayout(outrow)
        opts=QHBoxLayout(); self.limit=QDoubleSpinBox(); self.limit.setDecimals(4); self.limit.setRange(.0001,1000); self.limit.setValue(float(self.settings['max_gb'])); self.limit.setSuffix(' GB'); self.limit.setToolTip('Tamaño máximo por archivo. 1 GB = 1 000 000 000 bytes.'); self.codec=combo([('H.264 · compatible','h264'),('H.265 · más compresión','h265')],self.settings['codec']); self.speed=combo([('Rápido','rapido'),('Equilibrado','equilibrado'),('Máxima calidad','calidad')],self.settings['speed']); self.res=combo([('Original',0),('4K',2160),('1440p',1440),('Full HD · 1080p',1080),('720p',720),('480p',480)],self.settings['height'])
        for text,w in [('Máximo por video con límite',self.limit),('Códec',self.codec),('Velocidad',self.speed),('Resolución',self.res)]:
            col=QVBoxLayout(); col.addWidget(label(text,'muted')); col.addWidget(w); opts.addLayout(col)
        self.unlimited=QCheckBox('Máximo por video sin límite');self.unlimited.setChecked(bool(self.settings.get('unlimited',False)))
        self.unlimited.setToolTip('Comprime por calidad visual alta, sin límite de GB. Conserva el audio sin recodificar cuando es compatible con MP4. No es compresión sin pérdida de video.')
        col=QVBoxLayout();col.addStretch();col.addWidget(self.unlimited);opts.insertLayout(1,col)
        self.limit.setEnabled(not self.unlimited.isChecked());self.unlimited.toggled.connect(self.unlimited_changed)
        self.res.setToolTip('Full HD limita a 1920×1080 y conserva la proporción del video. No amplía videos pequeños. Un límite de GB muy bajo reduce el detalle.')
        c.addLayout(opts)
        mode_row=QHBoxLayout();mode_row.addWidget(label('Modo de compresión'));self.mode=combo(MODES,self.settings.get('mode','auto'));mode_row.addWidget(self.mode,1);c.addLayout(mode_row)
        self.mode.setToolTip('Automático elige NVIDIA si supera una prueba real; si falla usa CPU. CPU prioriza calidad por tamaño con dos pasadas.')
        self.mode.currentIndexChanged.connect(self.mode_changed);self.speed.setEnabled(self.mode.currentData()!='cpu')
        c.addWidget(button('Aplicar estos ajustes a los pendientes',self.apply_globals)); cl.addWidget(controls)
        self.recovery_notice=button('Recuperar guardados pendientes',self.restore_pending_saves);self.recovery_notice.hide();cl.addWidget(self.recovery_notice)
        bar=QHBoxLayout(); self.queue_title=label('COLA DE COMPRESIÓN  /  0 VIDEOS','eyebrow'); bar.addWidget(self.queue_title); bar.addStretch()
        self.add_folder_button=button('Agregar carpeta',self.add_folder);bar.addWidget(self.add_folder_button)
        self.add_videos_button=button('Agregar Videos',self.add_files,True);bar.addWidget(self.add_videos_button)
        self.ai_btn=button('Recomendar con IA local',self.ask_ai); bar.addWidget(self.ai_btn); bar.addWidget(button('Limpiar terminados',self.clear_finished)); cl.addLayout(bar)
        self.scroll=QScrollArea(); self.scroll.setWidgetResizable(True); self.queue_widget=QWidget(); self.queue_layout=QVBoxLayout(self.queue_widget); self.queue_layout.setContentsMargins(0,0,0,0); self.queue_layout.setSpacing(10); self.empty=label('<b>Arrastra tus videos aquí</b><br><span style="font-weight:400;font-size:13px">Tambien puedes soltarlos en cualquier parte de la ventana</span>','empty'); self.empty.setAlignment(Qt.AlignCenter); self.queue_layout.addWidget(self.empty,1); self.queue_layout.addStretch(0); self.scroll.setWidget(self.queue_widget); cl.addWidget(self.scroll,1)
        footer=card(); fl=QVBoxLayout(footer); self.status=label('Listo para comprimir.','section'); fl.addWidget(self.status); self.total_progress=ContrastProgressBar(); self.total_progress.setValue(0); fl.addWidget(self.total_progress); actions=QHBoxLayout(); actions.addWidget(label('Motor automático o seleccionado · calidad y tamaño según ajustes','muted')); actions.addStretch(); self.pause_btn=button('Pausar',self.pause); self.pause_btn.setEnabled(False); actions.addWidget(self.pause_btn); self.stop_btn=button('Detener todo',self.stop); self.stop_btn.setEnabled(False); actions.addWidget(self.stop_btn); self.start_btn=button('Iniciar compresión  →',self.start,True); actions.addWidget(self.start_btn); fl.addLayout(actions); cl.addWidget(footer)
        log_panel=card();log_layout=QVBoxLayout(log_panel);log_layout.setContentsMargins(10,8,10,8);log_layout.setSpacing(6)
        self.logs=QPlainTextEdit(); self.logs.setReadOnly(True); self.logs.setMaximumHeight(100); self.logs.setPlaceholderText('Registro de avisos y eventos'); self.logs.document().setMaximumBlockCount(500)
        log_controls=QHBoxLayout();log_controls.setSpacing(6)
        self.toggle_logs_button=QPushButton();self.toggle_logs_button.setObjectName('iconButton');self.toggle_logs_button.setFixedSize(32,30);self.toggle_logs_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown));self.toggle_logs_button.setToolTip('Minimizar historial');self.toggle_logs_button.setAccessibleName('Minimizar historial');self.toggle_logs_button.clicked.connect(self.toggle_logs)
        self.clear_logs_button=icon_button('broom','Limpiar registro de eventos',self.logs.clear);self.clear_logs_button.setFixedSize(32,30)
        log_controls.addWidget(label('Registro de avisos y eventos','muted'));log_controls.addStretch();log_controls.addWidget(self.clear_logs_button);log_controls.addWidget(self.toggle_logs_button);log_layout.addLayout(log_controls);log_layout.addWidget(self.logs);layout.addWidget(log_panel)
        self.apply_theme(); self.theme_timer=QTimer(self); self.theme_timer.timeout.connect(self.apply_theme); self.theme_timer.start(60000)
        self.log(f'MB Compresor de Video IA {VERSION}. IA local: {self.settings["ollama_model"]}.')
        update_result=DATA/'updates'/'last-result.json'
        if update_result.is_file():
            try:
                result=json.loads(update_result.read_text(encoding='utf-8-sig'))
                if result.get('ok') and str(result.get('version','')).lstrip('v')==VERSION:self.log('Actualización instalada: '+str(result.get('version')))
                elif not result.get('ok'):self.log('Último intento de actualización no completado: '+str(result.get('message')))
            except (OSError,ValueError):pass
        save_settings(self.settings)
        self._load_pending_saves()
        # Each launch starts with an empty drop area.
    def log(self,text): self.logs.appendPlainText(f'{time.strftime("%H:%M:%S")}  {text}')
    def toggle_logs(self):
        expanded=self.logs.isHidden()
        self.logs.setVisible(expanded)
        self.toggle_logs_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown if expanded else QStyle.SP_ArrowUp))
        description='Minimizar historial' if expanded else 'Mostrar historial'
        self.toggle_logs_button.setToolTip(description);self.toggle_logs_button.setAccessibleName(description)
    def apply_theme(self):
        theme=self.settings.get('theme','Automático').lower(); minutes=datetime.datetime.now().hour*60+datetime.datetime.now().minute
        dark=theme in ('oscuro','dark') or (theme in ('automático','automatico','auto') and not 330<=minutes<1110)
        bg,fg,cardbg,border,muted,field=('#080b18','#f6f9ff','#11182a','#263651','#98a6bf','#19243a') if dark else ('#f2f5fb','#101526','#ffffff','#d9e1ee','#647087','#eaf0f8')
        self.setStyleSheet(f'''
        QWidget {{font-family: 'Segoe UI'; font-size: 13px; color:{fg};}}
        QMainWindow, QDialog {{background:{bg};}} QScrollArea,QScrollArea>QWidget>QWidget {{background:transparent;border:0;}}
        QFrame#popupSurface {{background:{cardbg};border:1px solid {border};border-radius:22px;}} QPushButton#iconButton {{padding:7px;border-radius:12px;}} QFrame#card:hover {{border-color:#92bee8;}} QFrame#card {{background:{cardbg};border:1px solid {border};border-radius:20px;}}
        QLabel {{background:transparent;}} QLabel#title {{font-size:32px;font-weight:700;color:#156ff1;}} QLabel#section {{font-size:15px;font-weight:600;}}
        QLabel#eyebrow {{color:#3489f4;font-size:11px;font-weight:700;letter-spacing:2px;}} QLabel#muted {{color:{muted};font-size:12px;}}
        QLabel#badge {{color:#12a78a;font-size:11px;font-weight:700;}} QLabel#empty {{color:{muted};font-size:16px;min-height:60px;}}
        QPushButton {{background:{field};border:1px solid {border};border-radius:11px;padding:10px 16px;font-weight:600;}}
        QPushButton:hover {{border-color:#00b9e8;color:#156ff1;}} QPushButton:pressed {{padding-top:12px;padding-bottom:8px;}} QLineEdit:focus,QComboBox:focus {{border:2px solid #156ff1;}} QPushButton:disabled {{color:{muted};}}
        QPushButton#primary {{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #156ff1,stop:1 #00b9e8);color:white;border:0;padding:12px 20px;}} QPushButton#primary:hover {{background:#1269d5;}} QPushButton#primary:disabled {{background:{field};color:{muted};border:1px solid {border};}}
        QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QPlainTextEdit {{background:{field};border:1px solid {border};border-radius:7px;padding:8px;selection-background-color:#237bea;}}
        QComboBox QAbstractItemView {{background:{cardbg};color:{fg};selection-background-color:#237bea;}}
        QProgressBar {{background:{field};border:0;border-radius:4px;height:8px;max-height:12px;text-align:center;font-size:9px;font-weight:700;}}
        QProgressBar::chunk {{background:#2891f5;border-radius:4px;}} QGroupBox {{border:1px solid {border};border-radius:8px;margin-top:18px;padding:12px;}}
        QGroupBox::title {{subcontrol-origin:margin;left:12px;}} QCheckBox {{padding:5px;}}
        ''')
    def toggle_theme(self): self.settings['theme']='Claro' if self.settings.get('theme')=='Oscuro' else 'Oscuro'; save_settings(self.settings); self.apply_theme()
    def open_about(self):
        from popups import AboutDialog,show_popup
        show_popup(AboutDialog(VERSION,self),self)
    def original_output(self):
        self.output.clear();self.apply_output()
    def mode_changed(self):
        self.speed.setEnabled(self.mode.currentData()!='cpu')
        self.settings['mode']=self.mode.currentData();save_settings(self.settings)
    def choose_output(self):
        p=QFileDialog.getExistingDirectory(self,'Carpeta de salida',self.output.text())
        if p:self.output.setText(p);self.apply_output()
    def apply_output(self):
        for i in self.items:
            if i.state in ('Pendiente','Error','Cancelado'):i.output_dir=self.output.text().strip()
        self.settings['output_folder']=self.output.text().strip(); save_settings(self.settings); self.log('Carpeta aplicada a los videos pendientes.'); self.persist_queue()
    def unlimited_changed(self,checked):
        self.limit.setEnabled(not checked);self.settings['unlimited']=checked;save_settings(self.settings)
    def apply_globals(self):
        for i in self.items:
            if i.state in ('Pendiente','Error','Cancelado'):
                i.options.unlimited=self.unlimited.isChecked(); i.options.max_gb=self.limit.value(); i.options.codec=self.codec.currentData(); i.options.speed=self.speed.currentData(); i.options.height=self.res.currentData(); i.options.mode=self.mode.currentData(); self.refresh_row(i)
        self.settings.update(unlimited=self.unlimited.isChecked(),max_gb=self.limit.value(),codec=self.codec.currentData(),speed=self.speed.currentData(),height=self.res.currentData(),mode=self.mode.currentData()); save_settings(self.settings); self.persist_queue()
    def add_files(self):
        files,_=QFileDialog.getOpenFileNames(self,'Agregar videos','','Videos (*.mp4 *.mkv *.avi *.mov *.webm *.m4v *.mts *.m2ts *.wmv *.flv *.mpeg *.mpg *.ts *.vob *.3gp *.ogv);;Todos los archivos (*)')
        self.add_paths(files)
    def add_folder(self):
        p=QFileDialog.getExistingDirectory(self,'Agregar videos de una carpeta')
        if p:self.add_paths([str(x) for x in Path(p).iterdir() if x.suffix.lower() in ('.mp4','.mkv','.avi','.mov','.webm','.m4v','.mts','.m2ts','.wmv','.flv','.mpeg','.mpg','.ts','.vob','.3gp','.ogv')])
    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls():e.acceptProposedAction()
    def dropEvent(self,e): self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
    def add_paths(self,paths):
        cfg=dict(self.settings,unlimited=self.unlimited.isChecked(),max_gb=self.limit.value(),codec=self.codec.currentData(),speed=self.speed.currentData(),height=self.res.currentData(),output_folder=self.output.text().strip(),mode=self.mode.currentData())
        for path in paths:
            path=str(Path(path).resolve())
            if not Path(path).is_file() or any(i.path==path for i in self.items):continue
            item=Item(path,cfg); self.items.append(item); self.build_row(item)
            if self.active:
                self._run_paths.append(item.path);self.total_count=len(self._run_paths)
                self.refresh_row(item)
                self.log(f'Agregado a la compresión en curso: {Path(item.path).name}. Total: {self.total_count} videos.')
            def analyze(it=item):
                try:self.bridge.metadata.emit(it.path,engine.probe(it.path,self.settings.get('ffprobe') or None))
                except Exception as exc:self.bridge.metadata.emit(it.path,exc)
            threading.Thread(target=analyze,daemon=True).start()
        self.empty.setVisible(not self.items); self.update_title(); self.persist_queue();self._update_recovery_notice()
        if self.active:self._refresh_run_summary();self._process_next()
    def find(self,path):return next((i for i in self.items if i.path==path),None)
    def build_row(self,item):
        f=card(); l=QVBoxLayout(f); l.setContentsMargins(15,12,15,12); top=QHBoxLayout(); name=label(Path(item.path).name,'section'); name.setToolTip(item.path); top.addWidget(name,1); state=label(item.state,'badge'); top.addWidget(state); l.addLayout(top)
        meta=label('Leyendo metadatos…','muted'); l.addWidget(meta); progress=ContrastProgressBar(); progress.setValue(0); l.addWidget(progress)
        bottom=QHBoxLayout(); detail=label('','muted');detail.setWordWrap(True); bottom.addWidget(detail,1); options=button('Resolución, audio y subtítulos',lambda:self.item_options(item)); options.setEnabled(False); bottom.addWidget(options); retrybtn=button('Reintentar guardado',lambda:self.retry_save(item));retrybtn.hide();bottom.addWidget(retrybtn); openbtn=button('Abrir salida',lambda:self.open_result(item)); openbtn.setEnabled(False); bottom.addWidget(openbtn); remove=button('Quitar',lambda:self.remove_item(item)); bottom.addWidget(remove); l.addLayout(bottom)
        self.rows[item.path]=dict(frame=f,layout=l,state=state,meta=meta,progress=progress,detail=detail,options=options,retry_save=retrybtn,open=openbtn,remove=remove,editor=None)
        self.queue_layout.insertWidget(self.queue_layout.count()-1,f)
    def refresh_row(self,item):
        r=self.rows.get(item.path)
        if not r:return
        r['state'].setText(item.state)
        if item.info:r['meta'].setText(f"{fmt_time(item.info['duration'])}  ·  {item.info['width']}×{item.info['height']}  ·  {fmt_size(item.info['size'])}  →  MP4 / {item.options.codec.upper()} / {('sin límite · calidad alta' if item.options.unlimited else f'máximo {item.options.max_gb:g} GB')} · {dict((v,k) for k,v in MODES).get(item.options.mode,'Automático')}")
        r['options'].setEnabled(item.info is not None and not self.active and not self.saving and not item.recovery_target); r['remove'].setEnabled(not (self.active and getattr(self,'_current_path',None)==item.path) and not (self.saving and item.state=='Guardando')); r['open'].setEnabled(bool(item.output))
        r['options'].setVisible(not bool(item.recovery_target))
        r['retry_save'].setVisible(bool(item.recovery_target));r['retry_save'].setEnabled(bool(item.recovery_target) and not self.active and not self.saving)
        r['open'].setText('Abrir recuperación' if item.recovery_target else 'Abrir salida')
        if r.get('editor'):r['editor'].setEnabled(not self.active and not self.saving)
    def on_metadata(self,path,info):
        i=self.find(path)
        if not i:return
        if isinstance(info,Exception):i.state='Error'; self.rows[path]['meta'].setText(str(info)); self.log(f'{Path(path).name}: {info}')
        else:
            i.info=info; i.state='Pendiente'
            if not i.track_defaults_applied:
                i.options.audio_indices=[info['audio'][0]['index']] if info['audio'] else []
                i.options.subtitle_indices=[info['subtitles'][0]['index']] if info['subtitles'] else []
                i.options.subtitle_mode='burn'
                i.track_defaults_applied=True
            previous=self.restored.pop(path,None)
            if previous and previous.get('state')=='Completado' and Path(previous.get('output') or '').is_file():
                i.state='Completado';i.output=previous['output'];self.rows[path]['progress'].setValue(100);self.rows[path]['detail'].setText('Resultado guardado de la sesión anterior')
        self.refresh_row(i)
        if self.active:self._process_next()
    def item_options(self,item):
        if item.info and not self.active:
            row=self.rows[item.path];old=row.get('editor')
            if old and old.isVisible():
                old.hide();row['options'].setText('Resolución, audio y subtítulos');return
            if old:row['layout'].removeWidget(old);old.deleteLater()
            editor=ItemOptions(item,row['frame']);row['editor']=editor;row['layout'].addWidget(editor)
            editor.saved.connect(lambda:self.options_changed(item));editor.closed.connect(lambda:self.item_options(item))
            editor.show();row['options'].setText('Ocultar resolución, audio y subtítulos')
    def options_changed(self,item):
        self.refresh_row(item);self.persist_queue()
    def remove_item(self,item):
        if self.active and self._current_path==item.path:return
        if self.active and item.path in self._run_paths:
            self._run_paths.remove(item.path)
            if item.path in self._run_processed:
                self._run_processed.remove(item.path);self.completed_count-=1
            self.total_count=len(self._run_paths)
            self.log(f'Retirado de la cola: {Path(item.path).name}. Total actual: {self.total_count} videos.')
        self.items.remove(item); self.rows.pop(item.path)['frame'].deleteLater(); self.empty.setVisible(not self.items); self.update_title(); self.persist_queue();self._update_recovery_notice()
        if self.active:self._refresh_run_summary();self._process_next()
    def clear_finished(self):
        for i in self.items[:]:
            if i.state=='Completado':self.remove_item(i)
    def update_title(self):self.queue_title.setText(f'COLA DE COMPRESIÓN  /  {len(self.items)} VIDEOS')
    def open_result(self,item):
        if item.output:QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(item.output).parent)))
    def start(self):
        if self.active or self.saving:return
        for row in self.rows.values():
            if row.get('editor') and not row['editor'].apply():return
        batch=[i for i in self.items if i.info and i.state in ('Pendiente','Error','Cancelado')]
        if not batch:QMessageBox.information(self,'Cola','Agrega un video o espera a que termine su análisis.');return
        # Global controls are the initial defaults; explicit per-video edits survive.
        self._run_paths=[i.path for i in self.items if i in batch or (i.info is None and i.state=='Analizando')]
        self._run_processed=set();self._current_path=None;self._current_percent=0.
        self.active=True; self.stop_event.clear(); self.total_count=len(self._run_paths); self.completed_count=0; self.total_progress.setValue(0); self.start_btn.setEnabled(False); self.stop_btn.setEnabled(True); self.pause_btn.setEnabled(True); self.ai_btn.setEnabled(False)
        for i in self.items:self.refresh_row(i)
        self._process_next()
    def follow_current_video(self):
        if not self.active or not getattr(self,'_current_path',None):return
        row=self.rows.get(self._current_path)
        if not row:return
        frame=row['frame']
        self.queue_layout.activate()
        self.scroll.ensureWidgetVisible(frame,0,8)
    def _refresh_run_summary(self):
        if not self.active:return
        item=self.find(self._current_path) if self._current_path else None
        if item:
            self.status.setText(f'Cola · video {self.completed_count+1} de {self.total_count} · {item.state} · {Path(item.path).name}')
        else:self.status.setText(f'Cola · {self.completed_count} de {self.total_count} procesados · Analizando los siguientes videos…')
        self.total_progress.setValue(int((self.completed_count+self._current_percent/100)/max(1,self.total_count)*100))
    def _process_next(self):
        # All queue selection and counters run on the GUI thread. Only encoding
        # runs in a worker, so metadata and newly dropped files cannot be lost.
        if not self.active or self._current_path:return
        if self.stop_event.is_set():self.on_done();return
        for path in self._run_paths:
            if path in self._run_processed:continue
            item=self.find(path)
            if item is None:
                self._run_processed.add(path);self.completed_count+=1;continue
            if item.info is None:
                if item.state=='Error':
                    self._run_processed.add(path);self.completed_count+=1;continue
                self._refresh_run_summary();return
            break
        else:self.on_done();return
        self._current_path=item.path;self._current_percent=0.
        control=engine.Control();self.control=control
        if self.paused:control.pause()
        item.state='Analizando video';self.refresh_row(item);self._refresh_run_summary();QTimer.singleShot(0,self.follow_current_video)
        def run():
            try:
                out=engine.compress(item.path,item.output_dir or str(Path(item.path).parent),dataclasses.replace(item.options),control=control,progress=lambda d,p=item.path:self.bridge.progress.emit(p,d),log=self.bridge.event.emit,ffmpeg=self.settings.get('ffmpeg') or None,ffprobe=self.settings.get('ffprobe') or None)
                self.bridge.result.emit(item.path,'Completado',str(out))
            except engine.OutputPublishError as exc:self.bridge.result.emit(item.path,'Guardado pendiente',json.dumps(dict(recovery_path=str(exc.recovery_path),desired_target=str(exc.desired_target),message=str(exc)),ensure_ascii=False))
            except engine.EncodeCancelled:self.bridge.result.emit(item.path,'Cancelado','')
            except Exception as exc:self.bridge.result.emit(item.path,'Error',str(exc))
        self.worker=threading.Thread(target=run,daemon=True); self.worker.start()
    def on_progress(self,path,d):
        i=self.find(path)
        if not i:return
        i.state=d.get('status','Comprimiendo'); self.refresh_row(i); r=self.rows[path]; r['progress'].setValue(int(d['percent'])); eta=fmt_time(d.get('eta')) if d.get('eta') is not None else 'calculando'; r['detail'].setText(f"Pasada {d.get('pass_number',1)}/2  ·  {d['percent']:.1f}%  ·  ETA {eta}  ·  {d.get('speed',0):.2f}×")
        total_passes=d.get('total_passes',2);stage='Análisis CPU' if total_passes==2 and d.get('pass_number',1)==1 else 'Compresión NVIDIA' if d.get('backend')=='nvidia' else 'Compresión CPU'
        r['detail'].setText(f"{stage} · etapa {d.get('pass_number',1)}/{total_passes} · {d['percent']:.1f}% · ETA etapa {eta}" if not i.state.startswith('Pausado') else i.state)
        self.disk_paused=d.get('disk_paused',False)
        self._current_percent=d['percent'];self._refresh_run_summary()
    def on_result(self,path,state,value):
        i=self.find(path)
        if not i:return
        i.state=state; self.completed_count+=1
        self._apply_output_result(i,state,value)
        self.refresh_row(i); self.persist_queue()
        self._run_processed.add(path);self._current_path=None;self._current_percent=0.;self.control=None
        self._refresh_run_summary();QTimer.singleShot(0,self._process_next)
    def _apply_output_result(self,item,state,value):
        row=self.rows[item.path]
        if state=='Guardado pendiente':
            data=json.loads(value);item.output=data['recovery_path'];item.recovery_target=data['desired_target'];item.state=state
            row['progress'].setValue(100);row['detail'].setText('Compresión terminada · guardado pendiente; puedes reintentar sin comprimir otra vez.')
            row['detail'].setToolTip(data['message']);self.log(data['message'])
            self.pending_saves[item.path]=dict(path=item.path,output=item.output,desired_target=item.recovery_target,info=item.info,options=dataclasses.asdict(item.options),output_dir=item.output_dir)
            self._persist_pending_saves()
        elif state=='Completado':
            item.output=value;item.recovery_target=None;item.state=state;row['progress'].setValue(100)
            size=Path(value).stat().st_size;saved=1-size/item.info['size'] if item.info and item.info.get('size') else 0
            row['detail'].setText(f'{fmt_size(size)} · ahorro {saved:.0%} · '+('calidad sin límite de tamaño' if item.options.unlimited else 'límite verificado'))
            self.log(f'Completado: {value}');self.pending_saves.pop(item.path,None);self._persist_pending_saves()
        else:
            row['detail'].setText(value[:180] or state);self.log(f'{Path(item.path).name}: {state} {value}')
    def _load_pending_saves(self):
        try:
            records=json.loads((DATA/'pending-saves.json').read_text(encoding='utf-8'))
            if isinstance(records,list):self.pending_saves={r['path']:r for r in records if isinstance(r,dict) and isinstance(r.get('path'),str) and r.get('desired_target') and Path(r.get('output') or '').is_file()}
        except (OSError,ValueError,TypeError):pass
        self._update_recovery_notice()
        if self.pending_saves:self.log(f'Hay {len(self.pending_saves)} videos ya comprimidos pendientes de guardar. Usa Recuperar guardados pendientes.')
    def _persist_pending_saves(self):
        try:
            DATA.mkdir(parents=True,exist_ok=True);temp=DATA/'pending-saves.tmp';temp.write_text(json.dumps(list(self.pending_saves.values()),ensure_ascii=False),encoding='utf-8');os.replace(temp,DATA/'pending-saves.json')
        except OSError as exc:self.log('No se pudo registrar la recuperación; el MP4 se conserva en la ruta indicada: '+str(exc))
        self._update_recovery_notice()
    def _update_recovery_notice(self):
        missing=sum(not self.find(path) for path in self.pending_saves)
        self.recovery_notice.setVisible(missing>0);self.recovery_notice.setText(f'Recuperar guardados pendientes ({missing})')
    def restore_pending_saves(self):
        for record in list(self.pending_saves.values()):
            if self.find(record['path']) or not Path(record['output']).is_file():continue
            item=Item(record['path'],self.settings)
            try:item.options=engine.CompressionOptions(**record.get('options',{}))
            except (ValueError,TypeError):pass
            item.info=record.get('info');item.state='Guardado pendiente';item.output=record['output'];item.recovery_target=record['desired_target'];item.output_dir=record.get('output_dir','')
            self.items.append(item);self.build_row(item);self.rows[item.path]['progress'].setValue(100);self.rows[item.path]['detail'].setText('Video ya comprimido · reintenta únicamente el guardado.');self.refresh_row(item)
        self.empty.setVisible(not self.items);self.update_title();self._update_recovery_notice()
    def retry_save(self,item):
        if self.active or self.saving or not item.recovery_target:return
        self.saving=True;item.state='Guardando';self.start_btn.setEnabled(False);self.ai_btn.setEnabled(False)
        for other in self.items:self.refresh_row(other)
        recovery=Path(item.output);desired=Path(item.recovery_target)
        def run():
            try:
                output=engine.finalize_output(recovery,desired,source=item.path,log=self.bridge.event.emit)
                engine.cleanup_recovery_workspace(recovery,log=self.bridge.event.emit)
                self.bridge.save_result.emit(item.path,'Completado',str(output))
            except Exception as exc:
                data=dict(recovery_path=str(getattr(exc,'recovery_path',recovery)),desired_target=str(getattr(exc,'desired_target',desired)),message=str(exc))
                self.bridge.save_result.emit(item.path,'Guardado pendiente',json.dumps(data,ensure_ascii=False))
        self.save_worker=threading.Thread(target=run,daemon=True);self.save_worker.start()
    def on_save_result(self,path,state,value):
        self.saving=False;self.start_btn.setEnabled(True);self.ai_btn.setEnabled(True)
        item=self.find(path)
        if item:self._apply_output_result(item,state,value)
        for other in self.items:self.refresh_row(other)
        self.persist_queue()
    def on_done(self):
        self.active=False; self.control=None; self.paused=False; self.pause_btn.setText('Pausar'); self.pause_btn.setEnabled(False); self.stop_btn.setEnabled(False); self.start_btn.setEnabled(True); self.ai_btn.setEnabled(True); self.total_progress.setValue(int(self.completed_count/max(1,self.total_count)*100)); self.status.setText(f'Cola finalizada · {self.completed_count} procesados.');
        for i in self.items:self.refresh_row(i)
    def pause(self):
        if self.control:
            self.paused=not self.paused; self.control.pause() if self.paused else self.control.resume(); self.pause_btn.setText('Reanudar' if self.paused else 'Pausar'); self.status.setText('Pausado: espacio insuficiente' if getattr(self,'disk_paused',False) else 'Pausa manual' if self.paused else 'Procesando cola')
    def stop(self):
        self.stop_event.set()
        if self.control:self.control.cancel()
        self.status.setText('Deteniendo…'); self.log('Se solicitó detener la cola; los archivos originales se conservan.')
        if self.active and self.control is None:self._process_next()
    def ask_ai(self):
        batch=[i for i in self.items if i.info and i.state in ('Pendiente','Error','Cancelado')]
        if not batch:QMessageBox.information(self,'IA local','Agrega al menos un video para recomendar ajustes.');return
        if self.ai_busy or self.active or self.saving:return
        self.ai_busy=True; self.ai_btn.setEnabled(False); self.start_btn.setEnabled(False); self.log('Consultando el modelo local. Solo recibe metadatos técnicos; no recibe el video.')
        snapshot=[(i.path,dict(i.info),(None if i.options.unlimited else i.options.max_gb)) for i in batch]
        def run():
            try:
                client=OllamaClient(self.settings.get('ollama_url','http://127.0.0.1:11434'))
                for path,info,cap in snapshot:self.bridge.advice.emit(path,client.recommend(info,cap,self.settings.get('ollama_model','qwen3:0.6b')))
                self.bridge.advice_error.emit('')
            except Exception as exc:self.bridge.advice_error.emit(str(exc))
        threading.Thread(target=run,daemon=True).start()
    def on_advice(self,path,advice):
        item=self.find(path)
        if not item:return
        # Present before applying. Unsupported AV1 is never silently mapped.
        codec={'h264':'h264','hevc':'h265','h265':'h265'}.get(advice['codec'])
        text=f"{Path(path).name}\n\n{advice['reason']}\n\nCódec: {advice['codec']} · resolución: {advice['resolution'] or 'original'} · audio: {advice['audio_kbps']} kbps\nVelocidad: {advice['preset']} · FPS: {advice['fps'] or 'original'}"
        if not codec:QMessageBox.information(self,'Recomendación local',text+'\nAV1 no está habilitado en esta versión. No se aplicaron cambios.');return
        if QMessageBox.question(self,'Recomendación de IA local',text+'\n\n¿Aplicar al video?')==QMessageBox.Yes:
            item.options.codec=codec; item.options.height=advice['resolution']; item.options.audio_kbps=advice['audio_kbps']; item.options.speed={'fast':'rapido','medium':'equilibrado','slow':'calidad'}[advice['preset']]
            if hasattr(item.options,'fps'):item.options.fps=advice['fps'] or None
            self.refresh_row(item); self.persist_queue(); self.log('Recomendación local aplicada a '+Path(path).name)
    def ai_error(self,message):
        self.ai_busy=False; self.ai_btn.setEnabled(True); self.start_btn.setEnabled(True)
        if message:self.log(message); QMessageBox.warning(self,'IA local',message)
    def open_settings(self):
        if self.active or self.saving:QMessageBox.information(self,'Configuración','Detén o termina la cola antes de cambiar los motores.');return
        from settings_dialog import SettingsDialog
        d=SettingsDialog(dict(self.settings),self)
        from popups import show_popup
        accepted=show_popup(d,self)
        if accepted:
            self.settings.update(d.values); save_settings(self.settings); self.output.setText(self.settings.get('output_folder','')); self.apply_theme(); self.log('Configuración guardada.'); self.apply_output()
        if getattr(d,'imported_files',[]):self.add_paths(d.imported_files)
    def persist_queue(self):
        DATA.mkdir(parents=True,exist_ok=True)
        try:
            entries=[dict(path=i.path,options=dataclasses.asdict(i.options),output_dir=i.output_dir,state=i.state,output=i.output,track_defaults_applied=i.track_defaults_applied,output_profile_version=5) for i in self.items]
            p=DATA/'queue.tmp';p.write_text(json.dumps(entries,ensure_ascii=False),encoding='utf-8');os.replace(p,DATA/'queue.json')
        except OSError as exc:self.log('No se pudo guardar la cola: '+str(exc))
    def restore_queue(self):
        try: entries=json.loads((DATA/'queue.json').read_text(encoding='utf-8'))
        except (ValueError,OSError):return
        for entry in entries if isinstance(entries,list) else []:
            if not isinstance(entry,dict) or not Path(entry.get('path','')).is_file():continue
            self.restored[entry['path']]=entry;self.add_paths([entry['path']]); item=self.find(entry['path'])
            if item:
                try:
                    item.options=engine.CompressionOptions(**entry.get('options',{}));item.output_dir=entry.get('output_dir') or self.settings['output_folder']
                    item.track_defaults_applied=entry.get('track_defaults_applied',False)
                    if entry.get('output_profile_version',0)<5:item.output_dir=self.settings['output_folder']
                    if not item.track_defaults_applied:
                        item.options.height=1080;item.options.audio_kbps=512;item.options.speed='calidad';item.options.output_name=None;item.output_dir=self.settings['output_folder']
                except (TypeError,ValueError):pass
    def showEvent(self,event):
        super().showEvent(event)
        if os.name=='nt' and self._animate_windows and not hasattr(self,'_native_icons'):
            import ctypes
            from ctypes import wintypes
            user32=ctypes.WinDLL('user32',use_last_error=True)
            user32.LoadImageW.argtypes=[wintypes.HINSTANCE,wintypes.LPCWSTR,wintypes.UINT,ctypes.c_int,ctypes.c_int,wintypes.UINT]
            user32.LoadImageW.restype=wintypes.HANDLE
            user32.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
            user32.SendMessageW.restype=ctypes.c_ssize_t
            path=str(Path(__file__).resolve().parent/'assets'/'app-logo-02.ico')
            self._native_icons=[]
            for kind,size in ((0,16),(1,48)):
                handle=user32.LoadImageW(None,path,1,size,size,0x10)
                if handle:self._native_icons.append(handle);user32.SendMessageW(int(self.winId()),0x80,kind,handle)
        if not self._has_appeared:
            self._has_appeared=True
            if self._animate_windows:self._fade_to(1.0,260)
    def _fade_to(self,opacity,duration,finished=None):
        if self._fade_animation:self._fade_animation.stop()
        self._fade_animation=QPropertyAnimation(self,b'windowOpacity',self)
        self._fade_animation.setDuration(duration);self._fade_animation.setStartValue(self.windowOpacity());self._fade_animation.setEndValue(opacity);self._fade_animation.setEasingCurve(QEasingCurve.InOutQuad)
        if finished:self._fade_animation.finished.connect(finished)
        self._fade_animation.start()
    def _finish_close(self):
        self._allow_close=True;self.close()
    def closeEvent(self,event):
        if self._allow_close:event.accept();return
        if self._closing:event.ignore();return
        if self.active or self.saving or self.downloading or self.ai_busy:
            QMessageBox.information(self,'Trabajo en curso','Espera a que termine la IA o detén las tareas antes de cerrar.');event.ignore();return
        self.persist_queue()
        if self._animate_windows:
            event.ignore();self._closing=True;self.centralWidget().setEnabled(False);self._fade_to(0.0,220,self._finish_close)
        else:event.accept()

def main():
    if os.name=='nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('FloresRoldanLuis.MBCompresorVideoIA')
    application=QApplication(sys.argv); application.setApplicationName('MB Compresor de Video IA'); application.setStyle('Fusion')
    application.setWindowIcon(QIcon(str(Path(__file__).resolve().parent/'assets'/'app-logo-02.ico')))
    window=MainWindow();window.show()
    if len(sys.argv)>2 and sys.argv[1]=='--smoke-test':
        def verify():
            destination=Path(sys.argv[2]);destination.mkdir(parents=True,exist_ok=True)
            report=dict(unlimited_checked=window.unlimited.isChecked(),size_limit_enabled=window.limit.isEnabled(),max_gb=window.limit.value(),version=VERSION,frozen=bool(getattr(sys,'frozen',False)),tools=engine.discover_tools())
            try:
                from settings_dialog import SettingsDialog
                dialog=SettingsDialog(window.settings,window,check_motors_on_open=False);dialog.show();application.processEvents();dialog.grab().save(str(destination/'settings.png'));dialog.reject()
                from acceleration import find_nvenc
                import file_locks
                report['lock_diagnostics_available']=True
                report['compression_modes']=[label for label,value in MODES]
                report['nvenc']={codec:find_nvenc(report['tools'][0],codec) for codec in ('h264','h265')}
                report['models']=OllamaClient(timeout=15).models()
                for tool in report['tools']:
                    result=subprocess.run([tool,'-version'],capture_output=True,timeout=15,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
                    if result.returncode:raise RuntimeError('Fallo de motor')
                report['ok']=True
            except Exception as exc:report['ok']=False;report['error']=str(exc)
            application.processEvents();window.grab().save(str(destination/'window.png'));(destination/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');application.quit()
        QTimer.singleShot(500,verify)
    return application.exec()

if __name__=='__main__':sys.exit(main())

