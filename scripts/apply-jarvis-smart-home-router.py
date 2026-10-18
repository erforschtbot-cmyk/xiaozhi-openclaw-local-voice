#!/usr/bin/env python3
"""Setzt die Jarvis-Gateway-Weiche fuer schnelle Smart-Home-Befehle.

Wendet zwei zusammenhaengende Aenderungen an `dist/handlers-*.mjs` an:

  W1  Weiche am Consult-Eingang (Marker jarvis-safe-router-v1)
      Vor dem Agentenlauf prueft das Gateway den vollstaendigen Nutzersatz.
      Nur ein bekannter, eindeutiger Einzelbefehl geht direkt an Alexa;
      alles andere laeuft unveraendert zum Agenten.
      Die Logik selbst liegt in gateway/smart-home-router.mjs und wird zur
      Laufzeit aus dem Bridge-Verzeichnis importiert.

  W2  Abschluss ohne Agentenlauf (Marker jarvis-direct-completion-v2)
      Ein direkter Treffer startet bewusst keinen Agenten. Der Abschluss
      wird deshalb an den bestehenden Voice-Owner gebunden, statt auf einen
      KI-Lauf zu warten. Ohne diesen Teil wird die Antwort verworfen und das
      Geraet bleibt im Zustand "Sprechen" stehen.

Beides liegt in kompilierten dist-Dateien und wird von jedem OpenClaw-Update
ueberschrieben. Nach jedem Update erneut anwenden und pruefen:

    ./scripts/apply-jarvis-smart-home-router.py
    ./scripts/verify-jarvis-smart-home-router.py
    systemctl --user restart openclaw-gateway.service

Idempotent: vorhandene Marker werden nicht erneut geschrieben.
"""
from __future__ import annotations

import datetime
import pathlib
import subprocess
import sys

MARK_W1 = "jarvis-safe-router-v1"
MARK_W2 = "jarvis-direct-completion-v2"
BRIDGE_DIR = "/home/openclaw/.local/share/jarvis-realtime-bridge"

ANCHOR_W1 = (
    "\t\tif (!params.registerRun) assertClientVoiceSessionOpen({\n"
    "\t\t\tagentId,\n"
    "\t\t\tsessionKey,\n"
    "\t\t\tvoiceSessionId\n"
    "\t\t});\n"
)

BLOCK_W1 = r"""		// jarvis-safe-router-v1: single execution owner. The old bridge fast path
		// stays disabled. Only exact, complete native transcripts can bypass the
		// LLM; complex requests take the unchanged consult path in their entirety.
		if (/^agent:voice:xiaozhi-realtime-v\d+$/.test(canonicalKey) && source === "native-delegation") {
			let jarvisRouter;
			try {
				jarvisRouter = await import("file:///home/openclaw/.local/share/jarvis-realtime-bridge/smart-home-router.mjs");
			} catch (error) {
				params.context.logGateway.warn("Jarvis direct router unavailable; using normal agent consult");
			}
			if (jarvisRouter) {
				assertCurrent?.();
				const directResult = await jarvisRouter.routeSmartHome({ sessionKey: canonicalKey, source, voiceSessionId, question: parsedArgs.question, signal });
				if (directResult) {
					// jarvis-direct-completion-v2: no agent run exists on this path.
					// Bind its one-shot completion to the same live voice owner instead.
					if (owner) {
						owner.directResultReady = true;
						owner.isCurrent = () => promptOwner === owner &&
							owner.requestSignal?.aborted !== true &&
							isAgentEventLifecycleGenerationCurrent(owner.lifecycleGeneration) &&
							params.getVoiceSessionId() === voiceSessionId;
						owner.resolveRegistration(void 0);
					}
					return directResult;
				}
			}
		}
"""

ANCHOR_W2 = "\t\tconst completed = owner.completionClaim?.claimCompletion() === true;"

BLOCK_W2 = r"""		const completed = owner.directResultReady === true
			? current
			: owner.completionClaim?.claimCompletion() === true;
		owner.directResultReady = false;"""


def apply_router(path: pathlib.Path) -> str:
    text = path.read_text()
    if MARK_W1 in text and MARK_W2 in text:
        return "already"
    if MARK_W1 in text or MARK_W2 in text:
        raise SystemExit(
            f"{path.name}: nur ein Teil der Weiche ist gesetzt. "
            "Beide Marker muessen gemeinsam vorhanden sein - Datei pruefen."
        )
    if ANCHOR_W1 not in text:
        raise SystemExit(
            f"{path.name}: Anker W1 nicht gefunden - OpenClaw-Layout hat sich geaendert."
        )
    if ANCHOR_W2 not in text:
        raise SystemExit(
            f"{path.name}: Anker W2 nicht gefunden - OpenClaw-Layout hat sich geaendert."
        )

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path.with_name(path.name + f".bak-smart-home-router-{stamp}").write_text(text)

    text = text.replace(ANCHOR_W1, ANCHOR_W1 + BLOCK_W1, 1)
    text = text.replace(ANCHOR_W2, BLOCK_W2, 1)
    path.write_text(text)

    reread = path.read_text()
    for marker in (MARK_W1, MARK_W2):
        if marker not in reread:
            raise SystemExit(f"{path.name}: Verifikation fehlgeschlagen ({marker}).")
    if f"file://{BRIDGE_DIR}/smart-home-router.mjs" not in reread:
        raise SystemExit(f"{path.name}: Router-Pfad fehlt nach dem Schreiben.")
    return "changed"


def main() -> int:
    root = pathlib.Path(
        subprocess.run(["npm", "root", "-g"], check=True, text=True,
                       capture_output=True).stdout.strip()
    )
    dist = root / "openclaw" / "dist"
    if not dist.is_dir():
        raise SystemExit(f"Kein OpenClaw-Bundle unter {dist}")

    router = pathlib.Path(BRIDGE_DIR) / "smart-home-router.mjs"
    if not router.is_file():
        raise SystemExit(
            f"{router} fehlt. Zuerst gateway/smart-home-router.mjs dorthin installieren."
        )

    handlers = [p for p in sorted(dist.glob("handlers-*.mjs"))
                if "enqueueRelayVoiceTranscript" in p.read_text()]
    if not handlers:
        raise SystemExit("Kein Realtime-Relay-Handler gefunden.")

    for path in handlers:
        print(f"{path.name}: {apply_router(path)}")

    print("Neustart noetig: systemctl --user restart openclaw-gateway.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
