#include "jarvis_ring_led.h"
#include "application.h"
#include "device_state.h"

#include <esp_log.h>
#include <esp_timer.h>

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

// Ring-Helligkeit in Stufen, mit K2 durchschaltbar. 0 = aus.
// Die Kanalwerte entstehen aus dem Prozentwert, damit die Farbverhaeltnisse
// und damit die Zustandssprache erhalten bleiben.
static const uint8_t RING_LEVELS[] = {0, 8, 15, 25, 45, 70};
static const int RING_LEVEL_COUNT =
    static_cast<int>(sizeof(RING_LEVELS) / sizeof(RING_LEVELS[0]));
static int ring_level_index = 3;   // Start bei 25 %

static uint8_t HI  = 24;
static uint8_t MED = 10;
static uint8_t LO  = 2;

static inline uint8_t ScaleChannel(uint8_t base, uint8_t percent) {
    if (percent == 0) {
        return 0;
    }
    uint16_t value = static_cast<uint16_t>(base) * percent / 100;
    // Nie ganz auf 0 fallen, sonst waere die Zustandsfarbe unsichtbar.
    return value == 0 ? 1 : static_cast<uint8_t>(value);
}

static void ApplyRingLevel() {
    uint8_t percent = RING_LEVELS[ring_level_index];
    HI  = ScaleChannel(96, percent);
    MED = ScaleChannel(40, percent);
    LO  = ScaleChannel(6, percent);
}

// Breathe() zaehlt pro Tick genau +1. Ein kleiner Farbabstand ergaebe bei
// festem Intervall ein hektisches Pulsieren; das Intervall wird deshalb aus
// der Strecke berechnet, damit ein Atemzug gleich lange dauert.
static inline int BreatheInterval(uint8_t from, uint8_t to, int duration_ms) {
    int steps = (to > from) ? (to - from) : 1;
    int interval = duration_ms / steps;
    return interval < 20 ? 20 : interval;
}

// Natuerliche Farbeingabe (r, g, b) -> Treiber-/Platinenreihenfolge.
static inline StripColor PanelColor(uint8_t r, uint8_t g, uint8_t b) {
    // Platine ist RGB, Treiber GRB: Rot und Gruen tauschen.
    return StripColor{g, r, b};
}

void JarvisRingLed::CycleBrightness() {
    // K2: aus -> Stufe -> Stufe -> ... -> aus.
    ring_level_index = (ring_level_index + 1) % RING_LEVEL_COUNT;
    ApplyRingLevel();
    if (RING_LEVELS[ring_level_index] == 0) {
        SetAllColor(StripColor{0, 0, 0});
    } else {
        // Neue Stufe sofort im aktuellen Zustand zeigen (blau/gruen/cyan).
        OnStateChanged();
    }
    ESP_LOGI("JarvisRingLed", "Ring-Helligkeit: %u %%",
             static_cast<unsigned>(RING_LEVELS[ring_level_index]));
}

void JarvisRingLed::OnStateChanged() {
    // Waehrend einer Aufnahme hat das weisse Licht Vorrang: sonst wuerde ein
    // Zustandswechsel (z. B. Sprechen) das Foto-Licht ueberschreiben.
    if (camera_flash_) {
        return;
    }
    // Stufe 0 = aus: keine Animation starten, Ring dunkel lassen.
    if (RING_LEVELS[ring_level_index] == 0) {
        SetAllColor(StripColor{0, 0, 0});
        return;
    }
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
            Breathe(PanelColor(0, 0, LO), PanelColor(0, 0, MED),
                    BreatheInterval(LO, MED, 2000));
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
            Breathe(PanelColor(0, LO, LO), PanelColor(0, HI, HI),
                    BreatheInterval(LO, HI, 4000));
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

void JarvisRingLed::SetCameraFlash(bool on) {
    if (on) {
        if (camera_flash_) {
            // Laufende Aufnahme: Wachhund neu stellen, nicht erneut zeichnen.
            if (flash_timer_ != nullptr) {
                esp_timer_stop(flash_timer_);
                esp_timer_start_once(flash_timer_, 30 * 1000 * 1000);
            }
            return;
        }
        camera_flash_ = true;
        // Volles Weiss (255) unabhaengig von der gedimmten Zustandsfarbe:
        // maximale Sichtbarkeit und zugleich Ausleuchtung des Motivs.
        SetAllColor(PanelColor(255, 255, 255));

        // Wachhund: bleibt die Rueckmeldung aus (haengende Aufnahme, Fehler
        // im Werkzeug), darf der Ring nicht dauerhaft weiss stehen bleiben.
        if (flash_timer_ == nullptr) {
            esp_timer_create_args_t args = {
                .callback = [](void* arg) {
                    auto* self = static_cast<JarvisRingLed*>(arg);
                    self->camera_flash_ = false;
                    self->OnStateChanged();
                },
                .arg = this,
                .dispatch_method = ESP_TIMER_TASK,
                .name = "ring_camera_flash",
                .skip_unhandled_events = true,
            };
            esp_timer_create(&args, &flash_timer_);
        }
        esp_timer_stop(flash_timer_);
        esp_timer_start_once(flash_timer_, 30 * 1000 * 1000);
        return;
    }

    if (!camera_flash_) {
        return;
    }
    camera_flash_ = false;
    if (flash_timer_ != nullptr) {
        esp_timer_stop(flash_timer_);
    }
    // Vorherigen Geraetezustand wiederherstellen (blau/gruen/cyan).
    OnStateChanged();
}

void JarvisRingLed::ShowVolume(int percent) {
    if (percent < 0) percent = 0;
    if (percent > 100) percent = 100;

    // Farbe wandert mit der Lautstaerke: gruen (leise) -> gelb -> rot (laut).
    uint8_t r, g;
    if (percent <= 50) {
        r = static_cast<uint8_t>(percent * 2 * HI / 100);
        g = HI;
    } else {
        r = HI;
        g = static_cast<uint8_t>((100 - percent) * 2 * HI / 100);
    }

    // Balken: 7 LEDs fuer 0..100 %. Aufrunden, damit schon 1 % eine LED zeigt.
    const int filled = (percent * static_cast<int>(led_count_) + 99) / 100;

    std::vector<StripColor> colors(led_count_);
    for (uint16_t i = 0; i < led_count_; i++) {
        colors[i] = (i < filled) ? PanelColor(r, g, 0) : PanelColor(0, 0, 0);
    }
    SetMultiColors(colors);

    // Nach kurzer Anzeige zurueck in den Geraetezustand (blau/gruen/cyan).
    if (restore_timer_ == nullptr) {
        esp_timer_create_args_t args = {
            .callback = [](void* arg) {
                static_cast<JarvisRingLed*>(arg)->OnStateChanged();
            },
            .arg = this,
            .dispatch_method = ESP_TIMER_TASK,
            .name = "ring_volume_restore",
            .skip_unhandled_events = true,
        };
        esp_timer_create(&args, &restore_timer_);
    }
    esp_timer_stop(restore_timer_);
    esp_timer_start_once(restore_timer_, 1400 * 1000);
}
