# Other ESP32 Reticulum firmwares

Survey as of 2026-09-27.

## General ESP32 firmwares

The official RNode firmware (markqvist) is still a host-driven KISS radio modem. It has supported the T-Deck since v1.76, and on boards with a screen it draws only a status display.

attermann/microReticulum_Firmware is the main standalone firmware. It is a fork of RNode_Firmware with the microReticulum C++ stack embedded, and it has a family of forks: Reticulum-Community, ScotMesh, michelangelomo, DrLexus11 and genemichael. It has supported the T-Deck since 1.85.10, but only as a transport node. Configuration happens three ways: through `rnodeconf`; through a web console on a WiFi access point at `http://10.0.0.1/`, entered by double-rebooting; or through a provisioning subsystem with a draft-then-commit workflow that can also be reached remotely over a Reticulum link. The serial port carries KISS-framed logs only, with no command line. Native Linux/macOS builds exist for single-board computers.

Transport-node spin-offs built on microReticulum:

- jrl290/RTNode-HeltecV4 is a LoRa-to-TCP transport node for the Heltec V4.
- 5ugAv/RTNode-2400 and its fork GrayHatGuy/RTNode-2400 are RTNode variants for the Heltec V3/V4, the XIAO ESP32-S3 and nRF, and the T3S3 with SX1280 or LR1121 radios (2.4 GHz support still unverified).
- dobrevit/RetiMesh_Node is an ESP32-S3 transport node with an SX1262 or SX127x radio (reference board: T3-S3). It draws on an OLED with the Adafruit libraries, keeps its settings in non-volatile storage, and has a web settings page at `http://10.42.0.1/settings.html`. Its 115200-baud serial console is the only real console in this group, and it is minimal: `VERSION`, `STATUS`, `BOOTLOADER CONFIRM`. It also offers WiFi access point, USB CDC-NCM and PPP access.

## T-Deck firmwares

Pyxis (torlando-tech) is written in C++ on Arduino, on a heavily patched microReticulum fork. It does LXMF messaging, LXST voice calls, NomadNet browsing and BLE. The UI is LVGL 8.4 with PSRAM buffers. On serial it has only a `T:<CMD>` test protocol, compiled in with `-DPYXIS_TEST_HOOKS`. The protocol covers identity, the path table, sending, propagation sync, calls and screenshots. It is meant for test automation rather than for operating the device, and it shares the line with the log stream. Everything else is configured through on-screen settings.

rsDeck (Ratspeak) is C++ on Ratspeak's own stack. At boot it chooses between a standalone LXMF messenger and a bundled copy of the RNode firmware (RNode mode over BLE or USB). The display is LVGL on LovyanGFX. The flush uses a blocking `pushPixels` because the display shares the SPI bus with the SX1262.

ratspeak-handheld (Ratspeak) replaces rsDeck, rsPager and rsCardputer with a single codebase. It has a Rust core (rsReticulumLite and rsLXMFLite) with C++ services, and runs on the T-Deck Plus, T-Pager, Cardputer Adv and ThinkNode M9. The display uses its own `St7789Display` driver, an LVGL runtime and a "canvas" UI layer. It has a diagnostic serial-command parser and a remote UI snapshot feature, but no documented operator console. It is licensed AGPL-3.0.

varna9000/reticulum-tdeck is MicroPython on µReticulum, with native C modules for codec2, crypto and a DMA-driven ST7789 driver. It has its own drawing code (no LVGL), with diff-based redraws. It supports LXMF, NomadNet pages, rnsh shell sessions, RRC chat rooms and codec2 voice messages. There is no REPL or command line. Configuration is done in the on-device Setup menu or by editing `tdeck_config.py` and `/rns/settings.json`.

## Consoles and control

None of these firmwares treats serial as a proper operator console. The T-Deck firmwares give full control only through the on-device UI and have test or diagnostic hooks on serial. The transport-node firmwares do their control through web pages or `rnodeconf`. Only microReticulum_Firmware's provisioning subsystem offers structured configuration that can be reached remotely.

## Path requests forwarded on the receiving interface

Clones of the transport-node firmwares and their microReticulum stacks are in `competition/` in this workspace.

Upstream Python RNS forwards a discovery path request on every interface except the one it arrived on. It does this only when the receiving interface's mode is in `DISCOVER_PATHS_FOR` (access point, gateway or roaming).

attermann/microReticulum has changed this since 2024-06 (commit f415b5e, marked "CBA EXPERIMENTAL … to support path-finding over LoRa mesh"). In `Transport::path_request`, the discovery branch forwards on every interface, including the one the request came in on. Loops are held off only by the existing `_discovery_path_requests` and `_discovery_pr_tags` dedup. Since 2026-06 the change has sat behind `RNS_SAME_INTERFACE_PATH_REQUESTS`, which defaults to 1. The branch that forwards requests from local clients still excludes the receiving interface. The upstream firmware sets the LoRa interface to `MODE_GATEWAY`, so the change is live there.

The stack forks from DrLexus11, ScotMesh and dobrevit (branch `retimesh/combined`) carry the same flag with the same default. The Reticulum-Community stack and the copy vendored into RTNode-2400 (5ugAv and GrayHatGuy) have an older, unconditional `if (true)` version. RTNode-2400 also sets LoRa to `MODE_GATEWAY`, so the change is active there too.

jrl290/RTNode-HeltecV4 goes further. Any path request that arrives on an interface triggers discovery, whatever that interface's mode, and `MODE_FULL` and `MODE_BOUNDARY` are both in `DISCOVER_PATHS_FOR`. The request is forwarded on every interface except back to the sender on a backbone interface (`interface.is_backbone()`), because echoing on a point-to-point link is just noise.

dobrevit/RetiMesh_Node inherits the flag, but its LoRa interface defaults to `MODE_FULL`. That mode is not in `DISCOVER_PATHS_FOR`, so out of the box it does not forward unknown-path requests over LoRa at all. The user has to set the LoRa mode to gateway, access point or roaming first.
