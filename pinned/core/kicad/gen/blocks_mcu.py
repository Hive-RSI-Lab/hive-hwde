"""core: ESP32, USB-UART, expander (B5, B12 logic) and Ethernet (B6)."""
from __future__ import annotations

from design import C, FB, P, R, sheet  # noqa: F401

A = "aiee:"

# ------------------------------------------------------------ B5 ESP32
sheet("mcu")
P("U7", A + "ESP32-WROVER-E-N8R8", "ESP32-WROVER-E-N8R8",
  A + "WIFI-SMD_ESP32-WROVER-IE8MB",
  {"n:GND": "GND", "n:3V3": "+3V3", "n:EN": "ESP_EN",
   "n:SENSOR_VP": "I2S_DIN", "n:SENSOR_VN": "EXP_INT",
   "n:IO34": "ISENSE", "n:IO35": "IR_IN", "n:IO32": "LCD_SCLK",
   "n:IO33": "LCD_MOSI", "n:IO25": "RMII_RXD0", "n:IO26": "RMII_RXD1",
   "n:IO27": "RMII_CRS_DV", "n:IO14": "I2C_SCL", "n:IO12": "I2S_DOUT_SUB",
   "n:IO13": "I2C_SDA", "n:IO15": "LCD_DC", "n:IO2": "I2S_BCK",
   "n:IO0": "ETH_CLK", "n:IO4": "I2S_LRCK", "n:IO5": "I2S_DOUT_MAIN",
   "n:IO18": "MDIO", "n:IO19": "RMII_TXD0", "n:IO21": "RMII_TX_EN",
   "n:RXD0": "U0RXD", "n:TXD0": "U0TXD", "n:IO22": "RMII_TXD1",
   "n:IO23": "MDC", "n:NC": "NC"},
  lcsc="C2973667", role="Wi-Fi/BT module, 8 MB flash + 8 MB PSRAM")
C("C30", "22uF", "+3V3", pkg="0805", ic="U7", pin="2")
C("C31", "100nF", "+3V3", ic="U7", pin="2")
R("R30", "10k", "+3V3", "ESP_EN")
C("C32", "1uF", "ESP_EN")
R("R31", "10k", "+3V3", "ETH_CLK", role="GPIO0 boot strap pull-up")
# CH340C serial + auto-reset
P("U8", A + "CH340C", "CH340C", A + "SOP-16_L10.0-W3.9-P1.27-LS6.0-BL",
  {"n:GND": "GND", "n:TXD": "U0RXD", "n:RXD": "U0TXD", "n:V3": "+3V3",
   "n:D+": "USB_DP", "n:D-": "USB_DM", "n:VCC": "+3V3",
   "n:~{DTR}": "UART_DTR", "n:~{RTS}": "UART_RTS"},
  lcsc="C84681", role="USB-UART for console and flashing", nc_rest=True)
C("C33", "100nF", "+3V3", ic="U8", pin="16")
P("Q4", A + "S8050_C2146", "S8050", A + "SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR",
  {"n:B": "AR_B1", "n:E": "UART_RTS", "n:C": "ESP_EN"}, lcsc="C2146",
  basic=True, role="auto-reset: EN")
R("R32", "10k", "UART_DTR", "AR_B1")
P("Q5", A + "S8050_C2146", "S8050", A + "SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR",
  {"n:B": "AR_B2", "n:E": "UART_DTR", "n:C": "ETH_CLK"}, lcsc="C2146",
  basic=True, role="auto-reset: GPIO0")
R("R33", "10k", "UART_RTS", "AR_B2")
R("R34", "2.2k", "+3V3", "I2C_SDA")
R("R35", "2.2k", "+3V3", "I2C_SCL")
# TCA9555 expander (architecture/blocks.md map)
P("U17", A + "TCA9555PWR", "TCA9555", A + "TSSOP-24_L7.8-W4.4-P0.65-LS6.4-BL",
  {"n:~{INT}": "EXP_INT", "n:A0": "GND", "n:A1": "GND", "n:A2": "GND",
   "n:GND": "GND", "n:VCC": "+3V3", "n:SCL": "I2C_SCL", "n:SDA": "I2C_SDA",
   "n:P00": "AMP_A_RST", "n:P01": "AMP_B_RST", "n:P02": "DJ_DIRECT",
   "n:P03": "PVDD_EN", "n:P04": "DAC_EN", "n:P05": "PHY_RST",
   "n:P06": "LCD_BL", "n:P07": "LED_STATUS", "n:P10": "AMP_FAULT",
   "n:P11": "AMP_OTW", "n:P12": "ENC_A", "n:P13": "ENC_B",
   "n:P14": "ENC_SW", "n:P15": "BTN_1", "n:P16": "BTN_2", "n:P17": "LCD_CS"},
  lcsc="C465732", role="I2C I/O expander, 0x20")
