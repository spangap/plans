# hw-elecrow-thinknode-m9 — what a straddle for it would cost

The Elecrow ThinkNode M9 is a T-Deck-shaped handheld: ESP32-S3R8 (16 MB flash,
8 MB octal PSRAM), a 2.4" ST7789 240x320 panel with **no touch**, a 37-key QWERTY
keypad behind a companion MCU on I2C, an LR1110 modem, a GNSS receiver, microSD,
PCF8563 clock, QMI8658B IMU, QMC6309 magnetometer, a buzzer and a 2300 mAh cell.

Structurally it is `hw-lilygo-tdeck` with a different modem and no pointer, so
the straddle is mostly a pin map plus one board-local input HAL. Four things are
genuinely new to the platform; none of them is a wall.

## The pin map

Taken from Elecrow's wiki GPIO table and cross-checked against the two upstream
board definitions (MeshCore `variants/thinknode_m9`, Meshtastic
`variants/esp32s3/ELECROW-ThinkNode-M9`). They agree on the bus, modem, panel,
card, keypad and clock lines, and disagree on three (see below the table).

| Function | Pins |
| --- | --- |
| Shared SPI (LCD + microSD + LR1110) | SCK 40, MOSI 47, MISO 38 |
| LR1110 | NSS 39, BUSY 41, IRQ 42, RESET 45, TCXO 3.3 V, RF switch on chip DIO5/DIO6 |
| ST7789 | CS 16, DC 15, RST 14, TE 19, backlight 17 (active low) |
| microSD | CS 48 |
| Keypad MCU | I2C port 0: SDA 20, SCL 21, addr 0x6C; KB_INT 12 (idle low, rising on press); key lamp 46 |
| Sensors | I2C port 1: SDA 7, SCL 6 — PCF8563 0x51, QMI8658B, QMC6309 |
| GNSS | UART RX 2, TX 3, PPS 4, RESET 5, standby/wake 10, power enable 11 (active low) |
| Misc | peripheral rail enable 18 (active low), buzzer 9, external-power detect 1, charge-done 8 |

Three lines are unsettled, and the device itself is what settles them:

- **GPIO13** is the torch LED to MeshCore and the battery ADC to Meshtastic. The
  reconstructed keypad driver says the torch moved off 13 between board
  revisions, so which one a given unit has is a question for a meter.
- **The GNSS part**: the wiki says ATGM336H, MeshCore says L76K at 9600 baud,
  Meshtastic drives it at 115200. Autobaud as `gps` already does on the T-Deck,
  and treat the part as batch-dependent.
- **The keypad MCU**: the wiki's block diagram says ESP32-S2 and gives a row/
  column matrix; the upstream drivers say STC8H and never scan a matrix. Only
  the I2C face matters to us, and on that they agree.

The keypad reports one code per press over I2C — ASCII for printable keys,
`0x8x` for the function row and `0xbx` for the arrows, with separate codes for
the long-press of a key. Registers seen in use: `0x01..0x04` battery millivolts
little-endian, `0x03` long-press threshold in ms (write, 700 ms is upstream's
choice — the write and the battery read overlap, so one of the two register maps
is only partly right), `0x05` matrix key, `0x06` write 1 to sleep. There is no
release or repeat event: a held arrow produces one press and nothing more.

## What the platform already has

- **LR1110 as a chip.** `iface-lora` dispatches `LR11x0` across begin, RSSI,
  SF/BW/CR, sync word, implicit header and receive, and `CONFIG_LORA0_RADIO_LR1110`
  already exists. `detect_probe.h` already identifies the family by GetVersion.
- **PCF8563** is `spangap-rtc`'s default chip; **QMI8658** is the only chip the
  `imu` straddle speaks; `gps` takes exactly the UART/PPS/power/reset lines this
  board wires.
- **One SPI bus shared by panel, card and modem**, arbitrated by `spi_helper` —
  the T-Deck runs the identical arrangement on the identical three chip-selects.
- **A touchless panel**: `CONFIG_LCD_TOUCH_CONTROLLER_NONE` is the default, and
  the shell, settings and apps all carry an LVGL keypad focus group.
- **16 MB flash / 8 MB octal PSRAM** with the same `MAX_FIRMWARE_KB=8192` split
  the T-Deck and the 2.8B use.

