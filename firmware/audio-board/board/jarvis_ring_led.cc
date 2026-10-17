#include "jarvis_ring_led.h"
#include "application.h"
#include "device_state.h"

// Visuelle Zustandssprache des 7er-RGB-Rings.
//
// Leitidee: Auch ohne Blick auf ein Display soll sofort erkennbar sein, was
// das Geraet gerade tut. Farbe = Rolle, Bewegung = Aktivitaet.
//
//   Bereitschaft    gedimmt blau, ruhig atmend (nicht aus -> im Dunkeln findbar)
//   WLAN fehlt      blau, blinkend
//   Verbinden       weiss, blinkend (schneller)
//   Zuhören         gruen, laufendes Licht im Kreis ("jetzt sprechen")
//   Sprechen        cyan, ruhig atmend
//   Hinweis/Audio   cyan, atmend (wie Sprechen)
//   Update          violett, schnell laufend
//   Aktivierung     bernstein, blinkend
//   Fehler          rot, stehend (aus jeder Entfernung sichtbar)
//   Start           weisses Licht zieht einmal durch
//
// WICHTIG: In OnStateChanged() darf NICHT SetBrightness() gerufen werden —
// jene Methode ruft ihrerseits OnStateChanged() und erzeugt damit eine
// Endlos-Rekursion (Stack-Ueberlauf, belegter Absturz). Die Helligkeit wird
// deshalb einmalig im Konstruktor gesetzt.
//
// FARBORDNUNG: Der LED-Treiber (led_strip) ist auf GRB eingestellt, weil das
// fuer WS2812 ueblich ist. Der Ring auf dieser Platine arbeitet aber in
// RGB-Reihenfolge. Ohne Anpassung landen Rot und Gruen vertauscht: "gruen"
// leuchtet rot, waehrend Blau korrekt bleibt (Blau liegt in beiden Ordnungen
// auf Position 2). Deshalb laufen alle Farben hier durch PanelColor(), das
// die beiden Kanaele passend zur Platine tauscht. Farben werden im Aufruf
// weiterhin in der natuerlichen Reihenfolge Rot, Gruen, Blau angegeben.
//
// Dasselbe Vorgehen nutzen die anderen Waveshare-Boards im Upstream
// (z. B. esp32-c6-lcd-0.85, esp32-s3-lcd-0.85): sie rufen ihre Farbhelfer
// ebenfalls mit (gruen, rot, blau).

static const uint8_t HI  = 96;   // kraeftig, aber nicht blendend
static const uint8_t MED = 40;
static const uint8_t LO  = 6;

// Natuerliche Farbeingabe (r, g, b) -> Treiber-/Platinenreihenfolge.
static inline StripColor PanelColor(uint8_t r, uint8_t g, uint8_t b) {
    // Platine ist RGB, Treiber GRB: Rot und Gruen tauschen.
    return StripColor{g, r, b};
}

void JarvisRingLed::OnStateChanged() {
    auto& app = Application::GetInstance();
    switch (app.GetDeviceState()) {
        case kDeviceStateStarting:
            // Hochfahren: helles Licht zieht einmal durch den Ring.
            Scroll(PanelColor(0, 0, 0), PanelColor(HI, HI, HI), 2, 90);
            break;

        case kDeviceStateWifiConfiguring:
            Blink(PanelColor(0, 0, HI), 700);
            break;

        case kDeviceStateIdle:
            // Bereitschaft: gedimmt blau, ruhiges Atmen.
            Breathe(PanelColor(0, 0, LO), PanelColor(0, 0, MED), 60);
            break;

        case kDeviceStateConnecting:
            Blink(PanelColor(MED, MED, MED), 250);
            break;

        case kDeviceStateListening:
        case kDeviceStateAudioTesting:
            // Zuhören: gruenes Licht laeuft im Kreis — eindeutig "ich bin
            // jetzt fuer dich da". Die Bewegung unterscheidet es von Idle.
            Scroll(PanelColor(0, LO, 0), PanelColor(0, HI, 0), 2, 120);
            break;

        case kDeviceStateSpeaking:
        case kDeviceStateNotifying:
            Breathe(PanelColor(0, LO, LO), PanelColor(0, HI, HI), 45);
            break;

        case kDeviceStateUpgrading:
            Scroll(PanelColor(LO, 0, LO), PanelColor(HI, 0, HI), 3, 70);
            break;

        case kDeviceStateActivating:
            Blink(PanelColor(HI, MED, 0), 300);
            break;

        case kDeviceStateFatalError:
        case kDeviceStateUnknown:
        default:
            // Fehler/unbekannt: rot, stehend. Bleibt an, bis der Zustand wechselt.
            SetAllColor(PanelColor(HI, 0, 0));
            break;
    }
}