C("C34", "100nF", "+3V3", ic="U17", pin="24")
R("R36", "10k", "+3V3", "EXP_INT")
for i, net in enumerate(["AMP_A_RST", "AMP_B_RST", "DJ_DIRECT", "PVDD_EN",
                         "DAC_EN", "PHY_RST"]):
    R(f"R{40 + i}", "100k", net, "GND", role="safe default: off")
for i, net in enumerate(["AMP_FAULT", "AMP_OTW", "ENC_A", "ENC_B", "ENC_SW",
                         "BTN_1", "BTN_2", "LCD_CS", "IR_IN"]):
    R(f"R{50 + i}", "10k", "+3V3", net)
for i, net in enumerate(["ENC_A", "ENC_B", "ENC_SW"]):
    C(f"C{37 + i}", "10nF", net, role="encoder debounce")

# ------------------------------------------------------------ B6 Ethernet
sheet("ethernet")
P("U9", A + "LAN8720A-CP-TR", "LAN8720A", A + "QFN-24_L4.0-W4.0-P0.50-BL-EP2.6",
  {"n:VDD2A": "+3V3E", "n:VDD1A": "+3V3E", "n:VDDIO": "+3V3",
   "n:LED2/nINTSEL": "NC", "n:LED1/REGOFF": "NC", "n:XTAL2": "NC",
   "n:XTAL1/CLKIN": "ETH_CLK", "n:VDDCR": "PHY_VDDCR",
   "n:RXD1/MODE1": "RMII_RXD1", "n:RXD0/MODE0": "RMII_RXD0",
   "n:RXER/PHYAD0": "NC", "n:CRS_DV/MODE2": "RMII_CRS_DV", "n:MDIO": "MDIO",
   "n:MDC": "MDC", "n:nINT/REFCLKO": "NC", "n:nRST": "PHY_NRST",
   "n:TXEN": "RMII_TX_EN", "n:TXD0": "RMII_TXD0", "n:TXD1": "RMII_TXD1",
   "n:TXN": "ETH_TXN", "n:TXP": "ETH_TXP", "n:RXN": "ETH_RXN",
   "n:RXP": "ETH_RXP", "n:RBIAS": "PHY_RBIAS", "n:EP": "GND"},
  lcsc="C45223", role="10/100 RMII PHY, REF_CLK in from ESP32 GPIO0")
P("Y2", A + "OT322550MJBA4SL", "50MHz", A + "x",
  {"1": "PHY_RST", "2": "GND", "3": "ETH_OSC", "4": "+3V3"}, lcsc="C717684",
  role="RMII 50 MHz clock into PHY CLKIN and ESP32 GPIO0; held off (OE low) "
       "until firmware releases PHY_RST, so GPIO0 boots clean")
C("C46", "100nF", "+3V3", ic="Y2", pin="4")
R("R60", "33R", "ETH_OSC", "ETH_CLK", role="50 MHz clock series term")
R("R67", "10k", "PHY_RST", "PHY_NRST", role="nRST lags the clock enable ~1 ms")
C("C47", "100nF", "PHY_NRST")
C("C40", "1uF", "PHY_VDDCR", ic="U9", pin="6")
C("C41", "100nF", "PHY_VDDCR")
C("C42", "100nF", "+3V3E", ic="U9", pin="1")
C("C43", "100nF", "+3V3E", ic="U9", pin="19")
C("C44", "100nF", "+3V3", ic="U9", pin="9")
R("R61", "12k", "PHY_RBIAS", "GND", role="RBIAS (12.1k 1% spec; 12k is inside 1%)")
R("R62", "1.5k", "+3V3", "MDIO")
for i, net in enumerate(["ETH_TXP", "ETH_TXN", "ETH_RXP", "ETH_RXN"]):
    R(f"R{63 + i}", "49.9R", net, "+3V3E", role="line termination")
P("J2", A + "HR911105A_C12074", "HR911105A", A + "RJ45-TH_HR911105A",
  {"n:TD+": "ETH_TXP", "n:TD-": "ETH_TXN", "n:RD+": "ETH_RXP",
   "n:RD-": "ETH_RXN", "4": "+3V3E", "5": "+3V3E", "n:CHSGND": "GND",
   "SH1": "GND", "SH2": "GND"},
  lcsc="C12074", role="RJ45 with magnetics", nc_rest=True)
C("C45", "100nF", "+3V3E", role="magjack centre tap")

import blocks_audio  # noqa: E402,F401
