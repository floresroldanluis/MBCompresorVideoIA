"""Preferences and optional local integration setup."""
import json
import os
import subprocess
import sys
import urllib.request
import urllib.parse
import html
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QComboBox, QFileDialog, QScrollArea, QWidget,
    QDialogButtonBox, QFrame, QMessageBox, QListWidget, QAbstractItemView, QInputDialog, QApplication)


class Task(QThread):
    done = Signal(object)
    failed = Signal(str)
    def __init__(self, fn, parent=None):
        super().__init__(parent); self.fn = fn
    def run(self):
        try: self.done.emit(self.fn())
        except Exception as exc: self.failed.emit(str(exc))


from popups import PopupDialog,icon_button

class SettingsDialog(PopupDialog):
    def __init__(self, settings, parent=None, check_motors_on_open=True):
        super().__init__(parent)
        self.values = dict(settings); self.tasks = []; self.fields = {}
        self.setWindowTitle('Configuración · MB Compresor Pro')
        self.resize(680, 790)
        root = self.body
        controls=QHBoxLayout();controls.addStretch();controls.addWidget(icon_button('close','Cerrar configuración',self.reject));root.addLayout(controls)
        heading = QLabel('PREFERENCIAS'); heading.setObjectName('eyebrow'); root.addWidget(heading)
        title = QLabel('Configuración'); title.setStyleSheet('font-size:28px;font-weight:700'); root.addWidget(title)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget(); self.form = QVBoxLayout(body); self.form.setSpacing(13)
        scroll.setWidget(body); root.addWidget(scroll)
        self.section('Apariencia')
        self.combo('theme', 'Tema', ['Claro', 'Oscuro', 'Automático'], 'Automático')
        self.note('Automático: claro de 05:30 a 18:29 y oscuro de 18:30 a 05:29.')
        self.section('Videos de salida')
        self.path('output_folder','Carpeta de salida (vacío: junto al video original)','',True)
        self.fields['output_folder'].setPlaceholderText('Misma carpeta que cada video original')
        self.button('Guardar en Downloads',lambda:self.fields['output_folder'].setText(str(Path.home()/'Downloads')))
        self.button('Usar carpeta del video original',lambda:self.fields['output_folder'].clear())
        self.note('Por defecto se guarda junto al original. Downloads es opcional. El original se conserva.')
        self.section('Inteligencia artificial · 100 % local')
        self.line('ollama_url', 'Servidor Ollama local', 'http://127.0.0.1:11434')
        self.combo('ollama_model', 'Modelo instalado', [], '')
        self.button('Buscar modelos locales', self.refresh_models)
        self.note('Instala Ollama y un modelo local para recibir recomendaciones. Se envían solo metadatos técnicos al servidor de tu equipo; no se sube el video.')
        self.section('Actualización de la aplicación')
        from app_updates import DEFAULT_REPOSITORY
        self.line('releases_url','Repositorio o GitHub Releases',DEFAULT_REPOSITORY)
        self.fields['releases_url'].setStyleSheet('font-weight:700;')
        self.form.itemAt(self.form.count()-2).widget().setStyleSheet('font-weight:700;')
        self.release=None;self.update_busy=False
        self.release_status=QLabel('Versión actual: '+getattr(parent,'version','1.0.8')+'. Busca actualizaciones para comprobar si hay una nueva versión.');self.release_status.setWordWrap(True);self.release_status.setObjectName('muted');self.release_status.setStyleSheet('font-size:11px;color:#71819c;')
        update_actions=QHBoxLayout();update_actions.setSpacing(10)
        self.search_update=QPushButton('Buscar actualización');self.search_update.clicked.connect(self.check_release);update_actions.addWidget(self.search_update)
        self.install_update=QPushButton('Instalar actualización y reiniciar el programa');self.install_update.setObjectName('primary');self.install_update.setEnabled(False);self.install_update.clicked.connect(self.install_release);update_actions.addWidget(self.install_update,1)
        self.search_update.setFixedHeight(46);self.install_update.setFixedHeight(46)
        self.form.addLayout(update_actions);self.form.addWidget(self.release_status)
        self.fields['releases_url'].textChanged.connect(self.invalidate_release)
        separator=QFrame();separator.setFrameShape(QFrame.HLine);separator.setStyleSheet('color:#d9e1ee;margin-top:8px;margin-bottom:8px;');self.form.addWidget(separator)
        self.section('Motores de descarga')
        self.note('Motores de compresión: FFmpeg, FFprobe y componente NVIDIA. yt-dlp/Deno no se utilizan para comprimir archivos locales.')
        self.motor_status=QLabel('Estado: comprobación pendiente…');self.motor_status.setWordWrap(True);self.motor_status.setTextFormat(Qt.RichText)
        self.motor_status.setStyleSheet('background:#e9f0fa;color:#132c49;border-radius:12px;padding:16px;line-height:1.6')
        self.form.addWidget(self.motor_status)
        motor_actions=QHBoxLayout();self.motor_buttons=[]
        for title,callback in [('Actualizar motores',self.update_downloader),('Reparar FFmpeg',self.repair_engines)]:
            b=QPushButton(title);b.setFixedHeight(44);b.clicked.connect(callback);motor_actions.addWidget(b);self.motor_buttons.append(b)
        self.form.addLayout(motor_actions);self.motor_busy=False;self.motor_refresh_pending=False
        self.note('Compara las versiones instaladas con sus publicaciones oficiales. Si no hay conexión o no se pueden comparar, lo indica sin afirmar que estén actualizadas.')
        self.status = QLabel('Listo.'); self.status.setWordWrap(True); self.form.addWidget(self.status)
        self.form.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Guardar cambios')
        buttons.button(QDialogButtonBox.Cancel).setText('Cancelar')
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        if check_motors_on_open:QTimer.singleShot(100,lambda:self.check_engines() if self.isVisible() else None)

    def section(self, title):
        label = QLabel(title); label.setStyleSheet('font-size:18px;font-weight:650;margin-top:17px'); self.form.addWidget(label)
    def note(self, text):
        label = QLabel(text); label.setWordWrap(True); label.setStyleSheet('font-size:11px;color:#71819c'); self.form.addWidget(label)
    def line(self, key, title, default='', secret=False):
        self.form.addWidget(QLabel(title)); field = QLineEdit(str(self.values.get(key, default)))
        if secret: field.setEchoMode(QLineEdit.Password)
        self.fields[key] = field; self.form.addWidget(field); return field
    def combo(self, key, title, choices, default):
        self.form.addWidget(QLabel(title)); field = QComboBox(); field.addItems(choices)
        current = str(self.values.get(key, default))
        if current and field.findText(current)<0: field.addItem(current)
        field.setCurrentText(current); self.fields[key]=field; self.form.addWidget(field); return field
    def path(self, key, title, default='', directory=False):
        self.form.addWidget(QLabel(title)); row=QHBoxLayout(); field=QLineEdit(str(self.values.get(key,default))); self.fields[key]=field
        button=QPushButton('Cambiar'); row.addWidget(field); row.addWidget(button); self.form.addLayout(row)
        def choose():
            selected=QFileDialog.getExistingDirectory(self,title,field.text()) if directory else QFileDialog.getOpenFileName(self,title,field.text(),'Ejecutables (*.exe);;Todos (*)')[0]
            if selected: field.setText(selected)
        button.clicked.connect(choose)
    def button(self, title, callback):
        button=QPushButton(title); button.clicked.connect(callback); self.form.addWidget(button)
    def run_task(self, fn, callback=None):
        self.status.setText('Procesando…'); task=Task(fn,self); self.tasks.append(task)
        task.done.connect(callback or (lambda result:self.status.setText(str(result))))
        task.failed.connect(lambda error:self.status.setText('No se pudo completar: '+error))
        task.finished.connect(lambda:self.tasks.remove(task)); task.start()
        return task
    def refresh_models(self):
        from local_ai import OllamaClient
        url=self.fields['ollama_url'].text().strip()
        def loaded(models):
            field=self.fields['ollama_model']; current=field.currentText(); field.clear(); field.addItems(models)
            if current in models: field.setCurrentText(current)
            self.status.setText(f'{len(models)} modelos locales disponibles.' if models else 'No hay modelos locales instalados en Ollama.')
        self.run_task(lambda:OllamaClient(url,timeout=10).models(),loaded)
    def check_engines(self):
        from motors import inspect_motors
        settings=self.motor_settings()
        self.run_motor_task(lambda:inspect_motors(settings,online=True),self.show_motor_report,'Buscando motores y versiones oficiales…')
    def motor_settings(self):
        values=dict(self.values)
        values['compression_only']=True
        return values
    def run_motor_task(self,fn,callback,text):
        if self.motor_busy:return
        parent=self.parent()
        if getattr(parent,'active',False) or getattr(parent,'ai_busy',False):
            self.motor_status.setText('Espera a que terminen las tareas actuales antes de comprobar, actualizar o reparar motores.')
            return
        self.motor_busy=True;self.motor_status.setText(html.escape(text))
        for b in self.motor_buttons:b.setEnabled(False)
        task=self.run_task(fn,callback)
        task.failed.connect(lambda error:self.motor_status.setText('No se pudo completar: '+html.escape(error)))
        def finished():
            self.motor_busy=False
            for b in self.motor_buttons:b.setEnabled(True)
            if self.motor_refresh_pending:
                self.motor_refresh_pending=False
                QTimer.singleShot(0,self.check_engines)
        task.finished.connect(finished)
    def show_motor_report(self,report):
        rows=['Estado: <b>'+('Listo para comprimir' if report.get('ready') else 'Requiere atención')+'</b>']
        for name in ('ffmpeg','ffprobe'):
            entry=report.get('entries',{}).get(name,{})
            if not entry.get('installed',bool(entry.get('version'))):state='No instalado'
            elif not entry.get('healthy'):state='Instalado · requiere reparación'
            elif entry.get('update_available') is True:state='Instalado · requiere actualización → '+str(entry.get('latest',''))
            elif entry.get('update_available') is False:state='Instalado · actualizado'
            else:state='Instalado · actualización no comprobada'
            rows.append(html.escape({'ffmpeg':'FFmpeg','ffprobe':'FFprobe'}.get(name,name))+': <b>'+html.escape(str(entry.get('version') or 'No encontrado'))+'</b> · '+html.escape(state))
            if entry.get('error'):rows.append('<small>'+html.escape(str(entry['error']))+'</small>')
            if entry.get('update_error'):rows.append('<small>No se pudo consultar la versión remota: '+html.escape(str(entry['update_error']))+'</small>')
        gpu=report.get('gpu',{})
        encoders=set(report.get('installed_encoders',[]))
        rows.append('AMD AMF: <b>'+('Instalado en FFmpeg · compatible y probado' if gpu.get('h264_amf',{}).get('available') else 'Instalado en FFmpeg · no utilizable con la GPU/controlador actual' if 'h264_amf' in encoders else 'No instalado')+'</b>')
        nvenc=report.get('nvenc',{})
        for codec,title in [('h264','NVIDIA NVENC H.264'),('h265','NVIDIA NVENC H.265')]:
            status=nvenc.get(codec,{})
            rows.append(title+': <b>'+('Instalado · compatible y probado' if status.get('available') else 'Instalado en FFmpeg · requiere GPU/controlador compatible' if ('h264_nvenc' if codec=='h264' else 'hevc_nvenc') in encoders else 'No instalado')+'</b>')
            if status.get('error'):rows.append('<small>'+html.escape(status['error'][:350])+'</small>')
        if report.get('nvidia_device'):rows.append('GPU y controlador: <b>'+html.escape(report['nvidia_device'])+'</b>')
        if report.get('nvidia_version'):rows.append('Motor NVIDIA compatible: <b>FFmpeg '+html.escape(report['nvidia_version'])+'</b>')
        for encoder,title in [('libx264','CPU H.264'),('libx265','CPU H.265')]:
            rows.append(title+': <b>'+('Instalado en FFmpeg · dos pasadas' if encoder in encoders else 'No instalado')+'</b>')
        rows.append('<small>Los codificadores incluidos se actualizan con FFmpeg. El motor NVIDIA compatible se conserva mientras funcione.</small>')
        rows.append('<small>yt-dlp y Deno no son necesarios para comprimir archivos locales.</small>')
        rows.append('Automático: <b>NVIDIA si funciona; CPU como alternativa</b>')
        rows.append('Modo autónomo: <b>'+('Sí, motores incluidos' if report.get('autonomous') else 'Usa uno o más motores externos')+'</b>')
        self.motor_status.setText('<br>'.join(rows));self.status.setText('Comprobación de motores terminada.')
    def update_downloader(self):
        from motors import update_motors
        settings=self.motor_settings()
        self.run_motor_task(lambda:update_motors(settings),self.motors_changed,'Comprobando e instalando actualizaciones de motores…')
    def repair_engines(self):
        from motors import repair_ffmpeg
        settings=self.motor_settings()
        self.run_motor_task(lambda:repair_ffmpeg(settings),self.motors_changed,'Validando FFmpeg y FFprobe; reparando si es necesario…')
    def motors_changed(self,result):
        for name in ('ffmpeg','ffprobe'):
            if result.get(name):self.values[name]=result[name]
        messages=result.get('messages',[])
        self.status.setText('\n'.join(messages)+'\nGuarda los cambios para conservar las rutas.')
        self.motor_status.setText('Operación terminada.<br>'+html.escape('\n'.join(messages)).replace('\n','<br>'))
        self.motor_refresh_pending=True
    def check_release(self):
        if self.update_busy:return
        from app_updates import check_update
        repository=self.fields['releases_url'].text().strip();version=getattr(self.parent(),'version','1.0.6')
        self.release=None;self.install_update.setEnabled(False);self.search_update.setEnabled(False);self.fields['releases_url'].setEnabled(False);self.update_busy=True;self.release_status.setText('Buscando actualizaciones en GitHub Releases…')
        task=self.run_task(lambda:check_update(repository,version),self.release_checked)
        task.failed.connect(lambda error:self.release_status.setText('No se pudo buscar la actualización: '+error))
        task.finished.connect(self.release_task_finished)
    def invalidate_release(self):
        self.release=None;self.install_update.setEnabled(False)
    def release_checked(self,release):
        self.release=release;self.release_status.setText(('Versión actual: '+release['current']+'. Nueva versión disponible: '+release['latest']+'.' if release.get('available') else ('Ya tienes la versión más reciente ('+release['current']+').' if release.get('latest') else 'Versión actual: '+release['current']+'. '+release['message'])));self.install_update.setEnabled(bool(release['installable']));self.install_update.setToolTip(release['message']);self.status.setText('Búsqueda de actualizaciones terminada.')
    def release_task_finished(self):
        if getattr(self,'prepared_install',False):return
        self.update_busy=False;self.search_update.setEnabled(True);self.fields['releases_url'].setEnabled(True)
    def install_release(self):
        if self.update_busy or not self.release or not self.release.get('installable'):return
        parent=self.parent()
        if any(t.isRunning() for t in self.tasks) or getattr(parent,'active',False) or getattr(parent,'ai_busy',False):
            self.release_status.setText('Espera a que terminen las tareas actuales antes de actualizar.');return
        if not getattr(sys,'frozen',False):
            self.release_status.setText('La instalación automática está disponible desde el ejecutable instalado.');return
        from app_updates import prepare_update
        updates=Path(os.environ.get('USERPROFILE',str(Path.home())))/'AppData'/'Local'/'MBCompresorPro'/'updates'
        release=dict(self.release);self.update_busy=True;self.install_update.setEnabled(False);self.search_update.setEnabled(False);self.fields['releases_url'].setEnabled(False);self.release_status.setText('Descargando y verificando la actualización. La aplicación se reiniciará al terminar…')
        task=self.run_task(lambda:prepare_update(release,updates),self.install_prepared)
        task.failed.connect(lambda error:self.release_status.setText('No se instaló la actualización: '+error))
        task.finished.connect(self.release_task_finished)
    def install_prepared(self,prepared):
        self.prepared_install=True
        def finish():
            if any(t.isRunning() for t in self.tasks):QTimer.singleShot(100,finish);return
            try:
                from app_updates import make_install_plan,launch_installer
                script,plan=make_install_plan(prepared,Path(sys.executable).parent,os.getpid())
                parent=self.parent()
                # Persist the visible preferences before closing; no credentials are introduced.
                for key,field in self.fields.items():self.values[key]=field.currentText() if isinstance(field,QComboBox) else field.text().strip()
                if parent:
                    parent.settings.update(self.values);parent.persist_queue()
                    settings_path=Path(os.environ.get('USERPROFILE',str(Path.home())))/'AppData'/'Local'/'MBCompresorPro'/'settings.json'
                    temp=settings_path.with_suffix('.update.tmp');temp.write_text(json.dumps(parent.settings,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,settings_path)
                launch_installer(script,plan);self.accept()
                if parent:parent.close()
                else:QApplication.instance().quit()
            except Exception as exc:
                self.prepared_install=False;self.release_task_finished();self.release_status.setText('No se pudo iniciar la instalación: '+str(exc))
        QTimer.singleShot(150,finish)
    def save(self):
        if self.update_busy or any(t.isRunning() for t in self.tasks):
            self.status.setText('Espera a que termine la operación antes de guardar.'); return
        from local_ai import OllamaClient
        try: OllamaClient(self.fields['ollama_url'].text().strip())
        except Exception as exc: QMessageBox.warning(self,'IA local',str(exc)); return
        for key,field in self.fields.items():
            if key=='telegram_api_hash': continue
            self.values[key]=field.currentText() if isinstance(field,QComboBox) else field.text().strip()
        self.values.pop('telegram_api_hash',None); self.accept()
    def reject(self):
        if self.update_busy or any(t.isRunning() for t in self.tasks):
            self.status.setText('Espera a que termine la operación actual antes de cerrar.'); return
        super().reject()
    def closeEvent(self,event):
        if self.update_busy or any(t.isRunning() for t in self.tasks): event.ignore(); return
        super().closeEvent(event)

