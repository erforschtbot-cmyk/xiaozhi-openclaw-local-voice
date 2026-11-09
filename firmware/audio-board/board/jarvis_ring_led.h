#ifndef _JARVIS_RING_LED_H_
#define _JARVIS_RING_LED_H_

#include "led/circular_strip.h"

// Visuelle Zustandssprache fuer den RGB-Ring des Waveshare
// ESP32-S3-AUDIO-Board (kein Display auf der nackten Platine).
//
// Der Ring ist die einzige Ausgabe: Farbe UND Bewegung unterscheiden die
// Zustaende, damit man auch aus einiger Entfernung erkennt, was gerade
// passiert (wartet / hoert zu / denkt nach / spricht / Fehler).
class JarvisRingLed : public CircularStrip {
public:
    explicit JarvisRingLed(gpio_num_t gpio, uint16_t max_leds)
        : CircularStrip(gpio, max_leds), led_count_(max_leds) {
        // Helligkeit genau EINMAL setzen: SetBrightness() ruft seinerseits
        // OnStateChanged(); ein Aufruf darin waere eine Endlos-Rekursion.
        SetBrightness(96, 6);
    }

    void OnStateChanged() override;

    /// Schaltet die Ring-Helligkeit eine Stufe weiter (aus -> ... -> aus).
    ///
    /// Wird von Taste K2 ausgeloest. Der Aufruf kommt bewusst von aussen:
    /// SetBrightness() ruft seinerseits OnStateChanged() auf, ein Aufruf
    /// innerhalb dieser Methode waere eine Endlos-Rekursion.
    void CycleBrightness();

    /// Helles Weiss (100 %) waehrend einer Kameraaufnahme.
    ///
    /// Der Ring ist die einzige Rueckmeldung dieses Boards. Weiss zeigt dem
    /// Nutzer sichtbar "jetzt wird fotografiert" und leuchtet den Gegenstand
    /// gleichzeitig aus. Nach dem Abschalten stellt OnStateChanged() den
    /// vorherigen Geraetezustand wieder her (blau/gruen/cyan).
    ///
    /// Ein Wachhund gibt den Ring auch dann frei, wenn die Aufnahme haengt —
    /// sonst bliebe das Board dauerhaft weiss.
    void SetCameraFlash(bool on) override;

    /// Zeigt die Lautstaerke kurz als Balken im Ring an.
    ///
    /// Die sieben LEDs fuellen sich von 0 bis 100 % auf; die Farbe wandert
    /// dabei von gruen (leise) ueber gelb nach rot (laut). Nach einer kurzen
    /// Anzeigedauer kehrt der Ring selbsttaetig in den Gerätezustand zurueck.
    ///
    /// Das ist die einzige Rueckmeldung, die dieses Board ohne Display hat.
    void ShowVolume(int percent);

private:
    const uint16_t led_count_;
    esp_timer_handle_t restore_timer_ = nullptr;
    esp_timer_handle_t flash_timer_ = nullptr;
    bool camera_flash_ = false;
};

#endif // _JARVIS_RING_LED_H_
