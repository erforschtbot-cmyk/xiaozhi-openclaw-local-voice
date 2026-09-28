# Drittquellen und Lizenzen

Dieses Repository enthält Patchdateien und Anleitungen gegen folgende Projekte:

- `78/xiaozhi-esp32`, MIT-Lizenz — Upstream wird nicht dupliziert; das Buildskript
  klont den in `VERSIONS.md` gepinnten Commit und wendet den Patch an.
- `78/uart-uhci`, Lizenz des jeweiligen Upstream-Repositories — ebenso gepinnt und
  gepatcht.
- OpenClaw — die `dist`-Patches verändern eine lokal installierte
  OpenClaw-Distribution. OpenClaw selbst wird hier nicht mitgeliefert; das
  Anwenden geschieht über `scripts/apply-openclaw-voice-dist-patches.py`.
- OpenAI GPT-Live (`gpt-live-1-codex`) — wird zur Laufzeit über OpenClaw genutzt,
  kein Code oder Schlüssel in diesem Repository. Im lokalen Modus liefert es nur
  noch Erkennung und Text; die Sprachausgabe entsteht lokal.
- **Piper** (`piper-tts`, `OHF-Voice/piper1-gpl`) — lokale neuronale
  Sprachsynthese, **GPL-3.0-or-later**. Wird zur Laufzeit als Python-Paket
  genutzt (`gateway/requirements-tts.txt`), nicht hier mitgeliefert. Wer die
  Bridge mit `--tts-local` betreibt, bindet GPL-Code ein; das ist für
  Selbsthosting und interne Nutzung unproblematisch, für das Einbetten in
  Closed-Source-Produkte aber zu prüfen. Das frühere `rhasspy/piper`-Repository
  (MIT) ist archiviert.
- **Piper-Stimmen** (`rhasspy/piper-voices`) — MIT-Lizenz. Die Stimme
  `de_DE-thorsten-medium` stammt aus dem Thorsten-Voice-Projekt
  (CC0-1.0). Stimmen werden nicht mitgeliefert; Installationsanleitung siehe
  `docs/07-LOKALE-STIMME.md`.
- `faster-whisper` / `wyoming-faster-whisper` — nur relevant, wenn die
  Erkennung ebenfalls lokal laufen soll (nicht Teil dieses Aufbaus).

## Enthaltene Binärdateien

`firmware/prebuilt/` enthält zwei vorbestätigte Images des Referenzgeräts. Sie sind
Recovery-Anker, keine Upstream-Quellen:

- `xiaozhi.bin` — enthält ausschließlich den App-/OTA-0-Bereich des selbst gebauten
  Firmware-Stands (XiaoZhi + OpenClaw-Anpassungen, siehe `firmware/patches/`).
- `merged-binary.bin` — vollständiges Flash-Abbild; der NVS-Bereich wurde geleert.

Die vollständigen Upstream-Quellen werden nicht dupliziert.
