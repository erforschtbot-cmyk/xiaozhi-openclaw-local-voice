# Herkunft und Lizenzen — HassIL-Integration

Die zweite Erkennungsstufe in `hassil_router.py` nutzt fremde Vorlagen. Beide
Quellen verlangen eine Nennung, die hier dauerhaft steht.

## HassIL (Parser)

- Projekt: <https://github.com/OHF-Voice/hassil>
- Paket: `hassil` (PyPI)
- Lizenz: **Apache License 2.0**
- Verwendung: unverändert als Bibliothek eingebunden (`pip install hassil`).
  Der Quelltext liegt nicht in diesem Repository.

## Intents (deutsche Satzvorlagen)

- Projekt: <https://github.com/OHF-Voice/intents>
- Autoren: The Home Assistant Authors und die deutschen Beitragenden
  (genannt im Projekt: @andreasbrett, @easterapps, @hecktech27, @mib1185)
- Lizenz: **Creative Commons Attribution 4.0 International (CC-BY 4.0)**
- Verwendung: Die Dateien unter `hassil-data/` sind **unveränderte Kopien**
  aus `sentences/de/`, `rules/de/` und `lists/de/`. Es wurde nichts an den
  Vorlagen selbst geändert — die Anpassung an dieses Haus passiert
  ausschließlich im Python-Code (`hassil_router.py`), nicht in den YAML-Dateien.
- Stand: abgerufen am 2026-10-27.

### Was kopiert wurde

```text
hassil-data/sentences/_common.yaml
hassil-data/sentences/HassLightSet/        (12 Dateien)
hassil-data/sentences/HassTurnOn/          (13 Dateien)
hassil-data/sentences/HassTurnOff/         (10 Dateien)
hassil-data/sentences/HassClimateGetTemperature/
hassil-data/sentences/HassClimateSetTemperature/
hassil-data/sentences/HassGetState/
hassil-data/rules/*.yaml                   (6 Dateien)
hassil-data/lists/*.yaml                   (8 Dateien)
```

Nicht übernommen wurden die übrigen Intent-Gruppen (Timer, Medien, Einkaufs-
listen, Staubsauger und weitere) — sie werden hier nicht gebraucht.

## Eigener Code

`hassil_router.py`, `control.py`, `resolver.py` und die übrigen Dateien in
`~/.local/share/alexa-direct/` stammen aus diesem Projekt und stehen unter
derselben Lizenz wie das übrige Repository.
