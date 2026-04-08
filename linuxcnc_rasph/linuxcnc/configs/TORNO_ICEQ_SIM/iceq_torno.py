#!/usr/bin/env python3
import sys
from PyQt5 import QtWidgets, uic, QtCore, QtGui
from PyQt5.QtWidgets import QFileDialog
import linuxcnc
import time
import hal

# -------------------------------------------------
# CONFIGURACAO DA MAQUINA
# Defina se esta maquina tem eixo Y ou nao
# -------------------------------------------------
HAS_Y_AXIS = False  # Torno atual = apenas X e Z. Futuro com Y -> mudar para True.

class IceqMainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        # carrega o arquivo .ui que está na mesma pasta
        uic.loadUi("iceq_torno.ui", self)

        # ----- objetos do LinuxCNC -----
        self.cmd = linuxcnc.command()
        self._dbg_last_status = None
        self.stat = linuxcnc.stat()

        # setpoint do spindle (S do gcode / MDI) para calculo/label quando necessario
        self._spindle_rpm_setpoint = 0.0

        # ----------------- progresso do programa (barra) -----------------
        self._gcode_total_lines = 0
        self._last_progress_pct = 0

        # garante range/visual do progressbar (se existir)
        if hasattr(self, "prg_cycle_top"):
            self.prg_cycle_top.setRange(0, 100)
            self.prg_cycle_top.setValue(0)
            self.prg_cycle_top.setTextVisible(True)
            self.prg_cycle_top.setFormat("0%")

        # ----------------------------------------------------
        # CICLO: tempo + progresso (barra superior)
        # ----------------------------------------------------
        self._cycle_running = False
        self._cycle_start_ts = None
        self._cycle_last_elapsed = 0.0

        self._gcode_total_lines = 0
        self._gcode_loaded_path = None

        # Configura a barra para mostrar % dentro
        if hasattr(self, "prg_cycle_top"):
            self.prg_cycle_top.setRange(0, 100)
            self.prg_cycle_top.setValue(0)
            self.prg_cycle_top.setTextVisible(True)
            self.prg_cycle_top.setFormat("%p%")

        # ------ eixos das coordenadas do cabecalho --------
        self.AXIS_X = 0
        self.AXIS_Z = 2

        # ----- conecta o botão MACHINE ON (topo) -----
        # (botão do topo: "MACHINE ⏻")
        self.btn_machine_off_top.clicked.connect(self.toggle_machine)

        # ----- conecta o botão EMERGÊNCIA (topo) -----
        # TROQUE "btn_emerg_top" pelo nome REAL do botão no .ui
        self.btn_emergencia_bottom.clicked.connect(self.toggle_estop)

        #----- conecta o botao START/PAUSE  (Cycle start/Pause) -----
        # ---- botao iniciar / pausa ------
        self.btn_start_cycle.clicked.connect(self.cycle_start_toggle)

        # ----- botao stop ------
        self.btn_stop_cycle.clicked.connect(self.cycle_stop)

        # ----- timer para atualizar LEDs / estados da aba MANUT -----
        self.status_timer = QtCore.QTimer(self)
        self.status_timer.timeout.connect(self.update_status_panel)
        self.status_timer.start(200)  # 200 ms

        # faz uma atualização inicial dos LEDs
        self.update_status_panel()

        # ------ botao de abrir programa ---------
        # botao de abrir no editor
        self.btn_open_program_edit.clicked.connect(self.open_program)
        # botao de abrir no visualizador de g code
        self.btn_open_program_main.clicked.connect(self.open_program)

        # ---------------------------------------------------------
        # MDI (executar comandos e manter histórico)
        # ---------------------------------------------------------
        if hasattr(self, "btn_mdi_send"):
            self.btn_mdi_send.clicked.connect(self.on_mdi_send)

        if hasattr(self, "txt_mdi_entry"):
            # Enter no teclado virtual/físico envia o comando
            try:
                self.txt_mdi_entry.returnPressed.connect(self.on_mdi_send)
            except Exception:
                pass

        # ------------------------------------------------------------
        # SPINDLE / COOLANT — ESTADO INTERNO (fonte única de verdade)
        # ------------------------------------------------------------
        self._spindle_rpm_setpoint = 0
        self._spindle_dir = 0            # +1=CW | -1=CCW | 0=STOP
        self._spindle_running = False
        self._coolant_on = False

        self._spindle_step = 100          # ajuste fino depois se quiser

        if hasattr(self, "btn_spindle_rpm_plus"):
            self.btn_spindle_rpm_plus.clicked.connect(self.spindle_rpm_plus)

        if hasattr(self, "btn_spindle_rpm_minus"):
            self.btn_spindle_rpm_minus.clicked.connect(self.spindle_rpm_minus)

        if hasattr(self, "btn_spindle_cw"):
            self.btn_spindle_cw.clicked.connect(self.spindle_cw)

        if hasattr(self, "btn_spindle_ccw"):
            self.btn_spindle_ccw.clicked.connect(self.spindle_ccw)

        if hasattr(self, "btn_spindle_stop"):
            self.btn_spindle_stop.clicked.connect(self.spindle_stop)

        if hasattr(self, "btn_refri_button"):
            self.btn_refri_button.clicked.connect(self.coolant_toggle)

        # ----- botoes de referencia -----
        self.btn_ref_all.clicked.connect(self.ref_all)
        self.btn_ref_x.clicked.connect(self.ref_x)

        if HAS_Y_AXIS:
            self.btn_ref_y.clicked.connect(self.ref_y)
            self.btn_ref_y.setEnabled(True)
        else:
            # ainda nao usamos Y nesta maquina
            self.btn_ref_y.setEnabled(False)

        self.btn_ref_z.clicked.connect(self.ref_z)
        self.btn_zero_peca_g54.clicked.connect(self.zero_g54)

        # ------------------------------------------------------------
        # OVERRIDES (Vel. Máquina / Spindle) - init padrão 100%
        # ranges: 0..120
        # ------------------------------------------------------------
        self._machine_ovr_pct = 100
        self._spindle_ovr_pct = 100

        # ----- ranges + valores iniciais -----
        if hasattr(self, "sld_vel_machine_oper"):
            self.sld_vel_machine_oper.setRange(0, 120)
            self.sld_vel_machine_oper.setValue(100)

        if hasattr(self, "spn_vel_machine_oper"):
            self.spn_vel_machine_oper.setRange(0, 120)
            self.spn_vel_machine_oper.setValue(100)
            try:
                self.spn_vel_machine_oper.setSuffix("%")
            except Exception:
                pass

        if hasattr(self, "sld_vel_spindle_oper"):
            self.sld_vel_spindle_oper.setRange(0, 120)
            self.sld_vel_spindle_oper.setValue(100)

        if hasattr(self, "spn_vel_spindle_oper"):
            self.spn_vel_spindle_oper.setRange(0, 120)
            self.spn_vel_spindle_oper.setValue(100)
            try:
                self.spn_vel_spindle_oper.setSuffix("%")
            except Exception:
                pass

        # ----- desconecta qualquer ligação antiga e reconecta com debug -----
        if hasattr(self, "sld_vel_machine_oper"):
            try:
                self.sld_vel_machine_oper.valueChanged.disconnect()
            except Exception:
                pass
            self.sld_vel_machine_oper.valueChanged.connect(self._dbg_machine_ovr_changed)
            print("[ICEQ][DBG] conectado: sld_vel_machine_oper.valueChanged -> _dbg_machine_ovr_changed")

        if hasattr(self, "spn_vel_machine_oper"):
            try:
                self.spn_vel_machine_oper.valueChanged.disconnect()
            except Exception:
                pass
            self.spn_vel_machine_oper.valueChanged.connect(self.on_machine_ovr_spin)
            print("[ICEQ][DBG] conectado: spn_vel_machine_oper.valueChanged -> on_machine_ovr_spin")

        if hasattr(self, "sld_vel_spindle_oper"):
            try:
                self.sld_vel_spindle_oper.valueChanged.disconnect()
            except Exception:
                pass
            self.sld_vel_spindle_oper.valueChanged.connect(self._dbg_spindle_ovr_changed)
            print("[ICEQ][DBG] conectado: sld_vel_spindle_oper.valueChanged -> _dbg_spindle_ovr_changed")

        if hasattr(self, "spn_vel_spindle_oper"):
            try:
                self.spn_vel_spindle_oper.valueChanged.disconnect()
            except Exception:
                pass
            self.spn_vel_spindle_oper.valueChanged.connect(self._dbg_spindle_ovr_spin_changed)
            print("[ICEQ][DBG] conectado: spn_vel_spindle_oper.valueChanged -> _dbg_spindle_ovr_spin_changed")

        # ----- aplica defaults (100% / 100%) -----
        self._apply_machine_override_pct(100)
        self._apply_spindle_override_pct(100)

    # ------------------------------------------------------------
    # OVERRIDES (Feed / Spindle) - handlers + apply + sync
    # ------------------------------------------------------------
    def _apply_machine_override_pct(self, pct: int):
        """Aplica Feed Override (FRO) no LinuxCNC. pct: 0..120"""
        try:
            pct_i = int(max(0, min(120, pct)))
            self._machine_ovr_pct = pct_i
            # LinuxCNC espera fator: 1.00 = 100%
            self.cmd.feedrate(pct_i / 100.0)
        except Exception as e:
            print(f"[ICEQ] feed override erro: {e}")


    def _sync_machine_widgets(self, pct: int):
        """Sincroniza slider e spinbox da Vel. Máquina sem loop."""
        if hasattr(self, "sld_vel_machine_oper") and self.sld_vel_machine_oper.value() != pct:
            self.sld_vel_machine_oper.blockSignals(True)
            self.sld_vel_machine_oper.setValue(pct)
            self.sld_vel_machine_oper.blockSignals(False)

        if hasattr(self, "spn_vel_machine_oper") and self.spn_vel_machine_oper.value() != pct:
            self.spn_vel_machine_oper.blockSignals(True)
            self.spn_vel_machine_oper.setValue(pct)
            self.spn_vel_machine_oper.blockSignals(False)

    def _on_machine_ovr_slider(self, value: int):
        pct = int(max(0, min(120, value)))
        self._sync_machine_widgets(pct)
        self._apply_machine_override_pct(pct)

    def _on_machine_ovr_spin(self, value: int):
        pct = int(max(0, min(120, value)))
        self._sync_machine_widgets(pct)
        self._apply_machine_override_pct(pct)

    # -------- Spindle (override do spindle) --------

    def on_spindle_ovr_slider(self, value):
        pct = self._clamp_pct(value, 0, 120)
        self._sync_spindle_widgets(pct)
        self._apply_spindle_override_pct(pct)

    def on_spindle_ovr_spin(self, value):
        pct = self._clamp_pct(value, 0, 120)
        self._sync_spindle_widgets(pct)
        self._apply_spindle_override_pct(pct)

    def _sync_spindle_widgets(self, pct):
        self._spindle_ovr_pct = pct

        if hasattr(self, "sld_vel_spindle_oper"):
            self.sld_vel_spindle_oper.blockSignals(True)
            self.sld_vel_spindle_oper.setValue(pct)
            self.sld_vel_spindle_oper.blockSignals(False)

        if hasattr(self, "spn_vel_spindle_oper"):
            self.spn_vel_spindle_oper.blockSignals(True)
            self.spn_vel_spindle_oper.setValue(pct)
            self.spn_vel_spindle_oper.blockSignals(False)

        # NÃO mexe em lbl_vel_spindle_oper aqui (título fixo “SPINDLE”)

    def _apply_spindle_override_pct(self, pct):
        """
        Aplica spindle override (0..120%) no LinuxCNC.
        Isso NÃO é o "S" do programa: é multiplicador do spindle.
        """
        pct = self._clamp_pct(pct, 0, 120)
        scale = float(pct) / 100.0

        # 1) Preferência: linuxcnc.command()
        try:
            if hasattr(self.cmd, "spindleoverride"):
                self.cmd.spindleoverride(scale)
                return
        except Exception as e:
            print(f"[ICEQ] spindle override via cmd falhou: {e}")

        # 2) Fallback: HAL (se existir writer)
        try:
            import hal

            # nomes comuns em configs com halui
            if hal.pin_has_writer("halui.spindle-override.value"):
                hal.set_p("halui.spindle-override.value", str(scale))
                return

            if hal.pin_has_writer("halui.spindle-override"):
                hal.set_p("halui.spindle-override", str(scale))
                return

        except Exception as e:
            print(f"[ICEQ] spindle override via HAL falhou: {e}")

    # --------------------------------------------------
    # -------- wrappers de debug -----------------------
    # --------------------------------------------------
    def _dbg_machine_ovr_changed(self, v):
        try:
            print(f"[ICEQ][DBG] machine_ovr changed -> {v}")
        except Exception:
            pass
        self.on_machine_ovr_slider(v)

    def _dbg_spindle_ovr_changed(self, v):
        try:
            print(f"[ICEQ][DBG] spindle_ovr changed -> {v}")
        except Exception:
            pass
        self.on_spindle_ovr_slider(v)

    def _dbg_spindle_ovr_spin_changed(self, v):
        try:
            print(f"[ICEQ][DBG] spindle_ovr spin changed -> {v}")
        except Exception:
            pass
        self.on_spindle_ovr_spin(v)


    # ---------------------------------------------------------
    # ----------- HAL float + debug do spindle override -------
    # ---------------------------------------------------------
    def _hal_float(self, pin_name):
        """
        Lê pino HAL float (se existir).
        Retorna float ou None.
        """
        try:
            import hal
            try:
                # linuxcnc hal: hal.get_value pode existir dependendo da build
                v = hal.get_value(pin_name)
                return float(v)
            except Exception:
                pass

            # fallback: cria "pin handle" se o hal expuser isso (nem sempre)
            try:
                p = hal.Pin(pin_name)
                return float(p.get())
            except Exception:
                return None
        except Exception:
            return None

    def _hal_set_float(self, pin_name, value):
        """
        Escreve em pino HAL float (se existir e for writer).
        Retorna True/False.
        """
        try:
            import hal
            try:
                if hasattr(hal, "pin_has_writer") and hal.pin_has_writer(pin_name):
                    hal.set_p(pin_name, str(float(value)))
                    return True
            except Exception:
                pass
        except Exception:
            pass
        return False

    def _apply_spindle_override_pct(self, pct):
        """
        Aplica spindle override (0..120).
        - Não deve "desligar spindle" ao ir para 0%: apenas reduz setpoint.
        - Em termos de LinuxCNC, isso precisa refletir em cmd/HAL.
        """
        pct = self._clamp_pct(pct, 0, 120)
        scale = float(pct) / 100.0

        # 1) Tentativa via linuxcnc.command() (se disponível)
        try:
            if hasattr(self.cmd, "spindleoverride"):
                self.cmd.spindleoverride(scale)
                print(f"[ICEQ] spindleoverride(cmd) scale={scale:.3f} pct={pct}")
                return
        except Exception as e:
            print(f"[ICEQ] spindleoverride(cmd) falhou: {e}")

        # 2) Fallback via HALUI pin (se existir no seu setup)
        # Observação: alguns configs expõem:
        #   halui.spindle-override.value   (float, 0..?)
        # e/ou
        #   halui.spindle-override.counts / .increase / .decrease (dependendo)
        wrote = self._hal_set_float("halui.spindle-override.value", scale)
        print(f"[ICEQ] spindleoverride(HAL) scale={scale:.3f} pct={pct} wrote={wrote}")


    # ----------------------------------------------------------
    #   Função para acender/apagar um "LED" (QFrame quadradinho)
    # ----------------------------------------------------------
    def set_led(self, frame, is_on):
        """Muda a cor do QFrame: verde ligado, vermelho escuro desligado."""
        if is_on:
            frame.setStyleSheet(
                "background-color: rgb(0, 255, 0);"
                "border: 1px solid black;"
            )
        else:
            frame.setStyleSheet(
                "background-color: rgb(255, 0, 0);"
                "border: 1px solid black;"
            )

    # ----------------------------------------------------------
    #   Botão EMERGÊNCIA (Liga / Desliga E-STOP lógico)
    # ----------------------------------------------------------
    def toggle_estop(self):
        """
        Se E-STOP estiver ativo (estop=1):
            -> manda STATE_ESTOP_RESET (sai da emergência)
        Se E-STOP estiver inativo (estop=0):
            -> manda STATE_ESTOP (entra em emergência)
        """
        try:
            self.stat.poll()
        except Exception as e:
            print(f"[ICEQ] toggle_estop: erro no stat.poll(): {e}")
            return

        estop = bool(self.stat.estop)
        print(f"[ICEQ] toggle_estop: estop={estop}")

        if estop:
            print("[ICEQ] toggle_estop: resetando E-STOP (STATE_ESTOP_RESET)")
            self.cmd.state(linuxcnc.STATE_ESTOP_RESET)
        else:
            print("[ICEQ] toggle_estop: ativando E-STOP (STATE_ESTOP)")
            self.cmd.state(linuxcnc.STATE_ESTOP)

    # ----------------------------------------------------------
    #   Botão MACHINE (liga/desliga máquina)
    # ----------------------------------------------------------
    def toggle_machine(self):
        """
        Lógica:
          - Se estiver em E-STOP -> NÃO faz nada (só avisa no terminal).
          - Se não estiver em E-STOP:
                * se não estiver enabled  -> STATE_ON
                * se já estiver enabled   -> STATE_OFF
        """
        try:
            self.stat.poll()
        except Exception as e:
            print(f"[ICEQ] toggle_machine: erro no stat.poll(): {e}")
            return

        estop = bool(self.stat.estop)
        enabled = bool(self.stat.enabled)

        print(f"[ICEQ] toggle_machine: estop={estop} enabled={enabled}")

        if estop:
            print("[ICEQ] MACHINE: em E-STOP, não vou habilitar. "
                  "Use o botão EMERGÊNCIA para resetar.")
            return

        if not enabled:
            print("[ICEQ] ligando máquina (STATE_ON)")
            self.cmd.state(linuxcnc.STATE_ON)
        else:
            print("[ICEQ] desligando máquina (STATE_OFF)")
            self.cmd.state(linuxcnc.STATE_OFF)

    # ----- Botão INICIAR/PAUSAR (Cycle start / Pause / Resume) -----
    def cycle_start_toggle(self):
        """
        Comportamento do botão INICIAR/PAUSAR:

        - Se estiver em E-STOP ou máquina desligada -> ignora.
        - Se programa estiver PAUSADO              -> AUTO_RESUME.
        - Se programa estiver RODANDO              -> AUTO_PAUSE.
        - Se programa estiver PARADO / IDLE        -> AUTO_RUN (linha 0).
        """

        try:
            self.stat.poll()
        except Exception as e:
            print(f"[ICEQ] cycle_start: erro no stat.poll(): {e}")
            return

        estop   = bool(self.stat.estop)
        enabled = bool(self.stat.enabled)
        mode    = self.stat.task_mode
        interp  = self.stat.interp_state
        paused  = bool(self.stat.paused)

        print(f"[ICEQ] cycle_start: estop={estop} enabled={enabled} "
              f"mode={mode} interp={interp} paused={paused}")

        # Segurança: se estiver em E-STOP ou máquina OFF, não faz nada
        if estop or not enabled:
            print("[ICEQ] cycle_start: ignorado (E-STOP ativo ou máquina OFF).")
            return

        # Só troca para AUTO se ainda NÃO estiver em AUTO
        if mode != linuxcnc.MODE_AUTO:
            try:
                self.cmd.mode(linuxcnc.MODE_AUTO)
                self.cmd.wait_complete()
            except Exception as e:
                print(f"[ICEQ] cycle_start: erro ao mudar para MODE_AUTO: {e}")
                return

        # --- Lógica de estados equivalente ao halui.program.* ---

        # 1) Se estiver PAUSADO -> RESUME
        if paused:
            print("[ICEQ] cycle_start: AUTO_RESUME")
            try:
                self.cmd.auto(linuxcnc.AUTO_RESUME)
            except Exception as e:
                print(f"[ICEQ] cycle_start: erro no AUTO_RESUME: {e}")
            return

        # 2) Não pausado: checamos se está rodando
        #    Consideramos rodando se o interp NÃO for IDLE
        running = (interp != linuxcnc.INTERP_IDLE)

        if running:
            # 2a) Se estiver RODANDO -> PAUSE
            print("[ICEQ] cycle_start: AUTO_PAUSE")
            try:
                self.cmd.auto(linuxcnc.AUTO_PAUSE)
            except Exception as e:
                print(f"[ICEQ] cycle_start: erro no AUTO_PAUSE: {e}")
        else:
            # 3) Caso contrário -> RUN desde a linha 0
            print("[ICEQ] cycle_start: AUTO_RUN (linha 0)")
            try:
                self.cmd.auto(linuxcnc.AUTO_RUN, 0)
            except Exception as e:
                print(f"[ICEQ] cycle_start: erro no AUTO_RUN: {e}")

    # ----------------------------------------------------------
    #   Botão STOP (para o programa atual)
    # ----------------------------------------------------------
    def cycle_stop(self):
        """
        STOP do programa:
          - Garante modo AUTO
          - Manda AUTO_ABORT (para e volta para o início do programa)
        """
        try:
            print("[ICEQ] cycle_stop: abort()")
            # abort pode ser chamado em qualquer estado
            self.cmd.abort()
        except Exception as e:
            print(f"[ICEQ] cycle_stop: erro no abort(): {e}")

       # print("[ICEQ] cycle_stop: AUTO_ABORT")
       # self.cmd.mode(linuxcnc.MODE_AUTO)
       # self.cmd.auto(linuxcnc.AUTO_ABORT)

    # ============================================================
    # OVERRIDE: Vel. Máquina (Feed + Rapid) e Spindle (Spindle Override)
    # ============================================================

    def _clamp_pct(self, v, lo=0, hi=120):
        try:
            v = int(v)
        except Exception:
            v = 100
        if v < lo:
            return lo
        if v > hi:
            return hi
        return v

    # -------- Vel. Máquina (override geral) --------

    def on_machine_ovr_slider(self, value):
        pct = self._clamp_pct(value, 0, 120)
        self._sync_machine_widgets(pct)
        self._apply_machine_override_pct(pct)

    def on_machine_ovr_spin(self, value):
        pct = self._clamp_pct(value, 0, 120)
        self._sync_machine_widgets(pct)
        self._apply_machine_override_pct(pct)

    def _sync_machine_widgets(self, pct):
        self._machine_ovr_pct = pct

        if hasattr(self, "sld_vel_machine_oper"):
            self.sld_vel_machine_oper.blockSignals(True)
            self.sld_vel_machine_oper.setValue(pct)
            self.sld_vel_machine_oper.blockSignals(False)

        if hasattr(self, "spn_vel_machine_oper"):
            self.spn_vel_machine_oper.blockSignals(True)
            self.spn_vel_machine_oper.setValue(pct)
            self.spn_vel_machine_oper.blockSignals(False)

        # Se você tiver label de texto (opcional)
        if hasattr(self, "_machine_ovr_pct"):
            try:
                self._sync_machine_widgets(int(self._machine_ovr_pct))
            except Exception:
                pass

    def _apply_machine_override_pct(self, pct):
        """
        Aplica override geral: Feed override + Rapid override.
        Em IHMs tipo Axis, isso impacta avanço/jog/tempo de movimentos.
        """
        pct = self._clamp_pct(pct, 0, 120)
        scale = float(pct) / 100.0

        # 1) Tenta via linuxcnc.command() (preferência)
        try:
            # feed override
            if hasattr(self.cmd, "feedrate"):
                self.cmd.feedrate(scale)
            # rapid override
            if hasattr(self.cmd, "rapidrate"):
                self.cmd.rapidrate(scale)
            return
        except Exception as e:
            print(f"[ICEQ] machine override via cmd falhou: {e}")

        # 2) Fallback via HAL pins do halui (se existir no seu setup)
        try:
            import hal
            # nomes típicos do HALUI (podem variar conforme config)
            # - halui.feed-override.value
            # - halui.rapid-override.value
            if hal.pin_has_writer("halui.feed-override.value"):
                hal.set_p("halui.feed-override.value", str(scale))
            if hal.pin_has_writer("halui.rapid-override.value"):
                hal.set_p("halui.rapid-override.value", str(scale))
        except Exception as e:
            print(f"[ICEQ] machine override via halui falhou: {e}")

    # -------- Spindle (override só do spindle) --------

    def on_spindle_ovr_slider(self, value):
        pct = self._clamp_pct(value, 0, 120)
        self._sync_spindle_widgets(pct)
        # IMPORTANTE: 0% NÃO dá spindle_stop; só override = 0
        self._apply_spindle_override_pct(pct)

    def on_spindle_ovr_spin(self, value):
        pct = self._clamp_pct(value, 0, 120)
        self._sync_spindle_widgets(pct)
        self._apply_spindle_override_pct(pct)

    def _sync_spindle_widgets(self, pct):
        self._spindle_ovr_pct = pct

        if hasattr(self, "sld_vel_spindle_oper"):
            self.sld_vel_spindle_oper.blockSignals(True)
            self.sld_vel_spindle_oper.setValue(pct)
            self.sld_vel_spindle_oper.blockSignals(False)

        if hasattr(self, "spn_vel_spindle_oper"):
            self.spn_vel_spindle_oper.blockSignals(True)
            self.spn_vel_spindle_oper.setValue(pct)
            self.spn_vel_spindle_oper.blockSignals(False)

        # Se você tiver label de texto (opcional)
        if hasattr(self, "_spindle_ovr_pct"):
            try:
                self._sync_spindle_widgets(int(self._spindle_ovr_pct))
            except Exception:
                pass

    def _apply_spindle_override_pct(self, pct):
        """
        Aplica spindle override (somente spindle).
        0% é permitido e NÃO deve emitir spindle_stop.
        """
        pct = self._clamp_pct(pct, 0, 120)
        scale = float(pct) / 100.0

        # 1) Tenta via linuxcnc.command()
        try:
            if hasattr(self.cmd, "spindleoverride"):
                self.cmd.spindleoverride(scale)
                return
        except Exception as e:
            print(f"[ICEQ] spindle override via cmd falhou: {e}")

        # 2) Fallback via HALUI (se existir no seu setup)
        try:
            import hal
            # nomes típicos (podem variar)
            # ex.: halui.spindle.0.override.value
            if hal.pin_has_writer("halui.spindle.0.override.value"):
                hal.set_p("halui.spindle.0.override.value", str(scale))
        except Exception as e:
            print(f"[ICEQ] spindle override via halui falhou: {e}")


    # ----------------------------------------------------------
    #   Atualiza painel de manutenção (LEDs / estados)
    # ----------------------------------------------------------
    def update_status_panel(self):
        """Chamado periodicamente pelo timer."""

        try:
            self.stat.poll()
        except Exception as e:
            print(f"[ICEQ] update_status_panel: erro no stat.poll(): {e}")
            return

        estop   = bool(self.stat.estop)
        enabled = bool(self.stat.enabled)

        # Debug no terminal para a gente enxergar o que o LinuxCNC está vendo
        # print(f"[ICEQ] tick  estop={estop}  enabled={enabled}")

        # ----- EMERGÊNCIA -----
        # estop == 1 -> emergência ATIVA -> emerg_ok = False (LED vermelho)
        emerg_ok = not estop
        self.set_led(self.led_maint_sig_emerg, emerg_ok)
        self.set_led(self.led_emerg, emerg_ok)
        self.lbl_maint_sig_emerg_state.setText("TRUE" if emerg_ok else "FALSE")

        # ----- MACHINE ON -----
        # Máquina só é "ON" se habilitada e sem E-STOP
        machine_on = enabled and not estop
        self.set_led(self.led_maint_sig_machine_on, machine_on)
        self.set_led(self.led_machine, machine_on)
        self.lbl_maint_sig_machine_on_state.setText("TRUE" if machine_on else "FALSE")

        # ----- ESTADO DO PROGRAMA (RUN / PAUSE / STOP) -----
        mode   = self.stat.task_mode
        interp = self.stat.interp_state
        paused = bool(self.stat.paused)

        # ------------------------------------------------------------
        # Amarração industrial (botões do spindle travam durante ciclo)
        # ------------------------------------------------------------
        try:
            estop = bool(self.stat.estop)
            enabled = bool(self.stat.enabled)

            machine_ready = (not estop and enabled)
            auto_active = (mode == linuxcnc.MODE_AUTO and interp != linuxcnc.INTERP_IDLE)

            spindle_enable = (machine_ready and (not auto_active))
            coolant_enable = machine_ready

            if hasattr(self, "btn_spindle_rpm_plus"):
                self.btn_spindle_rpm_plus.setEnabled(spindle_enable)
            if hasattr(self, "btn_spindle_rpm_minus"):
                self.btn_spindle_rpm_minus.setEnabled(spindle_enable)
            if hasattr(self, "btn_spindle_cw"):
                self.btn_spindle_cw.setEnabled(spindle_enable)
            if hasattr(self, "btn_spindle_ccw"):
                self.btn_spindle_ccw.setEnabled(spindle_enable)
            if hasattr(self, "btn_spindle_stop"):
                self.btn_spindle_stop.setEnabled(spindle_enable)

            if hasattr(self, "btn_refri_button"):
                self.btn_refri_button.setEnabled(coolant_enable)

            # Atualiza o RPM mostrado na tela (sempre positivo)
            self._update_spindle_rpm_label()

        except Exception as e:
            print(f"[ICEQ] amarração spindle/coolant erro: {e}")

        # ------------------------------------------------------------
        # LEDs do rodapé + manutenção: spindle e coolant
        # Regra robusta:
        #   1) HAL (motion/halui/iocontrol) -> reflete AUTO e MDI
        #   2) STAT (self.stat.spindle[0])  -> fallback
        #   3) Estado interno (botões ICEQ)-> fallback final (manual)
        # Atualiza também dois padrões de widgets na MANUT:
        #   - novos: led_maint_sig_* + lbl_maint_sig_*_state
        #   - antigos: sig_* (se existirem)
        # ------------------------------------------------------------
        try:
            spindle_dir = 0   # +1=CW, -1=CCW, 0=STOP
            spindle_on  = False

            # -------------------- 1) HAL (AUTO + MDI) --------------------
            cw  = (self._hal_bit("halui.spindle.forward") or
                   self._hal_bit("halui.spindle.0.forward") or
                   self._hal_bit("motion.spindle-forward") or
                   self._hal_bit("iocontrol.0.spindle-forward") or
                   self._hal_bit("spindle.0.forward") or
                   False)

            ccw = (self._hal_bit("halui.spindle.reverse") or
                   self._hal_bit("halui.spindle.0.reverse") or
                   self._hal_bit("motion.spindle-reverse") or
                   self._hal_bit("iocontrol.0.spindle-reverse") or
                   self._hal_bit("spindle.0.reverse") or
                   False)

            on_hal = (self._hal_bit("motion.spindle-on") or
                      self._hal_bit("iocontrol.0.spindle-on") or
                      self._hal_bit("spindle.0.on") or
                      False)

            if not on_hal:
                on_hal = bool(cw or ccw)

            if on_hal and cw and not ccw:
                spindle_on = True
                spindle_dir = 1
            elif on_hal and ccw and not cw:
                spindle_on = True
                spindle_dir = -1
            elif on_hal and (cw or ccw):
                spindle_on = True
                spindle_dir = 1 if cw else (-1 if ccw else 0)

            # -------------------- 2) STAT (fallback) --------------------
            if not spindle_on:
                try:
                    sp = self.stat.spindle[0]
                    sp_enabled = bool(getattr(sp, "enabled", False))
                    sp_dir = int(getattr(sp, "direction", 0))
                    sp_speed = float(getattr(sp, "speed", 0.0))

                    if sp_dir == 0 and abs(sp_speed) > 0.1:
                        sp_dir = 1 if sp_speed > 0 else -1

                    if (sp_enabled and sp_dir != 0) or (abs(sp_speed) > 0.1 and sp_dir != 0):
                        spindle_on = True
                        spindle_dir = sp_dir
                except Exception:
                    pass

            # -------------------- 3) Estado interno (manual ICEQ) --------------------
            if not spindle_on:
                try:
                    rpm_sp = int(abs(getattr(self, "_spindle_rpm_setpoint", 0)))
                except Exception:
                    rpm_sp = 0

                try:
                    dir_int = int(getattr(self, "_spindle_dir", 0))  # 1, -1, 0
                except Exception:
                    dir_int = 0

                if dir_int != 0 and rpm_sp > 0:
                    spindle_on = True
                    spindle_dir = dir_int

            # -------------------- Coolant --------------------
            coolant_on = False
            try:
                coolant_on = bool(self._get_coolant_on_safe())
            except Exception:
                coolant_on = False

            # -------------------- Rodapé --------------------
            if hasattr(self, "led_spindle"):
                self.set_led(self.led_spindle, spindle_on)

            if hasattr(self, "led_coolant"):
                self.set_led(self.led_coolant, coolant_on)

            # -------------------- MANUT (novo padrão) --------------------
            if hasattr(self, "led_maint_sig_spindle_cw"):
                self.set_led(self.led_maint_sig_spindle_cw, spindle_dir > 0)
            if hasattr(self, "led_maint_sig_spindle_ccw"):
                self.set_led(self.led_maint_sig_spindle_ccw, spindle_dir < 0)
            if hasattr(self, "led_maint_sig_spindle_stop"):
                self.set_led(self.led_maint_sig_spindle_stop, not spindle_on)

            self._set_state_label("lbl_maint_sig_spindle_cw_state",   spindle_dir > 0)
            self._set_state_label("lbl_maint_sig_spindle_ccw_state",  spindle_dir < 0)
            self._set_state_label("lbl_maint_sig_spindle_stop_state", not spindle_on)

            if hasattr(self, "led_maint_sig_coolant"):
                self.set_led(self.led_maint_sig_coolant, coolant_on)
            self._set_state_label("lbl_maint_sig_coolant_state", coolant_on)

            # -------------------- MANUT (padrão antigo sig_*) --------------------
            if hasattr(self, "sig_spindle_cw"):
                self.set_led(self.sig_spindle_cw, spindle_dir > 0)
            if hasattr(self, "sig_spindle_ccw"):
                self.set_led(self.sig_spindle_ccw, spindle_dir < 0)
            if hasattr(self, "sig_spindle_stop"):
                self.set_led(self.sig_spindle_stop, not spindle_on)

            if hasattr(self, "sig_coolant"):
                self.set_led(self.sig_coolant, coolant_on)

        except Exception as e:
            print(f"[ICEQ] LEDs spindle/coolant erro: {e}")

        # ------------------------------------------------------------
        # MANUT - Diagnóstico RPM / Spindle Override
        # ------------------------------------------------------------
        try:
            rpm_real = self._get_spindle_rpm_safe()

            # Fonte do RPM (encoder ou sim)
            rpm_source = "SIM"
            try:
                import hal
                if hasattr(hal, "get_value"):
                    v = hal.get_value("spindle.0.speed-in")
                    if v is not None and abs(float(v)) > 0.1:
                        rpm_source = "ENCODER"
            except Exception:
                pass

            # Atualiza labels da aba MANUT (se existirem)
            self._set_label_if_exists(
                "lbl_maint_spindle_rpm",
                f"{rpm_real:.0f} RPM"
            )

            self._set_label_if_exists(
                "lbl_maint_spindle_rpm_src",
                rpm_source
            )

            self._set_label_if_exists(
                "lbl_maint_spindle_override",
                f"{int(getattr(self, '_spindle_ovr_pct', 100))}%"
            )

        except Exception as e:
            print(f"[ICEQ] MANUT RPM erro: {e}")

        # ------------------------------------------------------------
        # ESTADOS (coluna "ESTADO" na aba MANUT) - HALUI se existir / fallback via STAT
        # ------------------------------------------------------------
        try:
            # Feedback robusto do spindle (funciona em SIM e na máquina real)
            spindle_on_fb, spindle_dir_fb = self._get_spindle_fb()

            # 1) Tenta HALUI (variações comuns de nomes)
            v_cw = self._hal_bit_multi([
                "halui.spindle.forward",
                "halui.spindle.0.forward",
            ])

            v_ccw = self._hal_bit_multi([
                "halui.spindle.reverse",
                "halui.spindle.0.reverse",
            ])

            v_stop = self._hal_bit_multi([
                "halui.spindle.stop",
                "halui.spindle.0.stop",
            ])

            v_col = self._hal_bit_multi([
                "halui.coolant.mist",
                "halui.coolant.flood",
            ])

            # 2) Fallbacks quando HALUI não existir/não refletir MDI
            if v_cw is None:
                v_cw = (spindle_dir_fb > 0 and spindle_on_fb)

            if v_ccw is None:
                v_ccw = (spindle_dir_fb < 0 and spindle_on_fb)

            if v_stop is None:
                v_stop = (not spindle_on_fb)

            if v_col is None:
                try:
                    v_col = bool(coolant_on)
                except Exception:
                    v_col = False

            # Atualiza LABELs "estado" (TRUE/FALSE) — mantém seus objectNames atuais
            self._set_state_label("lbl_maint_sig_spindle_cw_state", v_cw)
            self._set_state_label("lbl_maint_sig_spindle_ccw_state", v_ccw)
            self._set_state_label("lbl_maint_sig_spindle_stop_state", v_stop)
            self._set_state_label("lbl_maint_sig_coolant_state", v_col)

            # Atualiza LEDs do MANUT usando o MESMO valor final (HALUI ou fallback)
            if hasattr(self, "led_maint_sig_spindle_cw"):
                self.set_led(self.led_maint_sig_spindle_cw, bool(v_cw))
            if hasattr(self, "led_maint_sig_spindle_ccw"):
                self.set_led(self.led_maint_sig_spindle_ccw, bool(v_ccw))
            if hasattr(self, "led_maint_sig_spindle_stop"):
                self.set_led(self.led_maint_sig_spindle_stop, bool(v_stop))
            if hasattr(self, "led_maint_sig_coolant"):
                self.set_led(self.led_maint_sig_coolant, bool(v_col))

        except Exception as e:
            print(f"[ICEQ] estados MANUT erro: {e}")


        # ----- RPM do spindle (rodapé) -----
        try:
            # Detecta se spindle está realmente ON no LinuxCNC (fonte real)
            sp_on = False
            sp_speed_base = 0.0
            try:
                sp = self.stat.spindle[0]
                sp_enabled = bool(sp.get("enabled", 0))
                sp_dir = int(sp.get("direction", 0))
                sp_speed_base = abs(float(sp.get("speed", 0.0)))
                sp_on = (sp_enabled and sp_dir != 0 and sp_speed_base > 0.1)
            except Exception:
                sp_on = False
                sp_speed_base = 0.0

            # Override atual (0..120)
            ovr = 100
            try:
                ovr = int(getattr(self, "_spindle_ovr_pct", 100))
            except Exception:
                ovr = 100
            ovr = max(0, min(120, ovr))

            if sp_on:
                rpm_eff = sp_speed_base * (float(ovr) / 100.0)
                self._set_label_if_exists("lbl_spindle_rpm", f"{rpm_eff:.0f} RPM")
            else:
                self._set_label_if_exists("lbl_spindle_rpm", "0 RPM")

        except Exception as e:
            print(f"[ICEQ] erro RPM: {e}")

        # -------------------------------------------------------------
        # PROGRESSO DO PROGRAMA (prg_cycle_top) - % por linha executada
        # -------------------------------------------------------------
        try:
            mode   = self.stat.task_mode
            interp = self.stat.interp_state

            # só atualiza enquanto o interpretador estiver "rodando" (não IDLE)
            if mode == linuxcnc.MODE_AUTO and interp != linuxcnc.INTERP_IDLE:
                if self._gcode_total_lines > 0:
                    cur_line = int(getattr(self.stat, "current_line", 0))

                    # current_line costuma ser 1-based; garante limites
                    if cur_line < 0:
                        cur_line = 0
                    if cur_line > self._gcode_total_lines:
                        cur_line = self._gcode_total_lines

                    pct = int((cur_line * 100) / float(self._gcode_total_lines))
                    if pct < 0:
                        pct = 0
                    if pct > 100:
                        pct = 100

                    self._last_progress_pct = pct

                    if hasattr(self, "prg_cycle_top"):
                        self.prg_cycle_top.setValue(pct)
                        self.prg_cycle_top.setFormat(f"{pct}%")
            else:
                # programa terminou (IDLE) -> fecha em 100% e congela
                if hasattr(self, "prg_cycle_top"):
                    self.prg_cycle_top.setValue(100)
                    self.prg_cycle_top.setFormat("100%")
                self._last_progress_pct = 100

            # quando termina/idle: não mexe (fica congelado no último valor)
            # (igual você pediu para o tempo do ciclo)
        except Exception as e:
            print(f"[ICEQ] erro progresso: {e}")

        # Debug para entender o que o LinuxCNC está vendo
        cur = (int(mode), int(interp), bool(paused))
        if cur != self._dbg_last_status:
            self._dbg_last_status = cur
            print(f"[ICEQ] status: mode={mode} interp={interp} paused={paused}")

        # Se não estiver em AUTO, consideramos programa parado
        if mode != linuxcnc.MODE_AUTO:
            program_running = False
            program_paused  = False
        else:
            # Em AUTO:
            #  - paused True                      -> PAUSADO
            #  - paused False e interp != IDLE    -> RODANDO
            program_paused  = paused
            program_running = (not paused and interp != linuxcnc.INTERP_IDLE)

        # LEDs de START / PAUSE na aba MANUT
        self.set_led(self.led_maint_sig_start, program_running)
        self.set_led(self.led_maint_sig_pause, program_paused)

        # Labels de estado na MANUT (se estiverem criados)
        if hasattr(self, "lbl_maint_sig_start_state"):
            self.lbl_maint_sig_start_state.setText(
                "TRUE" if program_running else "FALSE"
            )
        if hasattr(self, "lbl_maint_sig_pause_state"):
            self.lbl_maint_sig_pause_state.setText(
                "TRUE" if program_paused else "FALSE"
            )

        # LED PROGRAMA no rodapé (tri-cor)
        if program_running:
            # VERDE = rodando
            color = "rgb(0, 255, 0)"
        elif program_paused:
            # AMARELO = pausado
            color = "rgb(255, 255, 0)"
        else:
            # VERMELHO = parado / idle / abortado
            color = "rgb(255, 0, 0)"

        self.led_program.setStyleSheet(
            f"background-color: {color}; border: 1px solid black;"
        )

        # ----------------------------------------------------
        # CABEÇALHO: X / Z / VEL  (com legenda + formatação)
        # ----------------------------------------------------
        try:
            lu = float(getattr(self.stat, "linear_units", 1.0))

            # Converte "unidade do stat" -> mm (robusto para lu=0.03937 ou lu=25.4)
            def to_mm(v):
                try:
                    v = float(v)
                    if lu < 0.999:      # ex.: 0.03937 (inch/mm) -> divide para obter mm
                        return v / lu
                    elif lu > 1.001:    # ex.: 25.4 (mm/inch) -> multiplica
                        return v * lu
                    else:               # lu ~ 1.0
                        return v
                except Exception:
                    return 0.0

            # posição (prefere actual_position; fallback position)
            pos = getattr(self.stat, "actual_position", None)
            if pos is None:
                pos = getattr(self.stat, "position", None)

            if pos is not None:
                # Ajuste estes índices se seu mapeamento for diferente
                x_mm = to_mm(pos[0])   # X
                z_mm = to_mm(pos[2])   # Z

                # AJUSTE os nomes abaixo para os seus widgets do cabeçalho
                # (mantém legenda + 3 casas decimais)
                if hasattr(self, "lbl_hdr_x"):
                    self.lbl_hdr_x.setText(f"X: {x_mm:.3f}")
                if hasattr(self, "lbl_hdr_z"):
                    self.lbl_hdr_z.setText(f"Z: {z_mm:.3f}")

            # velocidade atual (current_vel é unidade/seg) -> mm/s
            v = getattr(self.stat, "current_vel", None)
            if v is not None:
                v_mm_min = to_mm(v) * 60.0

                # legenda + 2 casas decimais + unidade
                if hasattr(self, "lbl_hdr_vel"):
                    self.lbl_hdr_vel.setText(f"VEL: {v_mm_min:.2f} mm/min")

        except Exception as e:
            print(f"[ICEQ] cabecalho X/Z/VEL: erro: {e}")



        # ----------------------------------------------------
        # DESTAQUE DA LINHA ATUAL DO G-CODE (Editor + Viewer)
        # ----------------------------------------------------
        try:
            mode   = self.stat.task_mode
            interp = int(self.stat.interp_state)
            paused = bool(self.stat.paused)

            if mode == linuxcnc.MODE_AUTO and interp != linuxcnc.INTERP_IDLE:
                cl = int(self.stat.current_line)

                # ------------------------------------------------
                # LÓGICA CORRETA:
                # - Enquanto a linha AINDA está em execução,
                #   o interpreter costuma apontar para a próxima.
                # - Se estiver WAITING ou PAUSED, mantém a linha anterior.
                # - Caso contrário, usa a linha reportada.
                # ------------------------------------------------
                if paused:
                    current_line = max(0, cl - 1)
                elif interp == linuxcnc.INTERP_WAITING:
                    current_line = max(0, cl - 1)
                else:
                    current_line = max(0, cl)

                # Editor
                if hasattr(self, "txt_editor"):
                    self._highlight_gcode_line(self.txt_editor, current_line)

                # Visualização G-code
                if hasattr(self, "txt_gcode_view"):
                    self._highlight_gcode_line(self.txt_gcode_view, current_line)

            else:
                # Programa parado / abortado → limpa destaque
                if hasattr(self, "txt_editor"):
                    self._clear_gcode_highlight(self.txt_editor)

                if hasattr(self, "txt_gcode_view"):
                    self._clear_gcode_highlight(self.txt_gcode_view)

        except Exception as e:
            print(f"[ICEQ] erro highlight gcode: {e}")

        # ----------------------------------------------------
        # CICLO: tempo + progresso (barra superior)
        # ----------------------------------------------------
        try:
            mode = self.stat.task_mode
            interp = self.stat.interp_state
            paused = bool(getattr(self.stat, "paused", False))

            program_active = (mode == linuxcnc.MODE_AUTO and interp != linuxcnc.INTERP_IDLE)
            program_running = (program_active and not paused)

            # Detecta INÍCIO de ciclo (primeira vez que entra rodando)
            if program_running and not self._cycle_running:
                self._cycle_running = True
                self._cycle_start_ts = time.monotonic()
                self._cycle_last_elapsed = 0.0

            # Atualiza tempo enquanto estiver ativo (conta inclusive pausas, mas congela ao finalizar)
            if self._cycle_running and self._cycle_start_ts is not None:
                elapsed_now = time.monotonic() - self._cycle_start_ts
            else:
                elapsed_now = self._cycle_last_elapsed

            # Se programa terminou/abortou (voltou para IDLE), congela o último tempo
            if self._cycle_running and not program_active:
                self._cycle_running = False
                self._cycle_last_elapsed = elapsed_now

            # Atualiza label do tempo (rodando ou congelado)
            if hasattr(self, "lbl_cycle_time_top"):
                if self._cycle_running:
                    self.lbl_cycle_time_top.setText(self._format_hms(elapsed_now))
                else:
                    self.lbl_cycle_time_top.setText(self._format_hms(self._cycle_last_elapsed))

            # Progresso do G-code (0..100%)
            if hasattr(self, "prg_cycle_top"):
                if program_active and self._gcode_total_lines and self._gcode_total_lines > 0:
                    cur = int(getattr(self.stat, "current_line", 0))
                    # current_line pode apontar para "próxima linha"; para progresso, usar cur (não -1)
                    pct = (float(cur) / float(self._gcode_total_lines)) * 100.0
                    if pct < 0.0:
                        pct = 0.0
                    if pct > 100.0:
                        pct = 100.0
                    self.prg_cycle_top.setValue(int(pct))
                else:
                    # Se não tem programa ativo, não zera automaticamente (fica no último ciclo),
                    # mas se não tem arquivo carregado, mostra 0.
                    if not self._gcode_total_lines:
                        self.prg_cycle_top.setValue(0)

        except Exception as e:
            print(f"[ICEQ] ciclo tempo/progresso: erro: {e}")

    # ----------------------------------------------------
    # GRIFO DA LINHA ATUAL (ABA PROGRAMA) - via Highlighter
    # ----------------------------------------------------
    def _get_exec_gcode_line(self):
        """
        Retorna a melhor estimativa da linha REALMENTE em execução.

        Preferência:
        1) stat.motion_line (se existir) -> costuma representar a linha em movimento.
        2) stat.current_line (fallback)  -> pode ser "próxima linha", então corrigimos por estado.
        """
        try:
            self.stat.poll()
        except Exception:
            return None

        # 1) Preferir motion_line se existir na sua versão
        if hasattr(self.stat, "motion_line"):
            try:
                ml = int(self.stat.motion_line)
                if ml >= 0:
                    return ml
            except Exception:
                pass

        # 2) Fallback: current_line
        if not hasattr(self.stat, "current_line"):
            return None

        try:
            cl = int(self.stat.current_line)
        except Exception:
            return None

        if cl < 0:
            return None

        # Alguns LinuxCNC apontam current_line como "próxima linha" durante execução.
        # A correção NÃO pode ser fixa. Vamos usar interp_state/paused.
        try:
            interp = int(self.stat.interp_state)
            paused = bool(self.stat.paused)
        except Exception:
            interp = None
            paused = False

        # Regra prática:
        # - Se está PAUSADO: normalmente a linha reportada já é a linha onde parou (não desconta).
        # - Se está RODANDO (não pausado): muitas vezes current_line aponta para a próxima -> desconta 1.
        # Isso elimina o erro típico de "grifando a linha de baixo".
        if not paused:
            cl = cl - 1

        if cl < 0:
            cl = 0

        return cl

    def _highlight_gcode_line(self, widget, line_index):
        """
        Destaca visualmente a linha 'line_index' em um QTextEdit/QPlainTextEdit.
        """
        try:
            if widget is None:
                return

            doc = widget.document()
            block = doc.findBlockByNumber(int(line_index))

            if not block.isValid():
                return

            cursor = widget.textCursor()
            cursor.setPosition(block.position())
            cursor.select(cursor.LineUnderCursor)

            widget.setTextCursor(cursor)
            widget.ensureCursorVisible()

        except Exception as e:
            print(f"[ICEQ] erro ao destacar linha: {e}")


    def _clear_gcode_highlight(self, widget):
        try:
            if widget is None:
                return
            cursor = widget.textCursor()
            cursor.clearSelection()
            widget.setTextCursor(cursor)
        except Exception:
            pass

    # ---------------------------------------------------------
    # Funcao de abrir programa para os dois botoes de abrir
    # ---------------------------------------------------------
    def open_program(self):
        """
        Abre uma janela para escolher G-code e carrega no LinuxCNC.
        """

        # ---- janela para selecionar o arquivo ----
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Abrir Programa G-code",
            "/home/iceq/linuxcnc/configs/nc_files",  # pasta inicial
            "G-code (*.ngc *.nc *.tap *.gcode);;Todos (*.*)"
        )

        # se o usuario cancelar, sai da funcao
        if not filename:
            print("[ICEQ] open_program: usuario cancelou.")
            return

        print(f"[ICEQ] Abrindo arquivo: {filename}")

        # ---- abre o programa no LinuxCNC ----
        try:
            self.cmd.program_open(filename)
        except Exception as e:
            print(f"[ICEQ] Erro ao abrir programa no LinuxCNC: {e}")
            return

        # ---- le o conteudo do arquivo ----
        try:
            with open(filename, 'r') as f:
                conteudo = f.read()
                # ----------------- prepara progresso (total de linhas) -----------------
                try:
                    linhas = conteudo.splitlines()
                    self._gcode_total_lines = max(1, len(linhas))
                    self._last_progress_pct = 0

                    if hasattr(self, "prg_cycle_top"):
                        self.prg_cycle_top.setValue(0)
                        self.prg_cycle_top.setFormat("0%")
                except Exception as e:
                    print(f"[ICEQ] erro preparando progresso: {e}")
                    self._gcode_total_lines = 0
                    self._last_progress_pct = 0
        except Exception as e:
            print(f"[ICEQ] Erro ao ler arquivo '{filename}': {e}")
            return

            # Guarda path e calcula um "total de linhas" para estimar o progresso
            self._gcode_loaded_path = filename
            self._gcode_total_lines = self._count_gcode_lines(conteudo)

            # Zera barra ao carregar novo programa (o tempo do último ciclo fica congelado)
            if hasattr(self, "prg_cycle_top"):
                self.prg_cycle_top.setValue(0)

        # ---- aba PROGRAMA (visualizacao) ----
        if hasattr(self, "txt_gcode_view"):
            try:
                self.txt_gcode_view.setPlainText(conteudo)
            except Exception as e:
                print(f"[ICEQ] Erro ao carregar em txt_gcode_view: {e}")

        # ---- aba EDITOR (edicao) ----
        if hasattr(self, "txt_editor"):
            try:
                self.txt_editor.setPlainText(conteudo)
            except Exception as e:
                print(f"[ICEQ] Erro ao carregar em txt_editor: {e}")

    # ---------------------------------------------------------
    # MDI (comandos manuais + histórico)
    # ---------------------------------------------------------
    def _append_mdi_history(self, text):
        if not hasattr(self, "txt_mdi_history"):
            return
        try:
            # QPlainTextEdit
            self.txt_mdi_history.appendPlainText(text)
            try:
                sb = self.txt_mdi_history.verticalScrollBar()
                sb.setValue(sb.maximum())
            except Exception:
                pass
        except Exception:
            # fallback mínimo (caso widget seja QTextEdit ou algo diferente)
            try:
                self.txt_mdi_history.append(text)
            except Exception:
                pass

    def _run_mdi_command(self, cmd_text):
        """
        Executa um comando no canal MDI do LinuxCNC.
        """
        try:
            # garante modo MDI antes de mandar o comando
            self.cmd.mode(linuxcnc.MODE_MDI)
            self.cmd.wait_complete()

            self.cmd.mdi(cmd_text)
            self.cmd.wait_complete()
            return True, ""
        except Exception as e:
            return False, str(e)

    def on_mdi_send(self):
        """
        Lê txt_mdi_entry, executa e joga no histórico txt_mdi_history.
        """
        if not hasattr(self, "txt_mdi_entry"):
            return

        try:
            cmd_text = self.txt_mdi_entry.text().strip()
        except Exception:
            return

        if not cmd_text:
            return

        # 1) registra no histórico
        self._append_mdi_history(f"> {cmd_text}")

        # 2) executa
        ok, err = self._run_mdi_command(cmd_text)
        if not ok:
            self._append_mdi_history(f"ERR: {err}")

        # 3) limpa e foca para o próximo comando
        try:
            self.txt_mdi_entry.clear()
            self.txt_mdi_entry.setFocus()
        except Exception:
            pass

    # ---------------------------------------------------------
    # Referencia TODOS os eixos (Home All)
    # ---------------------------------------------------------
    def ref_all(self):
        try:
            axes = "XZ" if not HAS_Y_AXIS else "XYZ"
            print(f"[ICEQ] HOME ALL ({axes})")

            self.cmd.mode(linuxcnc.MODE_MANUAL)
            self.cmd.wait_complete()

            # Home X
            self.cmd.home(0)

            # Se a maquina tiver eixo Y, faz home nele também
            if HAS_Y_AXIS:
                self.cmd.home(1)

            # Home Z
            self.cmd.home(2)

            print("[ICEQ] HOME ALL concluido")
        except Exception as e:
            print(f"[ICEQ] Erro em ref_all: {e}")

    # ---------------------------------------------------------
    # Referencia somente o eixo X
    # ---------------------------------------------------------
    def ref_x(self):
        try:
            print("[ICEQ] HOME X")
            self.cmd.mode(linuxcnc.MODE_MANUAL)
            self.cmd.wait_complete()
            self.cmd.home(0)   # eixo 0 = X
            print("[ICEQ] HOME X concluido")
        except Exception as e:
            print(f"[ICEQ] Erro em ref_x: {e}")

    # ---------------------------------------------------------
    # Referencia somente o eixo Y (futuro)
    # So sera chamado se HAS_Y_AXIS = True
    # ---------------------------------------------------------
    def ref_y(self):
        try:
            print("[ICEQ] HOME Y")
            self.cmd.mode(linuxcnc.MODE_MANUAL)
            self.cmd.wait_complete()
            self.cmd.home(1)   # eixo 1 = Y
            print("[ICEQ] HOME Y concluido")
        except Exception as e:
            print(f"[ICEQ] Erro em ref_y: {e}")

    # ---------------------------------------------------------
    # Referencia somente o eixo Z
    # ---------------------------------------------------------
    def ref_z(self):
        try:
            print("[ICEQ] HOME Z")
            self.cmd.mode(linuxcnc.MODE_MANUAL)
            self.cmd.wait_complete()
            self.cmd.home(2)   # eixo 2 = Z
            print("[ICEQ] HOME Z concluido")
        except Exception as e:
            print(f"[ICEQ] Erro em ref_z: {e}")

    # ---------------------------------------------------------
    # ZERO PECA (G54) — zera offsets da peca no sistema G54
    # ---------------------------------------------------------
    def zero_g54(self):
        try:
            print("[ICEQ] ZERO PECA (G54)")
            self.cmd.mode(linuxcnc.MODE_MDI)
            self.cmd.wait_complete()

            # Zera offsets X e Z do G54 em relacao à posicao atual
            self.cmd.mdi("G10 L20 P1 X0 Z0")
            self.cmd.wait_complete()

            print("[ICEQ] G54 zerado")
        except Exception as e:
            print(f"[ICEQ] Erro em zero_g54: {e}")

    # ---------------------------------------------------------
    # ----------  COORDENADAS DO CABECALHO E RPM DO RODAPE ----
    # ---------------------------------------------------------
    def _set_label_if_exists(self, attr_name, text):
        """Seta texto em QLabel/QLineEdit se existir no .ui."""
        try:
            w = getattr(self, attr_name, None)
            if w is None:
                return
            if hasattr(w, "setText"):
                w.setText(text)
        except Exception as e:
            print(f"[ICEQ] _set_label_if_exists erro ({attr_name}): {e}")

    def _get_spindle_rpm_safe(self):
        """
        Retorna o RPM REAL do spindle.

        Prioridade:
        1) Encoder (feedback HAL), se existir
        2) RPM comandado (S) × spindle override (fallback para SIM)
        """

        # ------------------------------------------------------------
        # 1) Tenta RPM REAL via encoder (HAL)
        # ------------------------------------------------------------
        try:
            # Exemplo de nomes comuns (ajustaremos quando você definir o final):
            #   spindle.0.speed-in
            #   motion.spindle-speed-in
            #   encoder.0.velocity (convertido)
            import hal

            # Tente os pinos mais prováveis (sem quebrar se não existirem)
            for pin in (
                "spindle.0.speed-in",
                "motion.spindle-speed-in",
            ):
                try:
                    if hasattr(hal, "get_value"):
                        v = hal.get_value(pin)
                        if v is not None:
                            rpm = abs(float(v))
                            if rpm > 0.1:
                                return rpm
                except Exception:
                    pass
        except Exception:
            pass

        # ------------------------------------------------------------
        # 2) Fallback: RPM comandado × override (SIM / sem encoder)
        # ------------------------------------------------------------
        try:
            # RPM base do comando (S do G-code)
            rpm_base = None
            try:
                rpm_base = float(self.stat.spindle[0]['speed'])
            except Exception:
                pass

            if rpm_base is None:
                return 0.0

            # Override atual
            try:
                ovr = int(getattr(self, "_spindle_ovr_pct", 100))
            except Exception:
                ovr = 100

            ovr = max(0, min(120, ovr))
            rpm_eff = abs(rpm_base) * (float(ovr) / 100.0)
            return rpm_eff

        except Exception:
            return 0.0

    # ------------------------------------------------------------
    # SPINDLE / COOLANT - lógica consolidada (sem RPM negativo no display)
    # ------------------------------------------------------------
    def _spindle_start_if_zero(self):
        """Se clicar CW/CCW com RPM zerado, inicia em 100 RPM."""
        try:
            if int(self._spindle_rpm_setpoint) <= 0:
                self._spindle_rpm_setpoint = 100
        except Exception:
            self._spindle_rpm_setpoint = 100

    def _spindle_apply(self):
        """
        Aplica setpoint interno + direção interna no LinuxCNC.
        Envia RPM sempre positivo; sentido é pelo dir (1/-1).
        """
        try:
            rpm = int(abs(self._spindle_rpm_setpoint))

            if self._spindle_dir == 0 or rpm <= 0:
                self.cmd.spindle(0)
                return

            self.cmd.spindle(int(self._spindle_dir), float(rpm))

        except Exception as e:
            print(f"[ICEQ] spindle_apply erro: {e}")


    # ============================================================
    # SPINDLE — CONTROLE MANUAL (estado interno confiável)
    # ============================================================
    def spindle_cw(self):
        try:
            if self._spindle_rpm_setpoint <= 0:
                self._spindle_rpm_setpoint = 100

            self.cmd.spindle(
                linuxcnc.SPINDLE_FORWARD,
                self._spindle_rpm_setpoint
            )

            self._spindle_dir = 1
            self._spindle_running = True

        except Exception as e:
            print(f"[ICEQ] spindle_cw erro: {e}")

    def spindle_ccw(self):
        try:
            if self._spindle_rpm_setpoint <= 0:
                self._spindle_rpm_setpoint = 100

            self.cmd.spindle(
                linuxcnc.SPINDLE_REVERSE,
                self._spindle_rpm_setpoint
            )

            self._spindle_dir = -1
            self._spindle_running = True

        except Exception as e:
            print(f"[ICEQ] spindle_ccw erro: {e}")

    def spindle_stop(self):
        try:
            self.cmd.spindle(linuxcnc.SPINDLE_OFF)

            self._spindle_dir = 0
            self._spindle_running = False
            self._spindle_rpm_setpoint = 0

        except Exception as e:
            print(f"[ICEQ] spindle_stop erro: {e}")

    def spindle_rpm_plus(self):
        try:
            self._spindle_rpm_setpoint += self._spindle_step

            if self._spindle_running:
                self.cmd.spindle(
                    linuxcnc.SPINDLE_FORWARD if self._spindle_dir > 0 else linuxcnc.SPINDLE_REVERSE,
                    self._spindle_rpm_setpoint
                )
        except Exception as e:
            print(f"[ICEQ] spindle_rpm_plus erro: {e}")

    def spindle_rpm_minus(self):
        try:
            self._spindle_rpm_setpoint -= self._spindle_step

            if self._spindle_rpm_setpoint <= 0:
                self.spindle_stop()
                return

            if self._spindle_running:
                self.cmd.spindle(
                    linuxcnc.SPINDLE_FORWARD if self._spindle_dir > 0 else linuxcnc.SPINDLE_REVERSE,
                    self._spindle_rpm_setpoint
                )
        except Exception as e:
            print(f"[ICEQ] spindle_rpm_minus erro: {e}")

    # ============================================================
    # COOLANT — CONTROLE MANUAL
    # ============================================================
    def coolant_toggle(self):
        try:
            if not self._coolant_on:
                self.cmd.mist(1)
                self._coolant_on = True
            else:
                self.cmd.mist(0)
                self._coolant_on = False
        except Exception as e:
            print(f"[ICEQ] coolant_toggle erro: {e}")

    def spindle_rpm_plus(self):
        """
        Aumenta RPM em steps.
        Se spindle estiver girando (dir != 0), aplica na hora.
        """
        try:
            self._spindle_rpm_setpoint = int(self._spindle_rpm_setpoint) + int(self._spindle_step)
            if self._spindle_rpm_setpoint < 0:
                self._spindle_rpm_setpoint = 0

            if self._spindle_dir != 0:
                self._spindle_apply()

        except Exception as e:
            print(f"[ICEQ] spindle_rpm_plus erro: {e}")

    def spindle_rpm_minus(self):
        """
        Diminui RPM em steps.
        Regra: ao chegar em 0 -> desliga o spindle automaticamente.
        """
        try:
            new_rpm = int(self._spindle_rpm_setpoint) - int(self._spindle_step)

            if new_rpm <= 0:
                self.spindle_stop()
                return

            self._spindle_rpm_setpoint = new_rpm

            if self._spindle_dir != 0:
                self._spindle_apply()

        except Exception as e:
            print(f"[ICEQ] spindle_rpm_minus erro: {e}")

    def _update_spindle_rpm_label(self):
        """
        Atualiza o label de RPM da tela usando o setpoint interno.
        Sempre mostra positivo (corrige o problema do negativo).
        """
        try:
            rpm_disp = int(abs(self._spindle_rpm_setpoint))
            # Ajuste aqui somente se seu objectName for diferente:
            self._set_label_if_exists("lbl_spindle_rpm", f"{rpm_disp:d} RPM")
        except Exception as e:
            print(f"[ICEQ] update_spindle_rpm_label erro: {e}")

    def _get_coolant_on_safe(self):
        """Retorna True/False para mist/flood."""
        try:
            mist = bool(getattr(self.stat, "mist", False))
            flood = bool(getattr(self.stat, "flood", False))
            return (mist or flood)
        except Exception:
            return False


    # -----------------------------------------------------------------
    # ------ funcao auxiliar que grifa a linha executada no gcode -----
    # -----------------------------------------------------------------
    def _on_right_tab_changed(self, idx):
        """
        Quando o usuário abre a aba PROGRAMA, reaplica o grifo imediatamente.
        Isso resolve o problema clássico: 'só grifa quando eu clico'.
        """
        try:
            # TROQUE "idx_programa" se a ordem das abas for diferente.
            # Se você não souber o índice, eu te passo como pegar por nome.
            idx_programa = 0

            if idx == idx_programa:
                if self._pending_gcode_line is not None:
                    self._highlight_gcode_line(self.txt_gcode_view, self._pending_gcode_line)
        except Exception as e:
            print(f"[ICEQ] tab changed erro: {e}")

    # ------------------------------------------------------------------------------------
    # --------------------  funcao auxiliar do contador e barra de progresso do gcode ----
    def _format_hms(self, seconds):
        try:
            seconds = int(max(0, float(seconds)))
        except Exception:
            seconds = 0
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"


    def _count_gcode_lines(self, text):
        """
        Conta linhas "úteis" do G-code para estimar progresso.
        Remove vazias e comentários simples (; e linhas iniciando com '(' ).
        """
        try:
            total = 0
            for ln in text.splitlines():
                s = ln.strip()
                if not s:
                    continue
                if s.startswith(";"):
                    continue
                if s.startswith("("):
                    continue
                total += 1
            return max(1, total)
        except Exception:
            return 0

    # -------------------------------------------------------------
    # ------- controle de status dos pinos do spindle e coolant ---
    # -------------------------------------------------------------
    def _hal_bit(self, pin_name: str):
        """
        Lê um pino HAL e retorna:
        - True/False se conseguiu ler
        - None se o pino não existir / erro
        """
        try:
            v = hal.get_value(pin_name)
            return bool(v)
        except Exception:
            return None

    def _hal_bit_multi(self, pin_names):
        """
        Tenta ler uma lista de pinos HAL (primeiro que existir).
        Retorna True/False se conseguir ler; retorna None se nenhum existir.
        """
        for p in pin_names:
            v = self._hal_bit(p)
            if v is not None:
                return v
        return None

    def _get_spindle_fb(self):
        """
        Feedback do spindle via linuxcnc.stat (fallback robusto para SIM e máquina real).
        Retorna: (spindle_on_fb: bool, spindle_dir_fb: int)
          - dir: +1 (CW), -1 (CCW), 0 (parado/indefinido)
        """
        spindle_on_fb = False
        spindle_dir_fb = 0
        try:
            # LinuxCNC normalmente expõe spindle como array: self.stat.spindle[0]
            sp = self.stat.spindle[0]

            # enabled costuma refletir spindle "ligado"
            spindle_on_fb = bool(getattr(sp, "enabled", False))

            # direction normalmente: 1 CW, -1 CCW, 0 parado
            spindle_dir_fb = int(getattr(sp, "direction", 0))

            # fallback extra: se enabled não vier confiável, usa velocidade
            if not spindle_on_fb:
                try:
                    s = float(getattr(sp, "speed", 0.0))
                    spindle_on_fb = abs(s) > 0.0
                except Exception:
                    pass

        except Exception:
            pass

        return spindle_on_fb, spindle_dir_fb


    def _set_state_label(self, widget_name: str, value: bool):
        """Atualiza QLabel de estado (TRUE/FALSE) se existir."""
        if hasattr(self, widget_name):
            getattr(self, widget_name).setText("TRUE" if value else "FALSE") 

    # ============================================================
    # SLIDER CALLBACKS - Overrides industriais
    # ============================================================

    def _on_vel_machine_changed(self, value):
        """
        Velocidade da máquina:
        - Feed Override
        - Spindle Override
        Atua durante AUTO e MDI
        """
        try:
            value = int(value)
            value = max(0, min(120, value))

            self._vel_machine_pct = value

            # Sincroniza slider <-> spinbox
            if hasattr(self, "sld_vel_machine_oper") and self.sld_vel_machine_oper.value() != value:
                self.sld_vel_machine_oper.blockSignals(True)
                self.sld_vel_machine_oper.setValue(value)
                self.sld_vel_machine_oper.blockSignals(False)

            if hasattr(self, "spn_vel_machine_oper") and self.spn_vel_machine_oper.value() != value:
                self.spn_vel_machine_oper.blockSignals(True)
                self.spn_vel_machine_oper.setValue(value)
                self.spn_vel_machine_oper.blockSignals(False)

            # Feed Override (%)
            self.cmd.feedrate(value)

            # Spindle Override (%)
            self.cmd.spindleoverride(value)

        except Exception as e:
            print(f"[ICEQ] erro vel_machine slider: {e}")


    def _on_vel_spindle_changed(self, value):
        """
        Velocidade do spindle:
        - Spindle Override
        - 0% => STOP real do spindle
        """
        try:
            value = int(value)
            value = max(0, min(120, value))

            self._vel_spindle_pct = value

            # Sincroniza slider <-> spinbox
            if hasattr(self, "sld_vel_spindle_oper") and self.sld_vel_spindle_oper.value() != value:
                self.sld_vel_spindle_oper.blockSignals(True)
                self.sld_vel_spindle_oper.setValue(value)
                self.sld_vel_spindle_oper.blockSignals(False)

            if hasattr(self, "spn_vel_spindle_oper") and self.spn_vel_spindle_oper.value() != value:
                self.spn_vel_spindle_oper.blockSignals(True)
                self.spn_vel_spindle_oper.setValue(value)
                self.spn_vel_spindle_oper.blockSignals(False)

            # 0% => STOP spindle
            if value == 0:
                self.cmd.spindle(linuxcnc.SPINDLE_OFF)
                return

            # Spindle Override (%)
            self.cmd.spindleoverride(value)

        except Exception as e:
            print(f"[ICEQ] erro vel_spindle slider: {e}")


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = IceqMainWindow()
    win.show()
    sys.exit(app.exec_())
