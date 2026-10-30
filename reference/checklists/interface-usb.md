# Checklist: interface-usb (USB device/host on the board)

- DP/DM as a matched pair: routed together, 90R diff intent, skew within
  budget; reference plane continuous under the pair (FS: good practice;
  HS: mandatory).
- Device pull-up: 1.5k DP->3V3 for FS device when the PHY/MCU lacks an
  internal one (STM32F103: NO internal - external required); host: 15k
  pull-downs.
- VBUS: sense divider if monitored (F103 pins not all 5V-tolerant); input
  cap <=10uF at the connector per inrush spec unless soft-start; note
  100mA pre-enumeration budget stance.
- ESD array at the connector (or explicit waiver); shield vs GND strategy.
- Series termination per PHY datasheet (F103: none needed FS; do not
  cargo-cult 22R).
- Connector pads: micro-B/C mechanical anchors soldered; D+/D- pin order
  verified against the CONNECTOR datasheet not memory.
