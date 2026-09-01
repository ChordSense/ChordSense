# ChordSense Display Market Research

## Research Method and Supplied Specifications

An OpenAI agent was used to perform this market research. The agent searched current manufacturer documentation and retailer product pages, compared the available displays against the ChordSense requirements, and reported the results below.

The following project specifications and design constraints were supplied to the agent:

- ChordSense uses a **Raspberry Pi 5**.
- The current screen concept is approximately **15 cm × 10 cm**, roughly the size of a 7-inch-class display.
- The team wants a larger screen that remains practical for a compact tabletop guitar-practice device.
- The primary display task is showing synchronized guitar tabs and chord diagrams at playing distance.
- The proposed tab preview shows **previous, current, and next** chord diagrams simultaneously, with the current chord larger and more prominent.
- The screen should be used in landscape orientation.
- Physical Play, Stop, Mode, Library, Back, Forward, Mute, Volume, and Power controls remain available; touch is supplementary.
- ChordSense must retain an HDMI output for an external monitor or television.
- The enclosure must also accommodate the Raspberry Pi, audio electronics, cooling, guitar input, audio outputs, USB host, and power connections.
- Low cable count, reliable Raspberry Pi OS support, readability, and straightforward enclosure integration are more important than selecting the absolute cheapest panel.
- The research should include exact product or purchase links.

## Recommendation

The selected display is the **official Raspberry Pi Touch Display 2, 10-inch**, mounted in landscape orientation.

This display was chosen because it is directly compatible with the confirmed Raspberry Pi 5 and provides the best combination of size, resolution, integration, and capabilities for ChordSense:

- 10-inch-class size provides approximately twice the visible area of a similarly proportioned 7-inch display without making the product as large as a desktop monitor.
- Native 1200 × 1920 resolution becomes **1920 × 1200 in landscape**, leaving enough space for previous, current, and next chord diagrams plus playback information.
- Four-lane DSI carries display and touch data without consuming a micro-HDMI port, preserving HDMI for the required external display output.
- The display is powered by the Raspberry Pi, reducing internal power wiring and eliminating a separate display power adapter.
- It uses an IPS panel with ten-point capacitive touch.
- Raspberry Pi supplies the operating-system integration and publishes a production commitment through at least January 2030.
- A Raspberry Pi 5 can mount directly behind the display, simplifying mechanical integration.

### Selected display specifications

| Attribute                            | Specification                                            |
| ------------------------------------ | -------------------------------------------------------- |
| Product                              | Raspberry Pi Touch Display 2, 10-inch                    |
| Compute compatibility                | Raspberry Pi 5 and supported Compute Module arrangements |
| Native orientation                   | Portrait                                                 |
| Native resolution                    | 1200 × 1920                                              |
| ChordSense orientation               | Landscape, 1920 × 1200                                   |
| Display technology                   | IPS TFT                                                  |
| Touch                                | Ten-point capacitive                                     |
| Outline dimensions                   | 248.1 mm × 168.9 mm × 16 mm                              |
| Active area                          | 216.6 mm × 135.4 mm                                      |
| Display interface                    | Four-lane DSI                                            |
| Power                                | Supplied by the Raspberry Pi                             |
| Official list price at research time | $80                                                      |

Purchase and reference links:

- [Official Raspberry Pi product page and reseller selector](https://www.raspberrypi.com/products/touch-display-2/)
- [Official Raspberry Pi integration documentation](https://www.raspberrypi.com/documentation/accessories/touch-display-2.html)
- [CanaKit direct product page](https://www.canakit.com/raspberry-pi-touch-display-2-10-inch.html) — listed at $97 and in stock when researched

## Alternatives Evaluated

| Product                                                                                      | Price when researched | Interface        |  Resolution | Findings                                                                                                                                                             |
| -------------------------------------------------------------------------------------------- | --------------------: | ---------------- | ----------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Waveshare 10.1inch DSI LCD (C)](https://www.waveshare.com/10.1inch-DSI-LCD-C.htm)           |                $79.99 | DSI + I²C touch  |  1280 × 800 | Good Pi 4/5 compatibility, 178° IPS panel, optical bonding, and ten-point touch. Lower resolution and third-party setup make it the second choice.                   |
| [Waveshare 10.1DP-CAPLCD](https://www.waveshare.com/10.1DP-CAPLCD.htm)                       |                $74.99 | HDMI + USB touch |  1280 × 800 | Useful for rapid prototyping and broadly compatible. Requires additional internal cables and display power and consumes an HDMI output.                              |
| [Waveshare 13.3inch DSI LCD](https://www.waveshare.com/product/13.3inch-dsi-lcd.htm)         |       $152.99–$156.99 | DSI + I²C touch  | 1920 × 1080 | Excellent readability and preserves HDMI, but significantly increases enclosure size, power/thermal burden, and cost. Better suited to a future ChordSense XL study. |
| [Waveshare 13.3inch HDMI LCD (H)](https://www.waveshare.com/product/13.3inch-hdmi-lcd-h.htm) |               $159.99 | HDMI + USB touch | 1920 × 1080 | Works well as an external demonstration monitor but requires a 12 V supply and more cabling, making it a poor embedded choice.                                       |

## Size and Enclosure Implications

The existing 15 cm × 10 cm estimate has an approximately 18 cm or 7.1-inch diagonal. Moving to a 10-inch-class display increases the diagonal by roughly 42% and approximately doubles visible area for a similar aspect ratio. A 13.3-inch panel would provide roughly 3.5 times the area of the current concept and would begin to dominate the product.

The 10-inch display supports an initial enclosure target of approximately:

- 285–305 mm wide;
- 195–215 mm across the sloped front face; and
- 45–65 mm maximum rear depth.

The face should use a 10–15° viewing angle or an adjustable stand. Final dimensions must be confirmed in CAD after accounting for the audio board, Raspberry Pi cooling, wall thickness, jack clearance, button mechanisms, and cable bend radii.

## Procurement Conclusion

Purchase one official Raspberry Pi Touch Display 2 10-inch panel for UI, mechanical, thermal, and viewing-distance validation with the Raspberry Pi 5. Retain the physical controls even though the display supports touch. Consider a 13.3-inch version only if testing demonstrates that the 10-inch chord diagrams are not readable at the intended playing distance.

Prices, stock, taxes, shipping, and reseller markup can change. Confirm availability and return terms before ordering.
