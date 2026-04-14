We plan to add a companion app to ChordSense so users can upload songs, control the system, and view generated tabs more easily. I looked into mobile app development tools and found that a cross-platform framework would be the best fit, since it would let one app run on both Android and iPhone while still supporting file handling, networking, and a clean UI. Flutter and React Native both support this kind of development, so either would be a reasonable starting point.

From this research, I learned that Bluetooth is useful for connecting to the device and sending smaller control data, while larger transfers such as audio files may work better through a local network connection. I also found that keeping the app focused on user interaction, rather than direct low-level device management, would fit well with the current system design. In this setup, users would control ChordSense through the companion app or the physical buttons on the hardware, while the deployed frontend continues to manage the main system interaction.

This research helped show that a companion app could be added without changing the core design of ChordSense. Instead of replacing the current interface, the app would serve as an additional way for users to interact with the system.



Flask file uploads: https://flask.palletsprojects.com/en/stable/patterns/fileuploads/
Flask quickstart / request files: https://flask.palletsprojects.com/en/stable/quickstart/
Flask security and upload size limits: https://flask.palletsprojects.com/en/stable/web-security/
Flutter docs: https://docs.flutter.dev/
React Native docs: https://reactnative.dev/
Android BLE overview: https://developer.android.com/develop/connectivity/bluetooth/ble/ble-overview