## The four new things

**1. The LR11x0 RF switch table.** The M9 routes its front end from the modem's
own DIO5/DIO6: receive is DIO5, transmit is DIO5+DIO6, the high-power PA path is
DIO6 alone. Without that table the part transmits into the wrong path. Today
only the LR2021 gets a chip-driven switch applied (`lr2021ApplyDio`, via raw
`SET_DIO_RF_SWITCH_CONFIG` commands); LR11x0 needs RadioLib's own
`setRfSwitchTable` with the `RADIOLIB_LR11X0_DIO5/DIO6` pin constants — the same
call `lora_fem.cpp` already makes for MCU-driven front ends. The `LORA0_LR_RFSW_*`
masks carry the right idea but the wrong mode set: they model the LR2021's
idle/RX/TX/RX_HF/TX_HF/TX-bypass, and LR11x0 wants a distinct **TX_HP** mode
instead of the bypass. Either add a sixth mask or give the LR11x0 family its own,
and drop "LR2021 only" from the help text.

**2. The keypad is the entire input.** No touch, no trackball, no reachable
button. Every board so far has shipped with a pointer, so the keypad-only path
through the shell, settings and apps exists but has never been the only way
through anything — expect to find focus holes in it, and budget for that rather
than for the driver, which is small: an interrupt on GPIO12, an I2C read, a
scancode table, and the key lamp on GPIO46. The T-Deck's
`conditional/spangap-lcd/src/tdeck_lcd.cpp` is the shape to copy, minus the
pointer and multiclick halves.

**3. No native USB.** GPIO19 and GPIO20 are the ESP32-S3's USB D-/D+, and this
board spends them on the panel's TE line and the keypad's SDA. The USB-C port
goes to a CH340K on UART0 instead. So this is the first board that must override
the platform default `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y` to a UART console,
and the first where `CONFIG_SPANGAP_USB_CDC` can never apply. Desktop flashmon is
fine (Web Serial drives a CH340 like anything else); flashmon on Android is not —
its WebUSB road deliberately does not offer bridge chips.

**4. Battery sensing does not want an ADC here.** The only pin anyone points at
the cell is GPIO13, which is ADC2_CH2 — and ADC2 on the S3 is unavailable while
WiFi is up, where every other board here reads its cell on ADC1. GPIO13 is also
the pin the two upstreams disagree about. Both problems have the same answer: the
keypad MCU reports battery millivolts in its own registers, so read it over I2C
and leave the pin alone.

Smaller: the **QMC6309 magnetometer** has no driver anywhere in the platform (the
T-Beam Supreme leaves its own unwired), so heading is out of scope unless someone
writes one. The **buzzer** is a board-local GPIO, like the 2.8B's. `detect_radio`
reports every LR11xx as the slug `lr1121`, so an M9 would identify as an lr1121
board until that probe distinguishes the device byte (0xDF/0xDA/0xDB). And the
LR1110's front end needs its own `LORA0_TX_CAL` entries before any announced dBm
is true — a lab job, not a bring-up one.

## Sourcing and licensing

Elecrow publishes no schematic and no repo for the M9; the wiki GPIO table and
the block diagram are the whole of the vendor documentation. Their reference
source tree is also incomplete — the keypad driver is missing from it, and
Meshtastic's copy was reconstructed from the shipped firmware ELF.

Upstream is therefore where the hardware knowledge actually lives:

- **MeshCore** (MIT) has the board in-tree with repeater, room-server, companion
  and KISS builds. Its pin definitions are usable directly, with attribution.
- **Meshtastic** (GPL-3.0) has the fuller port — panel, keypad, sleep, battery.
  Read it for facts, write our own code: our straddles are Apache-2.0.

## Verdict

Nothing blocking. The LR11x0 switch table is a contained change in `iface-lora`
that any future LR1110 board needs anyway; the rest is an ordinary board
straddle. The one estimate that could run long is keypad-only navigation, which
is a UI question rather than a hardware one and is best answered with the device
in hand.

Order of work: pin map and detect signature → panel and card on the shared bus →
LR11x0 RF switch, then the modem → keypad HAL → GNSS, clock, IMU → console over
UART0 → battery over I2C → TX calibration.
