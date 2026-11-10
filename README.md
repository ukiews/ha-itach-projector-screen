# iTach Projector Screen

A Home Assistant cover integration for projector screens wired to a **Global Caché iTach IP2CC**. It uses the local TCP API and exposes standard cover actions for raising, lowering, and stopping the screen.

## Wiring and commands

This integration supports a screen that uses momentary dry contacts. On module 1, relay 1 raises the screen, relay 2 lowers it, and pulsing both together stops it. The raise and lower pulses are 100 ms. The Stop pulse is 500 ms. Confirm your screen controller uses this wiring and has functioning physical travel limits before enabling motion.

The integration serializes commands and checks that both contacts released after each pulse. If release verification fails, it reconnects to release the contacts. Movement commands are sent once.

## Install

Requires Home Assistant 2026.11 or later.

1. Add this GitHub repository as a custom **Integration** repository in HACS or Marketplace. For manual installation, extract the release ZIP into `<config>/custom_components/itach_projector_screen/`.
2. Restart Home Assistant.
3. In **Settings → Devices & services**, select **Add integration → iTach Projector Screen**. Enter the controller's IPv4 address. The default TCP port is 4998.
4. Use the new cover entity in dashboards and automations. **Open** raises the screen and **Close** lowers it.

The optional **Motion enable helper** accepts an input boolean. Switching it off blocks subsequent raise and lower commands. Stop remains available. To stop existing motion when switching the helper off, call `cover.stop_cover` from an automation.

## Optional timed positioning

Open the projector screen device in Home Assistant, select its **iTach Projector Screen** integration, and choose **Configure**. The guided calibration raises the screen to establish the starting point, then measures the descent and ascent as you mark the bottom and top. You can adjust the recorded times before saving. Saving sets the cover to 100% and enables the position slider immediately.

Stay where you can see the screen and keep its travel area clear. Choose **Stop and cancel** to end a calibration early. Home Assistant also attempts Stop when it closes the wizard or the session times out. Mark the endpoints as soon as the screen stops to reduce measurement error.

You can also use **Reconfigure** on the integration to enter separate full raise and full lower times in seconds. Leave both fields empty for basic Up, Down, and Stop with unknown position.

With timing enabled, raise the screen fully in Home Assistant to calibrate the estimate. **100%** is fully raised and **0%** is fully lowered. A percentage command starts a move and sends Stop after the calculated travel time. Full raise or lower commands run for the configured full travel time plus one second before resetting the estimate at the corresponding limit. The motor's physical limits must stop travel at the endpoints.

Position is a time estimate and assumes Home Assistant is the sole controller. Wall switches or other controllers can invalidate it. After a restart, reload, or failed movement command, raise the screen fully to establish the estimate again.

For automatic recalibration, set an optional **Daily calibration time** in Home Assistant's local time zone. The integration repeats the full-raise procedure only when the screen is idle, motion is enabled, and the last completed position was 100%. That last raised state is remembered across a restart so the next daily check can recalibrate it.

## Tests

Run `python -m unittest discover -s tests -v` from the repository root. The relay tests use a local simulated controller.
