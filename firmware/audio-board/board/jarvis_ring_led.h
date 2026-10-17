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
        : CircularStrip(gpio, max_leds) {
        // Helligkeit genau EINMAL setzen: SetBrightness() ruft seinerseits
        // OnStateChanged(); ein Aufruf darin waere eine Endlos-Rekursion.
        SetBrightness(96, 6);
    }

    void OnStateChanged() override;
};

#endif // _JARVIS_RING_LED_H_
