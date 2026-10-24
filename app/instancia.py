"""Um Flux Estúdio por pasta de instalação. Abrir o atalho com o programa já aberto (mesmo escondido
na bandeja) só traz a janela existente para frente: instantâneo, e sem segunda cópia carregando o
modelo de novo e dobrando o uso de RAM e de placa de vídeo."""
import hashlib

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import config

# Um nome por pasta: o da pasta de desenvolvimento (D:) e o instalado (E:) não se confundem.
NOME = "FluxEstudio-" + hashlib.md5(config.BASE.lower().encode("utf-8")).hexdigest()[:12]
_servidor = None


def avisar_aberta():
    """True se já existe uma cópia aberta (e ela foi avisada para aparecer)."""
    s = QLocalSocket()
    s.connectToServer(NOME)
    if not s.waitForConnected(250):
        return False
    s.write(b"mostrar")
    s.flush()
    s.waitForBytesWritten(250)
    s.disconnectFromServer()
    return True


def iniciar_com_windows(ligar):
    """Liga/desliga 'abrir junto com o Windows' (só para este usuário: HKCU\\...\\Run).
    Abre escondido na bandeja (--bandeja) e já carrega o modelo: quando for usar, está pronto."""
    import os
    import sys
    import winreg
    chave = r"Software\Microsoft\Windows\CurrentVersion\Run"
    nome = "FluxEstudio-" + NOME[-12:]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chave, 0, winreg.KEY_SET_VALUE) as k:
        if ligar:
            py = sys.executable.replace("python.exe", "pythonw.exe")
            script = os.path.join(config.BASE, "FluxEstudio.py")
            winreg.SetValueEx(k, nome, 0, winreg.REG_SZ, f'"{py}" "{script}" --bandeja')
        else:
            try:
                winreg.DeleteValue(k, nome)
            except FileNotFoundError:
                pass


def parar():
    """Antes de reiniciar: a cópia nova não pode achar esta e desistir de abrir."""
    if _servidor is not None:
        _servidor.close()
    QLocalServer.removeServer(NOME)


def escutar(ao_pedir_janela):
    global _servidor
    QLocalServer.removeServer(NOME)  # sobra de um fechamento à força
    _servidor = QLocalServer()
    _servidor.setSocketOptions(QLocalServer.UserAccessOption)

    def nova():
        while _servidor.hasPendingConnections():
            c = _servidor.nextPendingConnection()
            c.readyRead.connect(lambda c=c: (c.readAll(), ao_pedir_janela()))
            c.disconnected.connect(c.deleteLater)

    _servidor.newConnection.connect(nova)
    _servidor.listen(NOME)
